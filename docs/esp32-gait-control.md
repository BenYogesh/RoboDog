# Gait, IK, tư thế và cân bằng trên ESP32

File dog_esp32/dog_esp32.ino là firmware chạy trực tiếp trên ESP32. Nó không
chạy mô hình camera; nó nhận các ký tự lệnh, biến chúng thành vị trí bàn chân
rồi điều khiển 12 servo ST3215. Tài liệu này giải thích từ đường truyền đến
toán học và cách thử an toàn.

## 1. ESP32 nhận lệnh từ đâu?

Có hai đường lệnh:

- UNO Q gửi khung CMD:<char> qua UART2. STM32 trong UNO Q đã kiểm tra ký tự
  trước khi gửi.
- Điện thoại/máy tính gửi byte qua Bluetooth với tên RoboDog_ESP32.

Bluetooth có quyền ưu tiên:

- M (chữ hoa) chuyển ESP32 sang CONTROL_MANUAL, dừng chuyển động và báo
  MODE:MANUAL về UNO Q.
- O chuyển về CONTROL_AUTOMATIC và báo MODE:AUTO.
- Trong manual, byte chuyển động/camera từ Bluetooth được xử lý; khung CMD từ
  UNO Q bị chặn.
- Sau một lệnh Bluetooth, cửa sổ BLUETOOTH_PRIORITY_MS = 2000 ms ngăn UART
  UNO Q chen vào. Watchdog dừng gait nếu không có lệnh mới sau
  MOTION_COMMAND_TIMEOUT_MS = 5000 ms.

Nút **Manual** của dashboard không gửi M. Dashboard tạm dừng quyết định Python,
sau đó dùng UART UNO Q; vì thế ESP32 vẫn phải ở chế độ automatic để nhận khung
CMD. Xem tài liệu điều khiển thủ công để phân biệt hai ý nghĩa “automatic”.

## 2. Sơ đồ chân và tốc độ truyền

| Chức năng | ESP32 | Tốc độ/giao thức |
| --- | --- | --- |
| Bus servo ST3215 | RX GPIO18, TX GPIO19, Serial1 | 1.000.000 baud, half-duplex theo thư viện SCServo. |
| Liên kết UNO Q | RX GPIO16, TX GPIO17, UART2 | 115200 baud, khung ASCII kết thúc bằng newline. |
| MPU6050 | SDA GPIO21, SCL GPIO22 | I2C. |
| Servo nghiêng camera | Tín hiệu GPIO27 | Xung 50 Hz; xem tài liệu camera scan. |
| Bluetooth | Bộ BluetoothSerial tích hợp | Tên RoboDog_ESP32. |

Servo chân cần nguồn phù hợp với phiên bản 12 V và nguồn ngoài đủ dòng. ESP32,
UNO Q, servo chân, servo camera và các bộ nguồn phải có GND chung khi dùng
tín hiệu điều khiển. Nếu điện áp tụt mạnh lúc đổi tư thế, dừng robot trước khi
chỉnh phần mềm: nguồn yếu làm servo mất lực, UART/camera reset và có thể làm
firmware giữ sai vị trí.

## 3. Bảng lệnh firmware

| Ký tự | Tác dụng | Mô tả ngắn |
| --- | --- | --- |
| w | Đi tiến | Gait chéo mặc định. |
| b | Đi lùi | Cùng gait, đảo quy ước x. |
| a / d | Quay trái/phải | Đảo chuyển động x theo bên chân. |
| e / f | Strafe trái/phải | Gait ngang, dùng trục y. |
| p | Pace | Gait hai hàng, hai bên chân lệch pha 0,5. |
| s | Đứng hoặc dừng | Tư thế đứng mặc định; cũng là STOP và đứng lên từ tư thế thấp. |
| z | Hold | Đứng cùng hình học với s nhưng bỏ hiệu chỉnh cân bằng sau khi chuyển xong. |
| q | Sit | Gập chân sau, hạ nhẹ chân trước. |
| c | Prone | Hạ cả bốn chân xuống. |
| g | Wave | Chân trước phải vẫy, các chân còn lại giữ thân. |
| u | Bounce | Co/duỗi thân theo nhịp. |
| j | Jump | Chạy ba pha hạ/đẩy lên trong 300 ms rồi chuyển về đứng qua transition. |
| k | Calibration | Lần lượt đưa 12 servo tới vị trí tâm cộng trim. |

