# Pseudocode toàn bộ hệ thống RoboDog

Tài liệu này diễn giải các phần code chính của dự án RoboDog dưới dạng
pseudocode. Mục tiêu là giúp người đọc hiểu luồng điều khiển, trạng thái, dữ
liệu vào/ra, và cách các module phối hợp với nhau mà không cần đọc từng dòng
code thật.

Theo yêu cầu, tài liệu này **không mô tả nhánh nhận diện giọng nói**. Các file
và hàm phục vụ speech test như `python/speech_test.py`, `play_test_sound`, và
`esp32_speech_test/` được bỏ ra khỏi pseudocode bên dưới.

## Bức tranh tổng quát

```text
BẮT ĐẦU ứng dụng DogVision

Khởi động sketch Arduino UNO Q
    mở Serial1 để giao tiếp với ESP32
    khởi động OLED
    khởi động ma trận LED
    đăng ký các hàm Bridge cho Python gọi

Khởi động ứng dụng Python
    mở camera USB
    tải model nhận diện bàn tay
    tải model nhận diện khuôn mặt
    tải model YOLO nhận diện bóng/người
    tạo các biến trạng thái robot và camera

LẶP LIÊN TỤC
    lấy frame mới nhất từ camera
    chạy nhận diện bàn tay, khuôn mặt, người hoặc bóng
    cập nhật trạng thái robot
    đổi kết quả nhận diện thành lệnh một ký tự
    gọi Bridge để gửi lệnh sang Arduino
    Arduino cập nhật OLED / LED matrix
    Arduino chuyển lệnh dạng CMD:<ký tự> sang ESP32 qua Serial1
```

## app.yaml

`app.yaml` là metadata cho ứng dụng App Lab.

```text
KHAI BÁO tên ứng dụng là DogVision
KHAI BÁO mô tả là đường dẫn camera USB đang dùng
KHAI BÁO danh sách cổng runtime theo manifest
KHAI BÁO không dùng brick ngoài
KHAI BÁO icon ứng dụng
```

## sketch/sketch.yaml

`sketch/sketch.yaml` mô tả profile build cho phần Arduino.

```text
KHAI BÁO profile mặc định
SỬ DỤNG platform arduino:zephyr
CÀI thư viện SSD1306Ascii phiên bản 1.3.5
ĐẶT profile mặc định là default
```

## sketch/sketch.ino

`sketch/sketch.ino` chạy trên phía Arduino UNO Q. Vai trò chính của file này là
nhận lời gọi Bridge từ Python, hiển thị thông tin lên OLED/LED matrix, và gửi
lệnh hợp lệ sang ESP32.

### Khởi tạo toàn cục

```text
IMPORT thư viện Arduino_RouterBridge
IMPORT thư viện Wire cho I2C
IMPORT thư viện OLED SSD1306Ascii
IMPORT thư viện Arduino_LED_Matrix

ĐỊNH NGHĨA địa chỉ I2C của OLED là 0x3C
TẠO đối tượng oled
TẠO đối tượng matrix

ĐỊNH NGHĨA frame FACE_SMILEY kích thước 8x13
ĐỊNH NGHĨA frame FACE_INDIFFERENT kích thước 8x13
ĐỊNH NGHĨA frame FACE_NEUTRAL kích thước 8x13
```

### drawMatrixFrame(frame)

```text
HÀM drawMatrixFrame(frame):
    TẠO mảng packedFrame gồm 4 số nguyên 32-bit, ban đầu bằng 0
    bitCount = 0

    VỚI từng hàng r từ 0 đến 7:
        VỚI từng cột c từ 0 đến 12:
            NẾU frame[r][c] bật:
                wordIdx = bitCount / 32
                bitIdx = 31 - (bitCount % 32)
                bật bitIdx trong packedFrame[wordIdx]
            tăng bitCount

    nạp packedFrame vào ma trận LED
```

Ý nghĩa: ma trận LED dùng format 104 bit được đóng gói vào 4 số 32-bit. Hàm
này chuyển frame 2 chiều dễ đọc thành format phần cứng cần.

### handle_gesture(command)

```text
HÀM handle_gesture(command):
    xóa OLED
    in command lên OLED
```

Hàm này được Python gọi qua Bridge với tên `update_oled`.

### handle_face_expression(expression)

```text
HÀM handle_face_expression(expression):
    NẾU expression là "smiley":
        vẽ FACE_SMILEY
    NGƯỢC LẠI NẾU expression là "indifferent":
        vẽ FACE_INDIFFERENT
    NGƯỢC LẠI:
        vẽ FACE_NEUTRAL
```

Hàm này dùng để phản hồi kết quả nhận diện khuôn mặt:

- khuôn mặt quen: mặt cười
- khuôn mặt lạ: mặt thờ ơ
- trạng thái khác: tắt/neutral

### is_supported_esp_command(command)

```text
HÀM is_supported_esp_command(command):
    NẾU command nằm trong danh sách lệnh được hỗ trợ:
        trả về true
    NGƯỢC LẠI:
        trả về false
```

Các lệnh hợp lệ gồm:

```text
w b s a d p c g u q j z e f k h l n r v x
```

Một số lệnh được Python tạo trực tiếp, ví dụ `w`, `s`, `a`, `d`, `q`, `c`,
`h`, `l`, `n`, `r`, `v`, `x`. Một số lệnh khác được sketch cho phép để ESP32
có thể dùng cho các chế độ gait/phần cứng khác.

### send_motor_command(command)

```text
HÀM send_motor_command(command):
    NẾU command không có đúng 1 ký tự:
        bỏ qua

    NẾU ký tự command không nằm trong danh sách hỗ trợ:
        bỏ qua

    gửi chuỗi "CMD:" qua Serial1
    gửi ký tự command qua Serial1
    gửi ký tự xuống dòng qua Serial1
```

