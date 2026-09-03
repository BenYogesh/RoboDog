# Cấu trúc code và luồng dữ liệu RoboDog

Tài liệu này giải thích toàn bộ đường đi của dữ liệu, từ một khung hình webcam
đến lệnh servo. Không cần biết trước Python hay Arduino: trước hết hãy nắm
được ba tầng **nhìn**, **cầu nối** và **chuyển động**, sau đó đọc phần tương
ứng với việc bạn muốn sửa.

> Quy tắc đọc nhanh: Python quyết định *robot nên làm gì*; sketch.ino chuyển
> lời gọi giữa hai chip UNO Q; ESP32 quyết định *các servo phải di chuyển thế
> nào*. Các chuỗi như CMD:w và tên biến phải giữ nguyên khi làm theo hướng dẫn,
> vì đó là giao thức thật trong code.

## 1. Ba tầng của hệ thống

Arduino UNO Q có hai chip. Chip Qualcomm chạy Linux và Python; chip STM32 chạy
chương trình Arduino/Zephyr. ESP32 là bộ điều khiển servo độc lập.

| Tầng | Mã nguồn | Nhiệm vụ | Kết quả đầu ra |
| --- | --- | --- | --- |
| Nhìn và quyết định | python/main.py, detector.py, face_gate.py, ball_tracker.py | Đọc webcam, chạy mô hình, xác định người/cử chỉ/bóng và máy trạng thái. | Một lệnh một ký tự, ví dụ w, q hoặc a. |
| Cầu nối UNO Q | sketch/sketch.ino trên STM32 | Nhận lời gọi Bridge từ Python, kiểm tra ký tự, chuyển khung UART tới ESP32; hiển thị biểu cảm LED. | Khung CMD:<char>\n hoặc MODE:<mode>\n. |
| Chuyển động | ESP32, dog_esp32/dog_esp32.ino | Đổi ký tự thành quỹ đạo chân, tính IK, cân bằng bằng MPU6050, rồi gửi vị trí tới 12 servo ST3215. | Vị trí đồng bộ cho 12 servo ST3215 và telemetry. |

Webcam được đọc một lần nhưng có hai người dùng: vòng lặp Python dùng để nhận
diện, còn python/manual_video.py mã hóa khung hình mới nhất thành luồng
MJPEG cho dashboard. Khi manual, việc ra quyết định tự động dừng lại nhưng
luồng camera vẫn chạy.

## 2. Luồng dữ liệu tổng quát

~~~mermaid
flowchart LR
    Cam[Webcam] --> Stream[CameraStream<br/>python/main.py]
    Stream --> Loop[main_loop]
    Loop --> Hand[HandGestureDetector<br/>MediaPipe ONNX]
    Loop --> Face[FaceGate<br/>YuNet + SFace]
    Loop --> Ball[BallTracker<br/>YOLOv8n ONNX]
    Hand --> Decide[Máy trạng thái<br/>chọn lệnh]
    Face --> Decide
    Ball --> Decide
    Decide --> Bridge[Arduino Bridge]
    Bridge --> Sketch[sketch/sketch.ino<br/>STM32 UNO Q]
    Sketch --> LED[Ma trận LED]
    Sketch --> UART[Serial1<br/>CMD:<char>]
    UART --> ESP[ESP32 firmware]
    ESP --> Servos[12 servo ST3215]
    ESP --> IMU[MPU6050 + PID]
    Stream --> Web[manual_video.py<br/>HTTP :8080]
    Web --> Browser[Dashboard + MJPEG]
~~~

Ví dụ với cử chỉ chỉ sang trái:

1. CameraStream lấy khung hình và main_loop lật ngang để hình giống góc nhìn
   của người đối diện robot.
2. HandGestureDetector trả về 21 điểm mốc. main.py thấy ngón trỏ chỉ sang trái
   và chọn CMD_LEFT = "a".
3. bridge.call("send_motor_command", "a") chạy hàm an toàn trong
   sketch/sketch.ino.
4. STM32 gửi chính xác CMD:a\n trên Serial1.
5. ESP32 nhận ký tự a, tạo gait quay trái, tính IK cho từng chân rồi gửi vị trí
   mới bằng SyncWritePosEx.

Nếu bước 1 không có khung hình, bước 2 không có tay, bước 3 báo lỗi Bridge,
hoặc ESP32 không nhận khung UART, robot sẽ không chuyển động. Chia lỗi theo
đúng các bước này giúp chẩn đoán nhanh hơn việc nhìn mỗi servo.