Các lệnh camera h, l, n, r, v, x được xử lý riêng và không đi vào gait. Tên
lệnh dashboard như forward, backward chỉ là tên thân thiện; Python đổi chúng
thành ký tự ở bảng trên.

## 4. Bốn chân, ID servo và pha gait

Mỗi chân có khớp hông (coxa), đùi (femur) và cẳng (tibia). ID và dấu hướng đã
được hiệu chỉnh trong mảng legs:

| Chân | ID coxa / femur / tibia | Pha | Dấu coxa / femur / tibia |
| --- | --- | ---: | --- |
| Trước-trái (FL) | 2 / 6 / 10 | 0,0 | + / − / − |
| Trước-phải (FR) | 1 / 5 / 9 | 0,5 | + / + / + |
| Sau-trái (BL) | 3 / 7 / 11 | 0,5 | − / − / − |
| Sau-phải (BR) | 4 / 8 / 12 | 0,0 | − / + / + |

FL + BR là một cặp chéo; FR + BL là cặp còn lại. phaseOffset = 0.5 làm cặp
thứ hai bắt đầu sau nửa chu kỳ, tạo gait có ba chân đỡ khi một cặp đang nhấc.

Các kích thước mặc định (mm và ms):

~~~cpp
Lc = 40       // đoạn coxa
Lf = 100      // đùi
Lt = 100      // cẳng
stepLength = 40
stepHeight = 30
zRest = -150
totalCycleDuration = 800
dutyFactor = 0.65
crabStep = 30
~~~

dutyFactor = 0.65 nghĩa chân ở pha chống (stance) 65% chu kỳ và nhấc (swing)
35%. Bước swing đi theo đường cong Bezier bốn điểm; stance kéo bàn chân trên
mặt đất để đẩy thân. Bước ngang dùng cùng hình dạng Bezier nhưng đặt lên trục y.

Quy ước tọa độ thân:

- x: trước (+) và sau (−) theo thân robot;
- y: ngang qua khớp hông;
- z: lên/xuống; z âm đi xuống, nên zRest = −150 là độ cao đứng.

## 5. IK: từ bàn chân tới ba góc khớp

Hàm calculateIK_Mammal(x, y, z) nhận vị trí bàn chân mong muốn và trả về góc
coxa, femur, tibia theo độ. Ý tưởng là tách bài toán 3D thành một tam giác
ngang và một tam giác đùi–cẳng.

### Bước 1: khớp hông

Khoảng cách từ trục coxa tới bàn chân trong mặt phẳng y–z là:

~~~text
R² = y² + z²
R  = sqrt(R²)
z' = sqrt(R² − Lc²)
~~~

Nếu R < Lc, bàn chân nằm vào trong thân và IK báo thất bại. Góc coxa dùng hai
hàm atan2 để giữ đúng góc phần tư:

