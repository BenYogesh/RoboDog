import cv2
import numpy as np
import onnxruntime as ort
import os
import time
import threading
import queue
from arduino.app_utils import App, Bridge

bridge = Bridge()

# --- 1. MOTOR COMMAND OUTPUT (via Bridge, not direct serial) ---
# NOTE: the previous version tried to open /dev/ttyS0 directly with pyserial
# and call ser.write(). On the UNO Q, the D0/D1 hardware UART pins that lead
# to the ESP32 are wired to the STM32 microcontroller (MCU) side, not
# exposed as a Linux /dev/tty device the Python (MPU) side can open. That's
# why `ser` was never defined and every write silently failed.
#
# The correct path is: Python -> Bridge.call() -> MCU sketch -> Serial1
# (D0/D1) -> ESP32 UART2. The companion MCU sketch (sketch.ino) must be
# flashed and running for this to work; it exposes "send_motor_command".
def send_motor_command(command):
    try:
        bridge.call("send_motor_command", command)
    except Exception as e:
        print(f"Bridge call failed: {e}")

# --- 2. THREADED CAMERA CAPTURE ---
# cap.read() is blocking I/O. Running capture on its own thread means
# main_loop never stalls waiting on the camera — it just grabs whatever
# frame is freshest, so the control loop's latency is driven by inference
# time alone, not camera I/O.
camera_path = "/dev/v4l/by-id/usb-046d_C270_HD_WEBCAM_E21C4540-video-index0"


class CameraStream:
    def __init__(self, path, width=320, height=240):
        self.cap = cv2.VideoCapture(path, cv2.CAP_V4L2)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # don't let stale frames queue up

        if not self.cap.isOpened():
            # This is the case that was failing silently before: cv2 doesn't
            # raise an exception here, it just returns a capture object that
            # will never produce frames. Printing loudly means it'll show up
            # in `arduino-app-cli app logs`, which is the whole point.
            print(f"CAMERA ERROR: could not open '{path}'. Check that the "
                  f"path exists and (on the UNO Q specifically) that the "
                  f"device is passed through to this app's container.")
            bridge.call("update_oled", "CAM FAILED")

        self.lock = threading.Lock()
        self.frame = None
        self.running = True
        self.thread = threading.Thread(target=self._update, daemon=True)
        self.thread.start()

    def _update(self):
        fail_count = 0
        while self.running:
            if not self.cap.isOpened():
                time.sleep(1)
                continue
            ret, frame = self.cap.read()
            if ret:
                fail_count = 0
                with self.lock:
                    self.frame = frame
            else:
                fail_count += 1
                if fail_count % 30 == 1:  # don't spam the log every frame
                    print(f"CAMERA WARNING: read() failed ({fail_count} times so far)")
                time.sleep(0.1)

    def read(self):
        with self.lock:
            return None if self.frame is None else self.frame.copy()

    def stop(self):
        self.running = False
        self.thread.join(timeout=1)
        self.cap.release()


cam = CameraStream(camera_path)

# --- 3. LOAD POSE BRAIN ---
current_dir = os.path.dirname(os.path.abspath(__file__))
model_path = os.path.join(current_dir, 'yolov8n-pose.onnx')

bridge.call("update_oled", "Booting AI...")

so = ort.SessionOptions()
so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
so.intra_op_num_threads = max(1, os.cpu_count() or 1)
session = ort.InferenceSession(model_path, sess_options=so, providers=['CPUExecutionProvider'])
input_name = session.get_inputs()[0].name
_, _, model_h, model_w = session.get_inputs()[0].shape

# Avoid OpenCV's internal worker threads competing with ONNX Runtime's
# threads for CPU cores on a small embedded chip.
cv2.setNumThreads(1)

last_text = ""
bridge.call("update_oled", "SYSTEM READY")
frame_count = 0

# --- 4. ASYNC DEBUG-IMAGE WRITER ---
# cv2.imwrite is blocking disk I/O. Pushing it to a background thread keeps
# it from ever stalling the gesture-detection loop.
debug_queue = queue.Queue(maxsize=2)


