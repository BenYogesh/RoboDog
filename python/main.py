"""Code chính điều khiển robot RoboDog"""

# Khai báo các thư viện cần thiết
import os
import threading
import time
import traceback

import cv2
import numpy as np

from arduino.app_utils import App, Bridge
from ball_tracker import BallTracker
from detector import HandGestureDetector
from face_gate import FaceGate, verify_face_models
from manual_video import ManualVideoServer

cv2.setNumThreads(1)    # Khởi tạo OpenCV để sử dụng một luồng duy nhất, tránh xung đột với các luồng khác
bridge = Bridge()       # Khởi tạo cầu nối giữa Python và Arduino để gửi lệnh điều khiển robot

current_dir = os.path.dirname(os.path.abspath(__file__))                        # Thư mục hiện tại của file main.py
camera_path = "/dev/v4l/by-id/usb-046d_C270_HD_WEBCAM_E21C4540-video-index0"    # Đường dẫn đến cổng webcam


def send_motor_command(command):
    # Gửi lệnh điều khiển động cơ đến Arduino thông qua cầu nối
    try:
        bridge.call("send_motor_command", command)
    except Exception as error:
        print(f"Bridge call failed: {error}")


class CameraStream:
    # Lớp quản lý luồng video từ webcam, đọc khung hình liên tục và lưu trữ khung hình mới nhất
    def __init__(self, path, width=640, height=480):
        self.cap = cv2.VideoCapture(path, cv2.CAP_V4L2)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not self.cap.isOpened():
            print(f"CAMERA ERROR: could not open '{path}'.")

        self.lock = threading.Lock()
        self.frame = None
        self.running = True
        self.thread = threading.Thread(target=self._update, daemon=True)
        self.thread.start()

    def _update(self):
        # Luồng phụ liên tục đọc khung hình từ webcam và lưu trữ khung hình mới nhất
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
                if fail_count % 30 == 1:
                    print(f"CAMERA WARNING: read() failed ({fail_count} times so far)")
                time.sleep(0.1)

    def read(self):
        # Trả về khung hình mới nhất từ webcam, nếu không có khung hình nào thì trả về None
        with self.lock:
            return None if self.frame is None else self.frame.copy()

    def stop(self):
        # Dừng luồng đọc khung hình và giải phóng tài nguyên của webcam
        self.running = False
        self.thread.join(timeout=1)
        self.cap.release()


# Lệnh điều khiển robot
CMD_WALK = "w"
CMD_STOP = "s"
CMD_LEFT = "a"
CMD_RIGHT = "d"
CMD_SIT = "q"
CMD_PRONE = "c"
CMD_STAND_UP = CMD_STOP
CAM_CMD_UP = "h"
CAM_CMD_DOWN = "l"
CAM_CMD_NEUTRAL = "n"
CAM_CMD_SCAN_UP = "r"
CAM_CMD_SCAN_DOWN = "v"
CAM_CMD_SCAN_STOP = "x"

# Camera scanning is time-based because the tilt servo is continuous rotation.
CAMERA_SCAN_TIMEOUT_S = 1.5
CAMERA_TARGET_LOST_S = 1.0
CAMERA_HAND_CONFIRMATIONS = 2
CAMERA_SCAN_FACE_CHECK_PERIOD_S = 0.25
CAMERA_RETURN_SETTLE_S = 0.1

CAMERA_SCAN_IDLE = "IDLE"
CAMERA_SCAN_FACE_SCANNING = "FACE_SCANNING"
CAMERA_SCAN_HAND_SCANNING = "HAND_SCANNING"
CAMERA_SCAN_LOCKED = "LOCKED"
CAMERA_SCAN_RETURNING = "RETURNING"

cam = CameraStream(camera_path)
manual_video = ManualVideoServer(cam.read)
manual_video.start()
detector = HandGestureDetector(score_threshold=0.6, conf_threshold=0.6)
verify_face_models()
face_gate = FaceGate()
ball_tracker = BallTracker(
    os.path.join(current_dir, "yolov8n.onnx"),
    walk_command=CMD_WALK,
    stop_command=CMD_STOP,
    left_command=CMD_LEFT,
    right_command=CMD_RIGHT,
)


python_cam_state = "N"
camera_scan_state = CAMERA_SCAN_IDLE
camera_scan_started_at = 0.0
camera_scan_direction = 0
camera_net_offset_s = 0.0
camera_hand_scan_limit_s = 0.0
camera_target_last_seen_at = 0.0
camera_hand_confirmations = 0
camera_return_complete_at = 0.0


