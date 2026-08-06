"""
Code nhận diện khuôn mặt hiện tại là người quen hay người lạ
"""

# Khai báo các thư viện cần thiết
import os
import json
import numpy as np
import cv2

# Các đường dẫn thư mục sử dụng
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))                                                  # Thư mục hiện tại của file face_gate.py
FACE_DETECTOR_PATH = os.path.join(_THIS_DIR, "face_models", "face_detection_yunet_2023mar.onnx")        # Đường dẫn đến file ONNX của mô hình phát hiện khuôn mặt YuNet
FACE_RECOGNIZER_PATH = os.path.join(_THIS_DIR, "face_models", "face_recognition_sface_2021dec.onnx")    # Đường dẫn đến file ONNX của mô hình nhận diện khuôn mặt SFace
KNOWN_FACES_DB_PATH = os.path.join(_THIS_DIR, "known_faces_db.json")                                    # Đường dẫn đến file cơ sở dữ liệu khuôn mặt đã đăng ký

FACE_DETECT_CONF_THRESHOLD = 0.6    # Ngưỡng độ tin cậy của mô hình phát hiện khuôn mặt YuNet
FACE_NMS_THRESHOLD = 0.3            # Ngưỡng NMS cho mô hình phát hiện khuôn mặt YuNet
FACE_TOP_K = 5000                   # Số lượng khuôn mặt được giữ lại sau NMS
FACE_INPUT_SIZE = (320, 240)        # Giới hạn kích thước khung hình đầu vào cho mô hình phát hiện khuôn mặt YuNet
FACE_MATCH_THRESHOLD = 0.363        # Ngưỡng điểm số để xác định một khuôn mặt là người quen hay người lạ


class FaceGate:
    # Lớp nhận diện khuôn mặt hiện tại là người quen hay người lạ
    def __init__(self, detector_path=FACE_DETECTOR_PATH, recognizer_path=FACE_RECOGNIZER_PATH,
                 db_path=KNOWN_FACES_DB_PATH, input_size=FACE_INPUT_SIZE):
        self.detector = cv2.FaceDetectorYN.create(
            detector_path, "", input_size,
            FACE_DETECT_CONF_THRESHOLD, FACE_NMS_THRESHOLD, FACE_TOP_K
        )
        self.recognizer = cv2.FaceRecognizerSF.create(recognizer_path, "")
        self.input_size = input_size
        self.known = self._load_db(db_path)
        self._recognition_error_count = 0

    def _load_db(self, path):
        # Tải cơ sở dữ liệu khuôn mặt đã đăng ký từ file JSON
        if not os.path.exists(path):
            print(f"FACE GATE WARNING: no known-faces database at {path} — "
                  f"everyone will be treated as unfamiliar until you run "
                  f"enroll_faces.py to build one.")
            return {}
        with open(path) as f:
            raw = json.load(f)
        return {
            name: np.array(vec, dtype=np.float32).reshape(1, -1)
            for name, vec in raw.items()
        }

    def _set_frame_size(self, width, height):
        # Cập nhật kích thước khung hình đầu vào cho mô hình phát hiện khuôn mặt YuNet
        if (width, height) != self.input_size:
            self.input_size = (width, height)
            self.detector.setInputSize(self.input_size)

    def _prepare_frame(self, frame):
        # Chuẩn hóa khung hình đầu vào để phù hợp với kích thước yêu cầu của mô hình YuNet
        height, width = frame.shape[:2]
        target_width, target_height = FACE_INPUT_SIZE
        scale = min(target_width / width, target_height / height, 1.0)
        if scale == 1.0:
            return frame
        return cv2.resize(
            frame,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )

    def _log_recognition_error(self, error):
        # Ghi lại lỗi nhận diện khuôn mặt và in ra cảnh báo nếu số lượng lỗi vượt quá 30
        self._recognition_error_count += 1
        if self._recognition_error_count % 30 == 1:
            print(
                "FACE GATE WARNING: recognition skipped "
                f"({self._recognition_error_count} errors so far): {error}"
            )

    def recognize(self, frame):
        # Trả về kết quả nhận diện khuôn mặt là người quen hay người lạ, hay không có ai
        # Chỉ kiểm tra khuôn mặt lớn nhất được phát hiện trong khung hình
        try:
            if frame is None or frame.ndim != 3 or frame.shape[2] != 3:
                return 'none', None

            face_frame = self._prepare_frame(frame)
            h, w = face_frame.shape[:2]
            self._set_frame_size(w, h)

            _, faces = self.detector.detect(face_frame)
            if faces is None or len(faces) == 0:
                return 'none', None

            largest = max(faces, key=lambda f: f[2] * f[3])
            aligned = self.recognizer.alignCrop(face_frame, largest)
            feature = self.recognizer.feature(aligned)

            best_name = None
            best_score = -1.0
            for name, known_feature in self.known.items():
                score = self.recognizer.match(known_feature, feature, cv2.FaceRecognizerSF_FR_COSINE)
                if score > best_score:
                    best_score = score
                    best_name = name

            if best_score >= FACE_MATCH_THRESHOLD:
                return 'familiar', best_name
            return 'unfamiliar', None
        except (cv2.error, AttributeError, ValueError) as error:
            self._log_recognition_error(error)
            return 'none', None


def verify_face_models():
    # Kiểm tra các mô hình khuôn mặt YuNet và SFace có thể được tải và sử dụng đúng cách
    dummy = np.zeros((240, 320, 3), dtype=np.uint8)
    try:
        detector = cv2.FaceDetectorYN.create(FACE_DETECTOR_PATH, "", (320, 240))
        detector.detect(dummy)
        cv2.FaceRecognizerSF.create(FACE_RECOGNIZER_PATH, "")
        print("Face models OK.")
    except cv2.error as e:
        print(f"FACE MODEL ERROR: {e}. Check both .onnx files downloaded "
              f"correctly (not LFS pointers) and, if you're on OpenCV 5.x, "
              f"whether face_detection_yunet_2026may.onnx is needed instead "
              f"of the fixed-shape 2023mar version.")


if __name__ == "__main__":
    verify_face_models()