Ví dụ nếu Python gửi lệnh đi thẳng `w`, Arduino sẽ chuyển sang ESP32 thành:

```text
CMD:w
```

Format `CMD:<ký tự>` giúp ESP32 phân biệt lệnh thật với dữ liệu nhiễu trên
UART.

### setup()

```text
HÀM setup():
    mở Serial USB tốc độ 115200 để debug
    mở Serial1 tốc độ 115200 để gửi lệnh sang ESP32

    khởi động I2C
    khởi động OLED
    chọn font OLED
    xóa OLED

    khởi động ma trận LED
    vẽ mặt neutral

    khởi động Bridge
    đăng ký "update_oled" -> handle_gesture
    đăng ký "send_motor_command" -> send_motor_command
    đăng ký "update_face_matrix" -> handle_face_expression
```

### loop()

```text
HÀM loop():
    không làm gì thủ công
    Bridge tự xử lý polling nền
```

## python/main.py

`python/main.py` là trung tâm điều phối chính của ứng dụng vision. File này lấy
ảnh từ camera, gọi các model nhận diện, quản lý trạng thái robot, và gửi lệnh
sang Arduino qua Bridge.

### Khởi tạo module

```text
IMPORT các thư viện hệ thống
IMPORT OpenCV và NumPy
IMPORT App và Bridge từ arduino.app_utils
IMPORT BallTracker
IMPORT HandGestureDetector
IMPORT FaceGate và verify_face_models

giới hạn OpenCV dùng 1 luồng
tạo Bridge
xác định thư mục hiện tại
khai báo đường dẫn camera USB

tạo CameraStream
tạo HandGestureDetector
kiểm tra model khuôn mặt
tạo FaceGate
tạo BallTracker với model yolov8n.onnx

khởi tạo trạng thái camera
khởi tạo trạng thái nhận diện khuôn mặt
khởi tạo trạng thái robot
khởi tạo cooldown lệnh
```

### Bảng lệnh chính

```text
CMD_WALK        = "w"  # đi thẳng
CMD_STOP        = "s"  # dừng / đứng
CMD_LEFT        = "a"  # rẽ trái
CMD_RIGHT       = "d"  # rẽ phải
CMD_SIT         = "q"  # ngồi
CMD_PRONE       = "c"  # nằm
CMD_STAND_UP    = "s"  # đứng lên

CAM_CMD_UP        = "h"  # camera nâng lên theo bước cố định
CAM_CMD_DOWN      = "l"  # camera hạ xuống theo bước cố định
CAM_CMD_NEUTRAL   = "n"  # camera về trung tính / quay về theo offset
CAM_CMD_SCAN_UP   = "r"  # bắt đầu scan lên
CAM_CMD_SCAN_DOWN = "v"  # bắt đầu scan xuống
CAM_CMD_SCAN_STOP = "x"  # dừng scan và giữ vị trí
```

### send_motor_command(command)

```text
HÀM send_motor_command(command):
    THỬ:
        gọi Bridge "send_motor_command" với command
    NẾU Bridge lỗi:
        in lỗi ra console
```

Python không gửi UART trực tiếp. Nó chỉ gọi Bridge. Arduino mới là nơi kiểm tra
lệnh và chuyển tiếp sang ESP32.

### Class CameraStream

```text
CLASS CameraStream:
    HÀM __init__(path, width=640, height=480):
        mở camera bằng OpenCV VideoCapture với backend V4L2
        đặt chiều rộng frame
        đặt chiều cao frame
        đặt buffer size = 1

        NẾU camera không mở được:
            in lỗi camera
            gọi Bridge update_oled với "CAM FAILED"

        tạo lock
        frame mới nhất = None
        running = True
        tạo thread nền chạy _update
        start thread

    HÀM _update():
        fail_count = 0
        TRONG KHI running:
            NẾU camera chưa mở:
                ngủ 1 giây
                tiếp tục

            đọc frame từ camera
            NẾU đọc thành công:
                fail_count = 0
                khóa lock
                lưu frame thành frame mới nhất
                mở lock
            NGƯỢC LẠI:
                tăng fail_count
                thỉnh thoảng in cảnh báo
                ngủ 0.1 giây

    HÀM read():
        khóa lock
        NẾU chưa có frame:
            trả về None
        NGƯỢC LẠI:
            trả về bản copy của frame mới nhất

    HÀM stop():
        running = False
        chờ thread kết thúc tối đa 1 giây
        giải phóng camera
```

Ý nghĩa: camera được đọc ở thread riêng để vòng lặp điều khiển luôn lấy frame
mới nhất, không bị kẹt trong hàng đợi frame cũ.

## Trạng thái camera trong python/main.py

Camera dùng servo quay liên tục nên không có vị trí tuyệt đối. Vì vậy code dùng
thời gian quay để ước lượng camera đã đi lên/xuống bao lâu và cần quay ngược
bao lâu để trở về.

### set_cam_state(new_state)

```text
HÀM set_cam_state(new_state):
    NẾU camera đang trong scan tự động:
        bỏ qua

    NẾU new_state giống trạng thái đang lưu:
        bỏ qua

    NẾU new_state là "U":
        command = "h"
    NẾU new_state là "D":
        command = "l"
    NẾU new_state là "N":
        command = "n"

    gửi command sang Arduino
    lưu trạng thái camera mới
```

### start_camera_motion(now, command, direction, state, duration_limit_s)

```text
HÀM start_camera_motion(now, command, direction, state, duration_limit_s):
    gửi command sang Arduino
    camera_scan_state = state
    camera_scan_started_at = now
    camera_scan_direction = direction
    camera_hand_scan_limit_s = duration_limit_s
    camera_hand_confirmations = 0

    NẾU state là FACE_SCANNING:
        cho phép nhận diện khuôn mặt ngay lập tức

    python_cam_state = "N"
```

`direction` là `+1` khi quay lên và `-1` khi quay xuống.

