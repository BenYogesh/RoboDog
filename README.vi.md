# DogVision

Ứng dụng thị giác máy tính chạy trên Arduino UNO Q cho robot bốn chân
RoboDog.

Bản tiếng Anh: [`README.md`](README.md).

Để xem giải thích có hướng dẫn về bố cục code và luồng dữ liệu khi chạy, xem
[`docs/system-structure-dataflow.md`](docs/system-structure-dataflow.md).

Để xem pseudocode rút gọn cho luồng thị giác/điều khiển, xem
[`docs/project-pseudocode.vi.md`](docs/project-pseudocode.vi.md) hoặc bản tiếng
Anh tại [`docs/project-pseudocode.md`](docs/project-pseudocode.md).

Để xem tính năng quét camera bằng servo SG90 quay liên tục, gồm cách nối dây,
hiệu chỉnh và kiểm tra trên board, xem
[`docs/camera-scan-instructions.vi.md`](docs/camera-scan-instructions.vi.md).

Để xem động học chân ESP32, hiệu chỉnh dáng đi, cân bằng và các bài kiểm tra an
toàn, xem [`docs/esp32-gait-control.md`](docs/esp32-gait-control.md).

Để xem điều khiển Bluetooth thủ công kèm truyền camera/microphone và âm thanh
tùy chọn từ laptop, xem
[`docs/manual-control.vi.md`](docs/manual-control.vi.md).

Để xem hướng dẫn kiểm tra nhận dạng giọng nói và âm thanh, xem
[`docs/speech-test.vi.md`](docs/speech-test.vi.md).

Dashboard trình duyệt responsive đầu tiên được UNO Q phục vụ tại
`http://<uno-q-ip>:8080/` khi ứng dụng DogVision đang chạy. Dashboard cung cấp
khung hình camera, chuyển sang chế độ thủ công, các nút di chuyển/tư thế/hành
động/camera, chuyển đổi chế độ và trạng thái thời gian thực.

Ứng dụng Python nằm ở `python/main.py` và mặc định sử dụng đường dẫn camera
sau (có thể ghi đè bằng `UNO_Q_CAMERA_PATH`):

```text
/dev/v4l/by-id/usb-HX-MT9M114-201012_Integrated_Camera-video-index0
```

Microphone tích hợp trong webcam được thu trực tiếp bởi Linux trên UNO Q.
Xem [`docs/speech-test.md`](docs/speech-test.md) để tìm thiết bị ALSA và chạy
bài kiểm tra `python/usb_mic_test.py`.

## Đăng ký khuôn mặt quen thuộc

Đặt ảnh rõ nét vào `python/known_faces/<person-name>/`. Nên chụp ảnh bằng
camera của RoboDog ở khoảng cách và điều kiện ánh sáng tương tự lúc điều
khiển. Sau đó chạy các lệnh sau từ thư mục gốc của project:

```bash
python3 -m pip install -r python/requirements.txt
python3 python/enroll_faces.py
```

Lệnh này tạo `python/known_faces_db.json`, là file được
`python/face_gate.py` đọc khi ứng dụng thị giác khởi động.
