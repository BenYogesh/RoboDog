# Cấu trúc code trên ESP32
## Các cách điều khiển
ESP32 nhận kí tự mệnh lệnh từ Uno Q dưới dạng `CMD:<char>`, hoặc trực tiếp qua Bluetooth. Mệnh lệnh từ Bluetooth được ưu tiên trước Uno Q.

Nhấn `M` qua Bluetooth để bật chế độ điều khiển tay. ESP32 dừng chuyển động
hiện tại, bỏ qua toàn bộ khung `CMD:<char>` từ Uno Q và gửi trạng thái sau về
Uno Q:

```text
MODE:MANUAL
```

Nhấn `O` qua Bluetooth để quay lại điều khiển tự động; ESP32 gửi `MODE:AUTO`
về Uno Q. Firmware chính nằm tại `dog_esp32/dog_esp32.ino`. Watchdog cục bộ
dừng chuyển động nếu không nhận lệnh mới trong `MOTION_COMMAND_TIMEOUT_MS`.
Xem `docs/manual-control.md` để biết luồng điều khiển và webcam.
Vòng lặp cử động chân tuân theo trình tự sau:

```text
Mệnh lệnh -> Quỹ đạo cử động -> Điều chỉnh sai số theo cảm biến góc nghiêng
-> Động lực học nghịch đảo (Inverser Kinematics - IK) -> Lệnh góc quay ST3215
-> Xuất tín hiệu đồng loạt qua Serial -> Dữ liệu đo lường
```

## Cấu trúc chân và cài đặt pha cử động
Mỗi chân có 3 phần khớp, điều khiển bởi servo ST3215: Hông, đùi, cẳng. Biến dấu định hướng chuyển đổi góc quay được tính thành lệnh quay tương ứng với vị trí servo.

| Chân | ID servo khớp hông/đùi/cẳng | Pha | Hướng quay khớp hông/đùi/cẳng |
| --- | --- | ---: | --- |
| Trước-trái (FL) | 2 / 6 / 10 | 0.0 | + / - / - |
| Trước-phải (FR) | 1 / 5 / 9 | 0.5 | + / + / + |
| Sau-trái (BL) | 3 / 7 / 11 | 0.5 | - / - / - |
| Sau-phải (BR) | 4 / 8 / 12 | 0.0 | - / + / + |

Chuyển động bình thường được chia thành hai pha:
- Chân FL và BR di chuyển cùng nhau trước.
- Chân FR và BL di chuyển cùng nhau sau nửa chu kì.

Mỗi cặp chân có hai trạng thái trong một chu kì: giậm chân và nhấc chân. Khi cặp này đang nhấc thì cặp kia giậm và ngược lại. Tỉ lệ thời gian của mỗi trạng thái được cài đặt bằng biến `dutyFactor`.

## Cấu trúc hình học của chân robot
Tất cả độ dài được tính theo mm; thời gian được tính theo ms.

```cpp
constexpr float Lc = 40.0f;     // độ dài hông
constexpr float Lf = 100.0f;    // độ dài đùi
constexpr float Lt = 100.0f;    // độ dài cẳng

constexpr float stepLength;                        // độ dài bước dọc
constexpr float stepHeight                         // độ cao bước
constexpr float zRest = -150.0f;                   // độ cao robot khi đứng yên
constexpr float totalCycleDuration = 800.0f;       // thời gian một chu kì chuyển động chân
constexpr float dutyFactor = 0.65f;                // tỉ lệ phần trăm trạng thái giậm chiếm trong một chu kì
constexpr float crabStep = 30.0f;                  // độ dài bước ngang
```

Khi nhấc, chân robot chuyển động theo một đường cong Bezier. Khi giậm, chân robot chuyển động thẳng để đẩy robot về trước.
Trục tọa độ được sử dụng trong code tuân theo

