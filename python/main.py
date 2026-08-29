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
from manual_media import ManualMediaServer
from speech_test import MOVEMENT_COMMANDS, start_speech_service

cv2.setNumThreads(1)    # Khởi tạo OpenCV để sử dụng một luồng duy nhất, tránh xung đột với các luồng khác
bridge = Bridge()       # Khởi tạo cầu nối giữa Python và Arduino để gửi lệnh điều khiển robot

current_dir = os.path.dirname(os.path.abspath(__file__))                        # Thư mục hiện tại của file main.py
DEFAULT_CAMERA_PATH = "/dev/v4l/by-id/usb-HX-MT9M114-201012_Integrated_Camera-video-index0"
camera_path = os.getenv("UNO_Q_CAMERA_PATH") or DEFAULT_CAMERA_PATH             # Đường dẫn đến cổng webcam


def send_motor_command(command):
    # Gửi lệnh điều khiển động cơ đến Arduino thông qua cầu nối
    try:
        bridge.call("send_motor_command", command)
        return True
    except Exception as error:
        print(f"Bridge call failed: {error}")
        return False


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
manual_media = ManualMediaServer(cam.read)
manual_media.start()
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
STATE_MANUAL = "MANUAL_CONTROL"
robot_state = STATE_STANDING
manual_control_source = None
COMMAND_COOLDOWN_S = 0.7
POSTURE_TRANSITION_COOLDOWN_S = 2.0
command_cooldown_until = 0.0
last_dashboard_command = None
last_dashboard_command_at = None
last_dashboard_error = None


def send_control_mode(mode):
    """Tell the UNO Q MCU which control source the ESP32 must accept."""
    try:
        bridge.call("set_control_mode", mode)
    except Exception as error:
        print(f"Control-mode Bridge call failed: {error}")


def enter_manual_control(source="voice", notify_esp32=True):
    """Stop autonomous motion and expose the LAN media streams."""
    global robot_state, manual_control_source, command_cooldown_until
    global camera_scan_state, camera_scan_direction, camera_net_offset_s
    global python_cam_state
    send_motor_command(CMD_STOP)
    # Manual control owns the camera position, so cancel any autonomous scan
    # immediately instead of waiting for the vision loop to finish returning.
    send_motor_command(CAM_CMD_NEUTRAL)
    camera_scan_state = CAMERA_SCAN_IDLE
    camera_scan_direction = 0
    camera_net_offset_s = 0.0
    python_cam_state = "N"
    if notify_esp32:
        send_control_mode("manual")
    robot_state = STATE_MANUAL
    manual_control_source = source
    command_cooldown_until = 0.0
    manual_media.set_active(True)
    _update_oled("MANUAL CONTROL")
    print(f"MANUAL_CONTROL_ENTERED source={source}")


def leave_manual_control(source="voice", notify_esp32=True):
    """Return to the existing vision/voice-controlled state."""
    global robot_state, manual_control_source, command_cooldown_until
    global camera_scan_state, camera_scan_direction, camera_net_offset_s
    global python_cam_state
    manual_media.set_active(False)
    if notify_esp32:
        send_control_mode("automatic")
    send_motor_command(CMD_STOP)
    send_motor_command(CAM_CMD_NEUTRAL)
    camera_scan_state = CAMERA_SCAN_IDLE
    camera_scan_direction = 0
    camera_net_offset_s = 0.0
    python_cam_state = "N"
    robot_state = STATE_STANDING
    manual_control_source = None
    command_cooldown_until = time.time() + POSTURE_TRANSITION_COOLDOWN_S
    _update_oled("AUTOMATIC CONTROL")
    print(f"MANUAL_CONTROL_EXITED source={source}")


def handle_esp32_control_mode(mode):
    """Reflect a Bluetooth-originated mode change from the ESP32."""
    global manual_control_source
    mode = str(mode).lower().strip()
    if mode == "manual":
        if robot_state != STATE_MANUAL:
            enter_manual_control("bluetooth", notify_esp32=False)
        elif manual_control_source != "bluetooth":
            # A Bluetooth M command can take ownership while the dashboard is
            # open. Keep the media page alive, but reject dashboard movement
            # until the ESP32 reports automatic mode again.
            manual_control_source = "bluetooth"
            _update_oled("BLUETOOTH CONTROL")
            print("MANUAL_CONTROL_OWNER=bluetooth")
    elif mode in {"automatic", "auto"} and robot_state == STATE_MANUAL:
        leave_manual_control("bluetooth", notify_esp32=False)


def handle_speech_command(command):
    """Execute a Realtime movement command without face recognition gating."""
    global robot_state, command_cooldown_until

    command = str(command).lower().strip()
    if command == "manual":
        enter_manual_control("voice")
        return {"status": "accepted", "command": command, "mode": "manual"}
    if command == "automatic":
        leave_manual_control("voice")
        return {"status": "accepted", "command": command, "mode": "automatic"}
    if robot_state == STATE_MANUAL:
        print(f"SPEECH_COMMAND_REJECTED_MANUAL_MODE: {command!r}")
        return {
            "status": "rejected",
            "message": "Manual mode accepts Bluetooth movement commands only",
        }
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


