# RoboDog

RoboDog là phần mềm điều khiển một robot bốn chân dùng **Arduino UNO Q** làm
bộ xử lý hình ảnh và **ESP32** làm bộ điều khiển chuyển động. Tài liệu này là
điểm bắt đầu cho người chưa quen với Python, Arduino hoặc giao tiếp UART: đọc
từ trên xuống dưới để hiểu robot cần những gì, chương trình khởi động ra sao và
mỗi thư mục chịu trách nhiệm cho phần nào.

## Robot làm được gì?

- Phát hiện cử chỉ bàn tay để điều khiển khi ở chế độ tự động.
- Nhận diện khuôn mặt và, theo mặc định, chỉ nhận lệnh của người đã đăng ký.
- Phát hiện quả bóng bằng YOLOv8n rồi tự quay/đi theo bóng.
- Nhận lệnh thủ công bằng Bluetooth trên ESP32.
- Nhận lệnh thủ công bằng trình duyệt cùng luồng webcam trực tiếp trên mạng LAN.
- Điều khiển tư thế, dáng đi, cân bằng IMU và camera nghiêng bằng firmware ESP32.

Nhánh này không chứa chức năng âm thanh hoặc màn hình OLED. UNO Q vẫn có thể
hiển thị biểu cảm đơn giản trên ma trận LED tích hợp vì đó là phần của
`sketch/sketch.ino`, không phải một luồng âm thanh hay OLED.

## Bức tranh tổng thể

Hãy coi hệ thống như ba tầng nối tiếp nhau:

| Tầng | Phần cứng/chương trình | Việc chính |
| --- | --- | --- |
| Nhìn và quyết định | Chip Qualcomm trên UNO Q, Python trong `python/` | Đọc webcam, chạy mô hình bàn tay/khuôn mặt/bóng, chọn trạng thái và lệnh. |
| Cầu nối | Chip STM32 trên UNO Q, `sketch/sketch.ino` | Nhận lời gọi Bridge từ Python, kiểm tra lệnh, chuyển khung UART tới ESP32 và báo lại chế độ Bluetooth. |
| Chuyển động | ESP32, `dog_esp32/dog_esp32.ino` | Đổi ký tự thành quỹ đạo chân, tính IK, cân bằng bằng MPU6050, rồi gửi vị trí tới 12 servo ST3215. |

Ví dụ: khi Python thấy bóng ở bên trái, nó chọn lệnh `a`; Bridge đóng gói
thành `CMD:a\n`; ESP32 giải mã và tạo chuyển động quay trái. Chi tiết luồng dữ
liệu nằm trong [Cấu trúc và luồng dữ liệu](docs/system-structure-dataflow.md).

## Cây thư mục cần biết

| Đường dẫn | Vai trò |
| --- | --- |
| `python/main.py` | Điểm vào của ứng dụng Python, vòng lặp camera, máy trạng thái, điều phối mọi bộ phát hiện và dashboard. |
| `python/detector.py` | Phát hiện bàn tay bằng các mô hình MediaPipe ONNX và phân loại cử chỉ. |
| `python/face_gate.py` | Phát hiện khuôn mặt bằng YuNet, so khớp đặc trưng bằng SFace và trả về `familiar`, `unfamiliar` hoặc `none`. |
| `python/enroll_faces.py` | Đọc ảnh trong `python/known_faces/` và tạo cơ sở dữ liệu `known_faces_db.json`. |
| `python/ball_tracker.py` | Chạy YOLOv8n, phát hiện bóng/người và biến vị trí bóng thành lệnh đi hoặc quay. |
| `python/manual_video.py` | Máy chủ HTTP cho dashboard, MJPEG webcam, API trạng thái/lệnh và quyền sở hữu dashboard. |
| `python/face_models/` | Hai mô hình ONNX YuNet và SFace. |
| `python/hand_models/` | Mô hình lòng bàn tay/bàn tay và các tệp hỗ trợ MediaPipe. |
| `python/yolov8n.onnx` | Mô hình YOLO dùng trong chế độ đuổi bóng. |
| `python/known_faces/` | Ảnh mẫu, chia theo thư mục tên người. |
| `sketch/sketch.ino` | Chương trình chạy trên STM32 của UNO Q: Bridge, `Serial1` và ma trận LED. |
| `dog_esp32/dog_esp32.ino` | Firmware ESP32: Bluetooth, UART, servo camera, gait, IK, PID, tư thế và telemetry. |
| `web/dashboard.html` | Giao diện web song ngữ; mặc định có thể chuyển giữa English và Tiếng Việt. |
| `app.yaml` | Cấu hình App Lab, gồm cổng dashboard `8080`. |
| `python/requirements.txt` | Hai thư viện Python chính: OpenCV headless và NumPy. |

