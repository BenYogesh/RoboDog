import cv2
import numpy as np
import time
import threading
import queue
import os
import traceback

from arduino.app_utils import App, Bridge
from hand_gesture_detector import HandGestureDetector

bridge = Bridge()

# --- 1. MOTOR COMMAND OUTPUT (via Bridge) ---
def send_motor_command(command):
    try:
        bridge.call("send_motor_command", command)
    except Exception as e:
        print(f"Bridge call failed: {e}")

# --- 2. AI MODEL SETUP ---
current_dir = os.path.dirname(os.path.abspath(__file__))
camera_path = "/dev/v4l/by-id/usb-046d_C270_HD_WEBCAM_E21C4540-video-index0"


class CameraStream:
    def __init__(self, path, width=320, height=240):
        self.cap = cv2.VideoCapture(path, cv2.CAP_V4L2)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        if not self.cap.isOpened():
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


cam = CameraStream(camera_path)
detector = HandGestureDetector(score_threshold=0.6, conf_threshold=0.6)

from face_gate import FaceGate, verify_face_models
verify_face_models()
face_gate = FaceGate()

# --- ASYNC DEBUG-IMAGE WRITER ---
debug_queue = queue.Queue(maxsize=2)


def _debug_writer():
    while True:
        item = debug_queue.get()
        if item is None:
            break
        path, img = item
        cv2.imwrite(path, img)


threading.Thread(target=_debug_writer, daemon=True).start()

frame_count = 0
inference_counter = 0
last_text = ""

# --- COMMAND LETTERS (as clarified) ---
CMD_WALK = 'w'
CMD_STOP = 's'
CMD_LEFT = 'a'
CMD_RIGHT = 'd'
CMD_SIT = 'q'
CMD_PRONE = 'c'

# Verified against the actual firmware (RoboDog.ino): 'u' is a continuous
# bounce animation ("Nhún"), not a stand-up transition — that mismatch was
# the cause of "struggles to stand up from sitting." The real standing
# target pose is state 's' ("Đứng yên", stand still / default pose),
# which is the same state CMD_STOP already uses — that's correct, not a
# collision: stopping mid-walk and standing up from sit/prone both want
# the robot at the exact same neutral standing pose.
CMD_STAND_UP = CMD_STOP

# Camera-pitch commands, matching the new ESP32 firmware's independent
# head-servo handling ('h'/'l'/'n', separate from the body-gait letters
# above — verify these match if the firmware side ever changes).
CAM_CMD_UP = 'h'
CAM_CMD_DOWN = 'l'
CAM_CMD_NEUTRAL = 'n'

python_cam_state = 'N'  # 'U'/'D'/'N' — mirrors the firmware's camPitchState,
                          # so we don't resend a command that wouldn't change anything


def set_cam_state(new_state):
    """new_state: 'U', 'D', or 'N'. Only sends a command if it's an
    actual change, same debounce pattern used for the OLED text."""
    global python_cam_state
    if new_state == python_cam_state:
        return
    cmd = {'U': CAM_CMD_UP, 'D': CAM_CMD_DOWN, 'N': CAM_CMD_NEUTRAL}[new_state]
    send_motor_command(cmd)
    python_cam_state = new_state


# --- FACE RECOGNITION GATE ---
# Commands are only accepted from a recognized/familiar face. A single
# missed detection shouldn't immediately revoke command access (faces are
# lost between frames constantly — motion blur, looking away briefly,
# etc.), so a familiar sighting latches command access for a grace
# period rather than requiring re-confirmation every single frame.
FACE_CHECK_INTERVAL = 5       # throttled, same pattern as the other secondary checks
FAMILIAR_GRACE_S = 3.0
last_familiar_time = 0.0      # 0.0 means "never seen anyone familiar yet"
face_check_counter = 0
last_face_matrix_state = None  # avoids resending the same LED matrix expression repeatedly