### start_camera_scan(now)

```text
HÀM start_camera_scan(now):
    NẾU camera không ở trạng thái IDLE:
        bỏ qua

    camera_net_offset_s = 0

    NẾU REQUIRE_FAMILIAR_FACE là true:
        initial_state = FACE_SCANNING
    NGƯỢC LẠI:
        initial_state = HAND_SCANNING

    bắt đầu quay camera lên bằng lệnh "r"
```

Khi robot chỉ thấy chân người, camera sẽ scan lên để tìm khuôn mặt quen hoặc
bàn tay.

### stop_camera_scan(now)

```text
HÀM stop_camera_scan(now):
    NẾU trạng thái hiện tại không phải FACE_SCANNING hoặc HAND_SCANNING:
        trả về 0

    gửi lệnh dừng scan "x"
    travel_time = now - thời điểm bắt đầu scan
    camera_net_offset_s += direction * travel_time
    camera_scan_direction = 0
    camera_scan_state = LOCKED
    camera_target_last_seen_at = now
    trả về travel_time
```

### start_hand_scan_below_face(now)

```text
HÀM start_hand_scan_below_face(now):
    remaining_down_time = max(camera_net_offset_s, 0)

    NẾU remaining_down_time <= 0:
        return_camera_from_scan(now)
        trả về false

    bắt đầu scan xuống bằng lệnh "v"
    giới hạn thời gian scan xuống bằng remaining_down_time
    trả về true
```

Ý tưởng: nếu đã tìm thấy mặt quen nhưng chưa thấy tay, camera scan xuống để tìm
tay nhưng không cố tình đi thấp hơn vị trí neutral ban đầu.

### return_camera_from_scan(now)

```text
HÀM return_camera_from_scan(now):
    NẾU camera đang IDLE hoặc RETURNING:
        bỏ qua

    NẾU camera đang còn quay:
        cộng thời gian quay hiện tại vào camera_net_offset_s
        camera_scan_direction = 0

    gửi lệnh "n" để ESP32 đưa camera về
    camera_return_complete_at = now + abs(camera_net_offset_s) + thời gian chờ ổn định
    camera_scan_state = RETURNING
    camera_hand_confirmations = 0
    python_cam_state = "N"
```

### update_camera_scan(legs_detected, hand_detected, face_status, now)

```text
HÀM update_camera_scan(legs_detected, hand_detected, face_status, now):
    NẾU state là IDLE:
        NẾU legs_detected:
            start_camera_scan(now)
            trả về thông báo đang scan
        trả về None

    NẾU state là FACE_SCANNING:
        NẾU face_status là "familiar":
            stop_camera_scan(now)
            NẾU đã thấy tay:
                trả về "đã thấy mặt quen và tay"
            NẾU start_hand_scan_below_face(now) thành công:
                trả về "thấy mặt quen, scan xuống tìm tay"
            trả về "không còn khoảng scan xuống, quay về"

        NẾU scan mặt quá thời gian:
            return_camera_from_scan(now)
            trả về thông báo timeout

        trả về "đang scan tìm mặt quen"

    NẾU state là HAND_SCANNING:
        NẾU hand_detected:
            tăng số lần xác nhận tay
            NẾU đủ số lần xác nhận:
                stop_camera_scan(now)
                trả về "đã thấy tay"
        NGƯỢC LẠI:
            reset số lần xác nhận tay

        NẾU scan tay quá thời gian giới hạn:
            return_camera_from_scan(now)
            trả về thông báo timeout

        NẾU camera đang quay xuống:
            trả về "đang scan xuống tìm tay"
        NGƯỢC LẠI:
            trả về "đang scan tìm tay"

    NẾU state là RETURNING:
        NẾU đã chờ đủ thời gian quay về:
            state = IDLE
            camera_net_offset_s = 0
            trả về "camera đã quay về"
        trả về "camera đang quay về"

    NẾU state là LOCKED:
        NẾU vẫn thấy tay:
            cập nhật thời điểm cuối cùng thấy tay
            trả về "đang theo dõi tay"

        NẾU mất tay đủ lâu:
            return_camera_from_scan(now)
            trả về "mất mục tiêu, quay về"

        trả về "giữ vị trí camera"
```

## Nhận diện khuôn mặt và quyền điều khiển

### commands_currently_allowed()

```text
HÀM commands_currently_allowed():
    NẾU không yêu cầu mặt quen:
        trả về true

    NẾU thời gian từ lần cuối thấy mặt quen nhỏ hơn FAMILIAR_GRACE_S:
        trả về true

    NGƯỢC LẠI:
        trả về false
```

Nếu `REQUIRE_FAMILIAR_FACE = True`, robot chỉ nhận lệnh tay trong vài giây sau
lần nhận diện được khuôn mặt quen.

### set_face_matrix(expression)

```text
HÀM set_face_matrix(expression):
    NẾU expression giống lần gửi gần nhất:
        bỏ qua

    THỬ:
        gọi Bridge "update_face_matrix" với expression
    NẾU lỗi:
        in lỗi Bridge

    lưu expression là trạng thái mới nhất
```

## Trạng thái robot và nhận diện cử chỉ

Robot có 4 trạng thái chính:

```text
STANDING  # đứng, nhận lệnh di chuyển bình thường
SITTING   # ngồi
PRONE     # nằm
CHASING   # tự động đuổi bóng
```

### is_folded(landmarks, tip_idx, mcp_idx)

```text
HÀM is_folded(landmarks, tip_idx, mcp_idx):
    tip = tọa độ đầu ngón tay
    mcp = tọa độ khớp gốc ngón tay
    wrist = tọa độ cổ tay

    distance_tip = khoảng cách tip tới wrist
    distance_mcp = khoảng cách mcp tới wrist

    NẾU distance_tip < distance_mcp:
        trả về true
    NGƯỢC LẠI:
        trả về false
```

