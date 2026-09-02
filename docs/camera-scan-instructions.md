# Quét camera bằng servo quay liên tục

Tính năng này giúp RoboDog đưa người đang đứng ngoài khung hình vào tầm nhìn.
BallTracker nhận ra một hộp người chỉ có phần chân; Python ra lệnh cho servo
camera nghiêng lên, tìm khuôn mặt quen hoặc bàn tay, rồi đưa camera về gần vị
trí ban đầu sau khi có mục tiêu hoặc hết thời gian.

## 1. Giới hạn quan trọng của cơ cấu

Servo ở GPIO27 là loại quay liên tục (continuous rotation), không phải servo góc
có phản hồi vị trí. Giá trị xung chỉ quyết định **chiều và tốc độ quay**:

- Xung gần 1500 micro giây là dừng.
- Không có cảm biến cho biết camera đang ở góc bao nhiêu.
- Vì vậy các lệnh Up, Down và Return đều là chuyển động có thời gian; n
  không thể bảo đảm góc tuyệt đối nếu servo trượt, nguồn thay đổi hoặc cơ cấu bị
  kẹt.

Luôn kê robot, giữ cơ cấu camera không chạm điểm chặn và thay đổi từng thông số
một lần. Nếu servo quay liên tục sau khi phải dừng, chưa nên cho robot chạy.

## 2. Phần cứng và dây nối

| Thành phần | Kết nối |
| --- | --- |
| Servo camera SG90 continuous-rotation, dây tín hiệu | ESP32 GPIO27 |
| Nguồn servo | Nguồn ngoài 5 V đủ dòng; không lấy từ chân 3,3 V |
| Mass servo | Nối chung GND với ESP32 và UNO Q |
| UNO Q TX D1 | ESP32 UART2 RX GPIO16 |
| UNO Q RX D0 | ESP32 UART2 TX GPIO17 |
| UART UNO Q–ESP32 | 115200 baud, TX/RX nối chéo, chung GND |

Servo chân và servo camera có thể gây sụt áp lớn cùng lúc. Dây nguồn phải đủ
tiết diện, nguồn 5 V camera phải tách khỏi nguồn logic nếu cần, nhưng GND vẫn
phải chung. Trước khi nối UART, kiểm tra mức logic của hai board là 3,3 V.

## 3. Máy trạng thái trong Python

Các hàm start_camera_scan, stop_camera_scan, start_hand_scan_below_face,
return_camera_from_scan và update_camera_scan ở python/main.py giữ một biến
thời gian có dấu camera_net_offset_s. Dấu dương là đã đi lên nhiều hơn đi
xuống; dấu âm là ngược lại.

| Trạng thái | Khi nào vào | Việc thực hiện |
| --- | --- | --- |
| IDLE | Không quét. Nếu thấy người chỉ có chân, bắt đầu r. | Không tự di chuyển camera. |
| FACE_SCANNING | REQUIRE_FAMILIAR_FACE = True. | Quét lên, kiểm tra FaceGate thường xuyên hơn. Mặt quen làm dừng x. |
| HAND_SCANNING | Không yêu cầu mặt, hoặc đã thấy mặt quen. | Quét tay; sau mặt quen, hướng là xuống và thời gian không vượt phần đã đi lên. |
| LOCKED | Đã thấy mặt/tay. | Giữ camera tại chỗ; mất tay/mặt đủ lâu mới trả về. |
| RETURNING | Timeout, mất mục tiêu hoặc đã xác nhận cử chỉ. | Gửi n: ESP32 dùng thời gian còn lại đã ghi để quay về gần trung tính. |

Khi REQUIRE_FAMILIAR_FACE = True, mặt lạ không dừng giai đoạn tìm mặt. Khi
đặt False, robot bỏ qua bước mặt và quét tay ngay.

Một tay phải được phát hiện trong hai lần kiểm tra liên tiếp
(CAMERA_HAND_CONFIRMATIONS = 2) mới được coi là xác nhận. Điều này giảm việc
một khung hình nhiễu làm camera dừng quá sớm.

## 4. Lệnh camera trên UART

Mọi lệnh đi theo khung CMD:<char>\n từ UNO Q tới ESP32:

| Ký tự | Ý nghĩa | Nguồn |
| --- | --- | --- |
| h | Nghiêng lên một bước cố định | Nút Up thủ công trên dashboard. |
| l | Nghiêng xuống một bước cố định | Nút Down thủ công trên dashboard. |
| n | Trả về bằng thời gian còn lại đã ghi | Python khi scan kết thúc hoặc dashboard yêu cầu trung tính. |
| r | Bắt đầu quét liên tục lên và ghi thời gian | Phát hiện người chỉ có chân. |
| v | Bắt đầu quét liên tục xuống | Tìm tay sau mặt quen hoặc chế độ hand-only. |
| x | Dừng quét đang chạy và giữ vị trí | Đã thấy mặt/tay. |

h, l, n chỉ là “góc nhìn logic”. Vì servo không báo vị trí, không gọi chúng là
góc tuyệt đối.

## 5. Thông số hiện tại

### Firmware ESP32

Trong dog_esp32/dog_esp32.ino:

~~~cpp
CAMERA_SERVO_STOP_US = 1500
CAMERA_SERVO_UP_US = 1440
CAMERA_SERVO_DOWN_US = 1560
CAMERA_TILT_STEP_MS = 100
CAMERA_SCAN_MAX_MS = 500
~~~

- CAMERA_SERVO_STOP_US: chỉnh từng 5–10 micro giây cho tới khi servo đứng yên
  hoàn toàn.