def commands_currently_allowed():
    return (time.time() - last_familiar_time) < FAMILIAR_GRACE_S


def set_face_matrix(expression):
    global last_face_matrix_state
    if expression == last_face_matrix_state:
        return
    try:
        bridge.call("update_face_matrix", expression)
    except Exception as e:
        print(f"Bridge call failed: {e}")
    last_face_matrix_state = expression


# --- POSTURE STATE MACHINE ---
# STANDING: walk/turn/stop gestures, entry points into SITTING and CHASING.
# SITTING / PRONE: stationary; only point-up/point-down transition
#                  gestures are recognized.
# CHASING: pose/gesture detection is skipped entirely; color-blob ball
#          tracking drives the robot instead, until the ball is "found."
STATE_STANDING = "STANDING"
STATE_SITTING = "SITTING"
STATE_PRONE = "PRONE"
STATE_CHASING = "CHASING"
robot_state = STATE_STANDING

# Universal cooldown after ANY dispatched gesture command (not applied to
# CHASING's own continuous steering — that needs to re-evaluate every
# processed frame to track a moving ball, and cooldown-gating it would
# just make tracking laggy). Prevents a brief, transitional hand shape
# between two deliberate gestures from being read as its own command.
COMMAND_COOLDOWN_S = 0.7

# Posture transitions (sit/prone/stand) get a longer cooldown — the
# physical motion takes longer to settle, and sitting apparently disturbs
# the MPU/IMU readings briefly.
POSTURE_TRANSITION_COOLDOWN_S = 2.0

command_cooldown_until = 0.0

# Vertical pointing threshold, same pixel space / scale as the existing
# horizontal one below.
POINT_VERTICAL_THRESHOLD = 20.0

# MediaPipe hand landmark ids (fingertip / knuckle pairs)
WRIST = 0
INDEX_TIP, INDEX_MCP = 8, 5
MIDDLE_TIP, MIDDLE_MCP = 12, 9
RING_TIP, RING_MCP = 16, 13
PINKY_TIP, PINKY_MCP = 20, 17
THUMB_TIP = 4


def is_folded(landmarks, tip_idx, mcp_idx, wrist_idx=WRIST):
    """landmarks is the (21,3) array already in absolute frame-pixel
    coordinates, courtesy of MPHandPose's own postprocessing."""
    tip = landmarks[tip_idx][:2]
    mcp = landmarks[mcp_idx][:2]
    wrist = landmarks[wrist_idx][:2]
    return np.linalg.norm(tip - wrist) < np.linalg.norm(mcp - wrist)


def is_pointing_down(landmarks):
    """Index finger extended, other three folded, pointing along the
    vertical axis downward — same definition used for the STANDING->
    SITTING gesture, factored out here for reuse in chase mode's manual
    stop check."""
    idx_folded = is_folded(landmarks, INDEX_TIP, INDEX_MCP)
    mid_folded = is_folded(landmarks, MIDDLE_TIP, MIDDLE_MCP)
    rng_folded = is_folded(landmarks, RING_TIP, RING_MCP)
    pnk_folded = is_folded(landmarks, PINKY_TIP, PINKY_MCP)

    if idx_folded or not (mid_folded and rng_folded and pnk_folded):
        return False

    idx_tip_x, idx_tip_y = landmarks[INDEX_TIP][:2]
    idx_mcp_x, idx_mcp_y = landmarks[INDEX_MCP][:2]
    dx = idx_tip_x - idx_mcp_x
    dy = idx_tip_y - idx_mcp_y  # image y increases downward

    if abs(dx) >= abs(dy):
        return False  # horizontal offset dominates — this is left/right, not down

    return dy > POINT_VERTICAL_THRESHOLD