def _debug_writer():
    while True:
        item = debug_queue.get()
        if item is None:
            break
        path, img = item
        cv2.imwrite(path, img)


debug_thread = threading.Thread(target=_debug_writer, daemon=True)
debug_thread.start()

# Two-arm gesture thresholds, in the same pixel space as the model input
# (model_w x model_h), since that's the coordinate space the pose keypoints
# come back in. These will likely need tuning for your camera distance/angle
# — use the debug snapshots to see actual dx/dy values and adjust.
LEVEL_Y_THRESHOLD = 40.0     # max vertical wrist-shoulder gap to count as "shoulder height"
SIDE_DX_THRESHOLD = 70.0     # min horizontal wrist-shoulder gap to count as "extended to the side"
FORWARD_DX_THRESHOLD = 35.0  # max horizontal wrist-shoulder gap to count as "pointing forward"
WRIST_CONF_THRESHOLD = 0.6


# --- 5. THE SKELETAL SLICER ---
def slice_pose_matrix(outputs, threshold=0.60):
    # The Pose output matrix is (1, 56, 8400)
    predictions = np.squeeze(outputs[0]).T

    # Index 4 holds the confidence that it sees a Person
    person_scores = predictions[:, 4]
    best_box_index = np.argmax(person_scores)
    best_confidence = person_scores[best_box_index]

    if best_confidence > threshold:
        return predictions[best_box_index], best_confidence
    return None, 0.0


def get_keypoint(person_data, kp_id):
    """Returns (x, y, conf) for a given COCO keypoint id (0-16)."""
    base = 5 + (kp_id * 3)
    return person_data[base], person_data[base + 1], person_data[base + 2]


def classify_arm(shoulder_x, shoulder_y, wrist_x, wrist_y, wrist_conf):
    """Classifies one arm as FORWARD, SIDE, or OTHER based on where the
    wrist sits relative to the shoulder."""
    if wrist_conf < WRIST_CONF_THRESHOLD:
        return "UNKNOWN"

    dx = abs(wrist_x - shoulder_x)
    dy = abs(wrist_y - shoulder_y)

    if dy > LEVEL_Y_THRESHOLD:
        return "OTHER"  # arm raised or lowered, not held at shoulder height
    if dx > SIDE_DX_THRESHOLD:
        return "SIDE"
    if dx < FORWARD_DX_THRESHOLD:
        return "FORWARD"
    return "OTHER"  # in the dead zone between "forward" and "side"


def decide_command(left_state, right_state):
    """Combines both arm states into a motor command + OLED label."""
    if left_state == "FORWARD" and right_state == "FORWARD":
        return 'w', "Forward"
    if left_state == "SIDE" and right_state == "SIDE":
        return 's', "Stop"
    if right_state == "SIDE" and left_state != "SIDE":
        return 'a', "Turn Left"
    if left_state == "SIDE" and right_state != "SIDE":
        return 'd', "Turn Right"
    return None, "Waiting"