## 3. Vai trò của từng file

| Đường dẫn | Đọc file này khi bạn muốn… |
| --- | --- |
| python/main.py | Hiểu vòng lặp chính, các trạng thái, cooldown, quyền manual, camera scan và kết nối các detector. |
| python/detector.py | Thay mô hình bàn tay hoặc thêm cách phân loại OPEN_PALM, FIST, POINTING. |
| python/face_gate.py | Thay ngưỡng hoặc mô hình nhận diện khuôn mặt. |
| python/enroll_faces.py | Đăng ký người mới và tạo lại known_faces_db.json. |
| python/ball_tracker.py | Thay ngưỡng YOLO, vùng giữa khung hình, thời gian tìm bóng hoặc luật quay/đi. |
| python/manual_video.py | Thay máy chủ web, API, quyền sở hữu dashboard hoặc luồng MJPEG. |
| sketch/sketch.ino | Thay hàm Bridge, khung UART, danh sách lệnh được cho phép hoặc biểu cảm LED. |
| dog_esp32/dog_esp32.ino | Thay chân GPIO, servo, gait, IK, tư thế, PID, nguồn thời gian và telemetry. |
| web/dashboard.html | Thay bố cục, nhãn, phím PC hoặc cách hiển thị trạng thái. |
| app.yaml và sketch/sketch.yaml | Kiểm tra cấu hình App Lab và nền tảng build của UNO Q. |

Các mô hình không phải code: python/face_models/ chứa YuNet/SFace,
python/hand_models/ chứa MediaPipe ONNX, còn python/yolov8n.onnx là model
YOLO dùng cho bóng và người.

## 4. Điều gì xảy ra lúc khởi động?

### Trên STM32 UNO Q

setup() trong sketch/sketch.ino mở Serial1 ở 115200 baud, khởi động
Arduino_RouterBridge, khởi tạo ma trận LED và đăng ký ba hàm an toàn:

- send_motor_command: chỉ nhận đúng một ký tự trong danh sách cho phép rồi gửi
  CMD:<char>\n.
- set_control_mode: gửi MODE:MANUAL\n hoặc MODE:AUTO\n tới ESP32.
- update_face_matrix: đổi biểu cảm smiley, indifferent hoặc trạng thái trung
  tính trên ma trận LED.

Mỗi vòng loop(), STM32 đọc các dòng ESP32 trả về. Khi thấy
MODE:MANUAL/MODE:AUTO, nó gọi Bridge.notify("manual_mode_changed", …) để
Python cập nhật quyền điều khiển.

### Trên Qualcomm/Linux UNO Q

python/main.py thực hiện các bước sau khi được App Lab chạy:

1. Chọn webcam từ UNO_Q_CAMERA_PATH hoặc đường dẫn mặc định.
2. Tạo CameraStream, mở webcam trong một luồng nền và giữ lại *khung hình mới
   nhất* (không xếp hàng khung cũ).
3. Tạo ManualVideoServer, lắng nghe 0.0.0.0:8080 và phục vụ dashboard.
4. Tạo detector bàn tay, FaceGate và BallTracker. verify_face_models() kiểm tra
   OpenCV có API khuôn mặt và các file ONNX tồn tại.
5. Đăng ký callback chế độ ESP32 rồi gọi App.run(user_loop=main_loop).

Nếu webcam mất kết nối, CameraStream thử mở lại sau một khoảng chờ. Sau năm
lần read() lỗi liên tiếp, nó giải phóng thiết bị rồi khởi động lại; API
/api/camera/restart gọi cùng cơ chế này theo yêu cầu dashboard.

## 5. Vòng lặp main_loop và máy trạng thái

Mỗi lượt lặp lấy khung hình mới nhất rồi lật ngang. Cooldown ngăn một cử chỉ bị
gửi lặp quá nhanh: lệnh thường chờ COMMAND_COOLDOWN_S = 0.7 giây, đổi tư thế
chờ POSTURE_TRANSITION_COOLDOWN_S = 2.0 giây.

### Nhánh manual

Khi robot_state == STATE_MANUAL, Python không chạy quyết định khuôn mặt, người
hay bóng. Nó chỉ giữ webcam hoạt động và cứ ba lượt suy luận một lần để cập
nhật hand_detected cho dashboard. Lệnh chuyển động thuộc về Bluetooth hoặc
dashboard, không phải cử chỉ tay.

