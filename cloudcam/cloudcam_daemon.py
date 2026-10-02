"""
SmartSight CloudCam Daemon (Debian Trixie / RPi 4 & 5)
Handles:
1. Tailscale DroidCam HTTP snapshot fetching
2. Offline SQLite Queue buffering
3. Supabase Realtime command polling & execution
4. Supabase Storage upload & Postgres metadata insertion
5. Hardware & network telemetry heartbeat
"""

import os
import sys
import time
import json
import sqlite3
import datetime
import threading
import requests
import psutil
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client, Client

# --- LOAD ENVIRONMENT CONFIGURATION ---
BASE_DIR = Path(__file__).parent.resolve()
load_dotenv(BASE_DIR / ".env")

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "cloudcam_data").strip()
DEVICE_ID = os.getenv("DEVICE_ID", "smartsight-alpha-01").strip()
DROIDCAM_IP = os.getenv("DROIDCAM_IP", "100.x.y.z").strip()
DROIDCAM_PORT = os.getenv("DROIDCAM_PORT", "4747").strip()
LIVE_FEED_INTERVAL = int(os.getenv("LIVE_FEED_INTERVAL", "3"))   # seconds between live relay frames
LIVE_FEED_PATH = f"{os.getenv(\"DEVICE_ID\", \"smartsight-alpha-01\")}/live/latest.jpg"
LOCAL_CAPTURE_DIR = Path(os.getenv("LOCAL_CAPTURE_DIR", str(BASE_DIR / "captures")))
LOCAL_CAPTURE_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE_DIR / "offline_queue.db"

if not SUPABASE_URL or not SUPABASE_KEY or "your-project" in SUPABASE_URL:
    print("⚠️ [CONFIG WARNING] SUPABASE_URL or SUPABASE_KEY not properly set in .env!")
    print("👉 Edit the .env file with your valid Supabase project credentials.")

# Initialize Supabase client
supabase: Client = None
try:
    if SUPABASE_URL and SUPABASE_KEY and "your-project" not in SUPABASE_URL:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("✅ [SUPABASE] Connected successfully.")
except Exception as e:
    print(f"⚠️ [SUPABASE INIT ERROR] Could not initialize Supabase client: {e}")

