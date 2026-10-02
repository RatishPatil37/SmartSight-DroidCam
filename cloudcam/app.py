from flask import Flask, render_template, jsonify
import subprocess
import datetime
import os

app = Flask(__name__)

# --- CONFIGURATION ---
RCLONE_REMOTE = "cloud_sight"
CLOUD_FOLDER = "rpi cam"
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/capture_photo', methods=['POST'])
def capture_photo():
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"IMG_{timestamp}.jpg"
    
    # 1. Capture Photo
    # -t 500: Allows 0.5s for exposure adjustment
    cmd_cam = f"rpicam-still -o {filename} --width 1920 --height 1080 -t 500 --nopreview"
    
    if run_command(cmd_cam):
        if upload_file(filename):
            return jsonify({"status": "success", "type": "photo", "file": filename})
    
    return jsonify({"status": "error", "message": "Camera/Upload Failed"})

@app.route('/capture_video', methods=['POST'])
def capture_video():
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    raw_filename = f"temp_{timestamp}.h264"
    final_filename = f"VID_{timestamp}.mp4"
    
    # 1. Capture Video (5 Seconds)
    cmd_cam = f"rpicam-vid -t 5000 -o {raw_filename} --width 1920 --height 1080 --nopreview"
    
    if run_command(cmd_cam):
        # 2. Convert to MP4 (Readable by Android)
        print("[*] Converting to MP4...")
        cmd_convert = f"ffmpeg -r 30 -i {raw_filename} -c:v copy {final_filename} -y"
        run_command(cmd_convert)
        
        # 3. Upload
        if upload_file(final_filename):
            # Cleanup raw file
            if os.path.exists(raw_filename): os.remove(raw_filename)
            return jsonify({"status": "success", "type": "video", "file": final_filename})

    return jsonify({"status": "error", "message": "Video Failed"})

# --- HELPERS ---
def run_command(cmd):
    print(f"[*] Running: {cmd}")
    try:
        subprocess.run(cmd, shell=True, check=True)
        return True
    except:
        return False

def upload_file(filename):
    print(f"[*] Uploading {filename}...")
    # Quotes handle the space in 'rpi cam'
    cmd_upload = f'rclone copy {filename} "{RCLONE_REMOTE}:{CLOUD_FOLDER}"'
    try:
        subprocess.run(cmd_upload, shell=True, check=True)
        if os.path.exists(filename): os.remove(filename)
        return True
    except:
        return False

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
