"""
Code phát hiện cử chỉ bàn tay bằng cách sử dụng mô hình MediaPipe Palm Detection và Hand Pose Estimation.
"""

import os
import numpy as np
from hand_models.mp_palmdet import MPPalmDet
from hand_models.mp_handpose import MPHandPose

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PALM_MODEL_PATH = os.path.join(_THIS_DIR, "hand_models", "palm_detection_mediapipe_2023feb.onnx")
HAND_MODEL_PATH = os.path.join(_THIS_DIR, "hand_models", "handpose_estimation_mediapipe_2023feb.onnx")

# Verified from mp_handpose.py's _postprocess() return format:
# [0:4]=bbox, [4:67]=21 screen landmarks (x,y,z)*21, [67:130]=world landmarks,
# [130]=handedness (0=left,1=right), [131]=confidence
SCREEN_LANDMARKS_START = 4
SCREEN_LANDMARKS_END = 67
HANDEDNESS_IDX = 130
CONF_IDX = 131

# MediaPipe hand landmark ids (same numbering as the official docs)
WRIST = 0
THUMB_TIP = 4
INDEX_TIP = 8
MIDDLE_TIP = 12
RING_TIP = 16
PINKY_TIP = 20
INDEX_MCP = 5   # knuckle (base) joints, used as a reference for "curled vs extended"
MIDDLE_MCP = 9
RING_MCP = 13
PINKY_MCP = 17


class HandGestureDetector:
    def __init__(self, palm_model_path=PALM_MODEL_PATH, hand_model_path=HAND_MODEL_PATH,
                 score_threshold=0.6, conf_threshold=0.8):
        self.palm_detector = MPPalmDet(modelPath=palm_model_path,
                                        nmsThreshold=0.3,
                                        scoreThreshold=score_threshold)
        self.hand_detector = MPHandPose(modelPath=hand_model_path,
                                         confThreshold=conf_threshold)

    def detect(self, frame):
        """Returns a list of hands, each as a dict with 'landmarks' (21x3
        array, image-pixel x/y + relative z), 'handedness' ('Left'/'Right'),
        and 'confidence'. Empty list if no hand found."""
        palms = self.palm_detector.infer(frame)
        hands = []
        for palm in palms:
            result = self.hand_detector.infer(frame, palm)
            if result is None:
                continue
            landmarks = result[SCREEN_LANDMARKS_START:SCREEN_LANDMARKS_END].reshape(21, 3)
            handedness = "Right" if result[HANDEDNESS_IDX] > 0.5 else "Left"
            confidence = result[CONF_IDX]
            hands.append({
                "landmarks": landmarks,
                "handedness": handedness,
                "confidence": confidence,
            })
        return hands


# --- EXAMPLE GESTURE CLASSIFICATION ---
# Same pattern as your arm-gesture classifiers: distances/positions between
# landmarks, not a learned classifier. Tune against your own debug snapshots.

def _dist(a, b):
    return float(np.linalg.norm(a[:2] - b[:2]))  # 2D pixel distance, ignore z


def is_open_palm(landmarks):
    """All 4 fingertips (excluding thumb) are farther from the wrist than
    their corresponding knuckle — i.e. fingers are extended."""
    wrist = landmarks[WRIST]
    pairs = [(INDEX_TIP, INDEX_MCP), (MIDDLE_TIP, MIDDLE_MCP),
             (RING_TIP, RING_MCP), (PINKY_TIP, PINKY_MCP)]
    return all(_dist(landmarks[tip], wrist) > _dist(landmarks[mcp], wrist) for tip, mcp in pairs)


def is_fist(landmarks):
    """All 4 fingertips are closer to the wrist than their knuckles —
    i.e. fingers are curled in."""
    wrist = landmarks[WRIST]
    pairs = [(INDEX_TIP, INDEX_MCP), (MIDDLE_TIP, MIDDLE_MCP),
             (RING_TIP, RING_MCP), (PINKY_TIP, PINKY_MCP)]
    return all(_dist(landmarks[tip], wrist) < _dist(landmarks[mcp], wrist) for tip, mcp in pairs)


def is_pointing(landmarks):
    """Index finger extended, other three fingers curled — classic
    'point' gesture."""
    wrist = landmarks[WRIST]
    index_extended = _dist(landmarks[INDEX_TIP], wrist) > _dist(landmarks[INDEX_MCP], wrist)
    others_curled = all(
        _dist(landmarks[tip], wrist) < _dist(landmarks[mcp], wrist)
        for tip, mcp in [(MIDDLE_TIP, MIDDLE_MCP), (RING_TIP, RING_MCP), (PINKY_TIP, PINKY_MCP)]
    )
    return index_extended and others_curled


def classify_hand_gesture(landmarks):
    """Returns a string label for the recognized gesture, or 'UNKNOWN'."""
    if is_open_palm(landmarks):
        return "OPEN_PALM"
    if is_fist(landmarks):
        return "FIST"
    if is_pointing(landmarks):
        return "POINTING"
    return "UNKNOWN"


# --- EXAMPLE STANDALONE USAGE ---
if __name__ == "__main__":
    import cv2

    detector = HandGestureDetector()
    cap = cv2.VideoCapture(0)

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        hands = detector.detect(frame)
        for hand in hands:
            gesture = classify_hand_gesture(hand["landmarks"])
            print(f"{hand['handedness']} hand: {gesture} (conf={hand['confidence']:.2f})")

            for x, y, z in hand["landmarks"]:
                cv2.circle(frame, (int(x), int(y)), 3, (0, 255, 0), -1)

        cv2.imshow("Hand Gesture Debug", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()