### Nhánh đuổi bóng

Khi robot_state == STATE_CHASING, BallTracker.command_for_frame() được ưu tiên.
Cứ năm khung hình, Python vẫn kiểm tra cử chỉ chỉ xuống để dừng thủ công.
Nếu tìm thấy bóng đủ lớn, robot dừng và về STANDING; nếu chưa từng thấy bóng
trong 6 giây hoặc mất bóng sau 6 giây quay tìm, robot cũng bỏ cuộc và về đứng.

### Nhánh tự động thông thường

Python chạy detector tay mỗi ba lượt. FaceGate được gọi khoảng mỗi 0,8 giây;
trong giai đoạn quét tìm mặt, chu kỳ rút xuống 0,25 giây. Khi không thấy tay,
BallTracker thỉnh thoảng tìm một hộp người; nếu hộp chạm mép trên và đủ cao,
robot coi đó là “chỉ thấy chân” để bắt đầu camera scan.

Các trạng thái chính:

| Hằng số | Ý nghĩa dễ hiểu | Cử chỉ/điều kiện chuyển thường gặp |
| --- | --- | --- |
| STATE_STANDING | Đang đứng, trạng thái mặc định. | Lòng bàn tay: dừng; chỉ lên: w; trái/phải: a/d; chỉ xuống: ngồi; nắm tay: đuổi bóng. |
| STATE_SITTING | Đang ngồi. | Chỉ xuống: nằm (c); chỉ lên: đứng (s). |
| STATE_PRONE | Đang nằm. | Chỉ lên: ngồi (q). |
| STATE_CHASING | BallTracker tự tìm và tiến về bóng. | Chỉ xuống hoặc tìm thấy/bỏ cuộc: dừng và về đứng. |
| STATE_MANUAL | Python tạm dừng quyết định tự động. | Bluetooth M vào manual, O ra; dashboard có nút Manual/Automatic. |

~~~mermaid
stateDiagram-v2
    [*] --> STANDING
    STANDING --> SITTING: chỉ xuống / q
    SITTING --> STANDING: chỉ lên / s
    SITTING --> PRONE: chỉ xuống / c
    PRONE --> SITTING: chỉ lên / q
    STANDING --> CHASING: nắm tay
    CHASING --> STANDING: bóng đủ lớn, timeout hoặc chỉ xuống
    STANDING --> MANUAL: Bluetooth M hoặc dashboard Manual
    SITTING --> MANUAL: vào Manual
    PRONE --> MANUAL: vào Manual
    MANUAL --> STANDING: Bluetooth O hoặc dashboard Automatic
~~~

Một lệnh tư thế vẫn được gửi xuống ESP32 ngay khi máy trạng thái đổi, nhưng
firmware không “bật” servo tới vị trí cuối. Nó nội suy chuyển tư thế; xem
tài liệu ESP32 để hiểu phần cơ học.

## 6. Nhận diện bàn tay

detector.py dùng hai model MediaPipe ONNX:

1. Palm detector tìm vùng bàn tay.
2. Hand-pose model tìm 21 điểm mốc (x, y, z), độ tin cậy và tay trái/phải.
3. Các hàm hình học so khoảng cách đầu ngón tay–cổ tay với khớp MCP để phân
   loại OPEN_PALM, FIST, POINTING hoặc UNKNOWN.

main.py dùng thêm hướng của ngón trỏ để phân biệt trái/phải/lên/xuống. Ngưỡng
dọc hiện tại là POINT_VERTICAL_THRESHOLD = 20.0 pixel; các ngưỡng ngang cũng là
20 pixel. Detector khởi tạo với score_threshold=0.6 và conf_threshold=0.6.

Khi một tay xuất hiện nhưng khuôn mặt chưa được cho phép, terminal ghi
Ignoring (unfamiliar) và dashboard vẫn có thể báo đã thấy tay. Đây là cố ý:
phát hiện không đồng nghĩa với được quyền điều khiển.

## 7. Cổng khuôn mặt và đăng ký người

FaceGate dùng YuNet để tìm mặt rồi SFace để tạo vector đặc trưng. File
python/known_faces_db.json là một từ điển tên người → vector; nó được tạo bởi
enroll_faces.py, không nên sửa bằng tay.

Quy trình đăng ký:

1. Đặt ảnh vào python/known_faces/<ten-nguoi>/.
2. Chạy python3 python/enroll_faces.py.
3. Script chọn mặt lớn nhất mỗi ảnh, căn chỉnh và lấy đặc trưng SFace.
4. Các vector của cùng người được trung bình rồi ghi ra JSON.

