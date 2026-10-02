# SmartSight CloudCam // System Implementation Guide
**Deployment Target:** Raspberry Pi 4 (Debian 13 Trixie)  
**Wearer Unit:** `ratish@raspberrypi4`  
**Base Directory:** `/home/ratish/Desktop/SmartSight/SmartSight`  
**Environment:** `/home/ratish/Desktop/SmartSight/smartsight_env`  
**Optical Source:** Android Phone (DroidCam over Tailscale Mesh)  
**Cloud Backend:** Supabase (Database + Storage Bucket `cloudcam_data` + Realtime)

---

## 1. System Topology & Directory Map

```
/home/ratish/Desktop/SmartSight/
├── smartsight_env/                      # Virtual Environment (Python 3.12+ / Trixie)
└── SmartSight/                          # Project Root
    ├── captures/                        # Local image queue buffer (offline fallback)
    ├── cloudcam/                        # CloudCam Subsystem
    │   ├── cloudcam_daemon.py           # Daemon service (DroidCam grab, Supabase sync)
    │   ├── offline_queue.db             # Local SQLite database (offline persistence)
    │   ├── .env                         # Supabase credentials & Tailscale IP
    │   ├── requirements.txt             # Pinned Python dependencies
    │   ├── schema.sql                   # Supabase database migration script
    │   └── smartsight-cloudcam.service  # Systemd service unit file
    ├── control_centre/                  # Tactical LAEP Mission Control UI
    │   ├── index.html                   # Mission Control Dashboard (Space Grotesk / Inter)
    │   ├── style.css                    # LAEP Aerospace design tokens & styling
    │   └── app.js                       # Realtime Supabase synchronization engine
    └── LiveCam/                         # Local vision & obstacle pipeline
```

---

## 2. Supabase Backend Setup (2 Minutes)

### Step 2.1: Run Database Schema
1. Open your Supabase Dashboard $\rightarrow$ **SQL Editor**.
2. Run the SQL script from [`cloudcam/schema.sql`](schema.sql) (creates `device_telemetry`, `device_commands`, `captures`, Realtime publications, and RLS policies).

### Step 2.2: Storage Bucket & Policy (`cloudcam_data`)
1. In Supabase Dashboard, open **Storage** $\rightarrow$ Create bucket named: `cloudcam_data`.
2. Toggle **Public bucket** $\rightarrow$ **ON** (so the UI can render images directly).
3. Under **Policies** for `cloudcam_data`, click **New Policy** $\rightarrow$ **For full customization**:
   * **Policy Name:** `Allow public access to cloudcam_data`
   * **Allowed Operations:** Check `SELECT`, `INSERT`, `UPDATE`
   * **Target Roles:** Select `anon` and `authenticated`
   * **Policy Definition:**
     Leave `bucket_id = 'cloudcam_data'` *(or type `true`)*. Both evaluate to true and grant full access to this bucket.
   * Click **Review** $\rightarrow$ **Save policy**.

---

## 3. Remote DroidCam Over Tailscale Setup

Tailscale bridges the Android phone and Raspberry Pi over different networks (4G/5G, Wi-Fi) without port-forwarding or CGNAT issues:

1. **On Android Phone:**
   * Install **Tailscale** from Google Play Store $\rightarrow$ Log in and turn **ON**.
   * Note the Phone's Tailscale IP (e.g. `100.95.120.40`).
   * Launch **DroidCam** (Port `4747`).
2. **On Raspberry Pi (`ratish@raspberrypi4`):**
   ```bash
   # Check Tailscale status and verify connection to phone
   tailscale status
   tailscale ping <PHONE_TAILSCALE_IP>

   # Test grabbing a snapshot from phone over the internet
   curl -I http://<PHONE_TAILSCALE_IP>:4747/cam/1/frame.jpg
   ```
   *(Expected response: `HTTP/1.1 200 OK`)*

---

## 4. Raspberry Pi Deployment (`ratish@raspberrypi4`)

### Step 4.1: Environment & Dependencies
Your virtual environment is located at `~/Desktop/SmartSight/smartsight_env`:

```bash
# Activate your existing virtual environment
source /home/ratish/Desktop/SmartSight/smartsight_env/bin/activate

# Install required packages
cd /home/ratish/Desktop/SmartSight/SmartSight/cloudcam
pip install -r requirements.txt
```

### Step 4.2: Configure `.env`
Edit `/home/ratish/Desktop/SmartSight/SmartSight/cloudcam/.env`:

```ini
# Supabase Project Credentials (from Settings -> API)
SUPABASE_URL=https://your-project.supabase.co
# Use service_role key for the Pi daemon (allows unrestricted storage write)
SUPABASE_KEY=your-supabase-service-role-key-here
# Bucket name in Supabase Storage
SUPABASE_BUCKET=cloudcam_data

# Device Identifier
DEVICE_ID=smartsight-alpha-01

# Android Phone Tailscale IP & DroidCam Port
DROIDCAM_IP=100.95.120.40
DROIDCAM_PORT=4747

# Local Capture Directory for Offline Queue
LOCAL_CAPTURE_DIR=/home/ratish/Desktop/SmartSight/SmartSight/captures
```

### Step 4.3: Test Run the Daemon Manually
```bash
python3 cloudcam_daemon.py
```
*(The daemon will initialize the local SQLite buffer, start transmitting telemetry every 5s, and listen for commands).*

### Step 4.4: Enable 24/7 Autostart via Systemd
The pre-configured service file [`smartsight-cloudcam.service`](smartsight-cloudcam.service) is tailored to user `ratish`:

```bash
# Copy unit file to systemd directory
sudo cp /home/ratish/Desktop/SmartSight/SmartSight/cloudcam/smartsight-cloudcam.service /etc/systemd/system/

# Reload systemd and enable on boot
sudo systemctl daemon-reload
sudo systemctl enable --now smartsight-cloudcam.service

# View live service logs
sudo journalctl -u smartsight-cloudcam.service -f
```

---

## 5. Launching the Tactical Control Centre UI

The Control Centre files are located in `/home/ratish/Desktop/SmartSight/SmartSight/control_centre/`:
* [`index.html`](../control_centre/index.html) — LAEP Mission Control Dashboard
* [`style.css`](../control_centre/style.css) — Aerospace Dark Theme (Space Grotesk / Inter / IBM Plex Mono)
* [`app.js`](../control_centre/app.js) — Realtime Supabase Telemetry & Command Dispatcher

### Local Preview:
Open `control_centre/index.html` directly in your browser, or serve it via python:
```bash
cd /home/ratish/Desktop/SmartSight/SmartSight/control_centre
python3 -m http.server 8080
```
Open `http://localhost:8080` in any web browser.

### Cloud Deployment (Vercel / Render):
* Deploy the `control_centre/` folder directly to **Vercel** (`vercel deploy --prod`) or **Render Static Site**.
* Press **`⌘K`** or **`Ctrl+K`** on the dashboard to enter your Supabase Project URL and Public Anon Key.
* All telemetry, remote capture buttons, and the SIGINT event stream will sync in real time worldwide!
