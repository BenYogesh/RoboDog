# Pseudocode rút gọn cho hệ thống RoboDog

Tài liệu này tóm tắt code vision/control chính của RoboDog dưới dạng
pseudocode ngắn gọn. Nội dung bao gồm vòng lặp camera/perception bên Python,
sketch Bridge trên Arduino UNO Q, nhận diện khuôn mặt, cử chỉ tay, camera scan,
và đuổi bóng. Nhánh thử nghiệm nhận diện giọng nói được tách riêng và không
được mô tả ở đây; xem `docs/speech-test.md` nếu cần workflow đó.

## Tổng quan hệ thống

```text
BẮT ĐẦU DogVision

Sketch Arduino UNO Q:
    khởi động Serial1 để giao tiếp ESP32
    khởi động OLED và ma trận LED
    đăng ký các hàm Bridge cho Python gọi

Ứng dụng Python:
    mở camera USB
    tải model bàn tay, khuôn mặt, YOLO
    tạo biến trạng thái robot và camera

LẶP:
    đọc frame camera mới nhất
    chạy các model cần thiết
    cập nhật state machine robot/camera
    đổi trạng thái + kết quả nhận diện thành lệnh một ký tự
    gọi Arduino Bridge
    Arduino cập nhật hiển thị hoặc gửi CMD:<ký tự> sang ESP32
```

## Các file chính

```text
app.yaml
    khai báo metadata DogVision và port runtime

sketch/sketch.yaml
    khai báo platform Arduino và thư viện SSD1306Ascii

sketch/sketch.ino
    nhận Bridge call, cập nhật OLED/LED matrix, chuyển lệnh sang ESP32

python/main.py
    bộ điều phối chính của ứng dụng vision-control

python/detector.py
    wrapper nhận diện bàn tay, trả về 21 landmarks mỗi bàn tay

python/hand_models/mp_palmdet.py
    tiền xử lý, inference, hậu xử lý cho palm detector

python/hand_models/mp_handpose.py
    ước lượng landmark bàn tay từ palm detection

python/face_gate.py
    nhận diện khuôn mặt quen và kiểm soát quyền điều khiển

python/enroll_faces.py
    tạo known_faces_db.json từ ảnh khuôn mặt quen

python/ball_tracker.py
    nhận diện bóng/người bằng YOLO và quyết định lệnh đuổi bóng
```

## Sketch Arduino UNO Q

```text
SETUP:
    mở USB Serial để debug
    mở Serial1 tốc độ 115200 cho ESP32
    khởi động OLED I2C
    khởi động ma trận LED với mặt neutral
    Bridge.begin()
    đăng ký "update_oled" -> handle_gesture
    đăng ký "send_motor_command" -> send_motor_command
    đăng ký "update_face_matrix" -> handle_face_expression

LOOP:
    thư viện Bridge tự polling
```

```text
handle_gesture(text):
    xóa OLED
    in text

handle_face_expression(expression):
    nếu expression == "smiley": vẽ mặt cười
    nếu expression == "indifferent": vẽ mặt thờ ơ
    ngược lại: vẽ mặt neutral

drawMatrixFrame(frame_8x13):
    đóng gói 104 bit LED vào bốn số 32-bit
    nạp frame đã đóng gói vào ma trận LED

send_motor_command(command):
    bỏ qua nếu command không đúng 1 ký tự hoặc không nằm trong whitelist
    Serial1 gửi "CMD:" + command + xuống dòng
```

Các lệnh Python đang dùng:

```text
w đi thẳng          s dừng / đứng        a rẽ trái
d rẽ phải           q ngồi               c nằm
h camera nâng       l camera hạ          n neutral / quay về
r scan lên          v scan xuống         x dừng scan và giữ vị trí
```

## Khởi động Python

```text
cv2.setNumThreads(1)
bridge = Bridge()

cam = CameraStream(camera_path)
detector = HandGestureDetector(...)
verify_face_models()
face_gate = FaceGate()
ball_tracker = BallTracker(yolov8n.onnx, các lệnh di chuyển)

khởi tạo:
    robot_state = STANDING
    camera_scan_state = IDLE
    timer cooldown
    timer nhận diện khuôn mặt
    timer và offset camera scan

App.run(user_loop=main_loop)
```

