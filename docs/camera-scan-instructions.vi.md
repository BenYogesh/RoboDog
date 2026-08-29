# Hướng dẫn quét camera theo thời gian

Bản tiếng Anh: [camera-scan-instructions.md](camera-scan-instructions.md)

Hướng dẫn này mô tả tính năng nâng camera được dùng chung bởi bộ điều khiển
động cơ ESP32 và ứng dụng thị giác Arduino UNO Q. Tính năng dành cho servo quay
liên tục 360 độ kiểu SG90, nối với GPIO 27 của ESP32.

## Tính năng hoạt động như thế nào

Khi robot đang đứng và camera nhìn thấy một người có khung bao chỉ chứa phần
chân, UNO Q yêu cầu ESP32 xoay camera lên trên. Luồng xử lý phụ thuộc vào
`REQUIRE_FAMILIAR_FACE`:

1. Khi giá trị là `True`, quá trình quét lên chỉ tìm **khuôn mặt quen thuộc**.
2. Khi tìm thấy khuôn mặt quen thuộc và bàn tay đã xuất hiện, camera giữ nguyên
   vị trí để nhận diện cử chỉ.
3. Khi tìm thấy khuôn mặt quen thuộc nhưng chưa thấy bàn tay, ESP32 dừng lại,
   rồi quét xuống để tìm bàn tay. Thời gian quét xuống bị giới hạn bởi thời gian
   đã quét lên, nên không thể cố ý đi qua vị trí trung tính.
4. Camera dừng lần quét hiện tại sau hai lần liên tiếp phát hiện bàn tay; điều
   này giảm việc dừng nhầm. Camera giữ vị trí khi bàn tay còn trong khung hình.
5. Khi bàn tay biến mất trong một giây, ESP32 chỉ quay về theo phần độ lệch có
   dấu còn lại. Ví dụ: quét lên 0,9 giây rồi quét xuống 0,6 giây sẽ quay về
   khoảng 0,3 giây theo chiều xuống, thay vì quay đủ 0,9 giây.
6. Khi `REQUIRE_FAMILIAR_FACE` là `False`, robot bỏ qua giai đoạn khuôn mặt và
   chỉ quét lên để tìm bàn tay; khuôn mặt không dừng được quá trình quét đó.

ESP32 cũng dừng mọi lần quét liên tục theo chiều lên hoặc xuống sau 1,8 giây.
Đây là giới hạn an toàn trong trường hợp ứng dụng UNO Q hoặc đường UART ngừng
phản hồi.

Vì SG90 là servo quay liên tục nên nó không có phản hồi vị trí. Việc quay về
dựa trên thời gian, không dựa trên phép đo góc tuyệt đối. Một lượng lệch nhỏ là
bình thường và nên được sửa bằng cách hiệu chỉnh giá trị dừng và tốc độ servo.

## Nối dây

| Kết nối | Nối tới |
| --- | --- |
| Tín hiệu SG90 | GPIO 27 của ESP32 |
| Nguồn SG90 | Nguồn 5 V ngoài ổn định |
| GND SG90 | GND nguồn và GND ESP32 |
| UNO Q TX (D1) | UART2 RX ESP32, GPIO 16 |
| UNO Q RX (D0) | UART2 TX ESP32, GPIO 17 |
| GND UNO Q | GND ESP32 |

**Không** cấp nguồn SG90 từ chân 3.3 V của ESP32. Dùng nguồn 5 V phù hợp và
đảm bảo GND của nguồn nối chung với ESP32. Trước khi nối các chân UART của UNO
Q, xác nhận mức logic UART của board tương thích với đầu vào 3.3 V của ESP32.

## File và trách nhiệm

| File | Trách nhiệm |
| --- | --- |
| `python/main.py` | Phát hiện chân, bàn tay và khuôn mặt; quản lý state machine quét; gửi lệnh qua Arduino Bridge. |
| `sketch/sketch.ino` | Kiểm tra lệnh một ký tự và chuyển tiếp chúng thành frame `CMD:<char>` qua `Serial1`. |
| ESP32 `dog_esp32.ino` | Điều khiển GPIO 27, ghi thời lượng quét, thực hiện quay về ngược chiều và giới hạn lần quét không có giám sát ở 1,8 giây. |

