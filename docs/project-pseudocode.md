# RoboDog Compact Pseudocode

This guide summarizes the normal RoboDog vision/control code as compact
pseudocode. It covers the Python camera/perception loop, Arduino UNO Q Bridge
sketch, face gate, hand gestures, camera scan, and ball tracking. The separate
speech-recognition test is intentionally left out; see `docs/speech-test.md`
for that workflow.

## System Overview

```text
START DogVision

Arduino UNO Q sketch:
    initialize Serial1 to ESP32
    initialize OLED and LED matrix
    register Bridge handlers for Python

Python app:
    open USB camera
    load hand, face, and YOLO models
    create robot and camera state variables

LOOP:
    read newest camera frame
    run selected perception models
    update robot/camera state machines
    map state + perception to one-character commands
    call Arduino Bridge
    Arduino updates displays or forwards CMD:<char> to ESP32
```

## Project Files

```text
app.yaml
    declares DogVision metadata and runtime ports

sketch/sketch.yaml
    declares Arduino platform and SSD1306Ascii dependency

sketch/sketch.ino
    receives Bridge calls, updates OLED/LED matrix, forwards commands to ESP32

python/main.py
    main vision-control coordinator

python/detector.py
    hand detector wrapper returning 21 landmarks per hand

python/hand_models/mp_palmdet.py
    palm detector preprocessing, inference, and postprocessing

python/hand_models/mp_handpose.py
    hand landmark estimator

python/face_gate.py
    known-face recognizer and command permission gate

python/enroll_faces.py
    builds known_faces_db.json from known face photos

python/ball_tracker.py
    YOLO ball/person detector and ball-chasing decision logic
```

## Arduino UNO Q Sketch

```text
SETUP:
    start USB Serial for debug
    start Serial1 at 115200 for ESP32
    start I2C OLED
    start LED matrix with neutral face
    Bridge.begin()
    provide "update_oled" -> handle_gesture
    provide "send_motor_command" -> send_motor_command
    provide "update_face_matrix" -> handle_face_expression

LOOP:
    Bridge library handles polling
```

```text
handle_gesture(text):
    clear OLED
    print text

handle_face_expression(expression):
    if expression == "smiley": draw smiley frame
    else if expression == "indifferent": draw indifferent frame
    else: draw neutral frame

drawMatrixFrame(frame_8x13):
    pack 104 LED bits into four 32-bit integers
    load packed frame into UNO Q LED matrix

send_motor_command(command):
    reject unless command is exactly one whitelisted character
    Serial1 writes "CMD:" + command + newline
```

Commands used by Python:

```text
w walk forward      s stop / stand       a turn left
d turn right        q sit                c prone
h camera up step    l camera down step   n neutral / timed return
r scan up           v scan down          x stop scan and hold
```

## Python Startup

```text
cv2.setNumThreads(1)
bridge = Bridge()

cam = CameraStream(camera_path)
detector = HandGestureDetector(...)
verify_face_models()
face_gate = FaceGate()
ball_tracker = BallTracker(yolov8n.onnx, movement commands)

initialize:
    robot_state = STANDING
    camera_scan_state = IDLE
    cooldown timers
    face recognition timers
    camera scan timers and offsets

App.run(user_loop=main_loop)
```

## CameraStream

```text
CameraStream:
    open V4L2 camera at 640x480
    set camera buffer size to 1
    if camera fails, show "CAM FAILED" on OLED
    run background thread:
        continuously read camera frames
        store only the latest frame under a lock

read():
    return a copy of latest frame, or None

stop():
    stop thread and release camera
```

## Main Loop

```text
main_loop():
    frame = cam.read()
    if no frame: return
    mirror frame horizontally

    if robot_state == CHASING:
        run ball-chasing branch
        return

    if command cooldown is active: return
    process only every third pass

    hands = detector.detect(frame)
    face_status = recognize face when face timer expires
    update LED matrix for familiar/unfamiliar face

    if standing, no hand, scan idle, and camera-check interval passes:
        person_box = ball_tracker.detect_person(frame)
        legs_detected = person_box exists and looks legs-only

    scan_message = update_camera_scan(legs_detected, hands exist, face_status)

    if hands exist:
        classify first hand as open palm, fist, or pointing direction
        if familiar-face gate rejects commands:
            display "Ignoring (unfamiliar)"
        else:
            update robot_state and command from gesture table
        if command exists:
            send command through Bridge
            start cooldown

    if robot is not standing:
        return camera from active scan
    else if no command and scan_message exists:
        display scan_message

    update OLED only if display text changed

on error:
    print traceback
    return camera from scan
    send stop command
```

## Gesture State Machine