## CameraStream

```text
CameraStream:
    mở camera V4L2 ở 640x480
    đặt buffer size = 1
    nếu camera lỗi, hiển thị "CAM FAILED" trên OLED
    chạy thread nền:
        liên tục đọc frame camera
        chỉ lưu frame mới nhất trong lock

read():
    trả về bản copy frame mới nhất, hoặc None

stop():
    dừng thread và giải phóng camera
```

## Main Loop

```text
main_loop():
    frame = cam.read()
    nếu không có frame: return
    lật ngang frame

    nếu robot_state == CHASING:
        chạy nhánh đuổi bóng
        return

    nếu đang cooldown: return
    chỉ xử lý mỗi 3 vòng

    hands = detector.detect(frame)
    face_status = nhận diện khuôn mặt khi timer đến hạn
    cập nhật LED matrix cho mặt quen/lạ

    nếu robot đứng, không thấy tay, camera idle, đến lượt kiểm tra:
        person_box = ball_tracker.detect_person(frame)
        legs_detected = person_box tồn tại và giống chỉ thấy chân

    scan_message = update_camera_scan(legs_detected, có tay, face_status)

    nếu có tay:
        phân loại tay đầu tiên: bàn tay mở, nắm tay, hoặc hướng chỉ
        nếu FaceGate không cho phép:
            hiển thị "Ignoring (unfamiliar)"
        ngược lại:
            cập nhật robot_state và command theo bảng cử chỉ
        nếu có command:
            gửi command qua Bridge
            bắt đầu cooldown

    nếu robot không đứng:
        yêu cầu camera quay về nếu đang scan
    nếu không có command và có scan_message:
        hiển thị scan_message

    cập nhật OLED nếu text thay đổi

khi lỗi:
    in traceback
    đưa camera về
    gửi lệnh stop
```

## State Machine Cử Chỉ

```text
STANDING:
    bàn tay mở -> s, giữ STANDING
    chỉ lên    -> w, giữ STANDING
    chỉ trái   -> a, giữ STANDING
    chỉ phải   -> d, giữ STANDING
    chỉ xuống  -> q, state SITTING
    nắm tay    -> s, start BallTracker, state CHASING, camera xuống

SITTING:
    chỉ lên    -> s, state STANDING
    chỉ xuống  -> c, state PRONE
    khác       -> hiển thị "Sitting (point up/down)"

PRONE:
    chỉ lên    -> q, state SITTING
    khác       -> hiển thị "Prone (point up)"

CHASING:
    chỉ xuống khi kiểm tra dừng thủ công -> s, state STANDING
    còn lại BallTracker tự chọn lệnh di chuyển
```

```text
is_folded(landmarks, tip, mcp):
    return khoảng_cách(tip, wrist) < khoảng_cách(mcp, wrist)

is_pointing_down(landmarks):
    yêu cầu ngón trỏ duỗi
    yêu cầu ngón giữa/áp út/út gập
    yêu cầu hướng ngón trỏ chủ yếu dọc và đi xuống
```

## Face Gate

```text
Enroll:
    với mỗi thư mục người trong python/known_faces:
        đọc từng ảnh
        tìm khuôn mặt lớn nhất bằng YuNet
        align mặt và trích xuất embedding SFace
        lấy trung bình embedding cho người đó
    ghi name -> averaged embedding vào known_faces_db.json
```

```text
Runtime:
    load known_faces_db.json
    định kỳ tìm mặt bằng YuNet
    nếu không thấy mặt: trả về "none"
    chọn mặt lớn nhất
    trích xuất embedding SFace
    so với embedding đã biết
    nếu best score >= threshold: trả về "familiar", name
    ngược lại: trả về "unfamiliar"

commands_currently_allowed():
    nếu REQUIRE_FAMILIAR_FACE là false: return true
    chỉ return true trong FAMILIAR_GRACE_S sau lần thấy mặt quen gần nhất
```