def set_cam_state(new_state):
    """Set fixed camera pitch only when a vision-controlled scan is idle."""
    global python_cam_state
    if camera_scan_state != CAMERA_SCAN_IDLE:
        return
    if new_state == python_cam_state:
        return
    command = {"U": CAM_CMD_UP, "D": CAM_CMD_DOWN, "N": CAM_CMD_NEUTRAL}[new_state]
    send_motor_command(command)
    python_cam_state = new_state


def start_camera_motion(now, command, direction, state, duration_limit_s):
    """Start continuous motion and track its signed timed offset from neutral."""
    global camera_scan_state, camera_scan_started_at, camera_scan_direction
    global camera_hand_confirmations, camera_hand_scan_limit_s, python_cam_state
    global next_face_check_at
    send_motor_command(command)
    camera_scan_state = state
    camera_scan_started_at = now
    camera_scan_direction = direction
    camera_hand_scan_limit_s = duration_limit_s
    camera_hand_confirmations = 0
    if state == CAMERA_SCAN_FACE_SCANNING:
        # Do not wait for the normal, slower face-recognition interval.
        next_face_check_at = now
    python_cam_state = "N"


def start_camera_scan(now):
    """Start a face-first or hand-only upward scan after legs are detected."""
    global camera_net_offset_s
    if camera_scan_state != CAMERA_SCAN_IDLE:
        return
    camera_net_offset_s = 0.0
    initial_state = (
        CAMERA_SCAN_FACE_SCANNING
        if REQUIRE_FAMILIAR_FACE
        else CAMERA_SCAN_HAND_SCANNING
    )
    start_camera_motion(
        now,
        CAM_CMD_SCAN_UP,
        1,
        initial_state,
        CAMERA_SCAN_TIMEOUT_S,
    )


def stop_camera_scan(now):
    """Stop active scanning and add its signed movement to the net offset."""
    global camera_scan_state, camera_net_offset_s, camera_scan_direction
    global camera_target_last_seen_at
    if camera_scan_state not in (
        CAMERA_SCAN_FACE_SCANNING,
        CAMERA_SCAN_HAND_SCANNING,
    ):
        return 0.0
    send_motor_command(CAM_CMD_SCAN_STOP)
    travel_time = max(0.0, now - camera_scan_started_at)
    camera_net_offset_s += camera_scan_direction * travel_time
    camera_scan_direction = 0
    camera_scan_state = CAMERA_SCAN_LOCKED
    camera_target_last_seen_at = now
    return travel_time


def start_hand_scan_below_face(now):
    """Move down from a familiar face, stopping no lower than neutral."""
    remaining_down_time = max(0.0, camera_net_offset_s)
    if remaining_down_time <= 0.0:
        return_camera_from_scan(now)
        return False
    start_camera_motion(
        now,
        CAM_CMD_SCAN_DOWN,
        -1,
        CAMERA_SCAN_HAND_SCANNING,
        remaining_down_time,
    )
    return True


def return_camera_from_scan(now):
    """Return with the remaining offset after both the up and down scan stages."""
    global camera_scan_state, camera_hand_confirmations, camera_net_offset_s
    global camera_scan_direction, python_cam_state, camera_return_complete_at
    if camera_scan_state in (CAMERA_SCAN_IDLE, CAMERA_SCAN_RETURNING):
        return
    if camera_scan_state in (
        CAMERA_SCAN_FACE_SCANNING,
        CAMERA_SCAN_HAND_SCANNING,
    ):
        camera_net_offset_s += camera_scan_direction * max(
            0.0, now - camera_scan_started_at
        )
        camera_scan_direction = 0
    # The ESP32 records the exact times and chooses the required return direction.
    send_motor_command(CAM_CMD_NEUTRAL)
    camera_return_complete_at = (
        now + abs(camera_net_offset_s) + CAMERA_RETURN_SETTLE_S
    )
    camera_scan_state = CAMERA_SCAN_RETURNING
    camera_hand_confirmations = 0
    python_cam_state = "N"