## Lệnh camera

Tất cả lệnh camera đi từ UNO Q tới ESP32 dưới dạng `CMD:<character>` rồi đến
ký tự xuống dòng.

| Lệnh | Hành động trên ESP32 | Nơi tạo lệnh thông thường |
| --- | --- | --- |
| `h` | Bước nâng theo thời gian tới góc nhìn cố định | Điều khiển camera thủ công/cố định |
| `l` | Bước hạ theo thời gian tới góc nhìn cố định | Chế độ camera đuổi bóng |
| `n` | Quay về theo độ lệch có dấu còn lại; nếu không có thì yêu cầu trung tính logic | Mất mục tiêu, hết thời gian quét |
| `r` | Bắt đầu quét liên tục lên và bắt đầu ghi thời gian | Phát hiện người chỉ có phần chân |
| `v` | Bắt đầu quét liên tục xuống để tìm bàn tay và trừ thời gian này khỏi độ lệch | Tìm thấy khuôn mặt quen thuộc nhưng chưa thấy bàn tay |
| `x` | Dừng lần quét đang hoạt động, giữ độ lệch có dấu và giữ vị trí | Tìm thấy khuôn mặt quen thuộc hoặc phát hiện bàn tay đủ xác nhận |

`h`, `l` và `n` chỉ là các góc nhìn logic. Servo quay liên tục không biết góc
vật lý của mình, vì vậy đây là các chuyển động ngắn theo thời gian chứ không
phải vị trí tuyệt đối.

## Các giá trị cần hiệu chỉnh

### ESP32: chuyển động servo

Sửa các giá trị sau trong `dog_esp32.ino`:

```cpp
constexpr int CAMERA_SERVO_STOP_US = 1500;
constexpr int CAMERA_SERVO_UP_US = 1700;
constexpr int CAMERA_SERVO_DOWN_US = 1300;
constexpr unsigned long CAMERA_TILT_STEP_MS = 250;
constexpr unsigned long CAMERA_SCAN_MAX_MS = 1800;
```

- `CAMERA_SERVO_STOP_US`: Điều chỉnh tới khi servo đứng yên hoàn toàn. Bắt đầu
  với 1500 microsecond, sau đó thay đổi từng bước nhỏ 5–10 microsecond.
- `CAMERA_SERVO_UP_US` và `CAMERA_SERVO_DOWN_US`: Đặt độ lệch bằng nhau ở hai
  phía đối diện của giá trị dừng. Nếu quét làm ống kính đi xuống, đổi chỗ hai
  giá trị này.
- `CAMERA_TILT_STEP_MS`: Thời lượng của một chuyển động thủ công `h` hoặc `l`.
  Giữ giá trị nhỏ vì chuyển động này không được điều khiển theo vị trí.
- `CAMERA_SCAN_MAX_MS`: Giới hạn an toàn theo hành trình cơ khí. Đặt thấp hơn
  thời gian có thể làm ngàm camera chạm cữ cứng.

### UNO Q: hành vi thị giác

Sửa các giá trị gần nhóm hằng số lệnh camera trong `python/main.py`:

```python
CAMERA_SCAN_TIMEOUT_S = 1.5
CAMERA_TARGET_LOST_S = 1.0
CAMERA_HAND_CONFIRMATIONS = 2
CAMERA_SCAN_FACE_CHECK_PERIOD_S = 0.25
CAMERA_RETURN_SETTLE_S = 0.1
```

- Giữ `CAMERA_SCAN_TIMEOUT_S` ngắn hơn `CAMERA_SCAN_MAX_MS / 1000`.
- Tăng `CAMERA_HAND_CONFIRMATIONS` nếu phát hiện bàn tay nhầm làm dừng quá
  trình quét. Chỉ giảm xuống `1` khi bộ nhận diện bỏ sót mục tiêu quá thường xuyên.
- Tăng `CAMERA_TARGET_LOST_S` nếu ống kính quay về trong khi người vẫn còn đó
  nhưng bị che khuất trong thời gian ngắn.