## Chuẩn bị phần cứng và an toàn

1. Gắn webcam vào UNO Q và xác định đường dẫn thiết bị. Mặc định chương trình
   dùng:

   ```text
   /dev/v4l/by-id/usb-046d_C270_HD_WEBCAM_E21C4540-video-index0
   ```

   Có thể thay bằng biến môi trường `UNO_Q_CAMERA_PATH`.
2. Nối UART UNO Q–ESP32 đúng mức logic 3,3 V và nối chung GND. `sketch` dùng
   D0/D1 (`Serial1`), còn ESP32 dùng UART2 ở GPIO16 (RX) và GPIO17 (TX).
3. Cấp nguồn servo từ nguồn ngoài phù hợp với servo 12 V; không lấy dòng servo
   từ chân 3,3 V của UNO Q hoặc ESP32. Nguồn servo, ESP32 và UNO Q phải có
   điểm tham chiếu GND chung. Sụt áp, dây nhỏ hoặc nguồn không đủ dòng có thể
   làm camera/UNO Q khởi động lại khi tư thế đổi.
4. Khi nạp hoặc tinh chỉnh gait, kê robot lên giá để chân không chạm sàn. Chỉ
   đặt robot xuống sau khi đã kiểm tra chiều servo và nút dừng.

Sơ đồ chân, giới hạn chuyển tư thế và cách thử an toàn được giải thích trong
[Tài liệu gait ESP32](docs/esp32-gait-control.md) và [Tài liệu quét camera](docs/camera-scan-instructions.md).

## Cài đặt và chạy lần đầu

### 1. Cài thư viện Python

Trên Linux của UNO Q, chạy từ thư mục gốc:

```bash
python3 -m pip install -r python/requirements.txt
```

Ứng dụng cần OpenCV có các API `FaceDetectorYN` và `FaceRecognizerSF`; các
file ONNX đã có trong kho nên không cần tải thêm mô hình khi triển khai đúng
thư mục.

### 2. Đăng ký khuôn mặt (tùy chọn nhưng được bật mặc định)

Tạo một thư mục cho mỗi người và đặt ảnh rõ mặt vào đó, ví dụ
`python/known_faces/lam/`. Ảnh nên chụp bằng chính webcam, ở khoảng cách và
ánh sáng gần với lúc robot hoạt động. Sau đó chạy:

```bash
python3 python/enroll_faces.py
```

Lệnh tìm khuôn mặt lớn nhất trong từng ảnh, lấy vector đặc trưng SFace, trung
bình các vector của cùng một người và ghi vào
`python/known_faces_db.json`. Nếu chưa có file này, chương trình vẫn chạy nhưng
mọi khuôn mặt đều bị coi là `unfamiliar`.

### 3. Nạp hai chương trình

- Dùng Arduino App Lab theo quy trình của dự án để triển khai `sketch/` và
  `python/` lên UNO Q; `app.yaml` mở cổng 8080.
- Nạp `dog_esp32/dog_esp32.ino` bằng Arduino IDE/ESP32 toolchain vào ESP32.

Sau khi nguồn ổn định, có thể chạy trực tiếp để chẩn đoán:

```bash
python3 python/main.py
```

Trong triển khai App Lab, `App.run(user_loop=main_loop)` gọi cùng vòng lặp này.
Terminal sẽ in các dòng như `ROBOT_STATUS`, `CAMERA ...`, `DASHBOARD ...` và
telemetry servo; đó là nhật ký dễ nhất để biết tầng nào đang gặp vấn đề.

### 4. Mở dashboard

Từ máy tính/điện thoại trên cùng mạng LAN, mở:

```text
http://<uno-q-ip>:8080/
```