### is_pointing_down(landmarks)

```text
HÀM is_pointing_down(landmarks):
    NẾU ngón trỏ bị gập:
        trả về false

    NẾU ngón giữa, áp út, út không gập hết:
        trả về false

    dx = đầu ngón trỏ x - gốc ngón trỏ x
    dy = đầu ngón trỏ y - gốc ngón trỏ y

    NẾU hướng chủ yếu là dọc và dy đủ lớn theo chiều xuống:
        trả về true
    NGƯỢC LẠI:
        trả về false
```

## main_loop()

Đây là vòng lặp quan trọng nhất của ứng dụng.

```text
HÀM main_loop():
    THỬ:
        frame = cam.read()
        NẾU frame là None:
            trả về

        lật frame theo chiều ngang

        NẾU robot_state là CHASING:
            xử lý chế độ đuổi bóng
            trả về

        NẾU đang trong thời gian cooldown:
            trả về

        tăng inference_counter
        NẾU chưa tới lượt xử lý inference:
            trả về

        hands = detector.detect(frame)
        now = thời gian monotonic hiện tại
        face_status = "none"

        NẾU đã tới thời điểm kiểm tra khuôn mặt:
            NẾU đang scan tìm mặt:
                dùng chu kỳ kiểm tra mặt nhanh hơn
            NGƯỢC LẠI:
                dùng chu kỳ kiểm tra mặt bình thường

            face_status, name = face_gate.recognize(frame)

            NẾU face_status là "familiar":
                cập nhật last_familiar_time
                set_face_matrix("smiley")
            NGƯỢC LẠI NẾU face_status là "unfamiliar":
                set_face_matrix("indifferent")

        command = None
        display_text = "NO HAND"
        posture_transition = False
        legs_detected = False

        NẾU robot đang đứng
           VÀ camera scan đang IDLE
           VÀ không thấy tay
           VÀ tới lượt kiểm tra camera:
               person_box = ball_tracker.detect_person(frame)
               legs_detected = person_box tồn tại và là legs-only

        scan_display = update_camera_scan(
            legs_detected,
            có thấy tay hay không,
            face_status,
            now
        )

        NẾU có ít nhất một bàn tay:
            lấy landmarks của bàn tay đầu tiên
            xác định từng ngón có gập hay không
            is_open_palm = không ngón nào gập
            is_pointing = chỉ ngón trỏ duỗi, các ngón khác gập
            is_fist = tất cả ngón đều gập

            NẾU is_pointing:
                tính hướng từ gốc ngón trỏ tới đầu ngón trỏ
                xác định point_left / point_right / point_up / point_down

            NẾU commands_currently_allowed() là false:
                display_text = "Ignoring (unfamiliar)"

            NGƯỢC LẠI NẾU robot_state là STANDING:
                xử lý cử chỉ khi robot đang đứng

            NGƯỢC LẠI NẾU robot_state là SITTING:
                xử lý cử chỉ khi robot đang ngồi

            NGƯỢC LẠI NẾU robot_state là PRONE:
                xử lý cử chỉ khi robot đang nằm

            NẾU command không phải None:
                gửi command sang Arduino
                đặt cooldown theo loại lệnh

            NẾU robot đang đứng và camera scan IDLE:
                đặt camera về neutral

        NẾU robot không ở STANDING:
            yêu cầu camera quay về nếu đang scan
        NGƯỢC LẠI NẾU không có command và scan_display tồn tại:
            display_text = scan_display

        cập nhật OLED bằng display_text

    NẾU có exception:
        in traceback
        yêu cầu camera quay về
        gửi lệnh STOP
```

### Nhánh CHASING trong main_loop()

```text
NẾU robot_state là CHASING:
    NẾU tới lượt kiểm tra dừng thủ công:
        hands = detector.detect(frame)
        NẾU có tay và tay đang chỉ xuống:
            gửi STOP
            robot_state = STANDING
            đặt cooldown
            hiển thị "Stopped (manual)"
            trả về

    ball, top_candidates, command, display_text, exit_reason =
        ball_tracker.command_for_frame(frame)

    gửi command sang Arduino

    NẾU exit_reason là "found":
        robot_state = STANDING
        display_text = "Standing (ball found)"
        đặt cooldown
        đặt camera neutral

    NGƯỢC LẠI NẾU exit_reason là "gave_up":
        robot_state = STANDING
        đặt cooldown
        đặt camera neutral

    NGƯỢC LẠI NẾU chưa từng thấy bóng:
        NẾU YOLO thấy vật thể ứng viên:
            display_text = "Searching, see:<tên> <độ tin cậy>"
        NGƯỢC LẠI:
            display_text = "Searching, see: nothing"

    cập nhật OLED
    trả về
```

### Bảng chuyển cử chỉ khi robot đang đứng

```text
NẾU robot đang STANDING:
    NẾU open palm:
        command = "s"
        display = "CMD: STOP (Palm)"

    NẾU point up:
        command = "w"
        display = "CMD: WALK (w)"

    NẾU point left:
        command = "a"
        display = "CMD: LEFT (a)"

    NẾU point right:
        command = "d"
        display = "CMD: RIGHT (d)"

    NẾU point down:
        command = "q"
        display = "Sitting"
        robot_state = SITTING
        posture_transition = true

    NẾU fist:
        command = "s"
        display = "Ball Mode"
        robot_state = CHASING
        ball_tracker.start_chase()
        posture_transition = true
        đặt camera xuống

    NẾU không khớp cử chỉ nào:
        display = "UNKNOWN SIGN"
```

### Bảng chuyển cử chỉ khi robot đang ngồi

```text
NẾU robot đang SITTING:
    NẾU point down:
        command = "c"
        display = "Prone"
        robot_state = PRONE
        posture_transition = true

    NẾU point up:
        command = "s"
        display = "Standing"
        robot_state = STANDING
        posture_transition = true

    NẾU cử chỉ khác:
        display = "Sitting (point up/down)"
```

