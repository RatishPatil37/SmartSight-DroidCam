// ==========================================================
// LAEP // SMARTSIGHT MISSION ENGINE (app.js)
// Space Exploration & Telemetry Bus Controller
// ==========================================================

// --- CONFIGURATION STATE ---
const DEFAULT_URL = "https://dejkgmyhqgggrfjhynys.supabase.co";
const DEFAULT_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImRlamtnbXlocWdnZ3Jmamh5bnlzIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc5MDk1MjU5MCwiZXhwIjoyMTA2NTI4NTkwfQ.bj6zKKqbT6d840XRGCmvaPy4zN5SUW2M6cxm1cMqtbQ";
const DEFAULT_DEVICE = "smartsight-alpha-01";
const DEFAULT_BUCKET = "cloudcam_data";
const DEFAULT_GEMINI_KEY = "";
const DEFAULT_DROIDCAM_STREAM = "http://100.126.9.118:4747/video";

let savedUrl = localStorage.getItem("laep_sb_url");
let savedKey = localStorage.getItem("laep_sb_key");
if (savedUrl && savedUrl.includes("your-project")) savedUrl = null;
if (savedKey && (savedKey.includes("your-anon-key") || savedKey.includes("your-supabase"))) savedKey = null;

let config = {
    supabaseUrl: savedUrl || DEFAULT_URL,
    supabaseKey: savedKey || DEFAULT_KEY,
    bucket: DEFAULT_BUCKET,
    geminiKey: localStorage.getItem("laep_gemini_key") || DEFAULT_GEMINI_KEY,
    deviceId: localStorage.getItem("laep_device_id") || DEFAULT_DEVICE,
    droidcamStreamUrl: localStorage.getItem("laep_droidcam_url") || DEFAULT_DROIDCAM_STREAM
};

let supabaseClient = null;
let vaultCount = 0;
let missionStartTime = Date.now();
let activeLogFilter = 'all';

// --- CLOCK & ORBITAL METRICS ---
function updateClocksAndOrbit() {
    const now = new Date();
    
    // Local Time
    document.getElementById("clock-local").innerText = now.toLocaleTimeString('en-GB', { hour12: false });
    
    // Zulu / UTC Time
    const utcHours = String(now.getUTCHours()).padStart(2, '0');
    const utcMinutes = String(now.getUTCMinutes()).padStart(2, '0');
    const utcSeconds = String(now.getUTCSeconds()).padStart(2, '0');
    document.getElementById("clock-utc").innerText = `${utcHours}:${utcMinutes}:${utcSeconds}Z`;
    
    // Simulated Micro-Drift on Azimuth / Elevation
    const driftAz = (184.22 + (Math.sin(now.getTime() / 12000) * 0.12)).toFixed(2);
    const driftEl = (12.04 + (Math.cos(now.getTime() / 12000) * 0.06)).toFixed(2);
    const azEl = document.getElementById("hud-az");
    const elEl = document.getElementById("hud-el");
    if (azEl) azEl.innerText = `${driftAz}°`;
    if (elEl) elEl.innerText = `+${driftEl}°`;
}
setInterval(updateClocksAndOrbit, 1000);
updateClocksAndOrbit();

// --- LAEP MISSION EVENT LOG UTILITY ---
function logEvent(message, category = 'sys', customTag = null) {
    const stream = document.getElementById("event-stream");
    if (!stream) return;

    const div = document.createElement("div");
    div.className = `log-entry ${category}`;
    div.dataset.category = category;

    // Elapsed Mission Time: T+00:00:00
    const elapsedSec = Math.floor((Date.now() - missionStartTime) / 1000);
    const hrs = String(Math.floor(elapsedSec / 3600)).padStart(2, '0');
    const mins = String(Math.floor((elapsedSec % 3600) / 60)).padStart(2, '0');
    const secs = String(elapsedSec % 60).padStart(2, '0');
    const missionTS = `T+${hrs}:${mins}:${secs}`;

    let tag = "[SYSTEM]";
    if (category === 'cmd') tag = "[TASK_EXEC]";
    if (category === 'ack') tag = "[TELEM_ACK]";
    if (category === 'warn') tag = "[WARN_LIMIT]";
    if (category === 'err') tag = "[DOWNLINK_ERR]";
    if (customTag) tag = customTag;

    div.innerHTML = `
        <span class="log-ts">${missionTS}</span>
        <span class="log-tag">${tag}</span>
        <span class="log-text">${message}</span>
    `;

    if (activeLogFilter !== 'all' && activeLogFilter !== category) {
        div.style.display = 'none';
    }

    stream.appendChild(div);
    stream.scrollTop = stream.scrollHeight;
}

