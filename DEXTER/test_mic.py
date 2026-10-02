import sounddevice as sd
import numpy as np

def callback(indata, frames, time, status):
    if status:
        print(status)
    # Calculate volume level
    volume_norm = np.linalg.norm(indata) * 10
    print(f"Microphone Level: {'|' * int(volume_norm)}")

print("Speaking into the mic... (Press Ctrl+C to stop)")
with sd.InputStream(callback=callback):
    sd.sleep(10000) # Listens for 10 seconds