def update_camera_scan(legs_detected, hand_detected, face_status, now):
    """Advance legs -> face/hand scan -> hold -> reduced timed return."""
    global camera_scan_state, camera_hand_confirmations, camera_target_last_seen_at
    global camera_net_offset_s

    if camera_scan_state == CAMERA_SCAN_IDLE:
        if legs_detected:
            start_camera_scan(now)
            return (
                "Scanning for familiar face"
                if REQUIRE_FAMILIAR_FACE
                else "Scanning for hand"
            )
        return None

    if camera_scan_state == CAMERA_SCAN_FACE_SCANNING:
        if face_status == "familiar":
            stop_camera_scan(now)
            if hand_detected:
                return "Familiar face and hand found"
            if start_hand_scan_below_face(now):
                return "Familiar face found; scanning down for hand"
            return "No hand scan travel; returning"

        if now - camera_scan_started_at >= CAMERA_SCAN_TIMEOUT_S:
            return_camera_from_scan(now)
            return "Face scan timed out; returning"
        return "Scanning for familiar face"

    if camera_scan_state == CAMERA_SCAN_HAND_SCANNING:
        if hand_detected:
            camera_hand_confirmations += 1
            if camera_hand_confirmations >= CAMERA_HAND_CONFIRMATIONS:
                stop_camera_scan(now)
                return "Hand found"
        else:
            camera_hand_confirmations = 0

        if now - camera_scan_started_at >= camera_hand_scan_limit_s:
            return_camera_from_scan(now)
            return "Hand scan timed out; returning"
        return (
            "Scanning down for hand"
            if camera_scan_direction < 0
            else "Scanning for hand"
        )

    if camera_scan_state == CAMERA_SCAN_RETURNING:
        if now >= camera_return_complete_at:
            camera_scan_state = CAMERA_SCAN_IDLE
            camera_net_offset_s = 0.0
            return "Camera returned"
        return "Returning camera"

    # Once a hand is found, leave the lens there until it is gone long enough
    # to avoid returning during a brief dropped inference frame.
    if hand_detected:
        camera_target_last_seen_at = now
        return "Tracking hand"
    if now - camera_target_last_seen_at >= CAMERA_TARGET_LOST_S:
        return_camera_from_scan(now)
        return "Target lost; returning"
    return "Holding camera position"


# Set this to False to allow gesture commands without a familiar face. Face
# recognition and the LED expression still run for feedback.
REQUIRE_FAMILIAR_FACE = True
FACE_CHECK_PERIOD_S = 0.8
FAMILIAR_GRACE_S = 3.0
last_familiar_time = 0.0
next_face_check_at = 0.0
last_face_matrix_state = None


def commands_currently_allowed():
    return (
        not REQUIRE_FAMILIAR_FACE
        or (time.time() - last_familiar_time) < FAMILIAR_GRACE_S
    )


def set_face_matrix(expression):
    global last_face_matrix_state
    if expression == last_face_matrix_state:
        return
    try:
        bridge.call("update_face_matrix", expression)
    except Exception as error:
        print(f"Bridge call failed: {error}")
    last_face_matrix_state = expression


STATE_STANDING = "STANDING"
STATE_SITTING = "SITTING"
STATE_PRONE = "PRONE"
STATE_CHASING = "CHASING"
STATE_MANUAL = "MANUAL_CONTROL"
robot_state = STATE_STANDING
COMMAND_COOLDOWN_S = 0.7
POSTURE_TRANSITION_COOLDOWN_S = 2.0
command_cooldown_until = 0.0


def enter_manual_control():
    """Pause autonomous vision decisions and expose the webcam feed."""
    global robot_state, command_cooldown_until
    global camera_scan_state, camera_scan_direction, camera_net_offset_s
    global camera_hand_confirmations, python_cam_state

    camera_scan_state = CAMERA_SCAN_IDLE
    camera_scan_direction = 0
    camera_net_offset_s = 0.0
    camera_hand_confirmations = 0
    python_cam_state = "N"
    robot_state = STATE_MANUAL
    command_cooldown_until = 0.0
    manual_video.set_active(True)
    _log_status("MANUAL CONTROL")


def leave_manual_control():
    """Return to gesture, face, and ball processing after Bluetooth exits."""
    global robot_state, command_cooldown_until
    global camera_scan_state, camera_scan_direction, camera_net_offset_s
    global camera_hand_confirmations, python_cam_state

    manual_video.set_active(False)
    camera_scan_state = CAMERA_SCAN_IDLE
    camera_scan_direction = 0
    camera_net_offset_s = 0.0
    camera_hand_confirmations = 0
    python_cam_state = "N"
    robot_state = STATE_STANDING
    command_cooldown_until = time.time() + POSTURE_TRANSITION_COOLDOWN_S
    _log_status("AUTOMATIC CONTROL")