# --- BALL-CHASE (PICKLEBALL) TRACKING ---
# Real object detection instead of color-blob matching: a stock YOLOv8n
# (COCO-pretrained, no custom training) already includes "sports ball" as
# one of its 80 classes. This uses learned visual features instead of
# "anything roughly this color," which is what was causing other objects
# in the room to get mistaken for the ball.
ball_model_path = os.path.join(current_dir, 'yolov8n.onnx')
ball_net = cv2.dnn.readNet(ball_model_path)
ball_output_names = ball_net.getUnconnectedOutLayersNames()

BALL_MODEL_INPUT_SIZE = 192  # must match the imgsz used when exporting the ONNX file
SPORTS_BALL_CLASS_ID = 32    # COCO/YOLO class index for "sports ball" (0-indexed)
BALL_CONF_THRESHOLD = 0.25  # lowered from 0.4 to catch smaller/farther balls; revert if false positives increase
BALL_NMS_THRESHOLD = 0.45


def _verify_ball_model_input_size():
    """Runs one dummy forward pass at startup, so a size mismatch between
    BALL_MODEL_INPUT_SIZE and how yolov8n.onnx was actually exported fails
    immediately and clearly at boot, instead of surfacing deep in the
    chase loop as a cryptic OpenCV reshape assertion."""
    dummy = np.zeros((BALL_MODEL_INPUT_SIZE, BALL_MODEL_INPUT_SIZE, 3), dtype=np.uint8)
    blob = cv2.dnn.blobFromImage(dummy, scalefactor=1.0 / 255.0,
                                  size=(BALL_MODEL_INPUT_SIZE, BALL_MODEL_INPUT_SIZE),
                                  swapRB=True, crop=False)
    try:
        ball_net.setInput(blob)
        ball_net.forward(ball_output_names)
        print(f"Ball model OK: yolov8n.onnx accepts {BALL_MODEL_INPUT_SIZE}x{BALL_MODEL_INPUT_SIZE} input.")
    except cv2.error as e:
        print(f"BALL MODEL SIZE MISMATCH: yolov8n.onnx was not exported at "
              f"imgsz={BALL_MODEL_INPUT_SIZE}. Re-export with "
              f"'yolo export model=yolov8n.pt format=onnx imgsz={BALL_MODEL_INPUT_SIZE}', "
              f"or update BALL_MODEL_INPUT_SIZE to match whatever imgsz the "
              f"current file actually uses. Raw error: {e}")


_verify_ball_model_input_size()

# Very low bar just to see what the model notices at all, for diagnostic
# purposes — separate from BALL_CONF_THRESHOLD, which is the real
# "count this as a ball" threshold.
DIAGNOSTIC_MIN_CONF = 0.15
DIAGNOSTIC_TOP_N = 5

BALL_FOUND_RADIUS = 45
BALL_CENTER_DEADZONE = 25

# How long to wait, doing nothing, before giving up and returning to
# STANDING if the ball has never been seen at all since entering chase
# mode. Only applies to the "never seen it" case.
NEVER_SEEN_TIMEOUT_S = 6.0

# How long to keep spinning to re-find a ball that WAS seen before but is
# currently lost, before giving up and returning to STANDING. Separate
# from NEVER_SEEN_TIMEOUT_S so the two can be tuned independently —
# spinning covers more ground over time than sitting still, so this could
# reasonably be longer, but starting at the same value.
SPIN_SEARCH_TIMEOUT_S = 6.0

# Per-chase-session tracking, reset every time STANDING->CHASING fires.
ball_ever_seen_this_chase = False
last_seen_ball_side = None  # 'left' or 'right'
chase_entry_time = 0.0
last_ball_seen_time = 0.0  # timestamp of the most recent successful detection

# How often (in processed CHASING frames) to check for the manual
# open-palm stop override. Throttled rather than checked every frame,
# since running the hand model alongside the ball detector every single
# frame would slow tracking down.
CHASE_STOP_CHECK_INTERVAL = 5
chase_check_counter = 0

