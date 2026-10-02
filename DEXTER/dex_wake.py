#!/usr/bin/env python3
"""
Standard Dexter (Wake Word Only - No Vision)
Optimized for 5V 2.1A Power Bank
"""

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
TTS_SPEED = 1.0

# WAKE WORDS (Strict Mode)
WAKE_WORDS = ["dexter", "hey dexter", "hi dexter"]

# AUDIO SETTINGS
PREF_SAMPLE_RATE = 16000
PREF_CHANNELS = 1
FRAME_MS = 30
SILENCE_THRESHOLD = 300   # Adjusted for noise
END_SILENCE_MS = 800
MAX_RECORDING_MS = 10000 

TEMP_WAV = Path("/tmp/recording.wav")
MIC_TARGET = os.environ.get("MIC_TARGET")

# ===== HELPERS =====

def init_models():
    print("🚀 Loading Standard Dexter...")
    # cpu_threads=2 saves power on boot
    whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8", cpu_threads=2)
    tts = KPipeline(lang_code='a')
    return whisper, tts

def _spawn_pw_cat_record(rate, channels, target):
    cmd = ["pw-cat", "--record", "-", "--format", "s16", "--rate", str(rate), "--channels", str(channels)]
    if target: cmd += ["--target", str(target)]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def record_with_vad(timeout_seconds=30):
    # print("🎤 Listening...") # Commented out to reduce log spam
    proc = _spawn_pw_cat_record(PREF_SAMPLE_RATE, PREF_CHANNELS, MIC_TARGET)
    bytes_per_sample = 2
    frame_bytes = int(PREF_SAMPLE_RATE * FRAME_MS / 1000) * bytes_per_sample * PREF_CHANNELS
    audio_buffer = bytearray()
    
    is_speaking = False
    silence_ms = 0
    start_time = time.time()
    
    try:
        while True:
            if (time.time() - start_time) > timeout_seconds:
                break
            chunk = proc.stdout.read(frame_bytes)
            if not chunk: break
            
            samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
            rms = np.sqrt(np.mean(samples**2))
            
            if is_speaking:
                audio_buffer.extend(chunk)
                if rms < SILENCE_THRESHOLD:
                    silence_ms += FRAME_MS
                else:
                    silence_ms = 0
                if silence_ms >= END_SILENCE_MS: break
                if len(audio_buffer) > (PREF_SAMPLE_RATE * MAX_RECORDING_MS / 500): break
            else:
                if rms > SILENCE_THRESHOLD:
                    is_speaking = True
                    audio_buffer.extend(chunk)
    finally:
        proc.terminate(); proc.wait()
        
    if audio_buffer and len(audio_buffer) > 4000:
        return bytes(audio_buffer), PREF_SAMPLE_RATE, PREF_CHANNELS
    return None, None, None

def save_wav(audio_data, filepath, sample_rate, channels):
    with wave.open(str(filepath), 'wb') as wf:
        wf.setnchannels(channels); wf.setsampwidth(2); wf.setframerate(sample_rate); wf.writeframes(audio_data)

def transcribe_audio(model, filepath):
    segments, _ = model.transcribe(str(filepath), beam_size=1)
    text = " ".join([s.text for s in segments]).strip()
    return text if text else None

def generate_response(prompt):
    try:
        # num_thread=2 is CRITICAL for 2.1A power bank
        resp = ollama.chat(model=LLM_MODEL, messages=[{"role": "user", "content": prompt}], options={"num_thread": 2})
        return resp['message']['content']
    except: return "System error."

def speak_text(pipeline, text):
    print(f"🔊 Dexter: {text}")
    try:
        for _, _, audio in pipeline(text, voice=TTS_VOICE, speed=TTS_SPEED):
            audio = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
            process = subprocess.Popen(["aplay", "-r", "24000", "-f", "S16_LE", "-c", "1", "-q"], stdin=subprocess.PIPE)
            process.communicate(input=audio.tobytes())
    except: pass

def check_wake_word(text):
    text_clean = text.lower().translate(str.maketrans('', '', '!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~'))
    for trigger in WAKE_WORDS:
        if text_clean.startswith(trigger):
            return True, text[len(trigger):].strip().lstrip('!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~').strip()
    return False, None

# ===== MAIN =====
def main():
    whisper_model, tts_pipeline = init_models()
    
    print("\n" + "="*50)
    print("🤖 STANDARD DEXTER ONLINE (Wake Word Only)")
    print("="*50)
    
    # Short startup sound
    speak_text(tts_pipeline, "Dexter is online.")

    while True:
        try:
            # 1. Listen
            audio_data, rate, ch = record_with_vad(timeout_seconds=30)

            if audio_data:
                save_wav(audio_data, TEMP_WAV, sample_rate=rate, channels=ch)
                user_text = transcribe_audio(whisper_model, TEMP_WAV)

                if user_text:
                    print(f"👂 Heard: \"{user_text}\"")
                    # 2. Wake Word Check
                    is_wake, command = check_wake_word(user_text)

                    if is_wake:
                        print(f"🚀 Command: \"{command}\"")
                        if any(w in command.lower() for w in ["goodbye", "bye", "exit"]):
                            speak_text(tts_pipeline, "Goodbye.")
                            break
                        
                        # Generate Response
                        if command:
                            reply = generate_response(command)
                            speak_text(tts_pipeline, reply)
                        else:
                            speak_text(tts_pipeline, "Yes?")
                    else:
                        print("💤 Ignoring (No wake word)")
                        
                time.sleep(0.1)

        except KeyboardInterrupt:
            print("\n👋 System Shutdown.")
            break
        except Exception as e:
            print(f"\n❌ Error: {e}")
            time.sleep(3)

if __name__ == "__main__":
    main()