- `x`: Chiều tiến/lùi trên mặt đất.
- `y`: Chiều ngang theo hướng khớp hông.
- `z`: Chiều lên xuống, dọc theo chân robot. Chiều âm đi từ thân robot xuống.

Trong trường hợp gặp phải tọa độ ngoài khoảng cho phép, `calculateIK_Mammal()` sẽ chặn không cho servo quay. Thay vào đó, tọa độ cho phép cuối cùng được ghi nhận `[IK] ... OOB` được sử dụng thay vì cố gắng quay đến vị trí không cho phép.

## Động lực học nghịch đảo: Chuyển tọa độ chân thành góc quay servo

Động lực học nghịch đảo (Inverse Kinematics - IK) giải quyết bài toán: *Cho trước tọa độ mong muốn của bàn chân robot
`(x, y, z)`, các servo ở khớp hông, đùi, cẳng phải quay góc bao nhiêu?*
Phần tính toán trong in `calculateIK_Mammal()` sử dụng kết quả hình học hai bước.

```text
                tọa độ chân mong muốn (x, y, z)
                           o
                          / \
                         /   \   mặt phẳng khớp đùi, cẳng
                        /  d  \
         độ dài đùi Lf o-------o độ dài cẳng Lt
                       ^
        vị trí khớp đùi, sau khi thêm độ lệch từ khớp hông Lc
```

### 1. Loại bỏ độ lệch khớp hông
Khớp hông sẽ xoay mặt phẳng khớp đùi cẳng theo chiều ngang. Khoảng cách bàn chân tính từ trục khớp hông sẽ là:

```text
R² = y² + z²
R  = sqrt(R²)
```

Tọa độ sẽ là ngoài khoảng cho phép khi `R < Lc`, vì lúc đó chân robot sẽ bị gập vào trong thân.
Sau đó, code sẽ chiếu kết quả lên mặt phẳng khớp đùi + cẳng:

```text
z' = sqrt(R² - Lc²)
```

Góc quay của khớp hông sẽ là độ lệch giữa góc của 2 tam giác vuông:

```text
coxa = atan2(y, |z|) - atan2(Lc, z')
```

`atan2` bảo toàn góc phần tư tương ứng của kết quả. `|z|` để đổi tọa độ âm của chân thành dương để tính toán `atan2`.

### 2. Tính góc quay của khớp đùi và cẳng

Sau khi đã tính xong góc quay của khớp hông, giờ code sẽ giải quyết 2 tọa độ còn lại `(x, z')`:

```text
d² = x² + z'²
d  = sqrt(d²)
```

`d` phải nằm trong hình vành khuyên mà bàn chân có thể di chuyển tới, tức kết quả `d >= Lf + Lt` và `d <= |Lf - Lt|` sẽ không được chấp nhận.
Code sử dụng dùng định lý cos để tìm góc trong tam giác tạo bởi đùi và cẳng chân robot:

```text
cos(beta)  = (Lf² + d² - Lt²) / (2 Lf d)
cos(gamma) = (Lf² + Lt² - d²) / (2 Lf Lt)

beta  = acos(cos(beta))
gamma = acos(cos(gamma))
```

Đồng thời, nó cũng xác định hướng quay từ khớp đùi:

```text
alpha = atan2(x, z')
```

Kết quả đầu ra cuối cùng của bước tính này sẽ là:

```text
femur = alpha + beta
tibia = -(pi - gamma)
```

### 3. Đưa các góc tính được ra servo ST3215

Với mỗi chân, `prepareST3215()` áp dấu định hướng quay (`+1` hoặc `-1`) vào kết quả chung cho tất cả các chân. Sau đó nó sẽ đổi kết quả góc thành xung trong `[0-4095]` để đưa ra servo ST3215:

```text
servo_position = clamp(2048 + round(angle_degrees x 11.377), 0, 4095)
```

Sau đó, cả 12 góc quay sẽ được gửi đồng loạt qua `SyncWritePosEx` để các chân cử động cùng nhau.

