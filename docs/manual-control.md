# Điều khiển thủ công và dashboard

Tài liệu này mô tả hai cách điều khiển trực tiếp RoboDog: Bluetooth gửi lệnh
thẳng tới ESP32, và dashboard web gửi lệnh qua UNO Q. Cả hai đều dùng cùng
bảng ký tự ở firmware, nhưng cơ chế giữ quyền khác nhau. Nếu chưa từng dùng
API, bạn chỉ cần làm theo phần dashboard; các phần sau giải thích những gì
xảy ra bên trong để dễ chẩn đoán.

## 1. Hai nguồn điều khiển và quyền ưu tiên

### Bluetooth trên ESP32

Firmware ESP32 tự quản lý công tắc chế độ Bluetooth:

- Gửi chữ hoa M để vào manual.
- Gửi chữ hoa O để quay về automatic.
- Sau khi vào manual, gửi các byte chữ thường của lệnh chuyển động/camera.

Khi nhận M, ESP32 dừng chuyển động hiện tại, bỏ qua các khung CMD:<char>
từ UNO Q và gửi MODE:MANUAL về UNO Q. Khi nhận O, nó gửi MODE:AUTO và
cho phép lại lệnh từ UNO Q. Bluetooth được ưu tiên trong khoảng
BLUETOOTH_PRIORITY_MS = 2000 ms; dashboard không thể giành quyền khi
Bluetooth đang sở hữu manual.

### Dashboard trên UNO Q

Nút **Manual** trên trang web đưa Python vào STATE_MANUAL với nguồn
dashboard. Python dừng lệnh tự động, bật luồng video và gửi lệnh dashboard
qua Bridge. Trong trường hợp này Python yêu cầu ESP32 ở chế độ chấp nhận UART
(MODE:AUTO trong giao thức ESP32), vì dashboard đang điều khiển *thông qua
UNO Q*, không phải Bluetooth. Nút **Automatic** dừng lệnh dashboard và trả
Python về xử lý cử chỉ/khuôn mặt/bóng.

Đây là hai khái niệm khác nhau:

- **Automatic trên ESP32:** nhận lệnh từ UNO Q.
- **Automatic trong dashboard:** vòng lặp Python tự quyết định lệnh.
- **Manual bằng Bluetooth:** ESP32 nhận byte Bluetooth và chặn UNO Q.
- **Manual bằng dashboard:** Python tạm dừng nhận diện hành động, nhưng ESP32
  vẫn nhận khung UART do dashboard phát qua UNO Q.

Nếu Bluetooth gửi M trong lúc dashboard đang mở, callback
manual_mode_changed đổi nguồn thành bluetooth; mọi nút dashboard sẽ bị từ
chối cho tới khi gửi O.

## 2. Khởi động dashboard

1. Nạp sketch/sketch.ino lên STM32 của UNO Q và
   dog_esp32/dog_esp32.ino lên ESP32.
2. Trên UNO Q, cài thư viện và chạy ứng dụng theo hướng dẫn README:
   python3 -m pip install -r python/requirements.txt rồi
   python3 python/main.py (App Lab cũng gọi cùng main_loop).
3. Kết nối máy tính/điện thoại vào cùng mạng LAN riêng với UNO Q.
4. Mở:

   ~~~text
   http://<uno-q-ip>:8080/
   ~~~

Trang tải web/dashboard.html, tự làm mới /api/status và nhận video từ
/camera.mjpg. Không cần cài thêm web server.

## 3. Các nhóm điều khiển trên trang

Tất cả nút chuyển động là **Press & Hold**: nhấn giữ để gửi lệnh lặp, thả ra
để gửi STOP. Cách này giảm nguy cơ robot tiếp tục đi khi người điều khiển
buông tay hoặc mất kết nối.

| Nhóm | Nút trên dashboard | Phím PC | Ký tự UART gửi tới ESP32 |
| --- | --- | --- | --- |
| Di chuyển | Forward | W | w |
| Di chuyển | Backward | S | b |
| Di chuyển | Turn left / Turn right | A / D | a / d |
| Di chuyển | Strafe left / Strafe right | Q / E | e / f |
| Dừng | STOP | Space | s |
| Tư thế | Stand / Sit / Prone | X / C / V | s / q / c |
| Tư thế | Hold / Bounce | Z / B | z / u |
| Camera | Up / Down | I / K | h / l |
| Camera | Restart camera | Không có phím | Gọi API khởi động lại webcam, không phải lệnh servo. |
| Camera | Face gate toggle | Không có phím | Gọi API bật/tắt yêu cầu khuôn mặt quen. |