```text
STANDING:
    open palm   -> s, stay STANDING
    point up    -> w, stay STANDING
    point left  -> a, stay STANDING
    point right -> d, stay STANDING
    point down  -> q, state SITTING
    fist        -> s, start BallTracker, state CHASING, camera down

SITTING:
    point up    -> s, state STANDING
    point down  -> c, state PRONE
    otherwise   -> show "Sitting (point up/down)"

PRONE:
    point up    -> q, state SITTING
    otherwise   -> show "Prone (point up)"

CHASING:
    point down during manual-stop check -> s, state STANDING
    otherwise BallTracker chooses movement command
```

```text
is_folded(landmarks, tip, mcp):
    return distance(tip, wrist) < distance(mcp, wrist)

is_pointing_down(landmarks):
    require index extended
    require middle/ring/pinky folded
    require index direction mostly vertical and downward
```

## Face Gate

```text
Enrollment:
    for each person folder in python/known_faces:
        read each image
        detect largest face with YuNet
        align face and extract SFace embedding
        average embeddings for that person
    write name -> averaged embedding to known_faces_db.json
```

```text
Runtime recognition:
    load known_faces_db.json
    periodically detect faces with YuNet
    if no face: return "none"
    select largest face
    extract SFace embedding
    compare with known embeddings
    if best score >= threshold: return "familiar", name
    else: return "unfamiliar"

commands_currently_allowed():
    if REQUIRE_FAMILIAR_FACE is false: return true
    return true only within FAMILIAR_GRACE_S after last familiar face
```

## Camera Scan State Machine

```text
IDLE:
    if legs-only person is detected:
        reset timed offset
        scan upward with r
        state = FACE_SCANNING if familiar face required else HAND_SCANNING

FACE_SCANNING:
    if familiar face found:
        stop scan with x
        record upward travel time
        if hand already visible: state = LOCKED
        else scan downward with v for at most the upward travel time
    if timeout:
        return camera with n
        state = RETURNING

HAND_SCANNING:
    if hand detected enough consecutive times:
        stop scan with x
        update signed camera offset
        state = LOCKED
    if time limit expires:
        return camera with n
        state = RETURNING

LOCKED:
    keep camera position while hand remains visible
    if target lost long enough:
        return camera with n
        state = RETURNING

RETURNING:
    wait abs(signed offset) + settle time
    reset offset
    state = IDLE
```

## Hand Detection Pipeline

```text
detector.detect(frame):
    palms = MPPalmDet.infer(frame)
    for each palm:
        result = MPHandPose.infer(frame, palm)
        if result passes confidence threshold:
            return 21 landmarks, handedness, confidence
```

```text
MPPalmDet:
    resize/pad frame to 192x192
    normalize BGR -> RGB image
    run palm ONNX model
    decode anchor boxes and palm landmarks
    apply non-maximum suppression

MPHandPose:
    crop around palm
    rotate crop so hand is upright
    resize to 224x224
    run hand-pose ONNX model
    transform landmarks back to original frame coordinates
```

## BallTracker

```text
Prediction:
    letterbox frame to YOLO input size
    run yolov8n.onnx with OpenCV DNN
    normalize output shape
    convert model boxes back to frame coordinates
```

```text
detect_ball(frame):
    keep sports-ball candidates above threshold
    keep diagnostic top classes for OLED/debug text
    apply NMS
    return ball center/radius, or None

detect_person(frame):
    keep person boxes above threshold
    apply NMS
    return best person box, or None

is_legs_only(person_box):
    true if person box touches top margin and has enough height
```

```text
command_for_frame(frame):
    ball = detect_ball(frame)
    if ball visible:
        remember side and last-seen time
        if ball radius is large: return stop, "found"
        if ball left of center deadzone: return left
        if ball right of center deadzone: return right
        return walk

    if ball was never seen and timeout expires: return stop, "gave_up"
    if ball was seen before but lost too long: return stop, "gave_up"
    spin toward last side where ball was seen
```

## End-To-End Vision Flow

```text
camera frame
    -> hand landmarks
    -> face status
    -> optional person/legs detection
    -> optional ball detection
    -> robot state machine
    -> camera scan state machine
    -> command character or display text
    -> Python Bridge
    -> Arduino Bridge handler
    -> OLED / LED matrix / Serial1
    -> ESP32 receives CMD:<char>
```

## Runtime Assets

```text
python/yolov8n.onnx
    YOLOv8n object detector for ball/person detection

python/hand_models/*.onnx
    palm and hand-pose models

python/face_models/*.onnx
    YuNet and SFace face models

python/known_faces/<person-name>/*
    source photos for enrollment

python/known_faces_db.json
    known-face embedding database

python/requirements.txt
    Python dependencies for the UNO Q runtime
```
