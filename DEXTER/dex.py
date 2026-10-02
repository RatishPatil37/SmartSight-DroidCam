	#!/usr/bin/env python3
"""
Voice Chatbot (USB Mic + Bluetooth/Analog Speaker) — TTS Tensor-safe + PipeWire
Simplified Version: No GPIO/Buttons, uses TinyLlama.

Run:
  python3 chatbot.py
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

# ===== Configuration =====

# Audio Capture Settings
PREF_SAMPLE_RATE = 16000
PREF_CHANNELS = 1

# VAD (Voice Activity Detection) Settings
FRAME_MS = 30
SILENCE_THRESHOLD = 120   # Adjust if environment is noisy
END_SILENCE_MS = 800      # How long silence to wait before processing
MIN_SPEECH_MS = 300
MAX_RECORDING_MS = 15000

# Models
WHISPER_MODEL = "tiny.en"
LLM_MODEL = "tinyllama"   # Changed from gemma3:270m
TTS_VOICE = "bm_george"
TTS_SPEED = 1.1

# Conversation Settings
AUTO_RESTART_DELAY = 1.5
TEMP_WAV = Path("/tmp/recording.wav")

# Optional: force a specific PipeWire source (id or name)
MIC_TARGET = os.environ.get("MIC_TARGET")

# ===== Initialization =====
def init_models():
    print("🚀 Starting Voice Chatbot...")
    print("📦 Loading models (this may take a moment the first time)...")

    print("  Loading Whisper...")
    whisper = WhisperModel(
        WHISPER_MODEL,
        device="cpu",
        compute_type="int8",
        cpu_threads=4,
        download_root=str(Path.home() / ".cache" / "whisper")
    )

    print("  Loading Kokoro TTS...")
    # lang_code='a' is typically for American English in Kokoro
    tts = KPipeline(lang_code='a')

    print("  Checking Ollama...")
    try:
        ollama.list()
    except Exception:
        print("❌ Ollama not running! Start it with: sudo systemctl enable --now ollama")
        sys.exit(1)

    print("✅ All models loaded successfully!\n")
    return whisper, tts

# ===== Audio Helpers =====

def _spawn_pw_cat_record(rate, channels, target):
    cmd = [
        "pw-cat", "--record", "-",
        "--format", "s16",
        "--rate", str(rate),
        "--channels", str(channels)
    ]
    if target:
        cmd += ["--target", str(target)]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def _select_record_pipeline(target):
    """
    Try a few (rate,channels) combos so we don't crash if the device
    refuses 16k mono. Returns (proc, rate, channels, first_chunk or None, err_text).
    """
    attempts = [
        (PREF_SAMPLE_RATE, PREF_CHANNELS),  # 16k / mono
        (PREF_SAMPLE_RATE, 2),              # 16k / stereo
        (48000, PREF_CHANNELS),             # 48k / mono
        (48000, 2),                         # 48k / stereo
    ]
    for rate, ch in attempts:
        proc = _spawn_pw_cat_record(rate, ch, target)
        bytes_per_sample = 2
        frame_bytes = int(rate * FRAME_MS / 1000) * bytes_per_sample * ch
        
        # Try reading one chunk to see if it crashes immediately
        chunk = proc.stdout.read(frame_bytes)
        if chunk:
            return proc, rate, ch, chunk, ""
        
        # If read failed, capture error and cleanup
        err = (proc.stderr.read() or b"").decode("utf-8", errors="ignore")
        try:
            proc.terminate(); proc.wait(timeout=0.5)
        except Exception:
            pass
            
        if err.strip():
            print(f"   ⚠️  pw-cat refused {rate}Hz/{ch}ch: {err.strip()}")
        else:
            print(f"   ⚠️  pw-cat produced no data at {rate}Hz/{ch}ch, retrying...")
            
    return None, None, None, None, "No working pw-cat configuration found"

def record_with_vad(timeout_seconds=30):
    """Record audio until silence is detected (VAD). Returns (bytes, rate, channels) or (None, None, None)."""
    print("🎤 Listening... (speak now)")
    if MIC_TARGET:
        print(f"   🎯 Using source target: {MIC_TARGET}")

    proc, rate, ch, first_chunk, err = _select_record_pipeline(MIC_TARGET)
    if not proc:
        print(f"❌ {err}")
        return None, None, None

    bytes_per_sample = 2
    frame_bytes = int(rate * FRAME_MS / 1000) * bytes_per_sample * ch
    audio_buffer = bytearray()

    try:
        # Quick calibration (~300ms) to determine noise floor
        noise_samples = []
        if first_chunk:
            s = np.frombuffer(first_chunk, dtype=np.int16).astype(np.float32)
            noise_samples.append(float(np.sqrt(np.mean(s * s))))
        
        for _ in range(9):
            chunk = proc.stdout.read(frame_bytes)
            if chunk:
                s = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
                noise_samples.append(float(np.sqrt(np.mean(s * s))))
        
        noise_floor = float(np.median(noise_samples)) if noise_samples else 50.0
        threshold = max(SILENCE_THRESHOLD, noise_floor * 1.8)
        print(f"   📏 Noise floor: {noise_floor:.1f}  |  Threshold: {threshold:.1f}")

        is_speaking = False
        silence_ms = 0
        speech_ms = 0
        total_ms = 0
        start = time.time()

        if first_chunk is not None:
            samples = np.frombuffer(first_chunk, dtype=np.int16).astype(np.float32)
            rms = float(np.sqrt(np.mean(samples * samples)))
            level = int(rms / 100)
            print(f"\r  Level: {' '*min(level,20):<20} ", end="", flush=True)
            
            if rms > threshold:
                is_speaking = True
                speech_ms = FRAME_MS
                audio_buffer.extend(first_chunk)

        while True:
            if (time.time() - start) > timeout_seconds:
                if not is_speaking:
                    return None, None, None
                break

            chunk = proc.stdout.read(frame_bytes)
            if not chunk:
                break

            samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
            rms = float(np.sqrt(np.mean(samples * samples)))
            level = int(rms / 100)
            print(f"\r  Level: {' '*min(level,20):<20} ", end="", flush=True)

            if is_speaking:
                audio_buffer.extend(chunk)
                if rms < threshold:
                    silence_ms += FRAME_MS
                else:
                    silence_ms = 0
                    speech_ms += FRAME_MS

                if silence_ms >= END_SILENCE_MS and speech_ms >= MIN_SPEECH_MS:
                    dur_s = len(audio_buffer) / (rate * bytes_per_sample * ch)
                    print(f"\n  ✓ Recorded {dur_s:.1f}s")
                    break
                elif total_ms >= MAX_RECORDING_MS:
                    print("\n  ✓ Max recording length")
                    break
            else:
                if rms > threshold:
                    is_speaking = True
                    speech_ms = FRAME_MS
                    silence_ms = 0
                    audio_buffer.extend(chunk)
                    print("\n  💬 Speech detected!")

            total_ms += FRAME_MS

    except KeyboardInterrupt:
        print("\n  ⏹️  Recording stopped")
        raise  # Re-raise to be caught by main loop
    finally:
        try:
            proc.terminate(); proc.wait(timeout=0.8)
        except Exception:
            pass

    if audio_buffer and len(audio_buffer) > 1000:
        return bytes(audio_buffer), rate, ch
    return None, None, None

def save_wav(audio_data, filepath, sample_rate, channels):
    with wave.open(str(filepath), 'wb') as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(audio_data)

# ===== AI Functions =====

def transcribe_audio(whisper_model, audio_path):
    print("🧠 Transcribing...")
    try:
        segments, info = whisper_model.transcribe(
            str(audio_path),
            language="en",
            beam_size=1,
            best_of=1,
            temperature=0.0,
            vad_filter=True,
            vad_parameters=dict(
                min_silence_duration_ms=500,
                speech_pad_ms=200
            )
        )
        text = " ".join(seg.text.strip() for seg in segments)
        return text.strip() if text else None
    except Exception as e:
        print(f"❌ Transcription error: {e}")
        return None

def generate_response(user_text):
    print(f"💭 Thinking (using {LLM_MODEL})...")
    try:
        resp = ollama.chat(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": "You are a helpful voice assistant. Keep responses concise (max 2 sentences) and conversational."},
                {"role": "user", "content": user_text}
            ],
            options={"temperature": 0.7, "num_predict": 60, "top_p": 0.9}
        )
        return resp["message"]["content"].strip()
    except Exception as e:
        print(f"❌ LLM Error: {e}")
        return "I'm sorry, I had trouble processing that."

# ---- TTS utils (Tensor-safe) ----
def _to_numpy_audio(audio):
    """Convert various audio containers (torch.Tensor, list, np.ndarray) to 1-D float32 NumPy array."""
    try:
        import torch  # only for isinstance check; safe if not installed
        if isinstance(audio, torch.Tensor):
            audio = audio.detach().cpu().float().numpy()
    except Exception:
        pass
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim > 1:
        audio = np.squeeze(audio)
    return audio

def speak_text(tts_pipeline, text):
    print("🔊 Speaking...")
    try:
        # Use pipeline sample_rate if available; default to 24k.
        sr = int(getattr(tts_pipeline, "sample_rate", 24000) or 24000)
        gen = tts_pipeline(text, voice=TTS_VOICE, speed=TTS_SPEED)
        for _, _, audio in gen:
            audio_np = _to_numpy_audio(audio)
            pcm16 = (np.clip(audio_np, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
            play_cmd = [
                "pw-cat", "--playback", "-",
                "--format", "s16",
                "--rate", str(sr),
                "--channels", "1"
            ]
            proc = subprocess.Popen(play_cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
            _, stderr = proc.communicate(pcm16)
            if proc.returncode != 0:
                err = (stderr or b"").decode("utf-8", errors="ignore").strip()
                if err:
                    print(f"❗ pw-cat playback: {err}")
    except Exception as e:
        print(f"❌ TTS Error: {e}")

def record_fixed_seconds(seconds=3):
    print(f"🎙️  Recording ~{seconds}s for test...")
    if MIC_TARGET:
        print(f"   🎯 Using source target: {MIC_TARGET}")

    proc, rate, ch, first_chunk, err = _select_record_pipeline(MIC_TARGET)
    if not proc:
        print(f"❌ {err}")
        return None, None, None

    bytes_per_sample = 2
    frame_bytes = int(rate * FRAME_MS / 1000) * bytes_per_sample * ch
    total_frames = int((seconds * 1000) / FRAME_MS)
    buf = bytearray()
    if first_chunk:
        buf.extend(first_chunk)

    try:
        for _ in range(total_frames - (1 if first_chunk else 0)):
            chunk = proc.stdout.read(frame_bytes)
            if not chunk:
                break
            buf.extend(chunk)
    finally:
        try:
            proc.terminate(); proc.wait(timeout=0.8)
        except Exception:
            pass

    return (bytes(buf), rate, ch) if buf else (None, None, None)

# ===== Main =====
def main():
    global MIC_TARGET
    args = sys.argv[1:]
    if "--mic-target" in args:
        try:
            MIC_TARGET = args[args.index("--mic-target") + 1]
        except Exception:
            print("⚠️  Usage: --mic-target <source-id-or-name>")

    def shutdown_handler(sig, frame):
        print("\n\n👋 Shutting down...")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    if len(args) > 0:
        if args[0] == "--help":
            print("Voice Chatbot - Simplified")
            print("\nUsage: python3 chatbot.py [--mic-target <id-or-name>] [--test]")
            print("  --mic-target    Force a specific PipeWire source")
            print("  --test          Record ~3s and play back (quick audio check)")
            sys.exit(0)
        elif args[0] == "--test" or "--test" in args:
            data, rate, ch = record_fixed_seconds(seconds=3)
            if not data:
                print("❌ No audio captured during test.")
                sys.exit(1)
            out = Path("/tmp/test.wav")
            save_wav(data, out, sample_rate=rate, channels=ch)
            print("▶️  Playing back test recording...")
            subprocess.run(["aplay", str(out)], check=False)
            print("✅ Audio test complete!")
            sys.exit(0)

    whisper_model, tts_pipeline = init_models()

    print("\n" + "="*50)
    print("🤖 VOICE CHATBOT READY (TinyLlama Edition)!")
    print("="*50)
    speak_text(tts_pipeline, "I am Dexter, an offline local AI Virtual Assistant. System is now fully operational. So, my friend, what would you like me to do?")
    print("Setup:")
    print("  • Microphone: PipeWire default source")
    print("  • Speaker: PipeWire default sink")
    print("  • Stop: Press Ctrl+C")
    if MIC_TARGET:
        print(f"  • Mic target override: {MIC_TARGET}")
    print("\nListening for speech...\n")

    while True:
        try:
            audio_data, rate, ch = record_with_vad(timeout_seconds=30)

            if audio_data:
                save_wav(audio_data, TEMP_WAV, sample_rate=rate, channels=ch)
                user_text = transcribe_audio(whisper_model, TEMP_WAV)

                if user_text:
                    print(f"📝 You said: \"{user_text}\"")
                    # Simple exit commands
                    if any(w in user_text.lower() for w in ["goodbye", "bye", "stop", "exit", "quit"]):
                        speak_text(tts_pipeline, "Goodbye!")
                        break

                    reply = generate_response(user_text)
                    print(f"🤖 Assistant: \"{reply}\"\n")
                    speak_text(tts_pipeline, reply)

                    print(f"⏳ Ready again in {AUTO_RESTART_DELAY}s...")
                    time.sleep(AUTO_RESTART_DELAY)
                    print("🎤 Listening...\n")
                else:
                    print("❓ No speech detected in the captured audio\n")
            else:
                print("💤 No speech detected, still listening...\n")
                time.sleep(0.5)

        except KeyboardInterrupt:
            print("\n\n⌨️  Interrupted by user")
            break
        except Exception as e:
            print(f"\n❌ Error: {e}")
            print("Restarting in 3 seconds...\n")
            time.sleep(3)

    print("\n👋 Goodbye!")
    print("="*50)

if __name__ == "__main__":
    main()