# Standard 80-class COCO/YOLO ordering (0-indexed), for turning class ids
# into readable names on the debug snapshots.
COCO_CLASSES = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat",
    "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack",
    "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball",
    "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
    "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
    "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
    "couch", "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator",
    "book", "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush",
]


_ball_forward_error_count = 0


def _log_ball_forward_error(e):
    """Throttled logging for ball_net.forward() failures — without this,
    a persistent size mismatch would print the same error every single
    processed chase frame."""
    global _ball_forward_error_count
    _ball_forward_error_count += 1
    if _ball_forward_error_count % 30 == 1:
        print(f"BALL MODEL forward() failed ({_ball_forward_error_count} times so far): {e}")


def detect_ball(frame):
    """Runs YOLOv8n, keeps only 'sports ball' detections for the actual
    chase logic, and separately collects the top raw candidates across
    ALL classes (regardless of confidence threshold or class filter) for
    diagnostic display — so you can tell 'model sees nothing at all' from
    'model confidently sees a cup instead of a ball.'

    Returns (ball, top_candidates):
      ball: (x, y, radius) in frame-pixel coordinates, or None
      top_candidates: list of (class_name, confidence) strings for debug display
    """
    h, w = frame.shape[:2]

    blob = cv2.dnn.blobFromImage(
        frame, scalefactor=1.0 / 255.0,
        size=(BALL_MODEL_INPUT_SIZE, BALL_MODEL_INPUT_SIZE),
        swapRB=True, crop=False
    )
    ball_net.setInput(blob)
    try:
        outputs = ball_net.forward(ball_output_names)
    except cv2.error as e:
        _log_ball_forward_error(e)
        return None, []

    # Different YOLOv8 ONNX export settings can produce either (1, 84, N)
    # or (1, N, 84) — blindly transposing one when it's actually the
    # other silently scrambles rows/columns rather than erroring cleanly
    # in an obvious way, and argmax on garbage data can return a class
    # index outside 0-79, which then throws IndexError on the
    # COCO_CLASSES lookup below. Detect the orientation instead of
    # assuming it.
    raw = np.squeeze(outputs[0])
    num_classes_plus_box = 4 + len(COCO_CLASSES)  # 84
    if raw.shape[0] == num_classes_plus_box:
        predictions = raw.T  # (84, N) -> (N, 84)
    elif raw.shape[-1] == num_classes_plus_box:
        predictions = raw    # already (N, 84)
    else:
        print(f"BALL MODEL WARNING: unexpected output shape {raw.shape}, "
              f"expected a dimension of size {num_classes_plus_box}. "
              f"Check that yolov8n.onnx matches the standard 80-class COCO export.")
        return None, []

    scale_x = w / BALL_MODEL_INPUT_SIZE
    scale_y = h / BALL_MODEL_INPUT_SIZE

    boxes = []
    confidences = []
    all_candidates = []  # (class_id, confidence) — for diagnostics, no filtering

    for row in predictions:
        class_scores = row[4:]
        class_id = int(np.argmax(class_scores))
        confidence = float(class_scores[class_id])

        if confidence >= DIAGNOSTIC_MIN_CONF:
            all_candidates.append((class_id, confidence))

        if class_id != SPORTS_BALL_CLASS_ID or confidence < BALL_CONF_THRESHOLD:
            continue

        cx, cy, bw, bh = row[0], row[1], row[2], row[3]
        x1 = (cx - bw / 2) * scale_x
        y1 = (cy - bh / 2) * scale_y
        boxes.append([x1, y1, bw * scale_x, bh * scale_y])
        confidences.append(confidence)

    all_candidates.sort(key=lambda c: c[1], reverse=True)
    top_candidates = [(COCO_CLASSES[cid], conf) for cid, conf in all_candidates[:DIAGNOSTIC_TOP_N]]

    if not boxes:
        return None, top_candidates

    indices = cv2.dnn.NMSBoxes(boxes, confidences, BALL_CONF_THRESHOLD, BALL_NMS_THRESHOLD)
    if len(indices) == 0:
        return None, top_candidates

    best_idx = indices[0] if np.isscalar(indices[0]) else indices[0][0]
    x1, y1, bw, bh = boxes[best_idx]
    center_x = x1 + bw / 2
    center_y = y1 + bh / 2
    radius = max(bw, bh) / 2

    return (center_x, center_y, radius), top_candidates


