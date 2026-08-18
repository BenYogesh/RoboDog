# Tóm tắt code cho hệ thống RoboDog

## Tổng quan hệ thống

```text
BẮT ĐẦU DogVision

Sketch Arduino UNO Q:
    khởi động Serial1 để giao tiếp ESP32
    khởi động OLED và ma trận LED
    đăng ký các hàm Bridge cho Python gọi

Ứng dụng Python Arduino Uno Q:
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
sketch/sketch.ino
    nhận Bridge call từ Python, cập nhật OLED/LED matrix, chuyển lệnh sang ESP32

python/main.py
    bộ điều phối chính của ứng dụng

python/detector.py
    nhận diện bàn tay, trả về cử chỉ được phát hiện

python/face_gate.py
    nhận diện khuôn mặt người quen để xác định quyền điều khiển

python/enroll_faces.py
    trích xuất dữ liệu khuôn mặt người quen known_faces_db.json

python/ball_tracker.py
    nhận diện bóng/người và quyết định lệnh đuổi theo mục tiêu
```

## Sketch Arduino UNO Q

```text
SETUP:
    mở USB Serial để debug
    mở Serial1 tốc độ 115200 cho ESP32
    khởi động OLED I2C
    khởi động ma trận LED
    khởi động Bridge kết nối giữa hàm Arduino -> hàm Python
    kết nối update_oled -> handle_gesture
    kết nối send_motor_command -> send_motor_command
    kết nối update_face_matrix -> handle_face_expression

LOOP:
    cập nhật Bridge 

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

    Các lệnh Python đang dùng:
w đi thẳng          s dừng / đứng        a rẽ trái
d rẽ phải           q ngồi               c nằm
h camera nâng       l camera hạ          n neutral / quay về
r scan lên          v scan xuống         x dừng scan và giữ vị trí
```

## Chương trình Python

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
    bộ đếm thời gian cooldown giữa 2 lần nhận lệnh
    bộ đếm thời gian nhận diện khuôn mặt
    bộ đếm thời gian và offset camera scan

Lớp CameraStream:
    mở trình điểu khiển camera V4L2 ở độ phân giải 640x480
    đặt buffer size = 1
    nếu camera lỗi, hiển thị "CAM FAILED" trên OLED
    chạy thread nền:
        liên tục đọc frame camera
        chỉ lưu frame mới nhất trong lock

    read():
        trả về bản copy frame mới nhất, hoặc None

    stop():
        dừng luồng stream và giải phóng camera

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

## Chuyển đổi trạng thái

```text
STANDING:
    bàn tay mở -> s, giữ STANDING
    chỉ lên    -> w, giữ STANDING
    chỉ trái   -> a, giữ STANDING
    chỉ phải   -> d, giữ STANDING
    chỉ xuống  -> q, state SITTING
    nắm tay    -> s, chạy BallTracker, state CHASING, camera hạ xuống

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

## Nhận diện khuôn mặt

```text
Enroll:
    với mỗi thư mục người trong python/known_faces:
        đọc từng ảnh
        tìm khuôn mặt lớn nhất bằng YuNet
        chỉnh mặt về vị trí cân đối và trích xuất dữ liệu bằng SFace
        lấy trung bình dữ liệu lấy từ tất cả các ảnh của người đó
    ghi tên và trung bình dữ liệu vào known_faces_db.json
```

```text
Trong khi chạy:
    tải known_faces_db.json
    định kỳ tìm mặt bằng YuNet
    nếu không thấy mặt: trả về "none"
    chọn mặt lớn nhất
    trích xuất embedding SFace
    so với embedding đã biết
    nếu best score >= threshold: trả về "familiar", name
    ngược lại: trả về "unfamiliar"

commands_currently_allowed():
    (khóa yêu cầu thấy mặt người quen để nghe lệnh hay không)
    nếu REQUIRE_FAMILIAR_FACE là false: return true
    chỉ return true trong FAMILIAR_GRACE_S sau lần thấy mặt quen gần nhất
```

## Chuyển đổi trạng thái khi Camera tìm khuôn mặt

```text
IDLE:
    nếu phát hiện chỉ thấy chân người:
        reset bộ đếm thời gian
        ngẩng cam lên
        state = FACE_SCANNING nếu cần mặt quen, ngược lại HAND_SCANNING

FACE_SCANNING:
    nếu thấy mặt quen:
        dừng ngẩng cam
        lưu thời gian ngẩng lên
        nếu đã thấy tay: state = LOCKED
        nếu chưa thấy tay: hạ dần camera xuống, tối đa bằng thời gian đã ngẩng lên
    nếu timeout:
        quay về vị trí ban đầu
        state = RETURNING

HAND_SCANNING:
    nếu thấy tay:
        dừng di chuyển
        cập nhật thời gian di chuyển, đã đi lên rồi xuống bao nhiêu
        state = LOCKED
    nếu hết giới hạn thời gian:
        quay về vị trí ban đầu
        state = RETURNING

LOCKED:
    giữ vị trí camera khi còn thấy tay
    nếu mất mục tiêu đủ lâu:
        quay về vị trí ban đầu
        state = RETURNING

RETURNING:
    chờ một quãng bằng thời gian di chuyển đã lưu, thêm một khoảng để ổn định
    reset bộ đếm thời gian
    state = IDLE
```

## Nhận diện cử chỉ tay

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

## Theo đuổi mục tiêu

```text
tiền xử lý:
    đóng khung lại frame theo yêu cầu input của YOLO
    chạy yolov8n.onnx bằng OpenCV DNN
    chuẩn hóa shape output
    đổi bbox model về tọa độ frame gốc

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

command_for_frame(frame):
    ball = detect_ball(frame)
    nếu thấy bóng:
        nhớ phía cuối cùng thấy bóng và thời gian
        nếu radius lớn: return stop, "found"
        nếu bóng lệch trái: return left
        nếu bóng lệch phải: return right
        return walk

    nếu chưa từng thấy bóng và quá thời gian: return stop, "gave_up"
    nếu đã thấy bóng nhưng mất quá lâu: return stop, "gave_up"
    quay theo phía cuối cùng đã thấy bóng
```

## Luồng dữ liệu hình ảnh

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

## Các model nhận diện sử dụng

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