Điểm dễ nhầm là phím PC S có nghĩa **Backward**. Python nhận tên
backward rồi chuyển thành ký tự firmware b; ký tự s được dành cho STOP và tư
thế đứng. Tương tự, tên **Strafe** trên giao diện tương ứng với lệnh cũ
crab_left/crab_right trong API.

Firmware còn chấp nhận p (pace), g (wave), u (bounce) và j (jump). Bounce vẫn
có phím B; các lệnh legacy khác chưa có nút riêng trên giao diện hiện tại nhưng
có thể gửi bằng API hoặc Bluetooth nếu cần thử nghiệm.

Nút **Face gate toggle** không điều khiển servo. Nó đổi cài đặt
REQUIRE_FAMILIAR_FACE ngay lập tức và hoạt động trong cả **Manual** lẫn
**Automatic**. Khi nút hiển thị **Require familiar face**, cử chỉ chỉ được
chấp nhận sau khi FaceGate thấy người quen; khi hiển thị **Allow any face**,
mọi khuôn mặt (kể cả không nhận ra) đều có thể đi qua cổng cử chỉ. FaceGate vẫn
chạy để báo trạng thái nhận diện trong cả hai lựa chọn.
Thiết lập này chỉ có hiệu lực trong phiên chạy hiện tại; khi khởi động lại
python/main.py, giá trị mặc định REQUIRE_FAMILIAR_FACE = True được dùng lại.

## 4. Luồng một lần nhấn

Ví dụ nhấn giữ **Forward**:

1. JavaScript gửi POST /api/command với JSON
   {"command":"forward"}.
2. ManualVideoServer kiểm tra địa chỉ IP có giữ lease dashboard không.
3. main.py kiểm tra đang ở manual-dashboard, tra bảng DASHBOARD_COMMANDS và đổi
   tên forward thành w.
4. bridge.call("send_motor_command", "w") chạy trên STM32.
5. sketch.ino gửi CMD:w\n qua Serial1 ở 115200 baud.
6. ESP32 cập nhật gait. Khi đang giữ nút, bước 1 lặp theo bộ định thời của
   giao diện; khi thả, trình duyệt gửi stop → CMD:s\n.

Nếu dashboard không phải nguồn đang sở hữu, Python trả kết quả rejected thay
vì gửi lệnh. Nếu Bridge lỗi, API trả error và terminal in nguyên nhân.

## 5. API HTTP

Các yêu cầu POST phải có Content-Type: application/json. Những đường dẫn cần
quyền dashboard sẽ trả HTTP 409 và mã dashboard_busy nếu thiết bị khác đang
giữ lease.

| URL | Phương thức | Dữ liệu/kết quả |
| --- | --- | --- |
| / hoặc /dashboard.html | GET | Trang HTML. |
| /camera.mjpg | GET | multipart/x-mixed-replace gồm JPEG mới nhất. |
| /api/status | GET | JSON trạng thái robot, detector, camera và lệnh cuối. |
| /api/mode | POST | {"mode":"manual"} vào dashboard hoặc {"mode":"automatic"} trả về tự động. |
| /api/command | POST | {"command":"forward"}, tên lệnh hoặc một ký tự được hỗ trợ. |
| /api/camera/restart | POST | Đóng/mở lại webcam trong luồng CameraStream. |
| /api/face-gate | POST | {"require_familiar_face":true/false}; bật/tắt cổng khuôn mặt, không phụ thuộc chế độ robot. |
| /api/release | POST | Nhả lease; dashboard cố gửi STOP trước khi gọi endpoint này. |

Ví dụ chuyển sang dashboard manual (thay địa chỉ cho đúng UNO Q):

~~~bash
curl -X POST http://<uno-q-ip>:8080/api/mode \
  -H "Content-Type: application/json" \
  -d '{"mode":"manual"}'
~~~

Gửi lệnh tiến:

~~~bash
curl -X POST http://<uno-q-ip>:8080/api/command \
  -H "Content-Type: application/json" \
  -d '{"command":"forward"}'
~~~

Khi kết thúc, nên trả về automatic:

~~~bash
curl -X POST http://<uno-q-ip>:8080/api/mode \
  -H "Content-Type: application/json" \
  -d '{"mode":"automatic"}'
~~~

Bật cho phép mọi khuôn mặt:

~~~bash
curl -X POST http://<uno-q-ip>:8080/api/face-gate \
  -H "Content-Type: application/json" \
  -d '{"require_familiar_face":false}'