- CAMERA_SERVO_UP_US và CAMERA_SERVO_DOWN_US: cơ cấu mới quay ngược hướng,
  nên Up thấp hơn trung tính và Down cao hơn. Nếu chiều thực tế ngược, đổi hai
  giá trị; nếu quá nhanh, giảm độ lệch khỏi 1500.
- CAMERA_TILT_STEP_MS: thời lượng một bước Up/Down thủ công.
- CAMERA_SCAN_MAX_MS: khóa an toàn vật lý cho mỗi lần quét liên tục; đặt thấp
  hơn thời gian cần để cơ cấu chạm chặn.

### Ứng dụng Python

Trong python/main.py:

~~~python
CAMERA_SCAN_TIMEOUT_S = 1.5
CAMERA_TARGET_LOST_S = 1.0
CAMERA_HAND_CONFIRMATIONS = 2
CAMERA_SCAN_FACE_CHECK_PERIOD_S = 0.25
CAMERA_RETURN_SETTLE_S = 0.1
~~~

CAMERA_SCAN_TIMEOUT_S là giới hạn logic của Python; CAMERA_SCAN_MAX_MS là giới
hạn phần cứng trong ESP32. Giá trị kho hiện tại là 1,5 giây so với 500 ms, vì
vậy ESP32 có thể tự dừng trước khi Python hết timeout. Khi hiệu chỉnh một cơ
cấu mới, nên đặt hai giới hạn nhất quán (Python không nên chờ lâu hơn hành
trình an toàn của ESP32) và thử lại từ thời lượng nhỏ.

## 6. Các file liên quan

| File | Phần việc |
| --- | --- |
| python/main.py | Phát hiện người chỉ có chân, máy trạng thái scan, nhận mặt/tay và tính thời gian trả về. |
| python/ball_tracker.py | Cung cấp detect_person, is_legs_only và bộ đếm để không chạy model người ở mọi khung hình. |
| sketch/sketch.ino | Cho phép các ký tự camera và chuyển chúng thành khung CMD. |
| dog_esp32/dog_esp32.ino | Phát xung GPIO27, ghi thời gian scan, dừng safety timeout và thực hiện return. |
| python/manual_video.py, web/dashboard.html | Phục vụ nút Up/Down/Restart và hiển thị trạng thái camera. |

Trong manual, scan tự động bị hủy. Nút Up/Down chỉ chạy một bước 100 ms; nút
Restart khởi động lại webcam, không làm servo quay.

## 7. Cách thử từng bước

1. Kê robot lên giá, tháo tải khỏi cơ cấu nếu cần; flash ESP32 và deploy UNO Q.
2. Bật nguồn servo camera ngoài, xác nhận GND chung và kiểm tra servo đứng yên
   ở 1500.
3. Chạy ứng dụng, mở dashboard và xác nhận có khung hình. Nếu không, thử
   Restart camera trước khi chẩn đoán servo.
4. Ở Manual, nhấn Up rồi Down một lần; xác nhận chiều mới đúng và camera dừng
   sau khoảng 100 ms.
5. Chọn Automatic, đặt REQUIRE_FAMILIAR_FACE = True, đứng sao cho chỉ thấy
   chân. Terminal phải ghi Scanning for familiar face và camera đi lên.
6. Để mặt quen xuất hiện. Python gửi x, sau đó v trong thời gian không vượt
   thời gian đã đi lên để tìm tay.
7. Giơ tay cho tới khi có hai lần xác nhận. Camera giữ vị trí; khi tay mất hoặc
   timeout, Python gửi n và chờ CAMERA_RETURN_SETTLE_S.
8. Thử không có mục tiêu. ESP32 phải dừng vì CAMERA_SCAN_MAX_MS, sau đó Python
   báo timeout và đưa camera về.
9. Đặt REQUIRE_FAMILIAR_FACE = False để thử chế độ hand-only; mặt một mình
   không được dừng scan.
10. Sau mỗi lần thử, ghi lại chiều, thời lượng, điện áp và thông báo terminal.

## 8. Chẩn đoán

| Hiện tượng | Kiểm tra và cách xử lý |
| --- | --- |
| Servo bò khi phải đứng | Chỉnh CAMERA_SERVO_STOP_US từng 5–10 micro giây; kiểm tra nguồn 5 V. |
| Up/Down ngược | Đổi hai giá trị CAMERA_SERVO_UP_US và CAMERA_SERVO_DOWN_US. |
| Servo rung, ESP32 reset | Dùng nguồn ngoài 5 V đủ dòng, tụ gần servo và GND chung; không cấp từ 3,3 V. |
| Camera chạm điểm chặn | Giảm ngay CAMERA_SCAN_MAX_MS và CAMERA_SCAN_TIMEOUT_S; kiểm tra cơ khí. |
| Scan không bắt đầu | Đảm bảo robot ở trạng thái đứng, hộp người chạm mép trên và cao ít nhất 40 pixel. |
| Scan không dừng ở tay/mặt | Kiểm tra ánh sáng, model, REQUIRE_FAMILIAR_FACE và số lần xác nhận. |
| Luôn trả sai vị trí | Đây là giới hạn servo quay liên tục; kiểm tra trượt cơ khí và hiệu chỉnh thời gian. |
| Terminal ghi Camera scan safety timeout. | ESP32 đã tự dừng vì quá thời gian; kiểm tra Python/Bridge/UART và giảm thời lượng. |
| Nút Restart không có tác dụng | Xem camera_last_restart_reason, đường dẫn UNO_Q_CAMERA_PATH và quyền lease dashboard. |

Không tăng thời lượng chỉ để “đi đủ góc” nếu servo chưa đứng yên ở 1500. Với
cơ cấu cần vị trí lặp lại chính xác, hãy dùng servo có phản hồi góc hoặc thêm
cảm biến vị trí thay vì tiếp tục kéo dài bộ định thời.
