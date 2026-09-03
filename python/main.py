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
DEFAULT_CAMERA_PATH = "/dev/v4l/by-id/usb-046d_C270_HD_WEBCAM_E21C4540-video-index0"
camera_path = os.getenv("UNO_Q_CAMERA_PATH") or DEFAULT_CAMERA_PATH             # Đường dẫn đến cổng webcam

CAMERA_READ_FAILURE_LIMIT = 5
CAMERA_RESTART_BACKOFF_S = 0.5
CAMERA_FRAME_STALE_S = 1.5


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
        self.path = path
        self.width = width
        self.height = height
        self.cap = None
        self._cap_lock = threading.Lock()
        self.lock = threading.Lock()
        self.frame = None
        self._status_lock = threading.Lock()
        self._camera_live = False
        self._last_frame_at = 0.0
        self._restart_count = 0
        self._last_restart_reason = None
        self._last_camera_error = None
        self._restart_requested = threading.Event()
        self._restart_reason_lock = threading.Lock()
        self._restart_reason = "startup"
        self.running = True
        self._replace_capture("startup")
        self.thread = threading.Thread(target=self._update, daemon=True)
        self.thread.start()

    def _create_capture(self):
        cap = cv2.VideoCapture(self.path, cv2.CAP_V4L2)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _set_camera_error(self, message):
        with self._status_lock:
            self._camera_live = False
            self._last_camera_error = str(message)

    def _record_frame(self, now):
        with self._status_lock:
            self._camera_live = True
            self._last_frame_at = now
            self._last_camera_error = None

    def _replace_capture(self, reason):
        """Release and reopen the V4L2 handle from the reader thread."""

        with self._cap_lock:
            old_cap = self.cap
            self.cap = None
        if old_cap is not None:
            try:
                old_cap.release()
            except Exception:
                pass

        new_cap = None
        try:
            new_cap = self._create_capture()
            opened = bool(new_cap.isOpened())
        except Exception as error:
            if new_cap is not None:
                try:
                    new_cap.release()
                except Exception:
                    pass
            self._set_camera_error(f"open failed: {error}")
            print(f"CAMERA WARNING: reopen failed ({reason}): {error}")
            return False

        if not opened:
            try:
                new_cap.release()
            except Exception:
                pass
            message = f"could not open '{self.path}'"
            self._set_camera_error(message)
            if reason == "startup":
                print(f"CAMERA ERROR: {message}.")
            else:
                print(f"CAMERA WARNING: reopen failed ({reason}): {message}.")
            return False

        if not self.running:
            try:
                new_cap.release()
            except Exception:
                pass
            return False

        with self._cap_lock:
            self.cap = new_cap
        with self.lock:
            self.frame = None
        with self._status_lock:
            self._camera_live = False
            self._last_frame_at = 0.0
            self._last_camera_error = None
            self._last_restart_reason = reason
            if reason != "startup":
                self._restart_count += 1

        if reason != "startup":
            print(f"CAMERA RESTARTED: {reason}")
        return True

    def restart(self, reason="manual"):
        """Request a non-blocking release/reopen of the webcam handle."""

        if not self.running:
            return False
        with self._restart_reason_lock:
            self._restart_reason = str(reason)
        self._restart_requested.set()
        print(f"CAMERA RESTART REQUESTED: {reason}")
        return True

    def status(self):
        """Return health information for the dashboard/API."""

        now = time.monotonic()
        with self._status_lock:
            live = self._camera_live and now - self._last_frame_at <= CAMERA_FRAME_STALE_S
            return {
                "camera_live": live,
                "camera_restart_count": self._restart_count,
                "camera_last_restart_reason": self._last_restart_reason,
                "camera_error": self._last_camera_error,
            }

    def _take_restart_reason(self):
        with self._restart_reason_lock:
            return self._restart_reason

    def _update(self):
        # Luồng phụ liên tục đọc khung hình từ webcam và lưu trữ khung hình mới nhất
        fail_count = 0
        next_retry_at = 0.0
        while self.running:
            now = time.monotonic()
            if self._restart_requested.is_set():
                self._restart_requested.clear()
                self._replace_capture(self._take_restart_reason())
                fail_count = 0
                next_retry_at = time.monotonic() + CAMERA_RESTART_BACKOFF_S
                continue

            with self._cap_lock:
                cap = self.cap
            if cap is None:
                if now < next_retry_at:
                    time.sleep(min(0.05, next_retry_at - now))
                    continue
                self._replace_capture("auto-reconnect")
                fail_count = 0
                next_retry_at = time.monotonic() + CAMERA_RESTART_BACKOFF_S
                continue

            try:
                opened = bool(cap.isOpened())
            except Exception as error:
                opened = False
                self._set_camera_error(f"status failed: {error}")
            if not opened:
                self._replace_capture("device-closed")
                fail_count = 0
                next_retry_at = time.monotonic() + CAMERA_RESTART_BACKOFF_S
                continue

            try:
                ret, frame = cap.read()
            except Exception as error:
                ret, frame = False, None
                self._set_camera_error(f"read failed: {error}")
            if ret and frame is not None:
                fail_count = 0
                with self.lock:
                    self.frame = frame
                self._record_frame(time.monotonic())
            else:
                fail_count += 1
                self._set_camera_error(f"read failed ({fail_count} consecutive times)")
                if fail_count == 1 or fail_count % CAMERA_READ_FAILURE_LIMIT == 0:
                    print(f"CAMERA WARNING: read() failed ({fail_count} times so far)")
                if fail_count >= CAMERA_READ_FAILURE_LIMIT:
                    self._replace_capture(f"read-fail-{fail_count}")
                    fail_count = 0
                    next_retry_at = time.monotonic() + CAMERA_RESTART_BACKOFF_S
                else:
                    time.sleep(0.05)

    def read(self):
        # Trả về khung hình mới nhất từ webcam, nếu không có khung hình nào thì trả về None
        with self.lock:
            return None if self.frame is None else self.frame.copy()

    def stop(self):
        # Dừng luồng đọc khung hình và giải phóng tài nguyên của webcam
        self.running = False
        self._restart_requested.set()
        self.thread.join(timeout=1)
        with self._cap_lock:
            cap = self.cap
            self.cap = None
        if cap is not None:
            cap.release()


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
last_face_status = "none"
last_hand_detected = False
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