~~~

## 6. Lease một dashboard

Máy chủ nhận diện một trình duyệt bằng địa chỉ LAN nguồn. Lease mặc định là
15 giây (DASHBOARD_LEASE_S) và được gia hạn bằng các GET/POST tiếp theo.

- Thiết bị đầu tiên mở dashboard sẽ nhận lease.
- Thiết bị thứ hai thấy thông báo “đang được sử dụng” và không thể xem feed,
  trạng thái hoặc gửi lệnh.
- Khi trang đóng, JavaScript gọi /api/release bằng sendBeacon; nếu mạng mất,
  lease tự hết hạn sau tối đa 15 giây.
- Nếu rời trang trong lúc giữ nút, cơ chế releaseDashboard() cố gửi STOP trước
  khi nhả lease.

Đây là khóa truy cập đơn giản, không phải xác thực người dùng. Chỉ mở cổng trên
mạng tin cậy; không đưa cổng 8080 ra Internet công cộng.

## 7. Những gì vẫn chạy trong manual

Khi Python vào STATE_MANUAL:

- CameraStream và MJPEG vẫn chạy để người điều khiển nhìn thấy robot.
- Cứ ba lượt vòng lặp, detector bàn tay vẫn chạy và cập nhật hand_detected
  cho panel trạng thái.
- FaceGate, phát hiện người và BallTracker không ra quyết định chuyển động.
- Camera scan tự động bị hủy; các nút Up/Down là bước chỉnh thủ công có thời
  gian, không phải góc tuyệt đối.
- Khi rời manual, Python gửi STOP và camera về trung tính, rồi đặt cooldown
  chuyển tư thế trước khi nhận lệnh tự động mới.

Vì vậy thấy “Hand: Detected” trên dashboard khi manual không có nghĩa robot sẽ
làm theo cử chỉ; đó chỉ là thông tin giám sát.

## 8. Khởi động lại webcam khi servo làm sụt nguồn

Nút **Restart camera** gọi /api/camera/restart. Hàm force_restart_camera() đặt
cờ để luồng CameraStream giải phóng cv2.VideoCapture rồi mở lại ở lần lặp kế
tiếp; thao tác không chặn vòng lặp điều khiển. Endpoint này chỉ cần dashboard
đang giữ lease, nên dùng được cả khi robot ở **Automatic** hoặc **Manual**.
Camera cũng tự thử lại sau năm lần đọc lỗi liên tiếp, với thời gian chờ 0,5 giây.

Khởi động lại webcam không sửa được nguyên nhân nguồn yếu. Nếu camera tắt cùng
lúc servo kéo tải, hãy dừng robot, kiểm tra pin/nguồn 12 V, dây cấp servo và
GND chung trước khi tiếp tục.

## 9. Kiểm tra an toàn

1. Kê robot lên giá, thử STOP và kiểm tra chiều w/b/a/d trước.
2. Vào Manual bằng một thiết bị duy nhất; xác nhận thiết bị thứ hai bị từ chối.
3. Nhấn giữ mỗi lệnh trong khoảng một giây rồi thả; kiểm tra servo dừng ngay.
4. Thử Stand → Sit → Stand và Stand → Prone → Sit với nguồn có đồng hồ đo;
   quan sát dòng telemetry ESP32.
5. Thử camera Up/Down trong bước ngắn; không để cơ cấu chạm điểm chặn.
6. Chọn Automatic và xác nhận Python tiếp tục nhận diện mặt/tay/bóng.
7. Nếu lệnh không chạy, xem thứ tự nhật ký:
   DASHBOARD_COMMAND_ACCEPTED → CMD:<char> ở UART → log trạng thái ESP32.

Không chạy thử trên sàn khi chưa có nút dừng dễ tiếp cận. Servo 12 V có thể hút
dòng lớn lúc đổi tư thế; camera/UNO Q reset là dấu hiệu cần xử lý nguồn, không
phải lý do để tăng tốc hoặc kéo dài lệnh.

## 10. Mở rộng giao diện

- Thêm tên nút vào DASHBOARD_COMMANDS trong python/main.py.
- Thêm ánh xạ phím vào keyboardCommands và nhãn song ngữ trong
  web/dashboard.html.
- Bảo đảm ký tự mới được is_supported_esp_command() trong sketch.ino cho phép
  và có case tương ứng trong ESP32.
- Cập nhật bảng ở tài liệu này, chạy kiểm tra cú pháp Python/HTML và thử lại
  cơ chế lease, STOP khi thả nút và phản hồi lỗi.
