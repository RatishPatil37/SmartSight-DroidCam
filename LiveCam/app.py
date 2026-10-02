import pathlib
import platform
import os
import cv2
import time
import threading
import numpy as np
import pyttsx3
from flask import Flask, render_template, Response, jsonify, request

# --- CRITICAL FIX FOR WINDOWS MODELS ON LINUX ---
if platform.system() == 'Linux':
    pathlib.WindowsPath = pathlib.PosixPath

# --- CAMERA LIBRARY SELECTION ---
# Try loading Picamera2 (For Raspberry Pi)
try:
    from picamera2 import Picamera2
    USING_PICAM = True
    print(">> SYSTEM: Running on Raspberry Pi (Picamera2 detected)")
except ImportError:
    USING_PICAM = False
    print(">> SYSTEM: Running on PC/Windows (Falling back to Webcam)")

# --- INFERENCE ENGINE SELECTION ---
try:
    import onnxruntime as ort
    USE_ONNX = True
    print(">> INFERENCE: Using ONNX Runtime")
except ImportError:
    from ultralytics import YOLO
    USE_ONNX = False
    print(">> INFERENCE: Using Ultralytics YOLO")

app = Flask(__name__)

# --- Configuration ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH_PT = os.path.join(BASE_DIR, 'best.pt')
MODEL_PATH_ONNX = os.path.join(BASE_DIR, 'best.onnx')
LABELS_PATH = os.path.join(BASE_DIR, 'labels.txt')

CONFIDENCE_THRESHOLD = 0.45
SPEECH_INTERVAL = 3.0 

# --- Global State ---
picam2 = None       # Object for Pi Camera
webcam = None       # Object for Windows Webcam
is_detecting = False
audio_mode = 'mobile'
last_spoken_time = {}
current_announcement = "" 
model_session = None
yolo_model = None
class_names = []

# --- Initialization Functions ---

def init_camera():
    global picam2, webcam, USING_PICAM
    
    if USING_PICAM:
        try:
            # FIX: Capture in RGB first to ensure we know the starting format
            picam2 = Picamera2()
            config = picam2.create_video_configuration(
                main={"size": (640, 480), "format": "RGB888"}
            )
            picam2.configure(config)
            picam2.start()
            print(">> CAMERA: Picamera2 started successfully")
        except Exception as e:
            print(f"!! CAMERA ERROR: Could not start Picamera2: {e}")
            USING_PICAM = False # Fallback if Pi Cam fails
            
    if not USING_PICAM:
        # Windows / Standard Webcam Fallback
        backend = cv2.CAP_DSHOW if platform.system() == 'Windows' else cv2.CAP_V4L2
        webcam = cv2.VideoCapture(0, backend)
        webcam.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        webcam.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        print(">> CAMERA: Standard Webcam initialized")

def load_model():
    global model_session, yolo_model, class_names, USE_ONNX
    
    # Load Labels
    if os.path.exists(LABELS_PATH):
        with open(LABELS_PATH, 'r') as f:
            class_names = [line.strip() for line in f.readlines()]
    else:
        class_names = ['object'] * 80 

    # Load ONNX
    if USE_ONNX and os.path.exists(MODEL_PATH_ONNX):
        try:
            model_session = ort.InferenceSession(MODEL_PATH_ONNX)
            print(f">> MODEL: Loaded {MODEL_PATH_ONNX}")
            return
        except Exception as e:
            print(f"!! ONNX Load Failed: {e}")
            USE_ONNX = False
            
    # Load PT (Fallback)
    if os.path.exists(MODEL_PATH_PT):
        try:
            from ultralytics import YOLO
            yolo_model = YOLO(MODEL_PATH_PT)
            print(f">> MODEL: Loaded {MODEL_PATH_PT}")
        except Exception as e:
            print(f"!! PT Load Failed: {e}")
    else:
        print("!! CRITICAL: No model file found!")

# Initialize everything
load_model()
init_camera()

# --- Audio Logic ---
def speak_on_pi(text):
    def worker():
        try:
            engine = pyttsx3.init()
            engine.setProperty('rate', 150)
            engine.say(text)
            engine.runAndWait()
        except: pass
    t = threading.Thread(target=worker)
    t.daemon = True
    t.start()

def handle_audio(text):
    global current_announcement
    if audio_mode == 'mobile':
        current_announcement = text
    else:
        speak_on_pi(text)