### Bảng chuyển cử chỉ khi robot đang nằm

```text
NẾU robot đang PRONE:
    NẾU point up:
        command = "q"
        display = "Sitting"
        robot_state = SITTING
        posture_transition = true

    NẾU cử chỉ khác:
        display = "Prone (point up)"
```

### _update_oled(display_text)

```text
HÀM _update_oled(display_text):
    NẾU display_text giống dòng đã gửi lần trước:
        bỏ qua

    gọi Bridge "update_oled" với display_text
    lưu display_text thành last_text
```

Mục đích: giảm số lần gọi Bridge và tránh làm OLED bị xóa/in lại liên tục khi
nội dung không đổi.

### Entrypoint

```text
THỬ:
    App.run(user_loop = main_loop)

NẾU App không tồn tại:
    LẶP vô hạn:
        main_loop()
```

## python/detector.py

`detector.py` là lớp wrapper gọn cho hai model bàn tay: palm detection và hand
pose estimation.

### HandGestureDetector

```text
CLASS HandGestureDetector:
    HÀM __init__(palm_model_path, hand_model_path, score_threshold, conf_threshold):
        tạo MPPalmDet để tìm vùng lòng bàn tay
        tạo MPHandPose để tìm 21 landmarks của bàn tay

    HÀM detect(frame):
        palms = palm_detector.infer(frame)
        hands = danh sách rỗng

        VỚI mỗi palm trong palms:
            result = hand_detector.infer(frame, palm)

            NẾU result là None:
                bỏ qua palm này

            landmarks = lấy 21 điểm landmark màn hình từ result
            handedness = "Right" nếu chỉ số handedness > 0.5, ngược lại "Left"
            confidence = lấy độ tin cậy từ result

            thêm dictionary vào hands:
                landmarks
                handedness
                confidence

        trả về hands
```

### Các hàm phân loại cử chỉ

```text
HÀM _dist(a, b):
    trả về khoảng cách Euclidean giữa 2 điểm ảnh

HÀM is_open_palm(landmarks):
    NẾU đầu ngón trỏ, giữa, áp út, út đều xa cổ tay hơn khớp gốc:
        trả về true
    NGƯỢC LẠI:
        trả về false

HÀM is_fist(landmarks):
    NẾU đầu ngón trỏ, giữa, áp út, út đều gần cổ tay hơn khớp gốc:
        trả về true
    NGƯỢC LẠI:
        trả về false

HÀM is_pointing(landmarks):
    index_extended = ngón trỏ duỗi
    others_curled = các ngón giữa, áp út, út đều gập
    trả về index_extended AND others_curled

HÀM classify_hand_gesture(landmarks):
    NẾU is_open_palm:
        trả về "OPEN_PALM"
    NẾU is_fist:
        trả về "FIST"
    NẾU is_pointing:
        trả về "POINTING"
    NGƯỢC LẠI:
        trả về "UNKNOWN"
```

### Chế độ debug khi chạy trực tiếp

```text
NẾU chạy detector.py trực tiếp:
    mở camera mặc định

    LẶP:
        đọc frame
        NẾU đọc thất bại:
            thoát vòng lặp

        hands = detector.detect(frame)

        VỚI từng hand:
            gesture = classify_hand_gesture(hand.landmarks)
            in loại tay, cử chỉ, confidence
            vẽ các điểm landmark lên frame

        hiển thị cửa sổ debug
        NẾU người dùng nhấn q:
            thoát

    giải phóng camera
    đóng cửa sổ OpenCV
```

## python/hand_models/mp_palmdet.py

File này là lớp xử lý cấp thấp cho model phát hiện lòng bàn tay.

```text
CLASS MPPalmDet:
    HÀM __init__(modelPath, nmsThreshold, scoreThreshold, topK, backendId, targetId):
        lưu đường dẫn model và threshold
        đặt input_size = 192x192
        đọc model ONNX bằng OpenCV DNN
        đặt backend và target cho model
        nạp danh sách anchors cố định

    HÀM setBackendAndTarget(backendId, targetId):
        lưu backend/target mới
        cập nhật backend/target cho model

    HÀM _preprocess(image):
        tính tỉ lệ resize để ảnh vừa input_size
        resize ảnh giữ nguyên tỉ lệ
        padding phần còn thiếu bằng màu đen
        đổi BGR sang RGB
        chuẩn hóa pixel về khoảng 0..1
        trả về tensor NHWC và pad_bias

    HÀM infer(image):
        input_blob, pad_bias = _preprocess(image)
        đưa input_blob vào model
        chạy forward
        results = _postprocess(output_blob, shape gốc, pad_bias)
        trả về results

    HÀM _postprocess(output_blob, original_shape, pad_bias):
        lấy raw score, box_delta, landmark_delta
        áp dụng sigmoid cho score
        dùng anchors để đổi delta thành bounding box
        trừ padding bias để quay về tọa độ ảnh gốc
        chạy NMS để bỏ box trùng

        NẾU không còn box:
            trả về mảng rỗng

        đổi landmark_delta thành tọa độ ảnh
        trả về mỗi detection gồm:
            bbox 4 giá trị
            7 palm landmarks
            score

    HÀM _load_anchors():
        trả về mảng anchor cố định của model MediaPipe palm
```

## python/hand_models/mp_handpose.py

File này nhận một palm detection và ước lượng 21 điểm landmark của bàn tay.

