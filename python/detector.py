"""
Code phát hiện cử chỉ bàn tay bằng cách sử dụng mô hình MediaPipe Palm Detection và Hand Pose Estimation.
"""

# Khai báo thư viện sử dụng
import os
import numpy as np
from hand_models.mp_palmdet import MPPalmDet
from hand_models.mp_handpose import MPHandPose

# Địa chỉ của các file ONNX của mô hình MediaPipe
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PALM_MODEL_PATH = os.path.join(_THIS_DIR, "hand_models", "palm_detection_mediapipe_2023feb.onnx")
HAND_MODEL_PATH = os.path.join(_THIS_DIR, "hand_models", "handpose_estimation_mediapipe_2023feb.onnx")

# Các chỉ số cần sử dụng trong mảng kết quả trả về từ mô hình MediaPipe
# [0:4]=bbox, [4:67]=21 screen landmarks (x,y,z)*21, [67:130]=world landmarks,
# [130]=handedness (0=left,1=right), [131]=confidence
SCREEN_LANDMARKS_START = 4  # Chỉ số khởi đầu của các điểm mốc trên màn hình
SCREEN_LANDMARKS_END = 67   # Chỉ số kết thúc của các điểm mốc trên màn hình
HANDEDNESS_IDX = 130        # Chỉ số của thông tin bàn tay trái/phải
CONF_IDX = 131              # Chỉ số của độ tin cậy dự đoán bàn tay

# Các chỉ số của các điểm mốc quan trọng trên bàn tay từ kết quả nhận diện của MediaPipe
WRIST = 0       # Cổ tay
THUMB_TIP = 4   # Đầu ngón cái
INDEX_TIP = 8   # Đầu ngón trỏ   
MIDDLE_TIP = 12 # Đầu ngón giữa
RING_TIP = 16   # Đầu ngón áp út
PINKY_TIP = 20  # Đầu ngón út
INDEX_MCP = 5   # Khớp nối ngón trỏ
MIDDLE_MCP = 9  # Khớp nối ngón giữa
RING_MCP = 13   # Khớp nối ngón áp út 
PINKY_MCP = 17  # Khớp nối ngón út


class HandGestureDetector:
    # Lớp phát hiện cử chỉ bàn tay bằng cách sử dụng mô hình MediaPipe Palm Detection và Hand Pose Estimation.
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
        # Trả về thông tin chi tiết về các bàn tay được phát hiện trong khung hình
        palms = self.palm_detector.infer(frame) # Phát hiện các bàn tay trong khung hình
        hands = []                              # Khởi tạo mảng lưu trữ thông tin bàn tay được phát hiện
        for palm in palms:
            # Duyệt qua tưng từng bàn tay được phát hiện và thực hiện nhận diện các điểm mốc trên bàn tay
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


def _dist(a, b):
    # Trả về khoảng cách giữa 2 pixel
    return float(np.linalg.norm(a[:2] - b[:2]))  


def is_open_palm(landmarks):
    # Kiểm tra xem bàn tay có đang mở hay không
    wrist = landmarks[WRIST]
    pairs = [(INDEX_TIP, INDEX_MCP), (MIDDLE_TIP, MIDDLE_MCP),
             (RING_TIP, RING_MCP), (PINKY_TIP, PINKY_MCP)]
    return all(_dist(landmarks[tip], wrist) > _dist(landmarks[mcp], wrist) for tip, mcp in pairs)


def is_fist(landmarks):
    # Kiểm tra xem bàn tay có đang nắm hay không
    wrist = landmarks[WRIST]
    pairs = [(INDEX_TIP, INDEX_MCP), (MIDDLE_TIP, MIDDLE_MCP),
             (RING_TIP, RING_MCP), (PINKY_TIP, PINKY_MCP)]
    return all(_dist(landmarks[tip], wrist) < _dist(landmarks[mcp], wrist) for tip, mcp in pairs)


def is_pointing(landmarks):
    # Kiểm tra xem bàn tay có đang chỉ ra hay không
    wrist = landmarks[WRIST]
    index_extended = _dist(landmarks[INDEX_TIP], wrist) > _dist(landmarks[INDEX_MCP], wrist)
    others_curled = all(
        _dist(landmarks[tip], wrist) < _dist(landmarks[mcp], wrist)
        for tip, mcp in [(MIDDLE_TIP, MIDDLE_MCP), (RING_TIP, RING_MCP), (PINKY_TIP, PINKY_MCP)]
    )
    return index_extended and others_curled


def classify_hand_gesture(landmarks):
    # Trả về nhãn chuỗi cho cử chỉ được nhận diện, hoặc 'UNKNOWN'
    if is_open_palm(landmarks):
        return "OPEN_PALM"
    if is_fist(landmarks):
        return "FIST"
    if is_pointing(landmarks):
        return "POINTING"
    return "UNKNOWN"


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