# --- HEAD-PITCH TRACKING (person/legs detection) ---
# Reuses the same YOLOv8n model already loaded for ball chasing — COCO
# class 0 is "person," so no second model file is needed. This is a
# separate function rather than a generalized/refactored detect_ball()
# specifically to avoid touching the already-working ball detection code.
PERSON_CLASS_ID = 0
PERSON_CONF_THRESHOLD = 0.4

# Heuristic for "seeing legs but not hands": the person's bounding box is
# cut off at the top of frame (their upper body/hands are above what the
# camera can currently see) and tall enough to be a nearby, relevant
# detection rather than a distant one. Both need tuning against your
# actual camera mount height/angle.
LEG_ONLY_TOP_MARGIN_PX = 15
PERSON_MIN_HEIGHT_PX = 40

# Throttle the person-check the same way the chase-mode manual-stop check
# is throttled — running a second YOLO pass every single gesture frame,
# on top of the hand model, adds real cost.
CAM_CHECK_INTERVAL = 5
cam_check_counter = 0


def detect_person(frame):
    """Returns (x1, y1, x2, y2) for the highest-confidence 'person'
    detection, or None. Duplicates detect_ball()'s shape-handling logic
    intentionally, for isolation rather than code reuse."""
    h, w = frame.shape[:2]

    blob = cv2.dnn.blobFromImage(
        frame, scalefactor=1.0 / 255.0,
        size=(BALL_MODEL_INPUT_SIZE, BALL_MODEL_INPUT_SIZE),
        swapRB=True, crop=False
    )
    ball_net.setInput(blob)
    try:
        outputs = ball_net.forward(ball_output_names)
    except cv2.error as e:
        _log_ball_forward_error(e)
        return None

    raw = np.squeeze(outputs[0])
    num_classes_plus_box = 4 + len(COCO_CLASSES)
    if raw.shape[0] == num_classes_plus_box:
        predictions = raw.T
    elif raw.shape[-1] == num_classes_plus_box:
        predictions = raw
    else:
        return None

    scale_x = w / BALL_MODEL_INPUT_SIZE
    scale_y = h / BALL_MODEL_INPUT_SIZE

    boxes = []
    confidences = []

    for row in predictions:
        class_scores = row[4:]
        class_id = int(np.argmax(class_scores))
        confidence = float(class_scores[class_id])

        if class_id != PERSON_CLASS_ID or confidence < PERSON_CONF_THRESHOLD:
            continue

        cx, cy, bw, bh = row[0], row[1], row[2], row[3]
        x1 = (cx - bw / 2) * scale_x
        y1 = (cy - bh / 2) * scale_y
        boxes.append([x1, y1, bw * scale_x, bh * scale_y])
        confidences.append(confidence)

    if not boxes:
        return None

    indices = cv2.dnn.NMSBoxes(boxes, confidences, PERSON_CONF_THRESHOLD, BALL_NMS_THRESHOLD)
    if len(indices) == 0:
        return None

    best_idx = indices[0] if np.isscalar(indices[0]) else indices[0][0]
    x1, y1, bw, bh = boxes[best_idx]
    return (x1, y1, x1 + bw, y1 + bh)


def is_legs_only(person_box):
    x1, y1, x2, y2 = person_box
    box_height = y2 - y1
    return y1 <= LEG_ONLY_TOP_MARGIN_PX and box_height >= PERSON_MIN_HEIGHT_PX