```text
CLASS MPHandPose:
    HÀM __init__(modelPath, confThreshold, backendId, targetId):
        lưu threshold và cấu hình model
        đặt input_size = 224x224
        khai báo các hằng số crop/shift/enlarge
        đọc model ONNX bằng OpenCV DNN
        đặt backend và target

    HÀM setBackendAndTarget(backendId, targetId):
        lưu backend/target mới
        cập nhật backend/target cho model

    HÀM _cropAndPadFromPalm(image, palm_bbox, for_rotation):
        dịch bounding box theo vector cấu hình
        phóng to bounding box theo hệ số cấu hình
        giới hạn box nằm trong ảnh
        crop ảnh theo box
        padding crop thành hình vuông
        trả về crop, box đã chỉnh, và bias padding

    HÀM _preprocess(image, palm):
        crop vùng palm lớn để chuẩn bị xoay
        đổi ảnh sang RGB
        lấy palm bbox và palm landmarks
        tính góc xoay từ gốc lòng bàn tay tới gốc ngón giữa
        xoay ảnh để bàn tay thẳng hơn
        tính box mới sau khi xoay
        crop vùng bàn tay cuối cùng
        resize về 224x224
        chuẩn hóa pixel về 0..1
        trả về tensor và metadata để phục hồi tọa độ

    HÀM infer(image, palm):
        preprocess ảnh và palm
        chạy model hand pose
        postprocess kết quả về tọa độ ảnh gốc
        trả về bbox, landmarks, handedness, confidence

    HÀM _postprocess(blob, rotated_palm_bbox, angle, rotation_matrix, pad_bias):
        lấy landmarks, confidence, handedness, world_landmarks

        NẾU confidence < threshold:
            trả về None

        scale landmarks từ input model về kích thước crop
        xoay ngược landmarks về hướng ban đầu
        dịch landmarks về tọa độ ảnh gốc
        tính bounding box bàn tay
        dịch và phóng to box một chút
        trả về vector phẳng gồm:
            bbox
            21 screen landmarks
            21 world landmarks
            handedness
            confidence
```

## python/face_gate.py

`face_gate.py` kiểm tra khuôn mặt hiện tại là người quen hay người lạ. Kết quả
này được dùng để cho phép hoặc chặn lệnh cử chỉ tay.

### FaceGate.__init__()

```text
CLASS FaceGate:
    HÀM __init__(detector_path, recognizer_path, db_path, input_size):
        tạo YuNet face detector
        tạo SFace recognizer
        lưu input_size
        known = _load_db(db_path)
        recognition_error_count = 0
```

### _load_db(path)

```text
HÀM _load_db(path):
    NẾU file database không tồn tại:
        in cảnh báo
        trả về dictionary rỗng

    đọc JSON
    VỚI từng name, vector trong JSON:
        chuyển vector thành NumPy float32 shape (1, -1)
        lưu vào dictionary

    trả về dictionary name -> embedding
```

### _set_frame_size(width, height)

```text
HÀM _set_frame_size(width, height):
    NẾU kích thước mới khác input_size hiện tại:
        cập nhật input_size
        gọi detector.setInputSize(input_size)
```

### _prepare_frame(frame)

```text
HÀM _prepare_frame(frame):
    lấy width và height của frame
    tính scale để frame vừa trong FACE_INPUT_SIZE
    không phóng to ảnh nếu ảnh đã nhỏ

    NẾU scale = 1:
        trả về frame gốc
    NGƯỢC LẠI:
        resize frame và trả về frame mới
```

### recognize(frame)

```text
HÀM recognize(frame):
    THỬ:
        NẾU frame không hợp lệ:
            trả về ("none", None)

        face_frame = _prepare_frame(frame)
        cập nhật input size cho detector
        faces = detector.detect(face_frame)

        NẾU không có khuôn mặt:
            trả về ("none", None)

        largest = khuôn mặt có diện tích lớn nhất
        aligned = recognizer.alignCrop(face_frame, largest)
        feature = recognizer.feature(aligned)

        best_score = -1
        best_name = None

        VỚI từng người quen trong database:
            score = cosine similarity giữa embedding đã biết và feature hiện tại
            NẾU score > best_score:
                cập nhật best_score
                cập nhật best_name

        NẾU best_score >= FACE_MATCH_THRESHOLD:
            trả về ("familiar", best_name)
        NGƯỢC LẠI:
            trả về ("unfamiliar", None)

    NẾU lỗi OpenCV hoặc lỗi dữ liệu:
        ghi log lỗi
        trả về ("none", None)
```

### verify_face_models()

```text
HÀM verify_face_models():
    tạo ảnh dummy kích thước 320x240

    THỬ:
        tạo YuNet detector
        chạy detector trên ảnh dummy
        tạo SFace recognizer
        in "Face models OK."

    NẾU lỗi:
        in thông báo lỗi model
```

## python/enroll_faces.py

Script này tạo `known_faces_db.json` từ ảnh trong `python/known_faces/`.

### Các hàm kiểm tra và tạo model

```text
HÀM _check_dependencies():
    NẾU thiếu cv2:
        báo thiếu opencv-python-headless
    NẾU thiếu numpy:
        báo thiếu numpy
    NẾU có package thiếu:
        raise RuntimeError

    NẾU OpenCV không có FaceDetectorYN hoặc FaceRecognizerSF:
        raise RuntimeError

HÀM _check_model_file(path):
    NẾU file không tồn tại:
        raise FileNotFoundError
    NẾU file quá nhỏ:
        raise RuntimeError vì có thể là Git LFS pointer

HÀM _create_detector(score_threshold):
    kiểm tra file YuNet
    tạo và trả về FaceDetectorYN

HÀM _create_recognizer():
    kiểm tra file SFace
    tạo và trả về FaceRecognizerSF
```

### Duyệt dữ liệu ảnh

```text
HÀM _iter_people(photos_dir):
    VỚI từng thư mục con trong photos_dir theo thứ tự:
        yield tên người và đường dẫn thư mục

HÀM _iter_images(person_dir):
    VỚI từng file trong thư mục người:
        NẾU file có đuôi ảnh hỗ trợ:
            yield đường dẫn file
```

### enroll(photos_dir, output_path, detect_threshold)