function clearEventLog() {
    const stream = document.getElementById("event-stream");
    if (stream) stream.innerHTML = "";
    logEvent("MISSION EVENT STREAM BUFFER CLEARED // BUS RESYNCHRONIZED", "sys");
}

function filterLog(filterType) {
    activeLogFilter = filterType;
    document.querySelectorAll(".stream-filter-btn").forEach(btn => btn.classList.remove("active"));
    event.target.classList.add("active");

    const entries = document.querySelectorAll(".log-entry");
    entries.forEach(entry => {
        if (filterType === 'all') {
            entry.style.display = 'flex';
        } else if (filterType === 'cmd' && entry.classList.contains('cmd')) {
            entry.style.display = 'flex';
        } else if (filterType === 'ack' && (entry.classList.contains('ack') || entry.classList.contains('sys'))) {
            entry.style.display = 'flex';
        } else {
            entry.style.display = 'none';
        }
    });
}

// --- OPTICAL SPECTRUM CONTROLS (FLIR, NVG, POLAR) ---
function setSpectrum(mode) {
    const img = document.getElementById("active-viewport-img");
    const pills = document.querySelectorAll(".spectrum-pill");
    pills.forEach(p => p.classList.remove("active"));
    event.target.classList.add("active");

    if (mode === 'rgb') {
        img.style.filter = "none";
        logEvent("OPTICAL SENSOR: FULL RGB SPECTRUM CALIBRATED", "sys");
    } else if (mode === 'flir') {
        img.style.filter = "invert(1) hue-rotate(180deg) saturate(2.8) contrast(1.35)";
        logEvent("OPTICAL SENSOR: FLIR THERMAL RADIOMETRY SIMULATION ENGAGED", "sys");
    } else if (mode === 'nvg') {
        img.style.filter = "brightness(1.15) contrast(1.4) sepia(1) hue-rotate(85deg) saturate(3.2)";
        logEvent("OPTICAL SENSOR: NVG GREEN-PHOSPHOR INTENSIFIER ENGAGED", "sys");
    } else if (mode === 'polar') {
        img.style.filter = "contrast(1.9) brightness(1.05) grayscale(0.65) drop-shadow(0 0 4px #78c8dc)";
        logEvent("OPTICAL SENSOR: DUAL-FREQUENCY RADAR POLARIMETRY (CPR/DOP) ENGAGED", "sys");
    }
}

function toggleFullscreen() {
    const stage = document.getElementById("optical-stage");
    if (!document.fullscreenElement) {
        stage.requestFullscreen().catch(err => {
            logEvent(`FULLSCREEN FAILED: ${err.message}`, "err");
        });
    } else {
        document.exitFullscreen();
    }
}

// --- TAB SWITCHER ---
function switchTab(tab) {
    if (tab === 'telemetry') {
        document.querySelector(".hardware-metrics-grid").scrollIntoView({ behavior: 'smooth' });
    } else if (tab === 'archive') {
        document.querySelector(".archive-card").scrollIntoView({ behavior: 'smooth' });
    }
}