# --- Inference Helpers ---
def process_frame_onnx(frame):
    # Prepare image for ONNX
    img = cv2.resize(frame, (640, 640))
    # ONNX model usually expects RGB
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    img = img.transpose((2, 0, 1)) 
    img = np.expand_dims(img, axis=0)
    img = img / 255.0
    img = img.astype(np.float32)

    input_name = model_session.get_inputs()[0].name
    outputs = model_session.run(None, {input_name: img})
    predictions = np.squeeze(outputs[0]).T
    
    detected_objects = []
    scores = np.max(predictions[:, 4:], axis=1)
    keep = scores > CONFIDENCE_THRESHOLD
    
    filtered_preds = predictions[keep]
    filtered_scores = scores[keep]
    class_ids = np.argmax(filtered_preds[:, 4:], axis=1)
    
    h, w, _ = frame.shape
    
    for (pred, score, cls_id) in zip(filtered_preds, filtered_scores, class_ids):
        x, y, w_box, h_box = pred[:4]
        x1 = int((x - w_box/2) * w / 640)
        y1 = int((y - h_box/2) * h / 640)
        x2 = int((x + w_box/2) * w / 640)
        y2 = int((y + h_box/2) * h / 640)
        
        label = class_names[cls_id] if cls_id < len(class_names) else f"Class {cls_id}"
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(frame, f"{label} {score:.2f}", (x1, y1 - 10), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
        detected_objects.append(label)
        
    return frame, detected_objects

def process_frame_pt(frame):
    results = yolo_model(frame, conf=CONFIDENCE_THRESHOLD, verbose=False)
    annotated_frame = results[0].plot()
    
    detected_objects = []
    for r in results:
        for box in r.boxes:
            cls_id = int(box.cls[0])
            if cls_id < len(class_names):
                detected_objects.append(class_names[cls_id])
            else:
                detected_objects.append(yolo_model.names[cls_id])
                
    return annotated_frame, detected_objects

# --- Main Video Generator ---
def generate_frames():
    global is_detecting, last_spoken_time
    
    while True:
        frame = None
        
        # 1. Capture Frame (Switch between PiCam and Webcam)
        if USING_PICAM and picam2 is not None:
            try:
                # Capture numpy array
                frame = picam2.capture_array()
                
                # --- COLOR FIX ---
                # Swap Red and Blue channels (RGB -> BGR)
                # This fixes the "Blue/Gray skin" issue
                frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                
            except Exception as e:
                print(f"Picam Error: {e}")
                continue
        elif webcam is not None:
            success, frame = webcam.read()
            if not success:
                webcam.release()
                init_camera()
                time.sleep(0.1)
                continue
        
        if frame is None:
            continue

        # 2. Run Detection (Only if 'Start' was pressed)
        if is_detecting:
            try:
                current_time = time.time()
                
                if USE_ONNX:
                    frame, detections = process_frame_onnx(frame)
                else:
                    frame, detections = process_frame_pt(frame)
                
                unique_detections = set(detections)
                for obj in unique_detections:
                    if current_time - last_spoken_time.get(obj, 0) > SPEECH_INTERVAL:
                        handle_audio(f"{obj} detected")
                        last_spoken_time[obj] = current_time
                        
            except Exception as e:
                print(f"Inference Error: {e}")

        # 3. Encode for Web Streaming
        try:
            ret, buffer = cv2.imencode('.jpg', frame)
            frame_bytes = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
        except Exception as e:
            pass

# --- Flask Routes ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/control', methods=['POST'])
def control():
    global is_detecting, audio_mode
    data = request.json
    
    if 'command' in data:
        command = data.get('command')
        if command == 'start':
            is_detecting = True
            handle_audio("Object recognition started")
            return jsonify({"status": "active", "message": "Neural Network Active"})
        elif command == 'stop':
            is_detecting = False
            handle_audio("System paused")
            return jsonify({"status": "inactive", "message": "System Standby"})
            
    if 'audio_mode' in data:
        audio_mode = data.get('audio_mode')
        msg = f"Audio output switched to {audio_mode}"
        handle_audio(msg)
        return jsonify({"status": "ok", "mode": audio_mode})
        
    return jsonify({"status": "error"})

@app.route('/get_audio_status')
def get_audio_status():
    global current_announcement
    msg = current_announcement
    current_announcement = "" 
    return jsonify({"message": msg})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
