import cv2
from picamera2 import Picamera2
from ultralytics import YOLO
import pyttsx3
import time
import sys
import numpy as np

# --- Setup ---
# Initialize the text-to-speech engine
engine = pyttsx3.init()
voices = engine.getProperty('voices')
found_voice = None
for voice in voices:
    if 'en' in voice.languages or 'english' in voice.id.lower():
        found_voice = voice.id
        break
if found_voice:
    engine.setProperty("voice", found_voice)

engine.setProperty('rate', 150)  # Speed of speech
engine.setProperty('volume', 0.9)  # Volume

# --- Welcome Message ---
welcome_text = "Vision system active. Graphical interface enabled."
print(welcome_text)
engine.say(welcome_text)
engine.runAndWait()

# Set up the camera
picam2 = Picamera2()
WIDTH = 640
HEIGHT = 640
picam2.preview_configuration.main.size = (WIDTH, HEIGHT)
picam2.preview_configuration.main.format = "RGB888"
picam2.preview_configuration.align()
picam2.configure("preview")
picam2.start()

# Load YOLOv8 model
try:
    model = YOLO("yolov5su_ncnn_model")
except:
    print("Custom model not found, falling back to yolov8n.pt")
    model = YOLO("yolov8n.pt")

# --- Define Zones and Colors ---
# Left boundary is 25% of width, Right boundary is 75% of width
LEFT_LIMIT = int(WIDTH * 0.25)
RIGHT_LIMIT = int(WIDTH * 0.75)

# Define Colors in BGR format (Blue, Green, Red)
BOX_COLOR = (0, 255, 0)       # Bright Green for detected objects
# ** CHANGE COLOR HERE **
# Currently set to Yellow. Change the tuple numbers for different colors.
ZONE_LINE_COLOR = (0, 255, 255) # Yellow for zone dividers

# --- Main Loop ---
tts_interval = 2.0
last_tts_time = time.time() - tts_interval

print("Running Smart Sight. Press 'q' or Ctrl+C to stop.")

try:
    while True:
        # Capture a frame
        frame = picam2.capture_array()

        # Picamera gives RGB, OpenCV needs BGR for correct color display
        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

        # Run YOLO model
        results = model(frame, verbose=False)

        # Lists to store detections for speech
        detections_to_speak = []

        # Process detections
        for r in results:
            for box in r.boxes:
                confidence = float(box.conf)

                if confidence > 0.6:  # High confidence only
                    # 1. Get Coordinates
                    x1, y1, x2, y2 = map(int, box.xyxy[0])

                    # 2. Get Class Name
                    class_id = int(box.cls)
                    class_name = model.names[class_id]

                    # 3. Calculate Center X of the object
                    center_x = (x1 + x2) // 2

                    # 4. Determine Direction
                    if center_x < LEFT_LIMIT:
                        position = "left"
                    elif center_x > RIGHT_LIMIT:
                        position = "right"
                    else:
                        position = "center"

                    detections_to_speak.append(f"{class_name} at {position}")

                    # 5. Draw Bounding Box and Text on Frame
                    cv2.rectangle(frame, (x1, y1), (x2, y2), BOX_COLOR, 2)
                    label = f"{class_name} {confidence:.2f} ({position})"

                    # Draw background for text to make it readable
                    (w, h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
                    # Ensure text background doesn't go off screen if object is high up
                    top_y = max(y1, h + 5)
                    cv2.rectangle(frame, (x1, top_y - h - 5), (x1 + w, top_y), BOX_COLOR, -1)
                    cv2.putText(frame, label, (x1, top_y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)

        # --- Visual Guides for Zones ---
        # Draw vertical lines using the newly defined ZONE_LINE_COLOR
        # Increased thickness to 2 to make them more visible
        cv2.line(frame, (LEFT_LIMIT, 0), (LEFT_LIMIT, HEIGHT), ZONE_LINE_COLOR, 2)
        cv2.line(frame, (RIGHT_LIMIT, 0), (RIGHT_LIMIT, HEIGHT), ZONE_LINE_COLOR, 2)

        # --- Text-to-Speech Logic ---
        current_time = time.time()
        if current_time - last_tts_time >= tts_interval:
            if detections_to_speak:
                unique_detections = sorted(list(set(detections_to_speak)))

                if len(unique_detections) == 1:
                    text = f"I see a {unique_detections[0]}."
                else:
                    text = "I see " + ", ".join(unique_detections[:-1]) + " and " + unique_detections[-1] + "."

                print(f"🗣️ Speaking: {text}")
                engine.say(text)
                engine.runAndWait()
                last_tts_time = current_time

        # --- Display the Graphical Frame ---
        cv2.imshow("Smart Sight - Wearable View", frame)

        # Break loop on 'q' key press
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

except KeyboardInterrupt:
    print("\nStopping Vision System...")

finally:
    # Cleanup
    picam2.stop()
    engine.stop()
    cv2.destroyAllWindows()
    print("System Closed.")
