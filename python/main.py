"""Code chính điều khiển robot RoboDog"""

# Khai báo các thư viện cần thiết
import os

# App Lab launches this file as the Linux-side entrypoint. Set this toggle to
# True for the speech-only test so it runs inside App Lab's Python runtime,
# where arduino.app_utils.Bridge is available. Set it back to False to resume
# the normal vision application.
RUN_SPEECH_TEST_ONLY = False
if RUN_SPEECH_TEST_ONLY:
    from speech_test import main as run_speech_test

    run_speech_test()
    raise SystemExit

import threading
import time
import traceback

import cv2

from arduino.app_utils import App, Bridge
from ball_tracker import BallTracker
from face_gate import FaceGate, verify_face_models
from speech_test import MOVEMENT_COMMANDS, start_speech_service

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
            bridge.call("update_oled", "CAM FAILED")

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
CAM_CMD_SCAN_STOP = "x"

# Camera scanning is time-based because the tilt servo is continuous rotation.
CAMERA_SCAN_TIMEOUT_S = 1.5
CAMERA_TARGET_LOST_S = 1.0
CAMERA_SCAN_FACE_CHECK_PERIOD_S = 0.25
CAMERA_RETURN_SETTLE_S = 0.1

CAMERA_SCAN_IDLE = "IDLE"
CAMERA_SCAN_FACE_SCANNING = "FACE_SCANNING"
CAMERA_SCAN_LOCKED = "LOCKED"
CAMERA_SCAN_RETURNING = "RETURNING"

cam = CameraStream(camera_path)
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
camera_target_last_seen_at = 0.0
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


def start_camera_motion(now, command, direction, state):
    """Start continuous motion and track its signed timed offset from neutral."""
    global camera_scan_state, camera_scan_started_at, camera_scan_direction
    global next_face_check_at, python_cam_state
    send_motor_command(command)
    camera_scan_state = state
    camera_scan_started_at = now
    camera_scan_direction = direction
    if state == CAMERA_SCAN_FACE_SCANNING:
        # Do not wait for the normal, slower face-recognition interval.
        next_face_check_at = now
    python_cam_state = "N"


def start_camera_scan(now):
    """Start an upward face scan after legs are detected."""
    global camera_net_offset_s
    if camera_scan_state != CAMERA_SCAN_IDLE:
        return
    camera_net_offset_s = 0.0
    start_camera_motion(
        now,
        CAM_CMD_SCAN_UP,
        1,
        CAMERA_SCAN_FACE_SCANNING,
    )


def stop_camera_scan(now):
    """Stop active scanning and add its signed movement to the net offset."""
    global camera_scan_state, camera_net_offset_s, camera_scan_direction
    global camera_target_last_seen_at
    if camera_scan_state != CAMERA_SCAN_FACE_SCANNING:
        return 0.0
    send_motor_command(CAM_CMD_SCAN_STOP)
    travel_time = max(0.0, now - camera_scan_started_at)
    camera_net_offset_s += camera_scan_direction * travel_time
    camera_scan_direction = 0
    camera_scan_state = CAMERA_SCAN_LOCKED
    camera_target_last_seen_at = now
    return travel_time


def return_camera_from_scan(now):
    """Return the camera to its neutral position after a face scan."""
    global camera_scan_state, camera_net_offset_s
    global camera_scan_direction, python_cam_state, camera_return_complete_at
    if camera_scan_state in (CAMERA_SCAN_IDLE, CAMERA_SCAN_RETURNING):
        return
    if camera_scan_state == CAMERA_SCAN_FACE_SCANNING:
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
    python_cam_state = "N"


def update_camera_scan(legs_detected, face_status, now):
    """Advance legs -> face scan -> hold -> reduced timed return."""
    global camera_scan_state, camera_target_last_seen_at
    global camera_net_offset_s

    if camera_scan_state == CAMERA_SCAN_IDLE:
        if legs_detected:
            start_camera_scan(now)
            return "Scanning for familiar face"
        return None

    if camera_scan_state == CAMERA_SCAN_FACE_SCANNING:
        if face_status == "familiar":
            stop_camera_scan(now)
            return "Familiar face found"

        if now - camera_scan_started_at >= CAMERA_SCAN_TIMEOUT_S:
            return_camera_from_scan(now)
            return "Face scan timed out; returning"
        return "Scanning for familiar face"

    if camera_scan_state == CAMERA_SCAN_RETURNING:
        if now >= camera_return_complete_at:
            camera_scan_state = CAMERA_SCAN_IDLE
            camera_net_offset_s = 0.0
            return "Camera returned"
        return "Returning camera"

    # Once a familiar face is found, leave the lens there until recognition
    # reports a loss for long enough to avoid returning on one missed frame.
    if face_status == "familiar":
        camera_target_last_seen_at = now
        return "Tracking familiar face"
    if (
        face_status != "none"
        and now - camera_target_last_seen_at >= CAMERA_TARGET_LOST_S
    ):
        return_camera_from_scan(now)
        return "Target lost; returning"
    return "Holding familiar face"


