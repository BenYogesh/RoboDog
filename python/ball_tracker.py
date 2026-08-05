"""Code phát hiện và đi theo bóng và người sử dụng YOLOv8n."""

from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np

# Mảng chứa các vật thể mà model YOLOv8n có thể phát hiện, theo chuẩn COCO dataset.
COCO_CLASSES = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat",
    "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack",
    "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball",
    "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
    "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
    "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
    "couch", "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator",
    "book", "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush",
]


class BallTracker:
    # Lớp kiểm soát model YOLOv8n, theo dõi trạng thái hiện tại

    MODEL_INPUT_SIZE = 320          # Kích thước đầu vào của model YOLOv8n
    SPORTS_BALL_CLASS_ID = 32       # ID của lớp "sports ball" trong COCO_CLASSES
    PERSON_CLASS_ID = 0             # ID của lớp "person" trong COCO_CLASSES
    BALL_CONF_THRESHOLD = 0.15      # Ngưỡng tự tin để coi một vật thể là bóng
    PERSON_CONF_THRESHOLD = 0.4     # Ngưỡng tự tin để coi một vật thể là người
    NMS_THRESHOLD = 0.45            # Ngưỡng loại bỏ các dự đoán trùng lặp
    DIAGNOSTIC_MIN_CONF = 0.15      # Ngưỡng tự tin tối thiểu để hiển thị các lớp vật thể khác ngoài bóng
    DIAGNOSTIC_TOP_N = 5            # Số lượng vật thể hàng đầu để hiển thị trong chẩn đoán
    BALL_FOUND_RADIUS = 45          # Bán kính (pixel) của bóng để coi là đã tìm thấy
    BALL_CENTER_DEADZONE = 25       # Vùng chết (pixel) xung quanh tâm khung hình để coi bóng là ở giữa
    NEVER_SEEN_TIMEOUT_S = 6.0      # Thời gian (giây) để từ bỏ nếu chưa từng thấy bóng
    SPIN_SEARCH_TIMEOUT_S = 6.0     # Thời gian (giây) để từ bỏ nếu mất bóng trong khi đang theo dõi
    MANUAL_STOP_CHECK_INTERVAL = 5  # Số khung hình giữa các lần kiểm tra lệnh dừng thủ công
    CAMERA_CHECK_INTERVAL = 5       # Số khung hình giữa các lần kiểm tra camera
    LEG_ONLY_TOP_MARGIN_PX = 15     # Khoảng cách (pixel) từ trên cùng của khung hình để coi là chỉ thấy chân
    PERSON_MIN_HEIGHT_PX = 40       # Chiều cao tối thiểu (pixel) của người để coi là hợp lệ

    def __init__(self, model_path: str | Path, walk_command: str, stop_command: str,
                 left_command: str, right_command: str):
        # Khởi tạo BallTracker với đường dẫn model và các lệnh điều khiển

        self.net = cv2.dnn.readNet(str(model_path))                 # Đọc model YOLOv8n từ file ONNX
        self.output_names = self.net.getUnconnectedOutLayersNames() # Lấy tên các lớp đầu ra của model
        self.walk_command = walk_command                            # Lệnh để robot đi thẳng về phía trước
        self.stop_command = stop_command                            # Lệnh để robot dừng lại
        self.left_command = left_command                            # Lệnh để robot rẽ trái
        self.right_command = right_command                          # Lệnh để robot rẽ phải 
        self._forward_error_count = 0                               # Đếm số lần lỗi khi đưa dữ liệu qua model nhận diện
        self._manual_stop_check_counter = 0                         # Đếm số khung hình để kiểm tra lệnh dừng thủ công
        self._camera_check_counter = 0                              # Đếm số khung hình để kiểm tra camera
        self.ball_ever_seen = False                                 # Cờ để xác định xem bóng đã từng được nhìn thấy hay chưa
        self.last_seen_ball_side = None                             # Lưu trữ bên camera thấy bóng lần cuối
        self.chase_entry_time = 0.0                                 # Thời điểm bắt đầu theo dõi bóng
        self.last_ball_seen_time = 0.0                              # Thời gian lần cuối bóng được nhìn thấy
        self._verify_model_input_size()                             # Kiểm tra xem model có chấp nhận kích thước đầu vào đã cài đặt hay không

    def _verify_model_input_size(self):
        # Kiểm tra xem model YOLOv8n có chấp nhận kích thước đầu vào đã cài đặt hay không
        dummy = np.zeros(
            # Tạo một ảnh giả để kiểm tra kích thước đầu vào của model
            (self.MODEL_INPUT_SIZE, self.MODEL_INPUT_SIZE, 3), dtype=np.uint8 
        )
        blob = cv2.dnn.blobFromImage(
            # Tạo blob từ ảnh giả để đưa vào model
            dummy,
            scalefactor=1.0 / 255.0,
            size=(self.MODEL_INPUT_SIZE, self.MODEL_INPUT_SIZE),
            swapRB=True,
            crop=False,
        )
        try:
            # Đưa blob qua model nhận diện để kiểm tra kích thước đầu vào
            self.net.setInput(blob)
            self.net.forward(self.output_names)
            print(
                "Ball model OK: yolov8n.onnx accepts "
                f"{self.MODEL_INPUT_SIZE}x{self.MODEL_INPUT_SIZE} input."
            )
        except cv2.error as error:
            print(
                "BALL MODEL SIZE MISMATCH: yolov8n.onnx was not exported at "
                f"imgsz={self.MODEL_INPUT_SIZE}. Re-export with "
                f"'yolo export model=yolov8n.pt format=onnx imgsz={self.MODEL_INPUT_SIZE}', "
                f"or update MODEL_INPUT_SIZE. Raw error: {error}"
            )

    def _log_forward_error(self, error):
        # Ghi lại số lần lỗi khi đưa dữ liệu qua model
        self._forward_error_count += 1
        if self._forward_error_count % 30 == 1:
            print(
                "BALL MODEL forward() failed "
                f"({self._forward_error_count} times so far): {error}"
            )

    def _letterbox(self, frame):
        # Đóng viền cho khung hình camera để phù hợp với kích thước đầu vào của model YOLOv8n
        height, width = frame.shape[:2]
        scale = min(self.MODEL_INPUT_SIZE / width, self.MODEL_INPUT_SIZE / height)
        resized_width = round(width * scale)
        resized_height = round(height * scale)
        resized = cv2.resize(
            frame,
            (resized_width, resized_height),
            interpolation=cv2.INTER_LINEAR,
        )

        pad_width = self.MODEL_INPUT_SIZE - resized_width
        pad_height = self.MODEL_INPUT_SIZE - resized_height
        pad_left = pad_width // 2
        pad_top = pad_height // 2
        padded = cv2.copyMakeBorder(
            resized,
            pad_top,
            pad_height - pad_top,
            pad_left,
            pad_width - pad_left,
            cv2.BORDER_CONSTANT,
            value=(114, 114, 114),
        )
        return padded, scale, pad_left, pad_top

    @staticmethod
    def _to_frame_box(row, scale, pad_left, pad_top):
        # Chuyển đổi tọa độ bounding box từ đầu ra của model sang tọa độ trong khung hình gốc
        center_x, center_y, box_width, box_height = row[:4]
        left = (center_x - box_width / 2 - pad_left) / scale
        top = (center_y - box_height / 2 - pad_top) / scale
        return left, top, box_width / scale, box_height / scale

    def _predict(self, frame):
        # Thực hiện dự đoán trên khung hình camera bằng model YOLOv8n
        letterboxed, scale, pad_left, pad_top = self._letterbox(frame)
        blob = cv2.dnn.blobFromImage(
            letterboxed,
            scalefactor=1.0 / 255.0,
            size=(self.MODEL_INPUT_SIZE, self.MODEL_INPUT_SIZE),
            swapRB=True,
            crop=False,
        )
        self.net.setInput(blob)
        try:
            outputs = self.net.forward(self.output_names)
        except cv2.error as error:
            self._log_forward_error(error)
            return None

        raw = np.squeeze(outputs[0])
        expected_width = 4 + len(COCO_CLASSES)
        if raw.shape[0] == expected_width:
            return raw.T, scale, pad_left, pad_top
        if raw.shape[-1] == expected_width:
            return raw, scale, pad_left, pad_top

        print(
            f"BALL MODEL WARNING: unexpected output shape {raw.shape}; "
            f"expected a dimension of size {expected_width}."
        )
        return None

    def detect_ball(self, frame):
        # Trả về tọa độ tâm và bán kính của bóng, hoặc None nếu không tìm thấy bóng.
        prediction_result = self._predict(frame)
        if prediction_result is None:
            return None, []
        predictions, scale, pad_left, pad_top = prediction_result

        boxes = []
        confidences = []
        candidates = []

        for row in predictions:
            class_scores = row[4:]
            class_id = int(np.argmax(class_scores))
            confidence = float(class_scores[class_id])
            if confidence >= self.DIAGNOSTIC_MIN_CONF:
                candidates.append((class_id, confidence))
            if class_id != self.SPORTS_BALL_CLASS_ID or confidence < self.BALL_CONF_THRESHOLD:
                continue

            boxes.append(list(self._to_frame_box(row, scale, pad_left, pad_top)))
            confidences.append(confidence)

        candidates.sort(key=lambda candidate: candidate[1], reverse=True)
        top_candidates = [
            (COCO_CLASSES[class_id], confidence)
            for class_id, confidence in candidates[:self.DIAGNOSTIC_TOP_N]
        ]
        if not boxes:
            return None, top_candidates

        indices = cv2.dnn.NMSBoxes(
            boxes, confidences, self.BALL_CONF_THRESHOLD, self.NMS_THRESHOLD
        )
        if len(indices) == 0:
            return None, top_candidates

        index = indices[0] if np.isscalar(indices[0]) else indices[0][0]
        left, top, box_width, box_height = boxes[index]
        return (
            left + box_width / 2,
            top + box_height / 2,
            max(box_width, box_height) / 2,
        ), top_candidates

    def detect_person(self, frame):
        # Trả về bounding box của người, hoặc None nếu không tìm thấy người.
        prediction_result = self._predict(frame)
        if prediction_result is None:
            return None
        predictions, scale, pad_left, pad_top = prediction_result

        boxes = []
        confidences = []
        for row in predictions:
            class_scores = row[4:]
            class_id = int(np.argmax(class_scores))
            confidence = float(class_scores[class_id])
            if class_id != self.PERSON_CLASS_ID or confidence < self.PERSON_CONF_THRESHOLD:
                continue

            boxes.append(list(self._to_frame_box(row, scale, pad_left, pad_top)))
            confidences.append(confidence)

        if not boxes:
            return None
        indices = cv2.dnn.NMSBoxes(
            boxes, confidences, self.PERSON_CONF_THRESHOLD, self.NMS_THRESHOLD
        )
        if len(indices) == 0:
            return None

        index = indices[0] if np.isscalar(indices[0]) else indices[0][0]
        left, top, box_width, box_height = boxes[index]
        return left, top, left + box_width, top + box_height

    def is_legs_only(self, person_box):
        # Kiểm tra xem bounding box của người có chỉ thấy chân hay không dựa trên chiều cao và vị trí của bounding box.
        _, top, _, bottom = person_box
        return (
            top <= self.LEG_ONLY_TOP_MARGIN_PX
            and (bottom - top) >= self.PERSON_MIN_HEIGHT_PX
        )

    def should_check_manual_stop(self):
        # Kiểm tra xem có cần kiểm tra lệnh dừng thủ công hay không.
        self._manual_stop_check_counter += 1
        return self._manual_stop_check_counter % self.MANUAL_STOP_CHECK_INTERVAL == 0

    def should_check_camera(self):
        # Kiểm tra xem có cần kiểm tra camera hay không.
        self._camera_check_counter += 1
        return self._camera_check_counter % self.CAMERA_CHECK_INTERVAL == 0

    def start_chase(self):
        # Bắt đầu theo dõi bóng, đặt lại các trạng thái liên quan.
        self.ball_ever_seen = False
        self.last_seen_ball_side = None
        self.chase_entry_time = time.time()
        self.last_ball_seen_time = time.time()

    def command_for_frame(self, frame):
        # Xác định lệnh điều khiển dựa trên khung hình hiện tại
        ball, top_candidates = self.detect_ball(frame)
        command, display_text, exit_reason = self._decide_chase_command(
            ball, frame.shape[1]
        )
        return ball, top_candidates, command, display_text, exit_reason

    def _decide_chase_command(self, ball, frame_width):
        frame_center = frame_width / 2.0
        if ball is not None:
            x, _, radius = ball
            self.ball_ever_seen = True
            self.last_seen_ball_side = "left" if x < frame_center else "right"
            self.last_ball_seen_time = time.time()

            if radius >= self.BALL_FOUND_RADIUS:
                return self.stop_command, "Ball found!", "found"
            if x < frame_center - self.BALL_CENTER_DEADZONE:
                return self.left_command, "Chasing: left", None
            if x > frame_center + self.BALL_CENTER_DEADZONE:
                return self.right_command, "Chasing: right", None
            return self.walk_command, "Chasing: forward", None

        if not self.ball_ever_seen:
            if time.time() - self.chase_entry_time >= self.NEVER_SEEN_TIMEOUT_S:
                return self.stop_command, "No ball found, giving up", "gave_up"
            return self.stop_command, "Searching (never seen yet)", None

        if time.time() - self.last_ball_seen_time >= self.SPIN_SEARCH_TIMEOUT_S:
            return self.stop_command, "Lost ball, giving up", "gave_up"
        command = (
            self.left_command
            if self.last_seen_ball_side == "left"
            else self.right_command
        )
        return command, f"Chasing: lost, spin {self.last_seen_ball_side}", None