- Chỉ giảm `CAMERA_SCAN_FACE_CHECK_PERIOD_S` khi UNO Q còn đủ CPU để nhận diện
  khuôn mặt thường xuyên hơn.
- `CAMERA_RETURN_SETTLE_S` là khoảng bảo vệ bổ sung ở phần mềm sau thời gian
  quay về đã tính. Nó ngăn một lần phát hiện chân mới làm gián đoạn ESP32 khi
  servo quay liên tục đang trở về trung tính.

## Quy trình triển khai và kiểm tra

1. Đỡ robot sao cho chân không thể bước, rồi nạp firmware ESP32.
2. Triển khai project UNO Q có cả `python/main.py` và `sketch/sketch.ino` bằng
   quy trình App Lab thông thường.
3. Cấp nguồn cho servo camera từ nguồn 5 V ngoài và xác nhận các GND được nối
   chung.
4. Khởi động ứng dụng thị giác. Kiểm tra OLED không báo `CAM FAILED`.
5. Với `REQUIRE_FAMILIAR_FACE = True`, đứng ở vị trí camera chỉ nhìn thấy chân.
   OLED phải báo `Scanning for familiar face` và ống kính phải đi lên.
6. Để một khuôn mặt quen thuộc đi vào khung hình nhưng không giơ tay. OLED phải
   báo `Familiar face found; scanning down for hand`; ống kính dừng ngắn rồi đi
   xuống. Thời gian đi xuống không được dài hơn thời gian đã đi lên.
7. Giơ tay trong lúc đang quét xuống. OLED phải báo `Hand found`. Đưa tay ra
   khỏi khung hình; sau khoảng một giây, camera chỉ được quay về theo phần thời
   gian lên trừ thời gian xuống còn lại.
8. Đặt `REQUIRE_FAMILIAR_FACE = False` rồi lặp lại. OLED phải báo `Scanning for
   hand`; chỉ khuôn mặt không được dừng quá trình quét, còn bàn tay đã xác nhận
   phải dừng được nó.
9. Lặp lại khi không có mục tiêu dự kiến trong khung hình. Hệ thống phải báo hết
   thời gian quét khuôn mặt hoặc bàn tay và quay về sau khoảng 1,5 giây, hoặc
   sớm hơn đối với quét xuống.
10. Nếu hướng quay bị ngược, đổi chỗ `CAMERA_SERVO_UP_US` và
    `CAMERA_SERVO_DOWN_US`, nạp lại ESP32 rồi kiểm tra lại.

## Xử lý sự cố

| Hiện tượng | Nguyên nhân có thể và cách xử lý |
| --- | --- |
| Servo bò dần khi cần giữ vị trí | Hiệu chỉnh `CAMERA_SERVO_STOP_US` từng bước 5–10 microsecond. |
| Servo quay sai hướng | Đổi chỗ giá trị độ rộng xung UP và DOWN. |
| Servo kêu, reset hoặc làm ESP32 khởi động lại | Dùng nguồn 5 V ngoài mạnh hơn và kiểm tra GND chung. |
| Camera chạm cữ cơ khí | Lập tức giảm `CAMERA_SCAN_MAX_MS` và `CAMERA_SCAN_TIMEOUT_S`. |
| Quá trình quét không bắt đầu | Xác nhận bộ nhận diện người thấy khung chỉ có chân, robot đang đứng và frame UART từ UNO Q tới được ESP32. |
| Quét bắt đầu nhưng không dừng khi có mục tiêu | Kiểm tra lấy nét/ánh sáng camera, model bàn tay/khuôn mặt và giảm số lần xác nhận bàn tay nếu cần. |
| ESP32 in `Camera scan safety timeout.` | UNO Q không gửi lệnh dừng hoặc quay về đúng lúc; kiểm tra ứng dụng thị giác, kết nối Bridge và dây UART. |

Khi chỉnh thời gian servo, chỉ thay đổi một giá trị mỗi lần rồi kiểm tra lại
trong khi robot được đỡ. Không tăng thời lượng cho tới khi đã xác nhận ngàm
camera còn đủ hành trình cơ khí.