```text
HÀM enroll(photos_dir, output_path, detect_threshold):
    chuẩn hóa đường dẫn photos_dir
    chuẩn hóa đường dẫn output_path

    NẾU photos_dir không tồn tại:
        raise NotADirectoryError

    _check_dependencies()
    detector = _create_detector(detect_threshold)
    recognizer = _create_recognizer()
    db = dictionary rỗng

    VỚI từng name, person_dir trong _iter_people(photos_dir):
        embeddings = danh sách rỗng

        VỚI từng ảnh path trong _iter_images(person_dir):
            image = cv2.imread(path)

            NẾU image không đọc được:
                in thông báo bỏ qua
                tiếp tục

            đặt input size của detector bằng kích thước ảnh
            faces = detector.detect(image)

            NẾU không tìm thấy mặt:
                in thông báo bỏ qua
                tiếp tục

            largest = khuôn mặt lớn nhất
            aligned = recognizer.alignCrop(image, largest)
            embedding = recognizer.feature(aligned)
            thêm embedding vào embeddings
            in thông báo đã enroll ảnh

        NẾU embeddings không rỗng:
            tính trung bình embedding của người đó
            lưu vào db[name]
        NGƯỢC LẠI:
            in người này không có ảnh dùng được

    NẾU db rỗng:
        raise RuntimeError

    tạo thư mục output nếu cần
    ghi db ra JSON
    trả về db
```

### Entrypoint

```text
HÀM _parse_args():
    đọc photos_dir nếu người dùng truyền vào
    đọc output_json_path nếu người dùng truyền vào
    đọc --detect-threshold nếu người dùng truyền vào
    trả về args

NẾU chạy enroll_faces.py trực tiếp:
    args = _parse_args()
    THỬ:
        enroll(args.photos_dir, args.output_json_path, args.detect_threshold)
    NẾU lỗi:
        in "Enrollment failed"
        thoát với mã lỗi 1
```

## python/ball_tracker.py

`ball_tracker.py` dùng YOLOv8n để nhận diện bóng, người, và quyết định lệnh
khi robot ở chế độ đuổi bóng.

### Khởi tạo BallTracker

```text
CLASS BallTracker:
    HÀM __init__(model_path, walk_command, stop_command, left_command, right_command):
        đọc model YOLOv8n ONNX bằng OpenCV DNN
        lấy tên các output layer
        lưu các lệnh walk/stop/left/right

        reset bộ đếm lỗi forward
        reset bộ đếm kiểm tra dừng thủ công
        reset bộ đếm kiểm tra camera

        ball_ever_seen = False
        last_seen_ball_side = None
        chase_entry_time = 0
        last_ball_seen_time = 0

        _verify_model_input_size()
```

### _verify_model_input_size()

```text
HÀM _verify_model_input_size():
    tạo ảnh đen dummy kích thước MODEL_INPUT_SIZE x MODEL_INPUT_SIZE
    tạo blob từ ảnh dummy

    THỬ:
        đưa blob vào model
        chạy forward
        in model OK
    NẾU lỗi:
        in cảnh báo model sai kích thước input
```

### _letterbox(frame)

```text
HÀM _letterbox(frame):
    lấy width và height của frame
    tính scale để frame vừa trong MODEL_INPUT_SIZE
    resize frame theo scale
    tính padding trái/phải/trên/dưới
    thêm viền xám để ảnh thành hình vuông
    trả về:
        ảnh đã letterbox
        scale
        pad_left
        pad_top
```

### _predict(frame)

```text
HÀM _predict(frame):
    letterboxed, scale, pad_left, pad_top = _letterbox(frame)
    tạo blob chuẩn hóa từ ảnh letterboxed
    đưa blob vào YOLO

    THỬ:
        outputs = model.forward()
    NẾU lỗi:
        ghi log lỗi forward
        trả về None

    raw = squeeze outputs
    expected_width = 4 + số lớp COCO

    NẾU raw có dạng channels-first:
        transpose raw
    NGƯỢC LẠI NẾU raw có dạng row-first:
        giữ nguyên
    NGƯỢC LẠI:
        in cảnh báo shape lạ
        trả về None

    trả về predictions, scale, pad_left, pad_top
```

### detect_ball(frame)

```text
HÀM detect_ball(frame):
    prediction_result = _predict(frame)
    NẾU prediction_result là None:
        trả về None và danh sách diagnostics rỗng

    boxes = []
    confidences = []
    candidates = []

    VỚI từng prediction row:
        class_id = lớp có điểm cao nhất
        confidence = điểm của class_id

        NẾU confidence đủ cao cho diagnostic:
            thêm class_id và confidence vào candidates

        NẾU class_id không phải sports ball:
            tiếp tục
        NẾU confidence thấp hơn ngưỡng bóng:
            tiếp tục

        đổi bbox từ tọa độ YOLO về tọa độ frame gốc
        thêm box và confidence

    sắp xếp candidates theo confidence giảm dần
    lấy top candidates để hiển thị debug

    NẾU không có box bóng:
        trả về None và top candidates

    chạy NMS cho các box bóng
    NẾU không còn box sau NMS:
        trả về None và top candidates

    lấy box đầu tiên
    tính tâm bóng x, y
    tính radius xấp xỉ
    trả về thông tin bóng và top candidates
```

### detect_person(frame)

```text
HÀM detect_person(frame):
    prediction_result = _predict(frame)
    NẾU prediction_result là None:
        trả về None

    boxes = []
    confidences = []

    VỚI từng prediction row:
        class_id = lớp có điểm cao nhất
        confidence = điểm của class_id

        NẾU class_id là person và confidence đủ cao:
            đổi bbox về tọa độ frame gốc
            thêm box và confidence

    NẾU không có box người:
        trả về None

    chạy NMS
    NẾU không còn box:
        trả về None

    trả về box người dạng left, top, right, bottom
```

### is_legs_only(person_box)

