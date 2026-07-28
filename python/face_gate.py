"""
Face recognition gate: determines whether the person currently in frame
is a known/familiar face, an unfamiliar face, or nobody at all.

Built entirely on OpenCV's own face_detection/face_recognition classes
(cv2.FaceDetectorYN + cv2.FaceRecognizerSF) — no separate wrapper needed,
unlike the hand/ball pipelines, since these ship as native cv2 classes.
"""

import os
import json
import numpy as np
import cv2

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
FACE_DETECTOR_PATH = os.path.join(_THIS_DIR, "face_models", "face_detection_yunet_2023mar.onnx")
FACE_RECOGNIZER_PATH = os.path.join(_THIS_DIR, "face_models", "face_recognition_sface_2021dec.onnx")
KNOWN_FACES_DB_PATH = os.path.join(_THIS_DIR, "known_faces_db.json")

FACE_DETECT_CONF_THRESHOLD = 0.9  # OpenCV's own documented default
FACE_NMS_THRESHOLD = 0.3
FACE_TOP_K = 5000

# OpenCV's documented default threshold for SFace cosine similarity —
# scores at or above this are considered the same person.
FACE_MATCH_THRESHOLD = 0.363


class FaceGate:
    def __init__(self, detector_path=FACE_DETECTOR_PATH, recognizer_path=FACE_RECOGNIZER_PATH,
                 db_path=KNOWN_FACES_DB_PATH, input_size=(320, 240)):
        self.detector = cv2.FaceDetectorYN.create(
            detector_path, "", input_size,
            FACE_DETECT_CONF_THRESHOLD, FACE_NMS_THRESHOLD, FACE_TOP_K
        )
        self.recognizer = cv2.FaceRecognizerSF.create(recognizer_path, "")
        self.input_size = input_size
        self.known = self._load_db(db_path)

    def _load_db(self, path):
        if not os.path.exists(path):
            print(f"FACE GATE WARNING: no known-faces database at {path} — "
                  f"everyone will be treated as unfamiliar until you run "
                  f"enroll_faces.py to build one.")
            return {}
        with open(path) as f:
            raw = json.load(f)
        return {name: np.array(vec, dtype=np.float32) for name, vec in raw.items()}

    def _set_frame_size(self, width, height):
        if (width, height) != self.input_size:
            self.input_size = (width, height)
            self.detector.setInputSize(self.input_size)

    def recognize(self, frame):
        """Returns (status, name):
          status = 'familiar'   -> name is the matched person's name
          status = 'unfamiliar' -> a face was seen but didn't match anyone known
          status = 'none'       -> no face detected at all
        Only the single largest detected face is checked — multiple
        simultaneous faces aren't handled specially; assumes whoever is
        closest/most prominent is the one trying to control the robot.
        """
        h, w = frame.shape[:2]
        self._set_frame_size(w, h)

        _, faces = self.detector.detect(frame)
        if faces is None or len(faces) == 0:
            return 'none', None

        largest = max(faces, key=lambda f: f[2] * f[3])

        aligned = self.recognizer.alignCrop(frame, largest)
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


def verify_face_models():
    """Same fail-loud-at-startup pattern used for the ball model — a
    shape/compatibility mismatch here should be obvious immediately, not
    discovered later as a silent 'never recognizes anyone' bug."""
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