Trong lúc chạy, face_gate.recognize(frame) trả về:

- familiar: điểm so khớp đạt FACE_MATCH_THRESHOLD = 0.363;
- unfamiliar: có mặt nhưng không đạt ngưỡng;
- none: không có mặt hoặc không đủ dữ liệu.

REQUIRE_FAMILIAR_FACE = True là mặc định. Khi bật, thời điểm nhận diện người
quen được giữ trong FAMILIAR_GRACE_S = 3.0 giây để cử chỉ không bị mất chỉ vì
một khung hình mờ. Đặt thành False để cho mọi người điều khiển bằng cử chỉ,
nhưng FaceGate và biểu cảm LED vẫn tiếp tục chạy.

## 8. Quét camera khi chỉ thấy chân

BallTracker tìm hộp người ở các lượt thưa hơn. Nếu chỉ thấy phần chân, Python
không đoán cử chỉ ngay vì bàn tay đang ở ngoài khung. Nó điều khiển servo camera
theo thời gian:

| Trạng thái | Điều kiện | Lệnh ESP32 |
| --- | --- | --- |
| IDLE | Thấy người chỉ có chân. | r: quét lên; nếu không yêu cầu mặt thì quét tay ngay. |
| FACE_SCANNING | Đang tìm người quen. | Giữ r; gặp mặt quen thì x, sau đó v để quét xuống tìm tay. |
| HAND_SCANNING | Đang tìm tay (trực tiếp hoặc sau mặt). | v; cần hai lần xác nhận liên tiếp. |
| LOCKED | Đã thấy mục tiêu. | Giữ vị trí cho tới khi mất mục tiêu. |
| RETURNING | Timeout/mất mục tiêu/đã nhận lệnh. | n: ESP32 dùng thời gian đã ghi để quay về gần trung tính. |

Các hằng số Python hiện tại là CAMERA_SCAN_TIMEOUT_S = 1.5,
CAMERA_TARGET_LOST_S = 1.0, CAMERA_HAND_CONFIRMATIONS = 2, chu kỳ kiểm tra mặt
0,25 giây và thời gian ổn định khi trả về 0,1 giây. Firmware ESP32 có giới hạn
an toàn vật lý CAMERA_SCAN_MAX_MS = 500; đây là giới hạn thấp hơn thời gian
timeout Python hiện tại, nên servo có thể tự dừng trước khi Python báo timeout.
Khi tinh chỉnh cơ khí, hãy làm cho hai giới hạn nhất quán và luôn thử với robot
được đỡ. Mạch và cách chỉnh nằm trong hướng dẫn camera scan.

## 9. Phát hiện và đuổi bóng

BallTracker nạp python/yolov8n.onnx bằng OpenCV DNN, co khung hình về đầu vào
320×320 bằng letterbox rồi giải mã 80 lớp COCO. Lớp được dùng để đuổi là
sports ball (ID 32); lớp person (ID 0) chỉ dùng để phát hiện chân.

Khi nắm tay ở trạng thái đứng, main.py gọi start_chase() và đặt trạng thái
searching:

- Bóng bên trái vùng giữa → gửi a (quay trái).
- Bóng bên phải vùng giữa → gửi d (quay phải).
- Bóng trong vùng giữa → gửi w (tiến).
- Bán kính bóng từ 45 pixel trở lên → coi là đã tới, gửi s.
- Chưa từng thấy bóng sau 6 giây → not_found; đã từng thấy nhưng mất quá 6
  giây → lost rồi dừng.

Trong lúc CHASING, dashboard nhận tối đa năm dự đoán có điểm từ 0,15 trở lên,
không chỉ lớp bóng. Vì vậy panel Detected objects có thể hiện person, dog,
sports ball… dù chỉ sports ball mới làm robot đuổi. Các trường trạng thái là
ball_detection_active, ball_detection_status và ball_detection_objects.

## 10. Bridge, UART và bảng lệnh

Python không truy cập servo trực tiếp. Lời gọi Bridge chạy trên STM32; STM32
chỉ cho qua các ký tự mà is_supported_esp_command() chấp nhận. Khung UART
luôn có dạng:

~~~text
CMD:<một-ký-tự>\n
~~~

Chế độ cũng là một dòng riêng:

~~~text
MODE:MANUAL\n
MODE:AUTO\n
~~~