## State Machine Camera Scan

```text
IDLE:
    nếu phát hiện chỉ thấy chân người:
        reset offset thời gian
        scan lên bằng r
        state = FACE_SCANNING nếu cần mặt quen, ngược lại HAND_SCANNING

FACE_SCANNING:
    nếu thấy mặt quen:
        dừng scan bằng x
        lưu thời gian scan lên
        nếu đã thấy tay: state = LOCKED
        nếu chưa thấy tay: scan xuống bằng v, tối đa bằng thời gian đã scan lên
    nếu timeout:
        quay về bằng n
        state = RETURNING

HAND_SCANNING:
    nếu thấy tay đủ số lần liên tiếp:
        dừng scan bằng x
        cập nhật offset camera
        state = LOCKED
    nếu hết giới hạn thời gian:
        quay về bằng n
        state = RETURNING

LOCKED:
    giữ vị trí camera khi còn thấy tay
    nếu mất mục tiêu đủ lâu:
        quay về bằng n
        state = RETURNING

RETURNING:
    chờ abs(offset) + thời gian ổn định
    reset offset
    state = IDLE
```

## Pipeline Nhận Diện Tay

```text
detector.detect(frame):
    palms = MPPalmDet.infer(frame)
    với mỗi palm:
        result = MPHandPose.infer(frame, palm)
        nếu result đủ confidence:
            trả về 21 landmarks, handedness, confidence
```

```text
MPPalmDet:
    resize/pad frame về 192x192
    chuẩn hóa BGR -> RGB
    chạy model palm ONNX
    giải mã anchor box và palm landmarks
    chạy NMS

MPHandPose:
    crop quanh palm
    xoay crop để bàn tay thẳng
    resize về 224x224
    chạy model hand-pose ONNX
    đổi landmarks về tọa độ frame gốc
```

## BallTracker

```text
Prediction:
    letterbox frame về input YOLO
    chạy yolov8n.onnx bằng OpenCV DNN
    chuẩn hóa shape output
    đổi bbox model về tọa độ frame gốc
```

```text
detect_ball(frame):
    giữ candidate lớp sports ball đủ ngưỡng
    giữ top class diagnostic cho OLED/debug
    chạy NMS
    trả về center/radius của bóng, hoặc None

detect_person(frame):
    giữ bbox person đủ ngưỡng
    chạy NMS
    trả về person box tốt nhất hoặc None

is_legs_only(person_box):
    true nếu box chạm gần mép trên và đủ cao
```

```text
command_for_frame(frame):
    ball = detect_ball(frame)
    nếu thấy bóng:
        nhớ phía cuối cùng thấy bóng và thời gian
        nếu radius lớn: return stop, "found"
        nếu bóng lệch trái: return left
        nếu bóng lệch phải: return right
        return walk

    nếu chưa từng thấy bóng và quá timeout: return stop, "gave_up"
    nếu đã thấy bóng nhưng mất quá lâu: return stop, "gave_up"
    quay theo phía cuối cùng đã thấy bóng
```

## Luồng Vision End-To-End

```text
camera frame
    -> hand landmarks
    -> face status
    -> person/legs detection nếu cần
    -> ball detection nếu đang CHASING
    -> state machine robot
    -> state machine camera scan
    -> command hoặc text hiển thị
    -> Python Bridge
    -> Arduino Bridge handler
    -> OLED / LED matrix / Serial1
    -> ESP32 nhận CMD:<ký tự>
```

## Runtime Assets

```text
python/yolov8n.onnx
    YOLOv8n cho nhận diện bóng/người

python/hand_models/*.onnx
    model palm và hand-pose

python/face_models/*.onnx
    model YuNet và SFace

python/known_faces/<person-name>/*
    ảnh nguồn để enroll khuôn mặt

python/known_faces_db.json
    database embedding khuôn mặt quen

python/requirements.txt
    package Python cần cho runtime UNO Q
```