def handle_face_gate_requirement(required=None):
    # Cập nhật yêu cầu khuôn mặt quen do dashboard gửi tới.
    global REQUIRE_FAMILIAR_FACE

    if required is None:
        required = not REQUIRE_FAMILIAR_FACE
    REQUIRE_FAMILIAR_FACE = bool(required)

    # Đưa phiên quét đang chạy về bước trả camera để dùng cài đặt mới.
    if camera_scan_state not in (CAMERA_SCAN_IDLE, CAMERA_SCAN_RETURNING):
        return_camera_from_scan(time.monotonic())

    setting = "required" if REQUIRE_FAMILIAR_FACE else "not_required"
    print(f"FACE_GATE_REQUIREMENT={setting}")
    return {
        "status": "accepted",
        "require_familiar_face": REQUIRE_FAMILIAR_FACE,
    }


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
manual_control_source = None
COMMAND_COOLDOWN_S = 0.7
POSTURE_TRANSITION_COOLDOWN_S = 2.0
command_cooldown_until = 0.0
last_dashboard_command = None
last_dashboard_command_at = None
last_dashboard_error = None
last_ball_detection_status = "inactive"
last_ball_detection_objects = []


def send_control_mode(mode):
    """Tell the UNO Q which control source the ESP32 should accept."""
    try:
        bridge.call("set_control_mode", mode)
        return True
    except Exception as error:
        print(f"Control-mode Bridge call failed: {error}")
        return False