## Lệnh chuyển động
Danh sách lệnh có trên ESP32
| Lệnh | Chuyển động | Mô tả |
| --- | --- | --- |
| `w` | Đi tiến | Bước bình thường |
| `b` | Đi lùi | Bước bình thường nhưng ngược lại |
| `a` / `d` | Quay trái/phải | Robot xoay tại chỗ theo hướng chỉ định |
| `e` / `f` | Bước ngang như cua | Bước ngang sang hai bên |
| `p` | Bước hai hàng | Một cặp chân cùng bên sẽ di chuyển cùng nhau thay vì cặp chéo nhau |
| `s` | Đứng thẳng | Trạng thái mặc định |
| `z` | Đứng yên | Đứng yên, bỏ qua điều chỉnh PID |
| `q` | Ngồi | Hai chân sau gập lại, hai chân trước vẫn duỗi |
| `c` | Nằm | Cả bốn chân gập lại |
| `g` | Vẫy chào | Chân trước phải vẫy, các chân còn lại giữ thăng bằng |
| `u` | Nhún | Các chân co duỗi liên tục theo nhịp |
| `j` | Nhảy | Các chân đồng loạt gập lại rồi duỗi ra thật nhanh |
| `k` | Đưa tất cả servo về trung tâm | Chỉ dùng để tinh chỉnh |

Các lệnh di chuyển sẽ có giới hạn thời gian. Nếu không có lệnh mới trong khoảng `MOTION_COMMAND_TIMEOUT_MS`, ESP32 gửi lệnh `s` để dừng robot lại.

### Chuyển tư thế

Các tư thế tĩnh (`s`, `z`, `q`, `c`) không đổi mục tiêu chân ngay lập tức. Khi
nhận một tư thế mới, firmware lưu tọa độ chân đang được điều khiển rồi nội suy
đồng thời cả bốn chân bằng hàm smoothstep trong không gian Descartes. Thời gian
được tính theo chân phải di chuyển xa nhất (nhanh nhất 450 ms, tối đa 1,2 s),
nên mọi cặp chuyển tư thế đều dùng chung một đường đi và lệnh mới giữa chừng có
thể đổi hướng an toàn. Khi rời tư thế ngồi (`q`), firmware chèn thêm một điểm
trung gian dịch bàn chân `SIT_EXIT_COM_SHIFT_X` về phía sau trong hệ tọa độ thân,
để đưa trọng tâm về trên hai chân trước. Với đường về tư thế đứng, hai chân sau
còn hạ tới `SIT_EXIT_REAR_PUSH_Z` trước khi duỗi hết, tạo một điểm tựa có tải
giúp tibia đẩy thân lên thay vì phải thực hiện toàn bộ hành trình trong một lần.
Các chuyển tư thế tĩnh dùng tốc độ thấp hơn và gia tốc giới hạn cho tibia để
giảm sụt nguồn và giữ lực nâng; hiệu chỉnh cân bằng IMU chỉ còn 25% trong lúc
chuyển để không chống lại quỹ đạo. Lệnh `j` và watchdog cũng dùng đường chuyển
về tư thế đứng này khi kết thúc.

## Đọc cảm biến MPU6050 để cân bằng

ESP32 đọc cảm biến qua chân GPIO 21/22 để lấy các góc nghiêng theo chiều song song với mặt đất thông qua một bộ lọc, sau đó 4 bộ PIDs sẽ điều chỉnh sai số cho thân robot.

| PID | Áp dụng |
| --- | --- |
| `pitchZ_PID` | Độ nghiêng theo chiều dọc thân |
| `rollZ_PID` | Độ nghiêng theo chiều ngang thân |
| `pitchX_PID` | Độ lệch bàn chân theo chiều dọc thân |
| `rollY_PID` | Độ lệch bàn chân theo chiều ngang thân |

