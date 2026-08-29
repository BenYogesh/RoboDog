# Chế độ điều khiển thủ công

Bản tiếng Anh: [manual-control.md](manual-control.md)

Ứng dụng DogVision bình thường hiện hỗ trợ lệnh thoại `manual`. Lệnh này dừng
chuyển động tự động hiện tại, gửi `MODE:MANUAL` tới ESP32 qua UART của UNO Q,
và chuyển UNO Q thành bộ chuyển tiếp media. Ở trạng thái này, ứng dụng Python
không thực hiện quyết định dựa trên khuôn mặt, bàn tay, quả bóng hoặc lệnh di
chuyển bằng giọng nói. Lệnh thoại `automatic` được giữ lại như một lối thoát an
toàn; các lệnh thoại di chuyển thông thường sẽ bị từ chối khi chế độ thủ công
đang bật.
Trong sketch ESP32 chính, Bluetooth chữ hoa `M` vào chế độ thủ công và chữ hoa
`O` trở về chế độ tự động.

Bộ chuyển tiếp media lắng nghe trên địa chỉ LAN của UNO Q:

Các listener này hiện không có xác thực hoặc mã hóa. Chỉ sử dụng chúng trong
mạng LAN riêng đáng tin cậy, hoặc đặt chúng sau VPN/firewall trước khi mở UNO Q
ra mạng rộng hơn.

| Cổng | Mục đích | Định dạng dữ liệu |
| ---: | --- | --- |
| 8080 | Camera | `GET /camera.mjpg`, MJPEG dạng multipart |
| 3334 | Microphone USB của UNO Q tới laptop | `AUD0`, sau đó là các frame PCM 24 kHz, 1 kênh, 16 bit |
| 3335 | Âm thanh từ laptop tới robot | `AUD0`, sau đó là các frame PCM 16 kHz, 1 kênh, 16 bit |
| 3336 | ESP32 nhận âm thanh cho loa | Cùng luồng mono 16 kHz được gửi bởi cổng 3335 |

Để nhanh chóng nhận dữ liệu ở phía laptop, cài `opencv-python` và `sounddevice`
trên laptop rồi chạy:

```bash
python python/manual_controller.py <uno-q-ip>
```

Dashboard trình duyệt responsive đầu tiên cũng được phục vụ tại:

```text
http://<uno-q-ip>:8080/
```

Dashboard hoạt động trên trình duyệt hiện đại của điện thoại, máy tính bảng,
laptop hoặc desktop trong cùng mạng LAN riêng. Dashboard cung cấp khung hình
camera, nút vào chế độ thủ công, nút trở về chế độ tự động, các nút di chuyển
phải giữ, nút tư thế/hành động/camera, phím tắt và trạng thái thời gian thực.
Trình duyệt gửi các request JSON tới `/api/mode` và `/api/command`, sau đó
poll `/api/status`.

Phiên bản đầu tiên không sử dụng microphone của trình duyệt. Client tùy chọn
`manual_controller.py` vẫn có thể dùng cho bộ chuyển tiếp âm thanh.

Với firmware dáng đi được cung cấp, chế độ dashboard tạm dừng vòng lặp thị giác
của UNO Q nhưng vẫn để ESP32 ở chế độ tự động có nhận lệnh UART. Điều này cần
thiết vì firmware đó chỉ chấp nhận `CMD:<character>` từ UNO Q khi ở
`CONTROL_AUTOMATIC`. Lệnh di chuyển qua Bluetooth vẫn có thể được ưu tiên; nếu
Bluetooth vào chế độ thủ công, dashboard sẽ hiển thị rằng Bluetooth đang nắm
quyền điều khiển robot và từ chối lệnh di chuyển từ trình duyệt cho tới khi chế
độ tự động được khôi phục.

Client hỗ trợ hiển thị camera, phát luồng microphone mono và gửi microphone của
laptop tới loa robot. Nhấn `q` trong cửa sổ camera để dừng. Dùng `--no-camera`
hoặc `--no-speaker` khi chỉ muốn kiểm tra một chiều.

Mỗi frame PCM bắt đầu bằng độ dài frame không dấu, 4 byte, theo thứ tự big-endian.
Header `AUD0` tiếp theo là ba trường big-endian: tốc độ lấy mẫu (`uint32`), số
kênh (`uint16`) và số bit mỗi mẫu (`uint16`). UNO Q thu microphone USB của webcam
bằng ALSA, chuyển frame mono tới nhận dạng giọng nói Realtime và chuyển tiếp
tới cổng 3334 khi chế độ thủ công đang bật. Cổng 3333 chỉ còn là bộ nhận cũ
không bắt buộc cho firmware ESP32/INMP441 cũ.