def enter_manual_control(source="bluetooth", notify_esp32=True):
    """Stop autonomous motion and expose the webcam/dashboard controls."""
    global robot_state, manual_control_source, command_cooldown_until
    global camera_scan_state, camera_scan_direction, camera_net_offset_s
    global camera_hand_confirmations, python_cam_state

    send_motor_command(CMD_STOP)
    send_motor_command(CAM_CMD_NEUTRAL)
    camera_scan_state = CAMERA_SCAN_IDLE
    camera_scan_direction = 0
    camera_net_offset_s = 0.0
    camera_hand_confirmations = 0
    python_cam_state = "N"
    if notify_esp32:
        send_control_mode("manual")
    robot_state = STATE_MANUAL
    manual_control_source = source
    command_cooldown_until = 0.0
    manual_video.set_active(True)
    _log_status("MANUAL CONTROL")


def leave_manual_control(source="bluetooth", notify_esp32=True):
    """Return to gesture, face, and ball processing after Bluetooth exits."""
    global robot_state, manual_control_source, command_cooldown_until
    global camera_scan_state, camera_scan_direction, camera_net_offset_s
    global camera_hand_confirmations, python_cam_state

    manual_video.set_active(False)
    if notify_esp32:
        send_control_mode("automatic")
    send_motor_command(CMD_STOP)
    send_motor_command(CAM_CMD_NEUTRAL)
    camera_scan_state = CAMERA_SCAN_IDLE
    camera_scan_direction = 0
    camera_net_offset_s = 0.0
    camera_hand_confirmations = 0
    python_cam_state = "N"
    robot_state = STATE_STANDING
    manual_control_source = None
    command_cooldown_until = time.time() + POSTURE_TRANSITION_COOLDOWN_S
    _log_status("AUTOMATIC CONTROL")


def handle_esp32_control_mode(mode):
    """Follow Bluetooth M/O mode notifications from the ESP32."""
    global manual_control_source
    normalized = str(mode).strip().lower()
    if normalized == "manual":
        if robot_state != STATE_MANUAL:
            enter_manual_control("bluetooth", notify_esp32=False)
        elif manual_control_source != "bluetooth":
            # Bluetooth has priority if it enters manual mode while the
            # browser dashboard is open.
            manual_control_source = "bluetooth"
            print("MANUAL_CONTROL_OWNER=bluetooth")
    elif normalized in {"automatic", "auto"} and robot_state == STATE_MANUAL:
        leave_manual_control("bluetooth", notify_esp32=False)


# Names accepted by the browser dashboard. Values are the same one-byte
# command vocabulary used by the ESP32 firmware and the Bluetooth client.
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
    """Accept one dashboard command only while the dashboard owns manual mode."""
    global last_dashboard_command, last_dashboard_command_at, last_dashboard_error

    normalized = str(command).lower().strip()
    uart_command = DASHBOARD_COMMANDS.get(normalized)
    if uart_command is None and len(normalized) == 1:
        if normalized in set(DASHBOARD_COMMANDS.values()):
            uart_command = normalized
    if uart_command is None:
        last_dashboard_error = f"Unsupported dashboard command: {normalized!r}"
        return {"status": "rejected", "message": last_dashboard_error}

    if robot_state != STATE_MANUAL or manual_control_source != "dashboard":
        owner = manual_control_source or "automatic"
        last_dashboard_error = (
            f"Dashboard is not the active control source ({owner})"
        )
        return {"status": "rejected", "message": last_dashboard_error}

    if not send_motor_command(uart_command):
        last_dashboard_error = "Could not send command through the UNO Q bridge"
        return {"status": "error", "message": last_dashboard_error}

    last_dashboard_command = normalized
    last_dashboard_command_at = time.time()
    last_dashboard_error = None
    _log_status(f"WEB: {normalized.upper()}")
    print(f"DASHBOARD_COMMAND_ACCEPTED: {normalized} -> {uart_command}")
    return {"status": "accepted", "command": normalized, "uart": uart_command}