Sai số thăng bằng sẽ được áp dụng trước khi tính IK. Sai số chiều dọc chỉ áp dụng cho các chân đang ở trạng thái giậm xuống, các chân đang nhấc sẽ đi theo quỹ đạo đã định. Lệnh `z` bỏ qua sai số thăng bằng, giúp trong việc hiệu chỉnh các sai số đến từ phần cứng. 
Nếu ESP32 không phát hiện MPU6050, code sẽ gửi một cảnh báo và sẽ chuyển động mà không có điều chỉnh thăng bằng.

## Lệnh điều khiển ST3215

Từ kết quả góc, xung tín hiệu gửi tới servo được tính theo công thức:

```text
position = 2048 + angle_degrees x 11.377
```

Kết quả được giới hạn trong khoảng 0-4095, điều chỉnh theo hướng quay của từng
servo. Gait thông thường dùng tốc độ `3073` và gia tốc `0`; trong chuyển tư thế
tĩnh, tốc độ được hạ xuống (`1600` cho tibia, `2300` cho các khớp còn lại) và
gia tốc đặt `40` để các khớp đang chịu tải có thêm thời gian tạo lực.

Mỗi 100 ms, ESP32 đọc tín hiệu trả về từ 1 servo, rồi in ra thông số để theo dõi:

```text
[STS 6] goal=... pos=... err=...deg speed=... load=... V=... temp=...C current=...
```

Theo dõi thông tin này có thể phát hiện ra các lỗi trong quá trình vận hành để có điều chỉnh hợp lý.

## Các bước hiệu chỉnh robot

1. Nâng robot lên khỏi mặt đất để tránh va chạm.
2. Kiểm tra ID servo và hướng quay được cài đặt trong code. Khi khởi động, robot sẽ vào trạng thái `z` với tọa độ chân `x = 0`, `y = Lc`, `z = zRest`. Ngắt điện ngay khi thấy một khớp nào đó không cử động.
3. Sử dụng lệnh `k` để đưa lần lượt từng servo theo ID mỗi 500 ms tới vị trí `2048 + calibrationTrim[id]`. Đo đạc trên thực tế để tìm ra các góc hiệu chỉnh `calibrationTrim` và `coxaTrim` trong code.
4. Dùng lệnh `s` và `z` để kiểm tra trạng thái đứng yên.
5. Test lệnh đi tiến đầu tiên, rồi tới các lệnh di chuyển khác.
7. Chỉnh hằng số PID cuối cùng.

## Một số cách điều chỉnh dựa trên biểu hiện

| Biểu hiện | Kiểm tra | Điều chỉnh |
| --- | --- | --- |
| Rung lắc khi đứng | Thăng bằng của cảm biến; Hệ số PID | Kiểm tra cảm biến trước, rồi thay đổi hệ số PID |
| Robot khó đi về trước, hoặc bị trượt | Vật liệu làm bàn chân; Nguồn pin; Tải trọgn | Cải thiện độ bám qua phần cứng, có thể giảm độ cao bước rồi giảm thời gian chu kì bước |
| Một cặp chân chéo nhau có sai số quá lớn | ID servo; Góc hiệu chỉnh; Nguồn pin; Độ cứng khớp | Theo dõi tín hiệu trả về để điều chỉnh thực tế |
| Robot bị nghiêng quá mức khi di chuyển | Trọng tâm; Tỉ lệ thời gian nhấc/giậm chân | Điều chỉnh tỉ lệ thời gian nhấc/giậm hoặc chu kì bước |
| Chân không nhấc được khỏi mặt sàn | Độ cao bước; Độ cao thân; Phạm vi phần cứng | Tăng độ cao bước |
| Bước ngang thành bước chéo | Hiệu chỉnh khớp hông; Quỹ đạo chân ngang; Độ bám bàn chân | Điều chỉnh khớp hông, và độ dài bước ngang |