// --- SUPABASE SATELLITE LINK INITIALIZATION ---
function initLAEP() {
    document.getElementById("ribbon-device-id").innerText = config.deviceId.toUpperCase();

    if (!config.supabaseUrl || !config.supabaseKey || config.supabaseUrl.includes("your-project")) {
        logEvent("SATELLITE GROUND STATION NOT CONFIGURED. PRESS ⌘K TO LINK SUPABASE.", "warn");
        document.getElementById("status-droidcam-text").innerText = "UNCONFIGURED";
        return;
    }

    try {
        supabaseClient = window.supabase.createClient(config.supabaseUrl, config.supabaseKey);
        const endpointSlug = config.supabaseUrl.split('//')[1].split('.')[0].toUpperCase();
        logEvent(`ORBITAL SATELLITE LINK ONLINE // NODE: [${endpointSlug}]`, "ack");
        
        setupRealtimeSubscriptions();
        loadPastCaptures();
    } catch (err) {
        logEvent(`INITIALIZATION FAULT: ${err.message}`, "err");
    }
}

// --- REALTIME SUBSCRIPTIONS ---
function setupRealtimeSubscriptions() {
    logEvent(`SUBSCRIBING REALTIME BUS FOR UNIT: [${config.deviceId}]...`, "sys");

    // 1. Telemetry Channel (CPU, Temp, RAM, DroidCam, Queue)
    supabaseClient
        .channel("laep-telemetry")
        .on("postgres_changes", { event: "*", schema: "public", table: "device_telemetry" }, (payload) => {
            const data = payload.new;
            if (data && data.device_id === config.deviceId) {
                // CPU
                document.getElementById("val-cpu").innerText = `${data.cpu_usage}%`;
                document.getElementById("bar-cpu").style.width = `${Math.min(data.cpu_usage, 100)}%`;

                // Temperature
                document.getElementById("val-temp").innerText = `${data.cpu_temp}°C`;
                document.getElementById("bar-temp").style.width = `${Math.min((data.cpu_temp / 85) * 100, 100)}%`;
                const tempTag = document.getElementById("tag-temp-state");
                if (data.cpu_temp > 70) {
                    tempTag.innerText = "ELEVATED";
                    tempTag.style.color = "var(--c-hazard)";
                } else {
                    tempTag.innerText = "NOMINAL";
                    tempTag.style.color = "var(--c-path)";
                }

                // RAM
                document.getElementById("val-ram").innerText = `${data.ram_usage}%`;
                document.getElementById("bar-ram").style.width = `${Math.min(data.ram_usage, 100)}%`;

                // Comms Status
                document.getElementById("ribbon-comms-link").innerText = "DIRECT-TO-PI (NOMINAL)";
                document.getElementById("ribbon-comms-link").className = "ribbon-val text-teal";

                // DroidCam Status (online=streaming, standby=server up but not streaming, offline=unreachable)
                const camStatus = data.droidcam_status;
                const droidText = document.getElementById("status-droidcam-text");
                const droidDot = document.getElementById("dot-droidcam");
                if (camStatus === "online") {
                    droidText.innerText = "DROIDCAM: STREAMING";
                    droidDot.className = "badge-dot dot-green";
                } else if (camStatus === "standby") {
                    droidText.innerText = "DROIDCAM: STANDBY (open app!)";
                    droidDot.className = "badge-dot dot-amber";
                } else {
                    droidText.innerText = "DROIDCAM: OFFLINE";
                    droidDot.className = "badge-dot dot-red";
                }

                logEvent(`DOWNLINK TELEMETRY: CPU ${data.cpu_usage}% | TEMP ${data.cpu_temp}°C | RAM ${data.ram_usage}% | DROIDCAM: ${data.droidcam_status.toUpperCase()}`, "sys");
            }
        })
        .subscribe();

    // 2. Captures Channel (Live Imagery Burst)
    supabaseClient
        .channel("laep-captures")
        .on("postgres_changes", { event: "INSERT", schema: "public", table: "captures" }, (payload) => {
            const cap = payload.new;
            if (cap && cap.device_id === config.deviceId) {
                logEvent(`NEW RECON FRAME DOWNLINKED // BLOB: ${cap.file_size_kb || 0} KB // SYNC: ${cap.synced_from_offline ? 'OFFLINE_DRAIN' : 'LIVE_BURST'}`, "ack");
                
                // Update Optical Viewport
                updateOpticalViewport(cap.media_url, cap.captured_at);

                // Add to Vault Filmstrip
                prependVaultArtifact(cap);
            }
        })
        .subscribe();

    // 3. Mission Command Acknowledgments
    supabaseClient
        .channel("laep-commands")
        .on("postgres_changes", { event: "UPDATE", schema: "public", table: "device_commands" }, (payload) => {
            const cmd = payload.new;
            if (cmd && cmd.device_id === config.deviceId) {
                resetCommandButtons();
                if (cmd.status === "completed") {
                    logEvent(`MISSION [${cmd.command_type.toUpperCase()}] CONFIRMED & EXECUTED`, "ack");
                    if (cmd.command_type === "ai_inspect" && cmd.payload && cmd.payload.ai_reasoning) {
                        showAITerrainHUD(cmd.payload.ai_reasoning, cmd.payload.model);
                        logEvent(cmd.payload.ai_reasoning, "ack", "[GEMINI_AI]");
                    }
                } else if (cmd.status === "failed") {
                    logEvent(`MISSION FAILED: [${cmd.command_type.toUpperCase()}] REASON: ${cmd.error_message || "TIMEOUT"}`, "err");
                }
            }
        })
        .subscribe((status) => {
            if (status === "SUBSCRIBED") {
                logEvent("REALTIME MESH CHANNELS SYNCHRONIZED AND ARMED", "ack");
            }
        });
}