~~~text
coxa = atan2(y, |z|) − atan2(Lc, z')
~~~

### Bước 2: khớp đùi và cẳng

Sau khi bỏ độ lệch coxa, khoảng cách tới bàn chân trong mặt phẳng x–z' là:

~~~text
d² = x² + z'²
d  = sqrt(d²)
alpha = atan2(x, z')
~~~

Code dùng định lý cos:

~~~text
cos(beta)  = (Lf² + d² − Lt²) / (2 Lf d)
cos(gamma) = (Lf² + Lt² − d²) / (2 Lf Lt)

femur = alpha + acos(cos(beta))
tibia = −(pi − acos(cos(gamma)))
~~~

Nếu d vượt tầm với hoặc model số không hợp lệ, code giữ góc lastValid của
chân thay vì gửi vị trí nguy hiểm và in dòng [IK] Leg ... OOB.

### Bước 3: đổi góc thành vị trí servo

prepareST3215 nhân góc với dấu riêng của từng khớp rồi đổi sang miền 0–4095:

~~~text
position = clamp(2048 + round(angle_degrees × 11.377), 0, 4095)
~~~

Cả 12 vị trí được gom vào một lần SyncWritePosEx để bốn chân cập nhật gần như
đồng thời.

## 6. Tư thế tĩnh và chuyển tư thế

Các lệnh s, z, q, c là tư thế tĩnh. Firmware không nhảy thẳng từ tọa độ cũ
sang tọa độ mới. Nó lưu mục tiêu bàn chân thật sự ở frame trước, nội suy bằng
smoothstep rồi mới tính IK. Nếu lệnh mới đến giữa chừng, transition được lấy
mẫu từ vị trí đang đi tới; vì vậy chuỗi q → c → s không quay về một tư thế cũ
bị lưu nhầm.

Mục tiêu hình học:

| Lệnh | Chân trước | Chân sau | Ý nghĩa |
| --- | --- | --- | --- |
| s | x=0, y=Lc, z=−150 | giống chân trước | Đứng bình thường. |
| z | giống s | giống s | Đứng và bỏ cân bằng chủ động sau transition. |
| q | z=−160, y=Lc | z=−70, y=Lc ± 10 | Ngồi, chân sau gập. |
| c | z=−80, y=Lc ± 5 | giống chân trước | Nằm thấp hơn. |

Thời gian transition thường được tính từ chân đi xa nhất:

- tối thiểu POSE_TRANSITION_MIN_MS = 450 ms;
- cộng POSE_TRANSITION_MS_PER_MM = 4 ms cho mỗi mm;
- tối đa POSE_TRANSITION_MAX_MS = 1200 ms;
- sai số dưới POSE_TRANSITION_EPSILON_MM = 0,5 mm được coi là đã xong.

### Lối ra khỏi tư thế ngồi

Robot có trọng tâm lùi khi đang ngồi, nên q → s được tách thành hai đoạn:

1. Tất cả bàn chân dịch x = −30 mm theo thân (SIT_EXIT_COM_SHIFT_X) để thân
   nghiêng về vùng chân trước đang đỡ.
2. Với chân sau, waypoint hạ tới z = −120 mm (SIT_EXIT_REAR_PUSH_Z) trước khi
   duỗi tới tư thế đứng. Tibia có điểm tựa chịu tải thay vì phải nâng toàn bộ
   thân trong một hành trình.

Lối ra này có thời gian tối thiểu 800 ms và tối đa 1600 ms. Cùng waypoint được
dùng nếu đang chuyển từ tư thế ngồi sang z; các cặp khác dùng transition trực
tiếp. Hiệu chỉnh IMU trong transition chỉ còn 25%
(POSE_TRANSITION_BALANCE_SCALE) để không triệt tiêu lực nâng. Đây là lý do
nên xem dòng POSE_TRANSITION thay vì chỉ tăng speed servo khi robot khó đứng
lên.

Trong transition, tốc độ servo giảm xuống 2300; tibia chịu tải dùng 1600 và
gia tốc giới hạn 40. Gait bình thường vẫn dùng tốc độ 3073. Các giá trị này
giảm đỉnh dòng nhưng làm transition lâu hơn một chút.

## 7. IMU và PID cân bằng

MPU6050 được đọc mỗi khoảng 20 ms (xấp xỉ 50 Hz). Góc roll/pitch kết hợp gyro
và gia tốc bằng bộ lọc bổ sung 98% gyro + 2% gia tốc; gyro yaw chỉ dùng theo
dõi. Mẫu gyro có giá trị bất thường lớn sẽ bị bỏ qua.

Bốn bộ PID hiện tại:

| PID | Hệ số (Kp, Ki, Kd) | Tác dụng |
| --- | --- | --- |
| pitchZ_PID | 1,2; 0,005; 0,1 | Điều chỉnh độ cao theo pitch khi chân chống. |
| rollZ_PID | 1,0; 0,005; 0,05 | Điều chỉnh độ cao theo roll khi chân chống. |
| pitchX_PID | 0,5; 0,005; 0,03 | Bù dịch x theo pitch. |
| rollY_PID | 0,7; 0,005; 0,05 | Bù dịch y theo roll. |

Bù x/y được áp trước IK; bù z chỉ áp cho chân đang ở stance, chân swing giữ
quỹ đạo nhấc. Lệnh z bỏ qua bù khi đã đứng yên. Nếu MPU6050 không kết nối,
firmware in cảnh báo và chạy không có cân bằng; robot vẫn có thể cử động nhưng
dễ nghiêng hơn.

Khi robot rung, đừng đổi cả bốn PID. Kiểm tra hướng cảm biến trước, giảm Kp
nhỏ từng bước, rồi mới xem Ki/Kd. Cân bằng không thể bù cho servo thiếu lực
hoặc thân robot mất ổn định cơ khí.

## 8. Telemetry và hiệu chỉnh

Telemetry được bật mặc định. Mỗi 100 ms firmware hỏi một servo kế tiếp; mỗi
dòng có dạng:

~~~text
[STS 6] goal=... pos=... err=...deg speed=... load=... V=... temp=...C current=...
~~~

- goal/pos: vị trí muốn tới và vị trí servo báo về;
- err: sai số quy đổi ra độ;
- speed/load/current: mức làm việc;
- V/temp: điện áp và nhiệt độ servo.

Nếu ba lần đọc liên tiếp timeout, telemetry tự tắt để không làm nghẽn bus.
Điều này không dừng chuyển động; hãy kiểm tra dây bus, ID và nguồn.

Lệnh k chạy hiệu chỉnh tuần tự: mỗi 500 ms gửi từng ID 1–12 tới
2048 + calibrationTrim[id], hiện trim mặc định gần như bằng 0. Sau khi xác
định tâm cơ khí, điền trim và coxaTrim, nâng robot lên rồi thử lại. Không dùng
k khi chân đang chạm sàn.

## 9. Trình tự thử an toàn

1. Tháo tải hoặc kê robot; chuẩn bị nút/ngắt nguồn dễ tiếp cận.
2. Kiểm tra nguồn servo theo datasheet, GND chung và baud bus.
3. Bật ESP32, chờ giai đoạn bỏ qua nhiễu khởi động khoảng 7 giây, xem cảnh
   báo MPU6050 và camera.
4. Gửi k nếu cần hiệu chỉnh; dừng ngay khi một ID quay sai.
5. Gửi z rồi s để kiểm tra đứng yên; so sánh POSE_TRANSITION và telemetry.
6. Thử q rồi s. Nếu chân sau không nâng được, đo điện áp ngay tại servo khi
   chuyển và kiểm tra load, current, err; đừng chỉ tăng speed.
7. Thử w/b trong thời gian ngắn khi vẫn có người giữ nút STOP.
8. Thử a/d rồi e/f, sau đó mới thử g/u/j.
9. Kiểm tra watchdog bằng cách ngừng gửi lệnh; gait phải tự về đứng sau 5 giây.
10. Khi dùng dashboard, xác nhận Manual gửi UART; khi dùng Bluetooth, xác nhận
    MODE:MANUAL và UNO Q bị chặn.

Firmware hiện không có cảm biến tiếp xúc chân, phát hiện bậc hoặc lập kế hoạch
độ cao. Gait phẳng không tự biến thành chế độ leo cầu thang; không thử trên cầu
thang nếu chưa bổ sung phần cơ khí, cảm biến và quỹ đạo phù hợp.

## 10. Bảng lỗi thường gặp

| Hiện tượng | Nguyên nhân có thể | Việc nên làm |
| --- | --- | --- |
| Một chân quay ngược | Sai ID hoặc dấu coxa/femur/tibia | Kiểm tra mảng legs và hiệu chỉnh khi robot được kê. |
| In [IK] ... OOB | Tọa độ ngoài tầm với hoặc PID đẩy quá xa | Giảm bước/độ cao, kiểm tra Lc/Lf/Lt và giới hạn cơ khí. |
| Robot rung khi đứng | PID quá mạnh, IMU lệch hoặc khung lỏng | Giữ z, kiểm tra cảm biến, giảm Kp từng bộ. |
| Sit → Stand bị kẹt | Trọng tâm lùi, tibia thiếu lực hoặc nguồn sụt | Kiểm tra waypoint, điện áp tại servo, dây/battery; giữ tốc độ tibia 1600 trước khi đổi quỹ đạo. |
| Chuyển tư thế làm camera/UNO Q reset | Đỉnh dòng servo kéo sụt áp | Nguồn 12 V đủ dòng, dây ngắn/đủ lớn, tụ và GND chung; không tăng tốc. |
| Servo không dừng sau mất lệnh | Watchdog không chạy do state hoặc UART nhiễu | Kiểm tra MOTION_COMMAND_TIMEOUT_MS, đường UART và log command. |
| Bluetooth điều khiển nhưng dashboard không | ESP32 đang CONTROL_MANUAL | Gửi O, chờ MODE:AUTO rồi chọn Automatic/Manual dashboard. |
| Không có telemetry | Servo bus sai baud, ID hoặc ba timeout liên tiếp | Kiểm tra dây/nguồn; telemetry có thể đã tự tắt, khởi động lại ESP32. |

Thay đổi một hằng số mỗi lần, ghi lại kết quả và flash lại cả firmware sau khi
đổi. Khi nguồn có dấu hiệu tụt dưới mức cho phép của servo, ưu tiên sửa nguồn
và tải cơ khí trước khi tiếp tục tối ưu phần mềm.