def decide_chase_command(ball, frame_width):
    """Returns (command, display_text, exit_reason).
    exit_reason is None while still chasing, 'found' if the ball was
    reached, or 'gave_up' if it was never seen and the timeout elapsed."""
    global ball_ever_seen_this_chase, last_seen_ball_side, last_ball_seen_time

    frame_center = frame_width / 2.0

    if ball is not None:
        x, y, radius = ball
        ball_ever_seen_this_chase = True
        last_seen_ball_side = 'left' if x < frame_center else 'right'
        last_ball_seen_time = time.time()

        if radius >= BALL_FOUND_RADIUS:
            return CMD_STOP, "Ball found!", 'found'

        if x < frame_center - BALL_CENTER_DEADZONE:
            return CMD_LEFT, "Chasing: left", None
        if x > frame_center + BALL_CENTER_DEADZONE:
            return CMD_RIGHT, "Chasing: right", None
        return CMD_WALK, "Chasing: forward", None

    # Ball not visible this frame.
    if not ball_ever_seen_this_chase:
        elapsed = time.time() - chase_entry_time
        if elapsed >= NEVER_SEEN_TIMEOUT_S:
            return CMD_STOP, "No ball found, giving up", 'gave_up'
        return CMD_STOP, "Searching (never seen yet)", None

    # Seen it before, lost it now — spin toward whichever side it was
    # last seen on, rather than a fixed direction, but give up if it's
    # been too long since it was actually visible.
    elapsed_since_seen = time.time() - last_ball_seen_time
    if elapsed_since_seen >= SPIN_SEARCH_TIMEOUT_S:
        return CMD_STOP, "Lost ball, giving up", 'gave_up'

    search_cmd = CMD_LEFT if last_seen_ball_side == 'left' else CMD_RIGHT
    return search_cmd, f"Chasing: lost, spin {last_seen_ball_side}", None