// --- MISSION DISPATCH DECK ---
async function dispatchMission(commandType) {
    if (!supabaseClient) {
        logEvent("CANNOT DISPATCH: GROUND STATION UNLINKED. PRESS ⌘K.", "err");
        openConfigModal();
        return;
    }

    setButtonExecuting(commandType);
    // If AI inspect, show immediate feedback on HUD
    if (commandType === 'ai_inspect') {
        showAITerrainHUD("Initiating Gemini multimodal terrain scan (3.6-flash -> 3.5-flash-lite)...", "gemini-3.6-flash");
    }

    logEvent(`INJECTING MISSION PACKET: [${commandType.toUpperCase()}] -> UNIT [${config.deviceId}]`, "cmd");

    const tStart = performance.now();
    try {
        const { data, error } = await supabaseClient.from("device_commands").insert({
            device_id: config.deviceId,
            command_type: commandType,
            status: "pending"
        }).select();

        if (error) throw error;
        const latency = Math.round(performance.now() - tStart);
        document.getElementById("ribbon-latency").innerText = `${latency} ms`;
        logEvent(`MISSION PACKET QUEUED (RTT: ${latency}ms) // UUID: ${data[0].id.slice(0, 8)}...`, "cmd");
    } catch (err) {
        logEvent(`TRANSMISSION FAULT: ${err.message}`, "err");
        resetCommandButtons();
    }
}

function setButtonExecuting(commandType) {
    if (commandType === 'capture_photo') {
        const badge = document.getElementById("badge-snap");
        if (badge) badge.innerText = "EXECUTING...";
    } else if (commandType === 'ai_inspect') {
        const btnAi = document.getElementById("btn-ai");
        if (btnAi) {
            const title = btnAi.querySelector(".action-title");
            if (title) title.innerText = "REASONING [GEMINI]...";
        }
    }
}

function resetCommandButtons() {
    const badge = document.getElementById("badge-snap");
    if (badge) badge.innerText = "EXEC [CMD-01]";
    const btnAi = document.getElementById("btn-ai");
    if (btnAi) {
        const title = btnAi.querySelector(".action-title");
        if (title) title.innerText = "AI TERRAIN REASONING";
    }
}

// --- DIRECT STREAM / CLOUD CAPTURE MODES ---
let isDirectStreamActive = false;
let lastCloudImageUrl = null;
let liveStreamTimeout = null;