## Ranh giới phần cứng và firmware

Sketch dáng đi/Bluetooth chính được duy trì riêng tại
`Code/dog_esp32/dog_esp32.ino`. Sketch này triển khai các frame UART:

```text
MODE:MANUAL\n   -> chấp nhận lệnh di chuyển Bluetooth; bỏ qua frame CMD từ Uno Q
MODE:AUTO\n     -> khôi phục đường đi lệnh Uno Q/tự động hiện có
```

Firmware dáng đi giữ việc xử lý lệnh chế độ Bluetooth và watchdog chuyển động
ở cục bộ trên ESP32. Nhờ vậy, chế độ “chỉ nhận lệnh Bluetooth” vẫn được bảo
đảm ngay cả khi mạng của UNO Q hoặc tiến trình Python dừng. Khi chế độ Bluetooth
thay đổi, trạng thái được gửi ngược qua UART dưới dạng `MODE:MANUAL`/`MODE:AUTO`,
và UNO Q chuyển tiếp chúng lên Linux qua `Bridge.notify` để bộ chuyển tiếp media
đi theo cả hai cách vào chế độ.

Microphone webcam được nối USB trực tiếp với UNO Q. Sketch dáng đi chính hiện
chỉ cần đầu ra I2S của MAX98357A; hãy xác nhận đúng các chân trong firmware
dáng đi trước khi nối dây cho robot.
Sketch `esp32_speech_test` trước đây vẫn là chương trình chẩn đoán độc lập;
không nạp sketch đó làm firmware dáng đi của robot.

## Trình tự kiểm tra

1. Chỉ đặt `OPENAI_API_KEY` trong runtime của UNO Q nếu cần nhận dạng giọng nói.
   Camera và bộ chuyển tiếp microphone USB vẫn khởi động được khi không có khóa này.
2. Đảm bảo laptop và UNO Q ở cùng LAN. Chạy ứng dụng DogVision bình thường và
   xem địa chỉ IP của UNO Q trong cài đặt mạng hoặc terminal.
3. Sao chép `Code/dog_esp32/secrets.example.h` thành một `secrets.h` cục bộ,
   điền các giá trị Wi-Fi, và tải `Code/dog_esp32/data/sounds/` lên dưới dạng
   image LittleFS của ESP32. Nạp sketch chính `dog_esp32.ino`. Xác nhận log
   terminal UNO Q hiển thị thiết bị microphone USB đã chọn. Trạng thái `mic=ready`
   trên ESP32 chỉ còn được kỳ vọng nếu đường INMP441 cũ vẫn được bật.
4. Nói “manual control”. Log UNO Q phải hiển thị
   `MANUAL_CONTROL_ENTERED` và `MANUAL_MEDIA_ACTIVE`; OLED phải hiển thị
   `MANUAL CONTROL`.
5. Mở `http://<uno-q-ip>:8080/` trong trình duyệt và nhấn **Take dashboard
   control**. Chỉ kiểm tra nút di chuyển khi robot đã được nâng lên hoặc ở khu
   vực trống an toàn. Nhấn giữ nút di chuyển; khi thả nút, lệnh dừng sẽ được gửi.
6. Mở trực tiếp `http://<uno-q-ip>:8080/camera.mjpg` nếu cần luồng camera thô.
   Client media phải kết nối TCP tới cổng 3334 và phân tích header `AUD0` cùng
   các frame PCM mono có đóng khung để nghe microphone.
7. Để kiểm tra loa, kết nối laptop tới TCP cổng 3335, gửi header `AUD0` mono
   16 kHz tương ứng, sau đó gửi PCM16 có đóng khung. ESP32 phải kết nối sẵn tới
   cổng 3336; âm thanh sẽ bị bỏ qua nếu chế độ thủ công chưa bật.
8. Kiểm tra Bluetooth `M`, các lệnh di chuyển và watchdog của ESP32. Nhấn
   Bluetooth `O` hoặc nói “automatic”, sau đó xác nhận `MODE:AUTO`,
   `MANUAL_MEDIA_INACTIVE` và hoạt động thị giác bình thường.

Có thể thay đổi các cổng bằng `MANUAL_VIDEO_PORT`, `MANUAL_AUDIO_PORT`,
`MANUAL_SPEAKER_PORT` và `MANUAL_ROBOT_SPEAKER_PORT`. Cổng đầu vào speech ESP32
cũ không bắt buộc vẫn có thể cấu hình bằng `UNO_Q_AUDIO_PORT`.