def handle_esp32_control_mode(mode):
    """Follow Bluetooth M/O mode notifications from the ESP32."""
    normalized = str(mode).strip().lower()
    if normalized == "manual" and robot_state != STATE_MANUAL:
        enter_manual_control()
    elif normalized in {"automatic", "auto"} and robot_state == STATE_MANUAL:
        leave_manual_control()

POINT_VERTICAL_THRESHOLD = 20.0
WRIST = 0
INDEX_TIP, INDEX_MCP = 8, 5
MIDDLE_TIP, MIDDLE_MCP = 12, 9
RING_TIP, RING_MCP = 16, 13
PINKY_TIP, PINKY_MCP = 20, 17


def is_folded(landmarks, tip_idx, mcp_idx, wrist_idx=WRIST):
    tip = landmarks[tip_idx][:2]
    mcp = landmarks[mcp_idx][:2]
    wrist = landmarks[wrist_idx][:2]
    return np.linalg.norm(tip - wrist) < np.linalg.norm(mcp - wrist)


def is_pointing_down(landmarks):
    """Detect the pointing-down gesture used for sit and chase stop."""
    if is_folded(landmarks, INDEX_TIP, INDEX_MCP):
        return False
    if not all(
        is_folded(landmarks, tip, mcp)
        for tip, mcp in (
            (MIDDLE_TIP, MIDDLE_MCP),
            (RING_TIP, RING_MCP),
            (PINKY_TIP, PINKY_MCP),
        )
    ):
        return False

    dx = landmarks[INDEX_TIP][0] - landmarks[INDEX_MCP][0]
    dy = landmarks[INDEX_TIP][1] - landmarks[INDEX_MCP][1]
    return abs(dx) < abs(dy) and dy > POINT_VERTICAL_THRESHOLD


inference_counter = 0
last_status = ""