function pollNextLiveFrame() {
    if (!isDirectStreamActive) return;
    const img = document.getElementById("active-viewport-img");
    if (!img) return;

    const bucket = config.bucket || "cloudcam_data";
    const liveUrl = `${config.supabaseUrl}/storage/v1/object/public/${bucket}/${config.deviceId}/live/latest.jpg?t=${Date.now()}`;

    const nextImg = new Image();
    nextImg.onload = () => {
        if (isDirectStreamActive) {
            img.src = nextImg.src;
            // Adaptive sub-second polling: next frame loads 200ms after current one finishes
            liveStreamTimeout = setTimeout(pollNextLiveFrame, 200);
        }
    };
    nextImg.onerror = () => {
        if (isDirectStreamActive) {
            liveStreamTimeout = setTimeout(pollNextLiveFrame, 800);
        }
    };
    nextImg.src = liveUrl;
}

function toggleDirectStream() {
    isDirectStreamActive = true;
    const btnLive = document.getElementById("pill-live-stream");
    const btnCloud = document.getElementById("pill-cloud-feed");
    if (btnLive) btnLive.classList.add("active");
    if (btnCloud) btnCloud.classList.remove("active");

    if (liveStreamTimeout) { clearTimeout(liveStreamTimeout); liveStreamTimeout = null; }
    pollNextLiveFrame();

    logEvent("LIVE STREAM ACTIVE // Adaptive high-speed frame relay armed", "sys");
}

function toggleCloudFeed() {
    const img = document.getElementById("active-viewport-img");
    isDirectStreamActive = false;
    if (liveStreamTimeout) { clearTimeout(liveStreamTimeout); liveStreamTimeout = null; }
    const btnLive = document.getElementById("pill-live-stream");
    const btnCloud = document.getElementById("pill-cloud-feed");
    if (btnCloud) btnCloud.classList.add("active");
    if (btnLive) btnLive.classList.remove("active");
    if (lastCloudImageUrl) { img.src = lastCloudImageUrl; }
    logEvent("DISPLAYING CLOUD SATELLITE CAPTURES FROM SUPABASE", "sys");
}


function showAITerrainHUD(text, modelName) {
    const banner = document.getElementById("hud-ai-banner");
    const textField = document.getElementById("hud-ai-text");
    const chip = banner ? banner.querySelector(".ai-chip") : null;
    if (chip && modelName) {
        chip.innerHTML = `<i class="fas fa-brain"></i> ${modelName.toUpperCase()} // MULTIMODAL AI`;
    }
    if (banner && textField) {
        textField.innerText = text;
        banner.style.display = "block";
        if (window._aiBannerTimer) clearTimeout(window._aiBannerTimer);
        window._aiBannerTimer = setTimeout(() => { banner.style.display = "none"; }, 25000);
    }
}

function closeAIBanner() {
    const banner = document.getElementById("hud-ai-banner");
    if (banner) banner.style.display = "none";
}

// --- VIEWPORT & RECON FILMSTRIP ---
function updateOpticalViewport(url, timestamp) {
    lastCloudImageUrl = url;
    if (!isDirectStreamActive) {
        const img = document.getElementById("active-viewport-img");
        img.src = url;
    }

    const formatted = new Date(timestamp).toUTCString().slice(17, 25) + " UTC";
    const tsEl = document.getElementById("hud-frame-ts");
    if (tsEl) tsEl.innerText = formatted;
}

function prependVaultArtifact(cap) {
    const strip = document.getElementById("vault-filmstrip");
    if (!strip) return;
    const emptyNotice = strip.querySelector(".vault-empty-state");
    if (emptyNotice) emptyNotice.remove();

    vaultCount++;
    const counterCaptures = document.getElementById("counter-captures");
    if (counterCaptures) counterCaptures.innerText = vaultCount;
    const counterVaultTotal = document.getElementById("counter-vault-total");
    if (counterVaultTotal) counterVaultTotal.innerText = `${vaultCount} ARTIFACTS RECORDED`;

    const item = document.createElement("div");
    item.className = "vault-card-item";
    item.onclick = () => {
        updateOpticalViewport(cap.media_url, cap.captured_at);
        logEvent(`VIEWING ARTIFACT PASS #${String(vaultCount).padStart(3, '0')} IN PRIMARY STAGE`, "sys");
    };

    const utcTime = new Date(cap.captured_at).toUTCString().slice(17, 25) + "Z";
    item.innerHTML = `
        <img src="${cap.media_url}" alt="Recon Pass" loading="lazy">
        <div class="vault-tag-id">PASS #${String(vaultCount).padStart(3, '0')}</div>
        <button class="vault-del-btn" title="Purge Artifact" onclick="deleteVaultArtifact(event, '${cap.id}', '${cap.media_url}', this.parentElement)">
            <i class="fas fa-trash-can"></i>
        </button>
        <div class="vault-tag-time">${utcTime}</div>
    `;

    strip.prepend(item);
}