FACE_CHECK_PERIOD_S = 0.8
next_face_check_at = 0.0
last_face_matrix_state = None
last_face_status = "none"


def set_face_matrix(expression):
    global last_face_matrix_state
    if expression == last_face_matrix_state:
        return
    try:
        bridge.call("update_face_matrix", expression)
    except Exception as error:
        print(f"Bridge call failed: {error}")
    last_face_matrix_state = expression


def play_robot_sound(sound):
    """Ask the UNO Q MCU to send a framed sound event to the ESP32."""
    try:
        bridge.call("play_test_sound", sound)
        print(f"ROBOT_SOUND_REQUESTED: {sound}")
    except Exception as error:
        print(f"Sound Bridge call failed: {error}")


STATE_STANDING = "STANDING"
STATE_SITTING = "SITTING"
STATE_PRONE = "PRONE"
STATE_CHASING = "CHASING"
robot_state = STATE_STANDING
COMMAND_COOLDOWN_S = 0.7
POSTURE_TRANSITION_COOLDOWN_S = 2.0
command_cooldown_until = 0.0


def handle_speech_command(command):
    """Execute a Realtime movement command without face recognition gating."""
    global robot_state, command_cooldown_until

    command = str(command).lower().strip()
    uart_command = MOVEMENT_COMMANDS.get(command)
    if uart_command is None:
        print(f"SPEECH_COMMAND_REJECTED: {command!r}")
        return {"status": "rejected", "message": "Unsupported movement"}

    if command == "chase":
        send_motor_command(CMD_STOP)
        robot_state = STATE_CHASING
        ball_tracker.start_chase()
        set_cam_state("D")
        command_cooldown_until = time.time() + POSTURE_TRANSITION_COOLDOWN_S
        display_text = "VOICE: CHASE"
        uart_command = CMD_STOP
    else:
        previous_state = robot_state
        send_motor_command(uart_command)
        if command == "sit":
            robot_state = STATE_SITTING
        elif command == "prone":
            robot_state = STATE_PRONE
        elif command in {"stand", "stop"}:
            robot_state = STATE_STANDING
        if previous_state == STATE_CHASING:
            return_camera_from_scan(time.monotonic())
        if robot_state == STATE_STANDING:
            set_cam_state("N")
        transition = command in {"sit", "prone", "stand", "stop", "chase"}
        command_cooldown_until = time.time() + (
            POSTURE_TRANSITION_COOLDOWN_S if transition else COMMAND_COOLDOWN_S
        )
        display_text = f"VOICE: {command.upper()}"

    # Speech movement commands are allowed without a familiar face. The audio
    # acknowledgement is generated locally rather than by a model tool call.
    play_robot_sound("success")
    print(f"SPEECH_COMMAND_ACCEPTED: {command} -> {uart_command}")
    _update_oled(display_text)
    return {
        "status": "accepted",
        "command": command,
        "uart": uart_command,
    }


inference_counter = 0
last_text = ""


def main_loop():
    global inference_counter, last_text, robot_state, command_cooldown_until
    global last_face_status, next_face_check_at

    try:
        frame = cam.read()
        if frame is None:
            return
        frame = cv2.flip(frame, 1)

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
                if last_face_status != "familiar":
                    play_robot_sound("beep")
                set_face_matrix("smiley")
            elif face_status == "unfamiliar":
                set_face_matrix("indifferent")
            last_face_status = face_status

        if robot_state == STATE_CHASING:
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

        display_text = "WAITING FOR SPEECH"
        legs_detected = False
        if (
            robot_state == STATE_STANDING
            and camera_scan_state == CAMERA_SCAN_IDLE
            and ball_tracker.should_check_camera()
        ):
            person_box = ball_tracker.detect_person(frame)
            legs_detected = (
                person_box is not None and ball_tracker.is_legs_only(person_box)
            )

        scan_display = update_camera_scan(
            legs_detected,
            face_status,
            now,
        )

        if robot_state != STATE_STANDING:
            return_camera_from_scan(now)
        elif scan_display is not None:
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


speech_service = start_speech_service(bridge, on_move=handle_speech_command)

try:
    App.run(user_loop=main_loop)
except NameError:
    while True:
        main_loop()
finally:
    if speech_service is not None:
        speech_service.stop()