def main_loop():
    global inference_counter, robot_state, command_cooldown_until
    global last_familiar_time, next_face_check_at

    try:
        frame = cam.read()
        if frame is None:
            return
        frame = cv2.flip(frame, 1)

        # Bluetooth owns every movement command in manual mode. The camera
        # reader continues running so ManualVideoServer can publish frames.
        if robot_state == STATE_MANUAL:
            return

        if robot_state == STATE_CHASING:
            if ball_tracker.should_check_manual_stop():
                hands = detector.detect(frame)
                if hands and is_pointing_down(hands[0]["landmarks"]):
                    send_motor_command(CMD_STOP)
                    robot_state = STATE_STANDING
                    command_cooldown_until = time.time() + COMMAND_COOLDOWN_S
                    _log_status("Stopped (manual)")
                    return

            ball, top_candidates, command, display_text, exit_reason = (
                ball_tracker.command_for_frame(frame)
            )
            send_motor_command(command)
            if exit_reason == "found":
                robot_state = STATE_STANDING
                display_text = "Standing (ball found)"
                command_cooldown_until = time.time() + COMMAND_COOLDOWN_S
                set_cam_state("N")
            elif exit_reason == "gave_up":
                robot_state = STATE_STANDING
                command_cooldown_until = time.time() + COMMAND_COOLDOWN_S
                set_cam_state("N")
            elif ball is None and not ball_tracker.ball_ever_seen:
                if top_candidates:
                    name, confidence = top_candidates[0]
                    display_text = f"Searching, see:{name} {confidence:.2f}"
                else:
                    display_text = "Searching, see: nothing"

            _log_status(display_text)
            return

        if time.time() < command_cooldown_until:
            return

        inference_counter += 1
        if inference_counter % 3 != 0:
            return

        hands = detector.detect(frame)
        now = time.monotonic()
        face_status = "none"
        if now >= next_face_check_at:
            face_period = (
                CAMERA_SCAN_FACE_CHECK_PERIOD_S
                if camera_scan_state == CAMERA_SCAN_FACE_SCANNING
                else FACE_CHECK_PERIOD_S
            )
            next_face_check_at = now + face_period
            face_status, _ = face_gate.recognize(frame)
            if face_status == "familiar":
                last_familiar_time = time.time()
                set_face_matrix("smiley")
            elif face_status == "unfamiliar":
                set_face_matrix("indifferent")

        command = None
        display_text = "NO HAND"
        posture_transition = False
        legs_detected = False
        if (
            robot_state == STATE_STANDING
            and camera_scan_state == CAMERA_SCAN_IDLE
            and not hands
            and ball_tracker.should_check_camera()
        ):
            person_box = ball_tracker.detect_person(frame)
            legs_detected = (
                person_box is not None and ball_tracker.is_legs_only(person_box)
            )

        scan_display = update_camera_scan(
            legs_detected,
            bool(hands),
            face_status,
            now,
        )
        if hands:
            landmarks = hands[0]["landmarks"]
            folded = {
                "index": is_folded(landmarks, INDEX_TIP, INDEX_MCP),
                "middle": is_folded(landmarks, MIDDLE_TIP, MIDDLE_MCP),
                "ring": is_folded(landmarks, RING_TIP, RING_MCP),
                "pinky": is_folded(landmarks, PINKY_TIP, PINKY_MCP),
            }
            is_open_palm = not any(folded.values())
            is_pointing = (
                not folded["index"]
                and folded["middle"]
                and folded["ring"]
                and folded["pinky"]
            )
            is_fist = all(folded.values())

            point_left = point_right = point_up = point_down = False
            if is_pointing:
                dx = landmarks[INDEX_TIP][0] - landmarks[INDEX_MCP][0]
                dy = landmarks[INDEX_TIP][1] - landmarks[INDEX_MCP][1]
                if abs(dx) >= abs(dy):
                    point_left = dx < -20.0
                    point_right = dx > 20.0
                else:
                    point_down = dy > POINT_VERTICAL_THRESHOLD
                    point_up = dy < -POINT_VERTICAL_THRESHOLD

            if not commands_currently_allowed():
                display_text = "Ignoring (unfamiliar)"
            elif robot_state == STATE_STANDING:
                if is_open_palm:
                    command, display_text = CMD_STOP, "CMD: STOP (Palm)"
                elif point_up:
                    command, display_text = CMD_WALK, "CMD: WALK (w)"
                elif point_left:
                    command, display_text = CMD_LEFT, "CMD: LEFT (a)"
                elif point_right:
                    command, display_text = CMD_RIGHT, "CMD: RIGHT (d)"
                elif point_down:
                    command, display_text = CMD_SIT, "Sitting"
                    robot_state = STATE_SITTING
                    posture_transition = True
                elif is_fist:
                    command, display_text = CMD_STOP, "Ball Mode"
                    robot_state = STATE_CHASING
                    ball_tracker.start_chase()
                    posture_transition = True
                    set_cam_state("D")
                else:
                    display_text = "UNKNOWN SIGN"
            elif robot_state == STATE_SITTING:
                if point_down:
                    command, display_text = CMD_PRONE, "Prone"
                    robot_state = STATE_PRONE
                    posture_transition = True
                elif point_up:
                    command, display_text = CMD_STAND_UP, "Standing"
                    robot_state = STATE_STANDING
                    posture_transition = True
                else:
                    display_text = "Sitting (point up/down)"
            elif robot_state == STATE_PRONE:
                if point_up:
                    command, display_text = CMD_SIT, "Sitting"
                    robot_state = STATE_SITTING
                    posture_transition = True
                else:
                    display_text = "Prone (point up)"

            if command is not None:
                send_motor_command(command)
                cooldown = (
                    POSTURE_TRANSITION_COOLDOWN_S
                    if posture_transition
                    else COMMAND_COOLDOWN_S
                )
                command_cooldown_until = time.time() + cooldown
            if robot_state == STATE_STANDING and camera_scan_state == CAMERA_SCAN_IDLE:
                set_cam_state("N")

        if robot_state != STATE_STANDING:
            return_camera_from_scan(now)
        elif command is None and scan_display is not None:
            display_text = scan_display

        _log_status(display_text)
    except Exception:
        print("Loop Error:")
        traceback.print_exc()
        return_camera_from_scan(time.monotonic())
        send_motor_command(CMD_STOP)


def _log_status(status):
    global last_status
    if status == last_status:
        return
    print(f"ROBOT_STATUS: {status}")
    last_status = status


try:
    Bridge.provide("manual_mode_changed", handle_esp32_control_mode)
except Exception as error:
    print(f"Could not register ESP32 mode callback: {error}")


try:
    App.run(user_loop=main_loop)
except NameError:
    while True:
        main_loop()
finally:
    manual_video.stop()
    cam.stop()