# --- 3. MAIN LOOP ---
def main_loop():
    global frame_count, inference_counter, last_text, robot_state, command_cooldown_until
    global ball_ever_seen_this_chase, last_seen_ball_side, chase_entry_time, last_ball_seen_time

    try:
        frame = cam.read()
        if frame is None:
            return
        frame = cv2.flip(frame, 1)  # Mirror frame

        # --- CHASING: skip gesture detection, track the ball instead ---
        # No cooldown gate here on purpose — tracking needs fresh input
        # every processed frame to follow a moving ball. The manual-stop
        # check below is throttled instead (CHASE_STOP_CHECK_INTERVAL),
        # since running the hand model every frame here would fight the
        # ball detector for CPU and slow tracking down.
        if robot_state == STATE_CHASING:
            global chase_check_counter
            chase_check_counter += 1

            # Manual override: pointing down forces an immediate stop and
            # exits chase mode, regardless of what the ball detector is
            # doing. This exists because otherwise the only way out of
            # CHASING is the ball being found — not great if detection is
            # unreliable and the robot just keeps wandering.
            if chase_check_counter % CHASE_STOP_CHECK_INTERVAL == 0:
                hands = detector.detect(frame)
                if hands:
                    landmarks = hands[0]["landmarks"]
                    if is_pointing_down(landmarks):
                        send_motor_command(CMD_STOP)
                        robot_state = STATE_STANDING
                        display_text = "Stopped (manual)"
                        command_cooldown_until = time.time() + COMMAND_COOLDOWN_S
                        if display_text != last_text:
                            bridge.call("update_oled", display_text)
                            last_text = display_text
                        return

            ball, top_candidates = detect_ball(frame)
            command, display_text, exit_reason = decide_chase_command(ball, frame.shape[1])
            send_motor_command(command)

            if exit_reason == 'found':
                robot_state = STATE_STANDING
                display_text = "Standing (ball found)"
                command_cooldown_until = time.time() + COMMAND_COOLDOWN_S
                set_cam_state('N')
            elif exit_reason == 'gave_up':
                robot_state = STATE_STANDING
                command_cooldown_until = time.time() + COMMAND_COOLDOWN_S
                set_cam_state('N')
                # display_text is already "No ball found, giving up" from decide_chase_command
            elif ball is None and not ball_ever_seen_this_chase:
                # Still in the initial "never seen it yet" phase — show
                # what the model is most confident it's actually seeing
                # instead, so you can tell "sees nothing" from
                # "confidently sees a cup." Once the ball has been seen
                # at least once, decide_chase_command's own "lost, spin
                # left/right" message is more useful than this.
                if top_candidates:
                    name, conf = top_candidates[0]
                    display_text = f"Searching, see:{name} {conf:.2f}"
                else:
                    display_text = "Searching, see: nothing"

            frame_count += 1

            if display_text != last_text:
                bridge.call("update_oled", display_text)
                last_text = display_text
            return

        # --- STANDING / SITTING / PRONE: gesture-driven, cooldown-gated ---
        if time.time() < command_cooldown_until:
            return

        inference_counter += 1
        if inference_counter % 3 != 0:
            return

        hands = detector.detect(frame)

        # Throttled face recognition check — updates last_familiar_time
        # (which gates whether any command actually gets dispatched
        # below) and the LED matrix expression.
        global face_check_counter, last_familiar_time
        face_check_counter += 1
        if face_check_counter % FACE_CHECK_INTERVAL == 0:
            face_status, face_name = face_gate.recognize(frame)
            if face_status == 'familiar':
                last_familiar_time = time.time()
                set_face_matrix('smiley')
            elif face_status == 'unfamiliar':
                set_face_matrix('indifferent')
            # 'none' (nobody in frame): leave the matrix showing whatever
            # it last showed rather than resetting to neutral on every
            # single momentary gap — avoids flicker if someone briefly
            # steps out of frame.

        command = "WAITING"
        display_text = "NO HAND"
        landmarks = None
        conf = 0.0
        is_posture_transition = False

        if hands:
            hand = hands[0]
            landmarks = hand["landmarks"]
            conf = hand["confidence"]

            idx_folded = is_folded(landmarks, INDEX_TIP, INDEX_MCP)
            mid_folded = is_folded(landmarks, MIDDLE_TIP, MIDDLE_MCP)
            rng_folded = is_folded(landmarks, RING_TIP, RING_MCP)
            pnk_folded = is_folded(landmarks, PINKY_TIP, PINKY_MCP)

            idx_tip_x, idx_tip_y = landmarks[INDEX_TIP][:2]
            idx_mcp_x, idx_mcp_y = landmarks[INDEX_MCP][:2]
            dx = idx_tip_x - idx_mcp_x
            dy = idx_tip_y - idx_mcp_y  # image y increases downward

            is_open_palm = not idx_folded and not mid_folded and not rng_folded and not pnk_folded
            is_pointing = not idx_folded and mid_folded and rng_folded and pnk_folded
            is_fist = idx_folded and mid_folded and rng_folded and pnk_folded

            point_left = point_right = point_up = point_down = False
            if is_pointing:
                if abs(dx) >= abs(dy):
                    point_left = dx < -20.0
                    point_right = dx > 20.0
                else:
                    point_down = dy > POINT_VERTICAL_THRESHOLD
                    point_up = dy < -POINT_VERTICAL_THRESHOLD

            if not commands_currently_allowed():
                # No recent familiar-face sighting — don't just skip
                # sending a command, skip the entire routing decision
                # below. Letting robot_state (or is_posture_transition)
                # change here without the robot actually being told to
                # move would desync Python's tracked state from the
                # robot's real physical state.
                display_text = "Ignoring (unfamiliar)"
            elif robot_state == STATE_STANDING:
                if is_open_palm:
                    command = CMD_STOP
                    display_text = "CMD: STOP (Palm)"
                elif point_up:
                    command = CMD_WALK
                    display_text = "CMD: WALK (w)"
                elif point_left:
                    command = CMD_LEFT
                    display_text = "CMD: LEFT (a)"
                elif point_right:
                    command = CMD_RIGHT
                    display_text = "CMD: RIGHT (d)"
                elif point_down:
                    command = CMD_SIT
                    display_text = "Sitting"
                    robot_state = STATE_SITTING
                    is_posture_transition = True
                elif is_fist:
                    command = CMD_STOP  # halt any current motion before switching modes
                    display_text = "Ball Mode"
                    robot_state = STATE_CHASING
                    ball_ever_seen_this_chase = False
                    last_seen_ball_side = None
                    chase_entry_time = time.time()
                    last_ball_seen_time = time.time()
                    is_posture_transition = True
                    set_cam_state('D')
                else:
                    display_text = "UNKNOWN SIGN"

            elif robot_state == STATE_SITTING:
                if point_down:
                    command = CMD_PRONE
                    display_text = "Prone"
                    robot_state = STATE_PRONE
                    is_posture_transition = True
                elif point_up:
                    command = CMD_STAND_UP
                    display_text = "Standing"
                    robot_state = STATE_STANDING
                    is_posture_transition = True
                else:
                    display_text = "Sitting (point up/down)"

            elif robot_state == STATE_PRONE:
                if point_up:
                    command = CMD_SIT
                    display_text = "Sitting"
                    robot_state = STATE_SITTING
                    is_posture_transition = True
                else:
                    display_text = "Prone (point up)"

            if command not in ("WAITING", None):
                send_motor_command(command)
                cooldown = POSTURE_TRANSITION_COOLDOWN_S if is_posture_transition else COMMAND_COOLDOWN_S
                command_cooldown_until = time.time() + cooldown

            # A hand IS visible — gesture control is working fine, so
            # make sure the camera isn't still pitched up from a previous
            # "looking for hands" moment.
            if robot_state == STATE_STANDING:
                set_cam_state('N')

        elif robot_state == STATE_STANDING:
            # No hand detected. Throttled check: is someone's legs
            # visible but their hands out of the camera's current view?
            # If so, pitch the camera up to try to bring their hands into
            # frame. Throttled the same way the chase-mode manual-stop
            # check is, since this is a second YOLO pass on top of the
            # hand model already running every processed frame.
            global cam_check_counter
            cam_check_counter += 1
            if cam_check_counter % CAM_CHECK_INTERVAL == 0:
                person_box = detect_person(frame)
                if person_box is not None and is_legs_only(person_box):
                    set_cam_state('U')
                    display_text = "Looking up for hands"
                else:
                    set_cam_state('N')

        # Update Physical OLED
        if display_text != last_text:
            bridge.call("update_oled", display_text)
            last_text = display_text

        # --- HEADLESS DEBUGGER (off the hot path) ---
        frame_count += 1
        if frame_count % 10 == 0:
            debug_frame = frame.copy()

            if landmarks is not None:
                for x, y, z in landmarks:
                    cv2.circle(debug_frame, (int(x), int(y)), 5, (0, 255, 0), -1)

            cv2.putText(debug_frame, f"STATE:{robot_state} {display_text} conf={conf:.2f}", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

            snapshot_index = (frame_count // 10) % 10
            filename = os.path.join(current_dir, f'debug_vision_{snapshot_index}.jpg')
            if not debug_queue.full():
                debug_queue.put_nowait((filename, debug_frame))

    except Exception:
        # Full traceback, not just the message — a bare str(e) doesn't
        # say *where* something failed, which is exactly what made this
        # ball-mode issue hard to pin down from the logs alone.
        print("Loop Error:")
        traceback.print_exc()


try:
    App.run(user_loop=main_loop)
except NameError:
    while True:
        main_loop()
