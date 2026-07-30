"""YOLO-based ball chasing and person framing for the RoboDog vision app."""

from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np


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
    """Owns the YOLO model and state that persists during ball chasing."""

    MODEL_INPUT_SIZE = 320
    SPORTS_BALL_CLASS_ID = 32
    PERSON_CLASS_ID = 0
    BALL_CONF_THRESHOLD = 0.25
    PERSON_CONF_THRESHOLD = 0.4
    NMS_THRESHOLD = 0.45
    DIAGNOSTIC_MIN_CONF = 0.15
    DIAGNOSTIC_TOP_N = 5
    BALL_FOUND_RADIUS = 45
    BALL_CENTER_DEADZONE = 25
    NEVER_SEEN_TIMEOUT_S = 6.0
    SPIN_SEARCH_TIMEOUT_S = 6.0
    MANUAL_STOP_CHECK_INTERVAL = 5
    CAMERA_CHECK_INTERVAL = 5
    LEG_ONLY_TOP_MARGIN_PX = 15
    PERSON_MIN_HEIGHT_PX = 40

    def __init__(self, model_path: str | Path, walk_command: str, stop_command: str,
                 left_command: str, right_command: str):
        self.net = cv2.dnn.readNet(str(model_path))
        self.output_names = self.net.getUnconnectedOutLayersNames()
        self.walk_command = walk_command
        self.stop_command = stop_command
        self.left_command = left_command
        self.right_command = right_command
        self._forward_error_count = 0
        self._manual_stop_check_counter = 0
        self._camera_check_counter = 0
        self.ball_ever_seen = False
        self.last_seen_ball_side = None
        self.chase_entry_time = 0.0
        self.last_ball_seen_time = 0.0
        self._verify_model_input_size()

    def _verify_model_input_size(self):
        dummy = np.zeros(
            (self.MODEL_INPUT_SIZE, self.MODEL_INPUT_SIZE, 3), dtype=np.uint8
        )
        blob = cv2.dnn.blobFromImage(
            dummy,
            scalefactor=1.0 / 255.0,
            size=(self.MODEL_INPUT_SIZE, self.MODEL_INPUT_SIZE),
            swapRB=True,
            crop=False,
        )
        try:
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
        self._forward_error_count += 1
        if self._forward_error_count % 30 == 1:
            print(
                "BALL MODEL forward() failed "
                f"({self._forward_error_count} times so far): {error}"
            )

    def _predict(self, frame):
        blob = cv2.dnn.blobFromImage(
            frame,
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
            return raw.T
        if raw.shape[-1] == expected_width:
            return raw

        print(
            f"BALL MODEL WARNING: unexpected output shape {raw.shape}; "
            f"expected a dimension of size {expected_width}."
        )
        return None

    def detect_ball(self, frame):
        """Return ((x, y, radius) | None, diagnostic class candidates)."""
        height, width = frame.shape[:2]
        predictions = self._predict(frame)
        if predictions is None:
            return None, []

        scale_x = width / self.MODEL_INPUT_SIZE
        scale_y = height / self.MODEL_INPUT_SIZE
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

            center_x, center_y, box_width, box_height = row[:4]
            left = (center_x - box_width / 2) * scale_x
            top = (center_y - box_height / 2) * scale_y
            boxes.append([left, top, box_width * scale_x, box_height * scale_y])
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
        """Return the highest-confidence person box, or None."""
        height, width = frame.shape[:2]
        predictions = self._predict(frame)
        if predictions is None:
            return None

        scale_x = width / self.MODEL_INPUT_SIZE
        scale_y = height / self.MODEL_INPUT_SIZE
        boxes = []
        confidences = []
        for row in predictions:
            class_scores = row[4:]
            class_id = int(np.argmax(class_scores))
            confidence = float(class_scores[class_id])
            if class_id != self.PERSON_CLASS_ID or confidence < self.PERSON_CONF_THRESHOLD:
                continue

            center_x, center_y, box_width, box_height = row[:4]
            left = (center_x - box_width / 2) * scale_x
            top = (center_y - box_height / 2) * scale_y
            boxes.append([left, top, box_width * scale_x, box_height * scale_y])
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
        _, top, _, bottom = person_box
        return (
            top <= self.LEG_ONLY_TOP_MARGIN_PX
            and (bottom - top) >= self.PERSON_MIN_HEIGHT_PX
        )

    def should_check_manual_stop(self):
        self._manual_stop_check_counter += 1
        return self._manual_stop_check_counter % self.MANUAL_STOP_CHECK_INTERVAL == 0

    def should_check_camera(self):
        self._camera_check_counter += 1
        return self._camera_check_counter % self.CAMERA_CHECK_INTERVAL == 0

    def start_chase(self):
        self.ball_ever_seen = False
        self.last_seen_ball_side = None
        self.chase_entry_time = time.time()
        self.last_ball_seen_time = time.time()

    def command_for_frame(self, frame):
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
