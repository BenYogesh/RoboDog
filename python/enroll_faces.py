"""
Builds the known-faces database used by face_gate.py.

Usage:
  Put reference photos in a folder structure like:
    known_faces/
      ben/
        photo1.jpg
        photo2.jpg
      alice/
        photo1.jpg

  Then run:
    python3 enroll_faces.py known_faces/ known_faces_db.json

Multiple photos per person are averaged into a single embedding — 2-3
photos from slightly different angles/lighting tends to generalize
better than just one. Copy the resulting known_faces_db.json next to
face_gate.py on the robot.
"""

import cv2
import numpy as np
import json
import os
import sys

FACE_DETECTOR_PATH = "face_models/face_detection_yunet_2023mar.onnx"
FACE_RECOGNIZER_PATH = "face_models/face_recognition_sface_2021dec.onnx"


def enroll(photos_dir, output_path):
    detector = cv2.FaceDetectorYN.create(FACE_DETECTOR_PATH, "", (320, 320))
    recognizer = cv2.FaceRecognizerSF.create(FACE_RECOGNIZER_PATH, "")

    db = {}
    for name in sorted(os.listdir(photos_dir)):
        person_dir = os.path.join(photos_dir, name)
        if not os.path.isdir(person_dir):
            continue

        embeddings = []
        for filename in sorted(os.listdir(person_dir)):
            path = os.path.join(person_dir, filename)
            img = cv2.imread(path)
            if img is None:
                print(f"  skipping unreadable file: {path}")
                continue

            detector.setInputSize((img.shape[1], img.shape[0]))
            _, faces = detector.detect(img)
            if faces is None or len(faces) == 0:
                print(f"  no face found in {path}, skipping")
                continue

            largest = max(faces, key=lambda f: f[2] * f[3])
            aligned = recognizer.alignCrop(img, largest)
            feature = recognizer.feature(aligned)
            embeddings.append(feature.flatten())
            print(f"  enrolled a face from {path}")

        if embeddings:
            avg = np.mean(embeddings, axis=0)
            db[name] = avg.tolist()
            print(f"{name}: {len(embeddings)} photo(s) averaged\n")
        else:
            print(f"{name}: no usable photos found, skipped entirely\n")

    with open(output_path, 'w') as f:
        json.dump(db, f)
    print(f"Saved {len(db)} known face(s) to {output_path}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python3 enroll_faces.py <photos_dir> <output_json_path>")
        sys.exit(1)
    enroll(sys.argv[1], sys.argv[2])