async function loadPastCaptures() {
    if (!supabaseClient) return;
    try {
        logEvent("RETRIEVING HISTORICAL IMAGERY ARTIFACTS FROM S3 BUCKET...", "sys");
        const { data, error } = await supabaseClient
            .from("captures")
            .select("*")
            .eq("device_id", config.deviceId)
            .order("captured_at", { ascending: false })
            .limit(50);

        if (error) {
            logEvent(`STORAGE QUERY FAULT: ${error.message}`, "warn");
            return;
        }

        if (data && data.length > 0) {
            updateOpticalViewport(data[0].media_url, data[0].captured_at);
            const strip = document.getElementById("vault-filmstrip");
            if (strip) strip.innerHTML = "";
            vaultCount = 0;
            // Iterate reverse so oldest is prepended first, newest stays on the left
            data.slice().reverse().forEach(cap => prependVaultArtifact(cap));
            logEvent(`SYNCED ${data.length} HISTORICAL RECON PASSES`, "ack");
        } else {
            logEvent("NO RECON ARTIFACTS FOUND FOR UNIT: [" + config.deviceId + "]", "sys");
        }
    } catch (err) {
        logEvent(`STORAGE QUERY FAULT: ${err.message}`, "warn");
    }
}

// --- CONFIGURATION MODAL (⌘K) ---
function openConfigModal() {
    document.getElementById("cfg-url").value = config.supabaseUrl.includes("your-project") ? "" : config.supabaseUrl;
    document.getElementById("cfg-key").value = config.supabaseKey.includes("your-anon-key") ? "" : config.supabaseKey;
    document.getElementById("cfg-device").value = config.deviceId;
    const gKeyInput = document.getElementById("cfg-gemini-key");
    if (gKeyInput) gKeyInput.value = config.geminiKey || "";
    document.getElementById("config-modal").style.display = "flex";
}

function closeConfigModal() {
    document.getElementById("config-modal").style.display = "none";
}

function saveConfiguration() {
    const url = document.getElementById("cfg-url").value.trim();
    const key = document.getElementById("cfg-key").value.trim();
    const device = document.getElementById("cfg-device").value.trim();

    if (!url || !key) {
        alert("CRITICAL: Both Supabase URL and Anon Key are mandatory.");
        return;
    }

    config.supabaseUrl = url;
    config.supabaseKey = key;
    config.deviceId = device || "smartsight-alpha-01";

    localStorage.setItem("laep_sb_url", config.supabaseUrl);
    localStorage.setItem("laep_sb_key", config.supabaseKey);
    localStorage.setItem("laep_device_id", config.deviceId);
    const gKeyInput = document.getElementById("cfg-gemini-key");
    if (gKeyInput && gKeyInput.value.trim()) {
        config.geminiKey = gKeyInput.value.trim();
        localStorage.setItem("laep_gemini_key", config.geminiKey);
    }

    closeConfigModal();
    logEvent("SATELLITE GROUND LINK CREDENTIALS SAVED // REINITIALIZING...", "sys");
    initLAEP();
}

// KEYBOARD SHORTCUT: ⌘K or Ctrl+K to toggle modal
window.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        openConfigModal();
    }
    if (e.key === 'Escape') {
        closeConfigModal();
    }
});

// BOOTSTRAP ON LOAD
window.addEventListener("DOMContentLoaded", () => {
    initLAEP();
});