# --- 6. THE MAIN LOOP ---
def main_loop():
    global last_text, frame_count

    try:
        frame = cam.read()
        if frame is None:
            frame_count += 1
            if frame_count % 5000 == 1:  # throttle: don't flood the log
                print("No frame available yet from CameraStream — camera may have failed to open.")
            return

        # Single optimized call replaces cvtColor + resize + astype + transpose:
        # resize -> BGR->RGB swap -> /255 normalize -> HWC->CHW, all in one
        # C++ pass instead of four separate numpy array allocations.
        blob = cv2.dnn.blobFromImage(
            frame, scalefactor=1.0 / 255.0, size=(model_w, model_h),
            swapRB=True, crop=False
        )

        display_text = "Scanning..."

        outputs = session.run(None, {input_name: blob})
        person_data, conf = slice_pose_matrix(outputs)

        if person_data is not None:
            display_text = f"Tracking {int(conf * 100)}%"

            # --- EXTRACT SKELETAL JOINTS (both arms) ---
            # COCO keypoint ids: 5=L shoulder, 6=R shoulder, 7=L elbow,
            # 8=R elbow, 9=L wrist, 10=R wrist
            l_shoulder_x, l_shoulder_y, _ = get_keypoint(person_data, 5)
            r_shoulder_x, r_shoulder_y, _ = get_keypoint(person_data, 6)

            l_elbow_x, l_elbow_y, l_elbow_conf = get_keypoint(person_data, 7)
            r_elbow_x, r_elbow_y, r_elbow_conf = get_keypoint(person_data, 8)

            l_wrist_x, l_wrist_y, l_wrist_conf = get_keypoint(person_data, 9)
            r_wrist_x, r_wrist_y, r_wrist_conf = get_keypoint(person_data, 10)

            left_state = classify_arm(l_shoulder_x, l_shoulder_y, l_wrist_x, l_wrist_y, l_wrist_conf)
            right_state = classify_arm(r_shoulder_x, r_shoulder_y, r_wrist_x, r_wrist_y, r_wrist_conf)

            command, command_label = decide_command(left_state, right_state)

            if command is not None:
                send_motor_command(command)
                display_text = command_label

            # --- THE HEADLESS DEBUGGER (every 10th frame, off the hot path) ---
            # frame_count += 1
            # if frame_count % 10 == 0:
            #     debug_frame = cv2.resize(frame, (model_w, model_h))

            #     # Right arm: green/purple/red (as before)
            #     cv2.circle(debug_frame, (int(r_shoulder_x), int(r_shoulder_y)), 8, (0, 255, 0), -1)
            #     if r_elbow_conf > 0.50:
            #         cv2.circle(debug_frame, (int(r_elbow_x), int(r_elbow_y)), 8, (255, 0, 255), -1)
            #     cv2.circle(debug_frame, (int(r_wrist_x), int(r_wrist_y)), 8, (0, 0, 255), -1)
            #     cv2.line(debug_frame, (int(r_shoulder_x), int(r_shoulder_y)),
            #              (int(r_elbow_x), int(r_elbow_y)), (0, 255, 255), 2)
            #     cv2.line(debug_frame, (int(r_elbow_x), int(r_elbow_y)),
            #              (int(r_wrist_x), int(r_wrist_y)), (0, 255, 255), 2)

            #     # Left arm: cyan/orange/yellow, so it's visually distinct
            #     cv2.circle(debug_frame, (int(l_shoulder_x), int(l_shoulder_y)), 8, (255, 255, 0), -1)
            #     if l_elbow_conf > 0.50:
            #         cv2.circle(debug_frame, (int(l_elbow_x), int(l_elbow_y)), 8, (0, 165, 255), -1)
            #     cv2.circle(debug_frame, (int(l_wrist_x), int(l_wrist_y)), 8, (0, 255, 255), -1)
            #     cv2.line(debug_frame, (int(l_shoulder_x), int(l_shoulder_y)),
            #              (int(l_elbow_x), int(l_elbow_y)), (255, 165, 0), 2)
            #     cv2.line(debug_frame, (int(l_elbow_x), int(l_elbow_y)),
            #              (int(l_wrist_x), int(l_wrist_y)), (255, 165, 0), 2)

            #     cv2.putText(debug_frame, f"L:{left_state} R:{right_state} CMD:{command}",
            #                 (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

            #     snapshot_index = (frame_count // 10) % 10
            #     filename = os.path.join(current_dir, f'debug_vision_{snapshot_index}.jpg')
            #     if not debug_queue.full():
            #         debug_queue.put_nowait((filename, debug_frame))

            if display_text != last_text:
                bridge.call("update_oled", display_text)
                last_text = display_text

    except Exception as e:
        print(f"CRASH: {str(e)}")
        time.sleep(5)


App.run(user_loop=main_loop)