def handle_dashboard_mode(mode):
    """Enter or leave the browser-controlled state."""
    global last_dashboard_error

    normalized = str(mode).lower().strip()
    if normalized in {"manual", "dashboard"}:
        if robot_state == STATE_MANUAL and manual_control_source == "bluetooth":
            last_dashboard_error = "Bluetooth currently owns manual control"
            return {"status": "rejected", "message": last_dashboard_error}
        if robot_state != STATE_MANUAL or manual_control_source != "dashboard":
            enter_manual_control("dashboard", notify_esp32=False)
            # Python autonomy is paused, while the ESP32 remains in its UART
            # accepting mode for dashboard commands.
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
    """Return the state snapshot rendered by the browser dashboard."""
    if robot_state == STATE_MANUAL:
        control_mode = f"manual-{manual_control_source or 'unknown'}"
    else:
        control_mode = "automatic"
    status = {
        "control_mode": control_mode,
        "robot_state": robot_state,
        "manual_source": manual_control_source,
        "camera_path": camera_path,
        "require_familiar_face": REQUIRE_FAMILIAR_FACE,
        "face_status": last_face_status,
        "hand_detected": last_hand_detected,
        "camera_scan_state": camera_scan_state,
        "ball_detection_active": robot_state == STATE_CHASING,
        "ball_detection_status": (
            last_ball_detection_status
            if robot_state == STATE_CHASING
            else "inactive"
        ),
        "ball_detection_objects": (
            list(last_ball_detection_objects)
            if robot_state == STATE_CHASING
            else []
        ),
        "last_command": last_dashboard_command,
        "last_command_at": last_dashboard_command_at,
        "last_error": last_dashboard_error,
    }
    status.update(cam.status())
    return status


def force_restart_camera():
    """Ask the camera reader thread to release and reopen the webcam."""

    if not cam.restart(reason="dashboard"):
        return {"status": "error", "message": "Camera stream is stopped"}
    return {"status": "accepted", "message": "Camera restart requested"}

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
    global last_face_status, last_hand_detected
    global last_ball_detection_status, last_ball_detection_objects

    try:
        frame = cam.read()
        if frame is None:
            return
        frame = cv2.flip(frame, 1)

        # Bluetooth owns every movement command in manual mode. The camera
        # reader continues running so ManualVideoServer can publish frames.
        if robot_state == STATE_MANUAL:
            # Keep hand inference alive for status/monitoring even though
            # manual movement ownership belongs to Bluetooth or the dashboard.
            inference_counter += 1
            if inference_counter % 3 == 0:
                last_hand_detected = bool(detector.detect(frame))
            return

        if robot_state == STATE_CHASING:
            if ball_tracker.should_check_manual_stop():
                hands = detector.detect(frame)
                last_hand_detected = bool(hands)
                if hands and is_pointing_down(hands[0]["landmarks"]):
                    send_motor_command(CMD_STOP)
                    robot_state = STATE_STANDING
                    command_cooldown_until = time.time() + COMMAND_COOLDOWN_S
                    _log_status("Stopped (manual)")
                    return

            ball, top_candidates, command, display_text, exit_reason = (
                ball_tracker.command_for_frame(frame)
            )
            last_ball_detection_objects = [
                {"label": name, "confidence": round(float(confidence), 3)}
                for name, confidence in top_candidates[:5]
            ]
            if exit_reason == "found":
                last_ball_detection_status = "found"
            elif exit_reason == "gave_up":
                last_ball_detection_status = "not_found"
            elif ball is not None:
                last_ball_detection_status = "tracking"
            elif ball_tracker.ball_ever_seen:
                last_ball_detection_status = "lost"
            else:
                last_ball_detection_status = "searching"
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
        last_hand_detected = bool(hands)
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
            last_face_status = face_status
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
                    last_ball_detection_status = "searching"
                    last_ball_detection_objects = []
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


manual_video.configure_dashboard(
    command_handler=handle_dashboard_command,
    mode_handler=handle_dashboard_mode,
    status_provider=dashboard_status,
    camera_restart_handler=force_restart_camera,
    face_gate_handler=handle_face_gate_requirement,
)


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