# --- OFFLINE SQLITE QUEUE ENGINE ---
def init_offline_db():
    """Initializes local SQLite database to prevent any data loss when offline."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT NOT NULL,
            media_type TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            file_size_kb INTEGER,
            synced INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

def queue_offline_capture(file_path: str, media_type: str, timestamp_str: str, file_size_kb: int):
    """Saves capture metadata locally when network uplink is down."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO queue (file_path, media_type, timestamp, file_size_kb, synced)
        VALUES (?, ?, ?, ?, 0)
    """, (file_path, media_type, timestamp_str, file_size_kb))
    conn.commit()
    conn.close()
    print(f"📦 [OFFLINE QUEUE] Buffered capture locally: {file_path}")

def drain_offline_queue():
    """Background worker that flushes offline captures once internet is restored."""
    while True:
        time.sleep(15)
        if not supabase:
            continue
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT id, file_path, media_type, timestamp, file_size_kb FROM queue WHERE synced = 0")
            rows = cursor.fetchall()

            if not rows:
                conn.close()
                continue

            print(f"🔄 [SYNC WORKER] Detected {len(rows)} pending offline captures to flush...")

            for row in rows:
                row_id, file_path, media_type, ts_str, fsize = row
                if not os.path.exists(file_path):
                    cursor.execute("UPDATE queue SET synced = 1 WHERE id = ?", (row_id,))
                    conn.commit()
                    continue

                storage_filename = f"{DEVICE_ID}/{os.path.basename(file_path)}"
                with open(file_path, "rb") as f:
                    file_bytes = f.read()

                supabase.storage.from_(SUPABASE_BUCKET).upload(
                    path=storage_filename,
                    file=file_bytes,
                    file_options={"content-type": "image/jpeg" if media_type == "photo" else "video/mp4"}
                )

                public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{storage_filename}"

                supabase.table("captures").insert({
                    "device_id": DEVICE_ID,
                    "media_url": public_url,
                    "media_type": media_type,
                    "file_size_kb": fsize,
                    "captured_at": ts_str,
                    "synced_from_offline": True
                }).execute()

                cursor.execute("UPDATE queue SET synced = 1 WHERE id = ?", (row_id,))
                conn.commit()
                print(f"✅ [SYNC WORKER] Successfully flushed offline capture {row_id} -> {public_url}")

            conn.close()
        except Exception:
            # Remains offline or error during upload, will retry next cycle
            pass

# --- DROIDCAM CAPTURE LOGIC ---
def capture_droidcam_frame():
    """Fetches high-res JPEG frame directly from DroidCam over Tailscale or Local IP.
    DroidCam serves MJPEG on /video ONLY when the app is open & camera is active.
    If /video returns a small HTML page (<2KB), DroidCam is in standby mode."""
    base_url = f"http://{DROIDCAM_IP}:{DROIDCAM_PORT}"
    video_url = f"{base_url}/video"

    # --- Step 0: Check if DroidCam is reachable and in streaming mode ---
    try:
        probe = requests.get(video_url, stream=True, timeout=6)
        content_type = probe.headers.get("Content-Type", "")
        is_mjpeg = "multipart" in content_type or "jpeg" in content_type

        if not is_mjpeg:
            # DroidCam returned HTML (app in background / screen locked)
            print(f"📱 [DROIDCAM STANDBY] /video returned Content-Type: '{content_type}' (expected MJPEG).")
            print(f"   ⚡ ACTION NEEDED: Open DroidCam app on your phone, tap START, keep screen ON.")
            probe.close()

            # Still try to extract a JPEG in case Content-Type header is wrong
            bytes_data = b""
            for chunk in probe.iter_content(chunk_size=4096):
                bytes_data += chunk
                if len(bytes_data) > 8192:  # If > 8KB it might be a real stream
                    a = bytes_data.find(b'\xff\xd8')
                    b_pos = bytes_data.find(b'\xff\xd9')
                    if a != -1 and b_pos != -1 and b_pos > a:
                        jpg = bytes_data[a:b_pos+2]
                        if len(jpg) > 5000:
                            return jpg
                    break
        else:
            # Good - it's an MJPEG stream, extract first frame
            bytes_data = b""
            for chunk in probe.iter_content(chunk_size=4096):
                bytes_data += chunk
                a = bytes_data.find(b'\xff\xd8')
                b_pos = bytes_data.find(b'\xff\xd9')
                if a != -1 and b_pos != -1 and b_pos > a:
                    jpg = bytes_data[a:b_pos+2]
                    if len(jpg) > 1000:
                        probe.close()
                        return jpg
                if len(bytes_data) > 512 * 1024:  # 512KB safety cap
                    break
            probe.close()
    except Exception as e:
        print(f"⚠️ [DROIDCAM] /video request failed: {e}")

    # --- Fallback 1: Try OpenCV VideoCapture ---
    try:
        import cv2
        cap = cv2.VideoCapture(video_url)
        if cap.isOpened():
            ret, frame = cap.read()
            cap.release()
            if ret:
                ok, encoded = cv2.imencode('.jpg', frame)
                if ok and len(encoded) > 1000:
                    return encoded.tobytes()
    except Exception:
        pass

    # --- Fallback 2: Static snapshot endpoints ---
    for endpoint in ["/shot.jpg", "/cam/1/frame.jpg", "/jpeg"]:
        try:
            resp = requests.get(f"{base_url}{endpoint}", timeout=4)
            ct = resp.headers.get("Content-Type", "")
            if resp.status_code == 200 and "image" in ct and len(resp.content) > 1000:
                return resp.content
        except Exception:
            pass

    print(f"⚠️ [DROIDCAM ERROR] Could not extract frame from {base_url}")
    print(f"   → Ensure DroidCam is OPEN on your phone and camera is streaming.")
    return None

def execute_capture_command(command_id: str, command_type: str):
    """Executes capture command triggered remotely by Control Centre."""
    print(f"🚀 [COMMAND DISPATCH] Executing '{command_type}' (ID: {command_id})...")

    if supabase:
        try:
            supabase.table("device_commands").update({"status": "processing"}).eq("id", command_id).execute()
        except Exception:
            pass

    timestamp = datetime.datetime.now(datetime.timezone.utc)
    ts_str = timestamp.strftime("%Y%m%d_%H%M%S")
    filename = f"IMG_{ts_str}.jpg"
    local_path = LOCAL_CAPTURE_DIR / filename

    frame_bytes = capture_droidcam_frame()
    if not frame_bytes:
        print(f"❌ [DROIDCAM OFFLINE] Camera unreachable at {DROIDCAM_IP}:{DROIDCAM_PORT}")
        if supabase:
            try:
                supabase.table("device_commands").update({
                    "status": "failed",
                    "error_message": f"DroidCam unreachable at {DROIDCAM_IP}:{DROIDCAM_PORT}"
                }).eq("id", command_id).execute()
            except Exception:
                pass
        return

    # 1. Write to local storage first (zero data loss guarantee)
    with open(local_path, "wb") as f:
        f.write(frame_bytes)
    file_size_kb = int(len(frame_bytes) / 1024)

    # 2. Upload to Supabase Storage
    storage_path = f"{DEVICE_ID}/{filename}"
    try:
        if not supabase:
            raise ConnectionError("Supabase client not initialized")

        supabase.storage.from_(SUPABASE_BUCKET).upload(
            path=storage_path,
            file=frame_bytes,
            file_options={"content-type": "image/jpeg"}
        )
        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{storage_path}"

        supabase.table("captures").insert({
            "device_id": DEVICE_ID,
            "media_url": public_url,
            "media_type": "photo",
            "file_size_kb": file_size_kb,
            "captured_at": timestamp.isoformat(),
            "synced_from_offline": False
        }).execute()

        supabase.table("device_commands").update({
            "status": "completed",
            "executed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "payload": {"url": public_url, "file": filename}
        }).execute()
        print(f"✨ [SUCCESS] Capture completed and published: {public_url}")

    except Exception as e:
        print(f"⚠️ [UPLINK DOWN] Fallback to offline queue: {e}")
        queue_offline_capture(str(local_path), "photo", timestamp.isoformat(), file_size_kb)
        if supabase:
            try:
                supabase.table("device_commands").update({
                    "status": "completed",
                    "error_message": "Stored in offline queue (Uplink Down)"
                }).eq("id", command_id).execute()
            except Exception:
                pass

def play_audio_beacon():
    """Plays an alert tone/beacon for the wearer."""
    print("🔊 [BEACON] Playing audio alert beacon for wearer...")
    try:
        # Standard Linux aplay alert sound if available
        os.system("aplay /usr/share/sounds/alsa/Front_Center.wav >/dev/null 2>&1 || true")
    except Exception:
        pass

# --- TELEMETRY HEARTBEAT WORKER ---
def telemetry_worker():
    """Publishes hardware health & DroidCam connectivity status every 5 seconds."""
    while True:
        try:
            cpu_usage = psutil.cpu_percent(interval=1)
            ram_usage = psutil.virtual_memory().percent

            # Read CPU Temperature on Raspberry Pi OS
            cpu_temp = 0.0
            temp_path = Path("/sys/class/thermal/thermal_zone0/temp")
            if temp_path.exists():
                try:
                    cpu_temp = round(int(temp_path.read_text().strip()) / 1000.0, 1)
                except Exception:
                    pass

            # Check DroidCam connectivity — check /video Content-Type to detect REAL stream
            droid_status = "offline"
            try:
                r = requests.get(
                    f"http://{DROIDCAM_IP}:{DROIDCAM_PORT}/video",
                    stream=True, timeout=3
                )
                ct = r.headers.get("Content-Type", "")
                r.close()
                if "multipart" in ct or "image" in ct:
                    droid_status = "online"   # Camera is ACTIVELY streaming
                elif r.status_code in (200, 302, 404):
                    droid_status = "standby"  # Server up but not streaming
            except Exception:
                droid_status = "offline"

            if supabase:
                supabase.table("device_telemetry").upsert({
                    "device_id": DEVICE_ID,
                    "last_seen": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "cpu_temp": cpu_temp,
                    "cpu_usage": cpu_usage,
                    "ram_usage": ram_usage,
                    "droidcam_status": droid_status,
                    "is_online": True
                }).execute()

        except Exception:
            pass
        time.sleep(4)


# --- SUPABASE LIVE RELAY WORKER ---
def live_feed_worker():
    """Captures frames from DroidCam every LIVE_FEED_INTERVAL seconds and uploads
    as 'latest.jpg' to Supabase Storage. Powers the dashboard LIVE STREAM mode
    over HTTPS without mixed-content browser blocks."""
    print(f"📹 [LIVE RELAY] Starting live feed relay (every {LIVE_FEED_INTERVAL}s) → {SUPABASE_BUCKET}/{LIVE_FEED_PATH}")
    file_exists = False
    consecutive_failures = 0

    while True:
        try:
            if not supabase:
                time.sleep(LIVE_FEED_INTERVAL)
                continue

            frame = capture_droidcam_frame()
            if frame:
                consecutive_failures = 0
                try:
                    if not file_exists:
                        # First upload
                        supabase.storage.from_(SUPABASE_BUCKET).upload(
                            path=LIVE_FEED_PATH,
                            file=frame,
                            file_options={"content-type": "image/jpeg"}
                        )
                        file_exists = True
                        print(f"📹 [LIVE RELAY] Live feed active ✅")
                    else:
                        # Overwrite existing file
                        supabase.storage.from_(SUPABASE_BUCKET).update(
                            path=LIVE_FEED_PATH,
                            file=frame,
                            file_options={"content-type": "image/jpeg"}
                        )
                except Exception:
                    # Flip state and retry opposite operation next time
                    file_exists = not file_exists
            else:
                consecutive_failures += 1
                if consecutive_failures == 1:
                    print(f"📹 [LIVE RELAY] DroidCam unavailable — waiting for stream...")
        except Exception:
            pass
        time.sleep(LIVE_FEED_INTERVAL)
# --- COMMAND LISTENER ---
def command_listener():
    """Polls Supabase device_commands for pending missions."""
    print(f"👂 [LISTENER] Waiting for mission commands for [{DEVICE_ID}]...")
    while True:
        try:
            if not supabase:
                time.sleep(3)
                continue

            response = supabase.table("device_commands") \
                .select("*") \
                .eq("device_id", DEVICE_ID) \
                .eq("status", "pending") \
                .order("created_at", desc=False) \
                .limit(1) \
                .execute()

            if response.data and len(response.data) > 0:
                cmd = response.data[0]
                cmd_type = cmd.get("command_type", "")
                if cmd_type in ("capture_photo", "ai_inspect"):
                    execute_capture_command(cmd["id"], cmd_type)
                elif cmd_type == "audio_beacon":
                    play_audio_beacon()
                    supabase.table("device_commands").update({"status": "completed"}).eq("id", cmd["id"]).execute()
                elif cmd_type == "ping":
                    supabase.table("device_commands").update({
                        "status": "completed",
                        "payload": {"ping": "pong", "time": time.time()}
                    }).eq("id", cmd["id"]).execute()
        except Exception:
            pass
        time.sleep(1.0)

if __name__ == "__main__":
    print(f"======================================================")
    print(f"🚀 SMARTSIGHT CLOUDCAM DAEMON // UNIT: [{DEVICE_ID}]")
    print(f"📡 Target DroidCam Stream: http://{DROIDCAM_IP}:{DROIDCAM_PORT}")
    print(f"======================================================")
    init_offline_db()

    threading.Thread(target=telemetry_worker, daemon=True).start()
    threading.Thread(target=drain_offline_queue, daemon=True).start()
    threading.Thread(target=live_feed_worker, daemon=True).start()

    command_listener()
