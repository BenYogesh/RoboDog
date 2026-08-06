"""Main RoboDog vision loop: camera, face gate, gesture control, and Bridge I/O."""

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


cv2.setNumThreads(1)
bridge = Bridge()
current_dir = os.path.dirname(os.path.abspath(__file__))
camera_path = "/dev/v4l/by-id/usb-046d_C270_HD_WEBCAM_E21C4540-video-index0"


def send_motor_command(command):
    """Forward a one-letter movement command through the UNO Q MCU."""
    try:
        bridge.call("send_motor_command", command)
    except Exception as error:
        print(f"Bridge call failed: {error}")


class CameraStream:
    """Continuously capture frames so inference never blocks on camera I/O."""

    def __init__(self, path, width=640, height=480):
        self.cap = cv2.VideoCapture(path, cv2.CAP_V4L2)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not self.cap.isOpened():
            print(f"CAMERA ERROR: could not open '{path}'.")
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
                if fail_count % 30 == 1:
                    print(f"CAMERA WARNING: read() failed ({fail_count} times so far)")
                time.sleep(0.1)

    def read(self):
        with self.lock:
            return None if self.frame is None else self.frame.copy()

    def stop(self):
        self.running = False
        self.thread.join(timeout=1)
        self.cap.release()


# Movement and camera-pitch letters are interpreted by the ESP32 firmware.
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
CAM_CMD_SCAN_STOP = "x"

# The ESP32 enforces a final 1.8 s scan limit.  Stop and return a little
# earlier here so the normal vision path remains responsible for the return.
CAMERA_SCAN_TIMEOUT_S = 1.5
CAMERA_TARGET_LOST_S = 1.0
CAMERA_HAND_CONFIRMATIONS = 2
CAMERA_SCAN_FACE_CHECK_PERIOD_S = 0.25

CAMERA_SCAN_IDLE = "IDLE"
CAMERA_SCAN_SCANNING = "SCANNING"
CAMERA_SCAN_LOCKED = "LOCKED"
CAMERA_SCAN_RETURNING = "RETURNING"

cam = CameraStream(camera_path)
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
camera_scan_duration_s = 0.0
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


def start_camera_scan(now):
    """Begin a continuous upward scan after the camera sees a person's legs."""
    global camera_scan_state, camera_scan_started_at, camera_hand_confirmations
    global camera_scan_duration_s, python_cam_state, next_face_check_at
    if camera_scan_state != CAMERA_SCAN_IDLE:
        return
    send_motor_command(CAM_CMD_SCAN_UP)
    camera_scan_state = CAMERA_SCAN_SCANNING
    camera_scan_started_at = now
    camera_scan_duration_s = 0.0
    camera_hand_confirmations = 0
    # Do not wait for the normal, slower face-recognition interval after the
    # lens starts moving.
    next_face_check_at = now
    # The ESP32 now owns the servo until it receives the matching return command.
    python_cam_state = "N"


def lock_camera_on_target(now):
    """Stop the scan and retain the ESP32's measured upward travel time."""
    global camera_scan_state, camera_scan_duration_s, camera_target_last_seen_at
    if camera_scan_state != CAMERA_SCAN_SCANNING:
        return
    send_motor_command(CAM_CMD_SCAN_STOP)
    camera_scan_state = CAMERA_SCAN_LOCKED
    camera_scan_duration_s = max(0.0, now - camera_scan_started_at)
    camera_target_last_seen_at = now


def return_camera_from_scan(now):
    """Ask the ESP32 to rotate down for exactly the recorded scan duration."""
    global camera_scan_state, camera_hand_confirmations, camera_scan_duration_s
    global python_cam_state
    global camera_return_complete_at
    if camera_scan_state in (CAMERA_SCAN_IDLE, CAMERA_SCAN_RETURNING):
        return
    # 'n' stops an active scan if necessary, then starts the timed reverse run.
    send_motor_command(CAM_CMD_NEUTRAL)
    # Keep fixed camera commands and new leg detections out of the UART until
    # the ESP32 has had time to complete the matching reverse rotation.
    travel_time = camera_scan_duration_s or max(0.0, now - camera_scan_started_at)
    camera_return_complete_at = now + travel_time
    camera_scan_state = CAMERA_SCAN_RETURNING
    camera_hand_confirmations = 0
    python_cam_state = "N"


def update_camera_scan(legs_detected, hand_detected, face_detected, now):
    """Advance the legs -> scan -> target -> timed-return camera state machine."""
    global camera_scan_state, camera_hand_confirmations, camera_target_last_seen_at

    if camera_scan_state == CAMERA_SCAN_IDLE:
        if legs_detected:
            start_camera_scan(now)
            return "Scanning for hand/face"
        return None

    if camera_scan_state == CAMERA_SCAN_SCANNING:
        if face_detected:
            lock_camera_on_target(now)
            return "Face found"
        if hand_detected:
            camera_hand_confirmations += 1
            if camera_hand_confirmations >= CAMERA_HAND_CONFIRMATIONS:
                lock_camera_on_target(now)
                return "Hand found"
        else:
            camera_hand_confirmations = 0

        if now - camera_scan_started_at >= CAMERA_SCAN_TIMEOUT_S:
            return_camera_from_scan(now)
            return "Scan timed out; returning"
        return "Scanning for hand/face"

    if camera_scan_state == CAMERA_SCAN_RETURNING:
        if now >= camera_return_complete_at:
            camera_scan_state = CAMERA_SCAN_IDLE
            return "Camera returned"
        return "Returning camera"

    # Once locked, leave the lens at the detected target until it has been gone
    # long enough to avoid returning during a brief dropped inference frame.
    if hand_detected or face_detected:
        camera_target_last_seen_at = now
        return "Tracking hand/face"
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
robot_state = STATE_STANDING
COMMAND_COOLDOWN_S = 0.7
POSTURE_TRANSITION_COOLDOWN_S = 2.0
command_cooldown_until = 0.0

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
last_text = ""


def main_loop():
    global inference_counter, last_text, robot_state, command_cooldown_until
    global last_familiar_time, next_face_check_at

    try:
        frame = cam.read()
        if frame is None:
            return
        frame = cv2.flip(frame, 1)

        if robot_state == STATE_CHASING:
            if ball_tracker.should_check_manual_stop():
                hands = detector.detect(frame)
                if hands and is_pointing_down(hands[0]["landmarks"]):
                    send_motor_command(CMD_STOP)
                    robot_state = STATE_STANDING
                    command_cooldown_until = time.time() + COMMAND_COOLDOWN_S
                    _update_oled("Stopped (manual)")
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

            _update_oled(display_text)
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
                if camera_scan_state == CAMERA_SCAN_SCANNING
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
            face_status in ("familiar", "unfamiliar"),
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

        _update_oled(display_text)
    except Exception:
        print("Loop Error:")
        traceback.print_exc()
        return_camera_from_scan(time.monotonic())
        send_motor_command(CMD_STOP)


def _update_oled(display_text):
    global last_text
    if display_text == last_text:
        return
    bridge.call("update_oled", display_text)
    last_text = display_text


try:
    App.run(user_loop=main_loop)
except NameError:
    while True:
        main_loop()