```text
HÀM is_legs_only(person_box):
    lấy top và bottom của person_box

    NẾU top nằm rất gần mép trên frame
       VÀ chiều cao box đủ lớn:
           trả về true
    NGƯỢC LẠI:
           trả về false
```

Ý nghĩa: nếu camera đang nhìn thấp, YOLO có thể chỉ thấy phần chân/người bị cắt
ở mép trên. Khi đó robot hiểu là nên nâng camera lên để tìm mặt hoặc tay.

### Bộ đếm kiểm tra theo chu kỳ

```text
HÀM should_check_manual_stop():
    tăng _manual_stop_check_counter
    trả về true mỗi MANUAL_STOP_CHECK_INTERVAL frame

HÀM should_check_camera():
    tăng _camera_check_counter
    trả về true mỗi CAMERA_CHECK_INTERVAL frame
```

### start_chase()

```text
HÀM start_chase():
    ball_ever_seen = False
    last_seen_ball_side = None
    chase_entry_time = thời gian hiện tại
    last_ball_seen_time = thời gian hiện tại
```

### command_for_frame(frame)

```text
HÀM command_for_frame(frame):
    ball, top_candidates = detect_ball(frame)
    command, display_text, exit_reason =
        _decide_chase_command(ball, frame_width)

    trả về ball, top_candidates, command, display_text, exit_reason
```

### _decide_chase_command(ball, frame_width)

```text
HÀM _decide_chase_command(ball, frame_width):
    frame_center = frame_width / 2

    NẾU ball tồn tại:
        lấy x và radius của bóng
        ball_ever_seen = True
        last_seen_ball_side = "left" nếu bóng ở bên trái, ngược lại "right"
        last_ball_seen_time = thời gian hiện tại

        NẾU radius >= BALL_FOUND_RADIUS:
            trả về STOP, "Ball found!", "found"

        NẾU x < frame_center - BALL_CENTER_DEADZONE:
            trả về LEFT, "Chasing: left", None

        NẾU x > frame_center + BALL_CENTER_DEADZONE:
            trả về RIGHT, "Chasing: right", None

        trả về WALK, "Chasing: forward", None

    NẾU chưa từng thấy bóng:
        NẾU đã quá NEVER_SEEN_TIMEOUT_S:
            trả về STOP, "No ball found, giving up", "gave_up"
        NGƯỢC LẠI:
            trả về STOP, "Searching (never seen yet)", None

    NẾU đã từng thấy bóng nhưng mất dấu quá SPIN_SEARCH_TIMEOUT_S:
        trả về STOP, "Lost ball, giving up", "gave_up"

    NẾU lần cuối thấy bóng bên trái:
        trả về LEFT để quay tìm lại
    NGƯỢC LẠI:
        trả về RIGHT để quay tìm lại
```

## Luồng end-to-end của ứng dụng vision

```text
BẮT ĐẦU DogVision

Arduino UNO Q:
    setup Serial1
    setup OLED
    setup LED matrix
    setup Bridge handlers

Python:
    mở camera
    tải detector bàn tay
    tải FaceGate
    tải BallTracker
    vào main_loop

MỖI VÒNG main_loop:
    lấy frame mới nhất
    lật frame

    NẾU đang đuổi bóng:
        kiểm tra cử chỉ dừng thủ công theo chu kỳ
        chạy YOLO tìm bóng
        quyết định đi thẳng / rẽ trái / rẽ phải / dừng
        gửi lệnh qua Bridge
        cập nhật OLED
        tiếp tục vòng sau

    NẾU không ở chế độ đuổi bóng:
        chạy nhận diện tay theo chu kỳ
        chạy nhận diện khuôn mặt theo chu kỳ
        cập nhật ma trận LED theo trạng thái mặt

        NẾU chỉ thấy chân người:
            bắt đầu scan camera

        cập nhật state machine camera:
            scan lên tìm mặt quen hoặc tay
            scan xuống tìm tay nếu cần
            khóa camera khi thấy tay
            quay về khi mất mục tiêu hoặc timeout

        NẾU thấy tay:
            phân loại cử chỉ
            kiểm tra quyền điều khiển bằng FaceGate
            đổi cử chỉ thành command
            cập nhật robot_state nếu cần
            gửi command qua Bridge

        cập nhật OLED nếu nội dung thay đổi
```

## Dữ liệu và model

Các file dưới đây không phải code điều khiển, nhưng là dữ liệu runtime quan
trọng.

```text
python/yolov8n.onnx:
    model YOLOv8n dùng cho BallTracker để nhận diện bóng/người

python/hand_models/palm_detection_mediapipe_2023feb.onnx:
    model tìm palm detection

python/hand_models/handpose_estimation_mediapipe_2023feb.onnx:
    model tìm 21 hand landmarks

python/face_models/face_detection_yunet_2023mar.onnx:
    model YuNet phát hiện khuôn mặt

python/face_models/face_recognition_sface_2021dec.onnx:
    model SFace trích xuất embedding khuôn mặt

python/known_faces/<tên-người>/*:
    ảnh gốc dùng để enroll khuôn mặt quen

python/known_faces_db.json:
    database embedding trung bình cho từng người quen

python/debug_vision_*.jpg:
    ảnh debug trong quá trình thử nghiệm vision

python/requirements.txt:
    danh sách package Python cần cài cho runtime
```

## Tài liệu hỗ trợ trong thư mục docs

```text
docs/system-structure-dataflow.md:
    giải thích cấu trúc và dataflow của hệ thống

docs/camera-scan-instructions.md:
    hướng dẫn wiring, tuning, và test tính năng camera scan bằng servo quay liên tục

docs/esp32-gait-control.md:
    giải thích kiến trúc gait, IK, PID, servo ST3215, và các bước hiệu chỉnh ESP32

docs/project-pseudocode.md:
    chính là tài liệu pseudocode tiếng Việt này
```