Bảng ký tự ESP32 hiện hỗ trợ:

| Ký tự | Ý nghĩa ở ESP32 | Nguồn thường gặp |
| --- | --- | --- |
| w / b | Tiến / lùi | Cử chỉ lên hoặc dashboard W/S (S gửi b). |
| a / d | Quay trái / phải | Cử chỉ ngang hoặc dashboard A/D. |
| e / f | Strafe trái / phải | Dashboard Q/E hoặc Bluetooth. |
| s | Đứng/dừng hoặc đứng lên từ tư thế thấp | Lòng bàn tay, STOP, tư thế Stand. |
| z | Giữ tư thế, tắt hiệu chỉnh cân bằng | Nút Hold. |
| q / c | Ngồi / nằm | Cử chỉ và nút Sit/Prone. |
| p | Pace (gait hai hàng) | Giữ cho tương thích firmware; hiện không có nút riêng trên dashboard. |
| g / u / j | Vẫy / nhún / nhảy | Firmware hỗ trợ; giao diện hiện không đưa các nút này ra. |
| k | Đưa servo về tâm để hiệu chỉnh | Bảo trì. |
| h / l / n | Camera lên / xuống / trả về | Điều khiển camera cố định. |
| r / v / x | Quét lên / quét xuống / dừng quét | Máy trạng thái camera. |

Đừng nhầm s UART với phím PC S: dashboard ánh xạ phím S thành tên lệnh
backward, sau đó Python gửi ký tự UART b; nút STOP/phím Space mới gửi s.

## 11. Dashboard và trạng thái quan sát

ManualVideoServer phục vụ:

| URL | Phương thức | Chức năng |
| --- | --- | --- |
| /, /dashboard, /dashboard.html | GET | Giao diện web/dashboard.html. |
| /camera.mjpg | GET | Luồng multipart JPEG từ khung hình mới nhất. |
| /api/status | GET | JSON gồm chế độ, trạng thái robot, mặt/tay, camera scan, bóng và lỗi. |
| /api/mode | POST | JSON {"mode":"manual"} hoặc {"mode":"automatic"}. |
| /api/command | POST | JSON {"command":"forward"} hoặc tên lệnh dashboard. |
| /api/camera/restart | POST | Yêu cầu CameraStream đóng/mở lại webcam. |
| /api/face-gate | POST | JSON {"require_familiar_face":true/false}; bật/tắt cổng khuôn mặt. |
| /api/release | POST | Nhả quyền dashboard và gửi cơ chế dừng an toàn khi rời trang. |

Chỉ một địa chỉ LAN giữ lease tại một thời điểm; lease mặc định là 15 giây và
được gia hạn bởi các yêu cầu tiếp theo. Nếu trình duyệt khác truy cập khi lease
còn hiệu lực, API trả HTTP 409 thay vì âm thầm tranh quyền. Restart camera và
đổi yêu cầu khuôn mặt không phụ thuộc robot đang ở Manual hay Automatic; chúng
chỉ yêu cầu dashboard giữ lease.

Các trường quan trọng trong /api/status:

control_mode (automatic, manual-dashboard, manual-bluetooth), robot_state,
manual_source, require_familiar_face, face_status, hand_detected, camera_scan_state,
ball_detection_*, last_command, camera_live, camera_error,
camera_restart_count và camera_last_restart_reason.

require_familiar_face được đổi trong bộ nhớ khi dashboard gọi /api/face-gate;
thiết lập này không ghi xuống đĩa và sẽ trở về True sau khi khởi động lại
python/main.py.

## 12. Nơi chỉnh thông số

| Mục tiêu | File và biến tiêu biểu |
| --- | --- |
| Nhạy hơn/ít nhạy hơn với tay | python/main.py: tham số HandGestureDetector; POINT_VERTICAL_THRESHOLD. |
| Nhận mặt nghiêm ngặt hơn | python/face_gate.py: FACE_MATCH_THRESHOLD, ngưỡng YuNet. |
| Thời gian cho phép cử chỉ sau khi thấy người quen | python/main.py: FAMILIAR_GRACE_S. |
| Bắt bóng sớm/muộn, vùng giữa, timeout | python/ball_tracker.py: BALL_CONF_THRESHOLD, BALL_FOUND_RADIUS, BALL_CENTER_DEADZONE, NEVER_SEEN_TIMEOUT_S, SPIN_SEARCH_TIMEOUT_S. |
| Tốc độ và chiều camera | dog_esp32/dog_esp32.ino: CAMERA_SERVO_*, CAMERA_TILT_STEP_MS, CAMERA_SCAN_MAX_MS. |
| Quét tìm mặt/tay | python/main.py: nhóm CAMERA_SCAN_*. |
| Độ dài bước/gait | dog_esp32/dog_esp32.ino: stepLength, stepHeight, totalCycleDuration, dutyFactor, crabStep. |
| Tư thế/chuyển tư thế | ESP32: POSE_TRANSITION_*, SIT_EXIT_*. |
| Cân bằng | ESP32: bốn struct PID và POSE_TRANSITION_BALANCE_SCALE. |