Chọn **Manual** để dashboard giữ quyền điều khiển, rồi nhấn giữ nút chuyển
động. Chọn **Automatic** để trả quyền cho vòng lặp nhận diện. Bluetooth có
quyền ưu tiên: khi ESP32 nhận `M`, dashboard không thể giành quyền cho tới khi
ESP32 nhận `O`. Xem [Điều khiển thủ công](docs/manual-control.md) để biết API,
phím PC, lease một thiết bị và cách xử lý camera.

## Trình tự khởi động và vòng đời lệnh

1. `sketch/sketch.ino` mở `Serial1`, khởi động Bridge/ma trận LED và đăng ký
   các hàm an toàn cho Python gọi.
2. `python/main.py` tạo `CameraStream`, mở webcam trong một luồng riêng và
   khởi động máy chủ dashboard.
3. Các bộ phát hiện bàn tay, khuôn mặt và bóng được tạo; `verify_face_models()`
   kiểm tra model/API khuôn mặt trước khi vòng lặp chạy.
4. Mỗi vòng lặp lấy khung hình mới nhất. Nhánh `MANUAL_CONTROL` chỉ giữ luồng
   webcam và kiểm tra bàn tay; nhánh `CHASING` ưu tiên BallTracker; nhánh còn
   lại xử lý khuôn mặt, cử chỉ và quét camera.
5. Python gửi một ký tự lệnh qua Bridge. STM32 chuyển thành `CMD:<char>\n` và
   ESP32 cập nhật mục tiêu chuyển động. ESP32 tự gửi `MODE:MANUAL` hoặc
   `MODE:AUTO` về UNO Q khi quyền Bluetooth thay đổi.

Mô tả chi tiết máy trạng thái và bảng lệnh có trong
[Cấu trúc và luồng dữ liệu](docs/system-structure-dataflow.md).

## Kiểm tra tối thiểu sau khi triển khai

- [ ] Nâng robot lên, bật nguồn và xác nhận 12 ID servo/hướng quay đúng.
- [ ] Kiểm tra terminal không có `CAMERA ERROR`; mở được `/camera.mjpg`.
- [ ] Thử `s`/STOP trước, rồi thử một lệnh đi ngắn khi robot vẫn được đỡ.
- [ ] Vào Manual bằng Bluetooth hoặc dashboard; xác nhận chỉ nguồn đang sở hữu
  mới điều khiển được.
- [ ] Thử tư thế `s` (đứng), `q` (ngồi), `c` (nằm) và quay lại đứng; quan sát
  `POSE_TRANSITION` và điện áp nguồn.
- [ ] Với khuôn mặt đã đăng ký, kiểm tra cử chỉ; với người lạ, xác nhận robot
  bỏ qua cử chỉ khi `REQUIRE_FAMILIAR_FACE = True`.
- [ ] Nắm tay để vào đuổi bóng; kiểm tra panel **Ball detection** và danh sách
  vật thể trên dashboard; chỉ xuống để dừng.
- [ ] Đặt người ở ngoài khung để kiểm tra camera scan, nhưng tránh để servo
  camera chạm điểm cơ khí.

## Khi cần sửa hoặc mở rộng

Giữ mỗi thay đổi trong đúng tầng: thêm nhận diện ở `python/`, thêm tên lệnh ở
`main.py` và `sketch.ino`, rồi thêm xử lý ký tự trong ESP32. Sau mỗi thay đổi,
chạy kiểm tra cú pháp Python, xem `git diff --check`, nạp lên UNO Q/ESP32 và
thử với robot được kê. Không ghi mật khẩu Wi‑Fi, token hoặc thông tin bí mật
vào kho mã.

## Tài liệu chuyên sâu

- [Cấu trúc code và luồng dữ liệu](docs/system-structure-dataflow.md): giải
  thích từ webcam tới servo, máy trạng thái, API và cách thêm tính năng.
- [Điều khiển thủ công và dashboard](docs/manual-control.md): Bluetooth,
  dashboard, phím PC, quyền truy cập và luồng webcam.
- [Quét camera bằng servo quay liên tục](docs/camera-scan-instructions.md):
  dây nối, máy trạng thái quét, giới hạn thời gian và cách chỉnh.
- [Gait, IK, tư thế và cân bằng ESP32](docs/esp32-gait-control.md): hình học
  chân, quỹ đạo, chuyển tư thế, PID, telemetry và hiệu chỉnh.
