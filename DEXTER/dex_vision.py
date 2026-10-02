#!/usr/bin/env python3
import sys
import os
import signal
import time
import subprocess
import wave
import numpy as np
from pathlib import Path
import ollama
from kokoro import KPipeline
from faster_whisper import WhisperModel

# ===== CONFIGURATION =====
WHISPER_MODEL = "tiny.en"
LLM_MODEL = "tinyllama"
TTS_VOICE = "bm_george"
TTS_SPEED = 0.9 

# STRICT WAKE WORDS
WAKE_WORDS = ["dexter", "hey dexter", "hi dexter"]

# AUDIO SETTINGS
PREF_SAMPLE_RATE = 16000
PREF_CHANNELS = 1
FRAME_MS = 30
# Higher threshold to prevent YOLO pausing for background noise
SILENCE_THRESHOLD = 300  
END_SILENCE_MS = 800
MAX_RECORDING_MS = 8000 

TEMP_WAV = Path("/tmp/recording.wav")
MIC_TARGET = os.environ.get("MIC_TARGET")

# ===== HELPERS =====

def freeze_process(process):
    """Instantly pauses the YOLO process to save power."""
    if process and process.poll() is None:
        try:
            print("❄️  Freezing YOLO (Power Save Mode)...")
            os.kill(process.pid, signal.SIGSTOP)
            return True
        except: pass
    return False

def unfreeze_process(process):
    """Resumes the YOLO process."""
    if process and process.poll() is None:
        try:
            print("🔥 Resuming YOLO...")
            os.kill(process.pid, signal.SIGCONT)
            return True
        except: pass
    return False

# ... [PASTE YOUR EXISTING AUDIO FUNCTIONS HERE] ...
# (record_with_vad, save_wav, transcribe_audio, speak_text, init_models)
# NOTE: If you don't have them handy, let me know and I will paste the full block again.
# For now, I assume they are in the file as before.

# --- Paste this block if you need the check_wake_word function ---
def check_wake_word(text):
    text_lower = text.lower().strip()
    import string
    text_clean = text_lower.translate(str.maketrans('', '', string.punctuation))
    for trigger in WAKE_WORDS:
        if text_clean.startswith(trigger):
            command = text[len(trigger):].strip()
            command = command.lstrip(string.punctuation).strip()
            return True, command
    return False, None

# ===== MAIN EXECUTION =====
def main():
    whisper_model, tts_pipeline = init_models()
    
    yolo_process = None 
    yolo_paused = False

    print("\n" + "="*50)
    print("🤖 DEXTER: POWER SAFE MODE (Linked to tts.py)")
    print("="*50)
    speak_text(tts_pipeline, "Dexter is online.")

    while True:
        try:
            # 1. Listen for Audio
            # The Pi sits here efficiently waiting for sound
            audio_data, rate, ch = record_with_vad(timeout_seconds=30)

            if audio_data:
                # --- SAFETY STEP: FREEZE YOLO ---
                # The moment you speak, we PAUSE YOLO to free up CPU for Whisper
                if yolo_process and not yolo_paused:
                    freeze_process(yolo_process)
                    yolo_paused = True
                # -------------------------------

                # 2. Transcribe
                save_wav(audio_data, TEMP_WAV, sample_rate=rate, channels=ch)
                user_text = transcribe_audio(whisper_model, TEMP_WAV)

                if user_text:
                    print(f"👂 Heard: \"{user_text}\"")
                    is_wake, command = check_wake_word(user_text)

                    if is_wake:
                        cmd_lower = command.lower()
                        print(f"🚀 Command: \"{cmd_lower}\"")

                        # --- SCENARIO A: YOLO IS RUNNING ---
                        if yolo_process:
                            # We ONLY listen for the stop command here
                            if "stop" in cmd_lower and ("object" in cmd_lower or "recognition" in cmd_lower):
                                speak_text(tts_pipeline, "Stopping object recognition.")
                                yolo_process.terminate() # Kill the external script
                                yolo_process.wait()
                                yolo_process = None
                                yolo_paused = False
                            else:
                                print("🛡️  Ignoring other commands while Vision is active.")
                                # We don't reply, just resume YOLO below
                        
                        # --- SCENARIO B: NORMAL CHAT ---
                        else:
                            if "start" in cmd_lower and ("object" in cmd_lower or "recognition" in cmd_lower):
                                speak_text(tts_pipeline, "Starting object recognition.")
                                
                                # CRITICAL WAIT: Let Dexter finish speaking completely
                                time.sleep(4) 
                                
                                print("👁️  Launching tts.py...")
                                # Launch your specific file
                                yolo_process = subprocess.Popen(['python3', 'tts.py'])
                                yolo_paused = False
                                
                            elif any(w in cmd_lower for w in ["goodbye", "bye", "exit"]):
                                speak_text(tts_pipeline, "Goodbye.")
                                break
                            
                            else:
                                # Normal AI Chat
                                reply = generate_response(command)
                                print(f"🤖 Dexter: \"{reply}\"\n")
                                speak_text(tts_pipeline, reply)

                    else:
                        print("💤 No wake word.")

                # --- RESUME YOLO ---
                # We are done processing audio. If YOLO is alive, wake it up.
                if yolo_process and yolo_paused:
                    unfreeze_process(yolo_process)
                    yolo_paused = False
                
                time.sleep(0.1)

        except KeyboardInterrupt:
            if yolo_process: yolo_process.terminate()
            print("\n👋 System Shutdown.")
            break
        except Exception as e:
            print(f"\n❌ Critical Error: {e}")
            if yolo_process: yolo_process.terminate()
            break

# -----------------------------------------------------------------------------
# PASTE THE MISSING HELPER FUNCTIONS HERE IF YOU NEED THEM (record_with_vad etc)
# -----------------------------------------------------------------------------
# I am assuming you kept the helper functions from the previous code block. 
# If not, tell me "Paste full code" and I will give you the 100% full file.

if __name__ == "__main__":
    main()