Mỗi lần chỉ đổi một thông số, ghi lại giá trị cũ và kiểm tra khi robot được kê.
Đổi nhiều ngưỡng cùng lúc khiến không biết nguyên nhân cải thiện hay xấu đi.

## 13. Kiểm tra và chẩn đoán theo tầng

### Python/webcam

~~~bash
python3 -m pip install -r python/requirements.txt
python3 python/enroll_faces.py
python3 python/main.py
~~~

Kiểm tra đường dẫn camera, xem terminal có CAMERA ERROR hay không, rồi mở
http://<uno-q-ip>:8080/. Nếu khung hình đứng, thử nút Restart camera và kiểm tra
camera_last_restart_reason.

### Bridge/UART

Xác nhận sketch đã nạp, Serial1 ở 115200 và hai bên nối chéo TX/RX với GND
chung. Với robot được nâng, gửi STOP trước; chỉ khi thấy ESP32 nhận CMD:s mới
thử các lệnh khác.

### ESP32/servo

Mở Serial Monitor của ESP32. CONTROL_MODE=..., POSE_TRANSITION=... và dòng
[STS ...] giúp phân biệt lỗi lệnh, lỗi nội suy và lỗi servo. Nếu telemetry
cho thấy điện áp tụt mạnh hoặc camera/UNO Q reset khi đổi tư thế, dừng thử trên
sàn, kiểm tra nguồn 12 V, dây nguồn, đầu nối và GND chung; không cố chữa bằng
cách chỉ tăng tốc servo.

### Lỗi nhận diện

- Mặt luôn unfamiliar: chạy lại enroll_faces.py, dùng ảnh sáng và nhìn thẳng,
  rồi kiểm tra FACE_MATCH_THRESHOLD.
- Tay không được nhận: đưa cả bàn tay vào khung, tăng ánh sáng, quan sát
  hand_detected, rồi mới giảm ngưỡng.
- Bóng không được thấy: kiểm tra model 320×320, ánh sáng và panel vật thể; tên
  sports ball là lớp duy nhất điều khiển đuổi.

## 14. Cách thêm tính năng mới

1. Viết phần nhận diện hoặc luật quyết định trong Python; đặt kết quả thành một
   tên lệnh rõ ràng.
2. Thêm tên đó vào DASHBOARD_COMMANDS nếu dashboard cần dùng, và cập nhật
   sketch.ino để ký tự được cho phép.
3. Thêm case tương ứng trong ESP32; nếu là chuyển động mới, mô tả mục tiêu
   chân, tốc độ và watchdog.
4. Cập nhật bảng lệnh/tài liệu, kiểm tra python3 -m py_compile python/*.py và
   git diff --check.
5. Nạp từng tầng, thử với robot được kê, rồi mới thử chuyển động trên sàn.

## 15. Thuật ngữ cho người mới

- **Khung hình (frame):** một ảnh đơn lấy từ webcam.
- **Model/mô hình:** file ONNX và phép tính biến ảnh thành dự đoán.
- **Bounding box:** hình chữ nhật bao quanh mặt, tay, người hoặc bóng.
- **Cooldown:** khoảng thời gian khóa lệnh mới để tránh gửi lặp.
- **Bridge:** lớp giao tiếp giữa Python và chương trình STM32 trên UNO Q.
- **UART/Serial:** đường truyền byte; TX của bên này nối RX của bên kia.
- **Firmware:** chương trình chạy trực tiếp trên ESP32/STM32.
- **IK:** phép tính ngược từ tọa độ bàn chân sang góc khớp.
- **Gait:** nhịp phối hợp chân khi robot đi.
- **PID:** bộ điều chỉnh dùng sai số IMU để giảm nghiêng/lắc.
- **Telemetry:** số đo servo trả về, như vị trí, tốc độ, tải, điện áp và nhiệt độ.