# The supplied gait firmware accepts Uno Q CMD frames while it is in its
# automatic control mode. Dashboard mode pauses the UNO Q vision loop, then
# deliberately leaves the ESP32 in that UART-accepting mode. Bluetooth can
# still take priority; handle_esp32_control_mode reports that ownership change
# to the dashboard.
DASHBOARD_COMMANDS = {
    "forward": "w",
    "backward": "b",
    "turn_left": "a",
    "turn_right": "d",
    "crab_left": "e",
    "crab_right": "f",
    "pace": "p",
    "stop": "s",
    "stand": "s",
    "hold": "z",
    "sit": "q",
    "prone": "c",
    "wave": "g",
    "bounce": "u",
    "jump": "j",
    "center": "k",
    "camera_up": "h",
    "camera_down": "l",
    "camera_neutral": "n",
    "camera_scan_up": "r",
    "camera_scan_down": "v",
    "camera_stop": "x",
}


def handle_dashboard_command(command):
    """Accept one dashboard command when the dashboard owns manual mode."""
    global last_dashboard_command, last_dashboard_command_at, last_dashboard_error

    normalized = str(command).lower().strip()
    uart_command = DASHBOARD_COMMANDS.get(normalized)
    if uart_command is None and len(normalized) == 1:
        # Allow the dashboard to use the same one-character vocabulary as the
        # ESP32 firmware, while still rejecting arbitrary UART payloads.
        supported = set(DASHBOARD_COMMANDS.values())
        if normalized in supported:
            uart_command = normalized
    if uart_command is None:
        last_dashboard_error = f"Unsupported dashboard command: {normalized!r}"
        return {"status": "rejected", "message": last_dashboard_error}

    if robot_state != STATE_MANUAL or manual_control_source != "dashboard":
        owner = manual_control_source or "automatic"
        last_dashboard_error = f"Dashboard is not the active control source ({owner})"
        return {"status": "rejected", "message": last_dashboard_error}

    if not send_motor_command(uart_command):
        last_dashboard_error = "Could not send command through the UNO Q bridge"
        return {"status": "error", "message": last_dashboard_error}

    last_dashboard_command = normalized
    last_dashboard_command_at = time.time()
    last_dashboard_error = None
    _update_oled(f"WEB: {normalized.upper()}")
    print(f"DASHBOARD_COMMAND_ACCEPTED: {normalized} -> {uart_command}")
    return {
        "status": "accepted",
        "command": normalized,
        "uart": uart_command,
    }


def handle_dashboard_mode(mode):
    """Enter or leave the dashboard's browser-controlled state."""
    global last_dashboard_error

    normalized = str(mode).lower().strip()
    if normalized in {"manual", "dashboard"}:
        if robot_state == STATE_MANUAL and manual_control_source == "bluetooth":
            last_dashboard_error = "Bluetooth currently owns manual control"
            return {"status": "rejected", "message": last_dashboard_error}
        if robot_state != STATE_MANUAL or manual_control_source != "dashboard":
            enter_manual_control("dashboard", notify_esp32=False)
            # The supplied gait firmware only accepts Uno Q CMD frames in its
            # automatic mode. Python autonomy is paused above, so the browser
            # becomes the only normal Uno Q command source in this state.
            send_control_mode("automatic")
        last_dashboard_error = None
        print("DASHBOARD_CONTROL_ENTERED")
        return {"status": "accepted", "mode": "dashboard"}

    if normalized in {"automatic", "auto"}:
        if robot_state == STATE_MANUAL:
            leave_manual_control("dashboard")
        last_dashboard_error = None
        print("DASHBOARD_CONTROL_EXITED")
        return {"status": "accepted", "mode": "automatic"}

    last_dashboard_error = f"Unsupported dashboard mode: {normalized!r}"
    return {"status": "rejected", "message": last_dashboard_error}


def dashboard_status():
    """Return the small state snapshot used by the browser dashboard."""
    if robot_state == STATE_MANUAL:
        control_mode = f"manual-{manual_control_source or 'unknown'}"
    else:
        control_mode = "automatic"
    realtime = globals().get("speech_service")
    return {
        "control_mode": control_mode,
        "robot_state": robot_state,
        "manual_source": manual_control_source,
        "camera_path": camera_path,
        "speech_input": os.getenv("UNO_Q_SPEECH_INPUT", "usb"),
        "microphone_device": os.getenv("UNO_Q_MIC_DEVICE", "default"),
        "face_status": last_face_status,
        "camera_scan_state": camera_scan_state,
        "speech_enabled": bool(os.getenv("OPENAI_API_KEY")),
        "realtime_ready": bool(
            realtime is not None
            and realtime.realtime is not None
            and realtime.realtime.ready.is_set()
        ),
        "last_command": last_dashboard_command,
        "last_command_at": last_dashboard_command_at,
        "last_error": last_dashboard_error,
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

        # Manual mode deliberately leaves the camera and microphone paths as
        # relays.  No gesture, face, ball, or voice-generated movement is run.
        if robot_state == STATE_MANUAL:
            return

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


manual_media.configure_dashboard(
    command_handler=handle_dashboard_command,
    mode_handler=handle_dashboard_mode,
    status_provider=dashboard_status,
)


try:
    # The MCU uses Bridge.notify when Bluetooth enters/exits manual mode.
    Bridge.provide("manual_mode_changed", handle_esp32_control_mode)
except Exception as error:
    print(f"Could not register ESP32 mode callback: {error}")


speech_service = start_speech_service(
    bridge,
    on_move=handle_speech_command,
    on_audio_frame=manual_media.publish_audio_frame,
)

try:
    App.run(user_loop=main_loop)
except NameError:
    while True:
        main_loop()
finally:
    if speech_service is not None:
        speech_service.stop()
    manual_media.stop()
