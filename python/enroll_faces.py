"""Build the known-faces database used by face_gate.py.

Folder layout:
  known_faces/
    lam/
      photo1.jpg
      photo2.jpg
    alice/
      photo1.jpg

Default usage from the project root:
  python3 python/enroll_faces.py

Or from inside the python/ folder:
  python3 enroll_faces.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

try:
    import cv2
except ModuleNotFoundError:
    cv2 = None

try:
    import numpy as np
except ModuleNotFoundError:
    np = None


THIS_DIR = Path(__file__).resolve().parent
DEFAULT_PHOTOS_DIR = THIS_DIR / "known_faces"
DEFAULT_OUTPUT_PATH = THIS_DIR / "known_faces_db.json"
FACE_DETECTOR_PATH = THIS_DIR / "face_models" / "face_detection_yunet_2023mar.onnx"
FACE_RECOGNIZER_PATH = THIS_DIR / "face_models" / "face_recognition_sface_2021dec.onnx"

IMAGE_EXTENSIONS = {".bmp", ".jpeg", ".jpg", ".png", ".webp"}
FACE_DETECT_CONF_THRESHOLD = 0.6
FACE_NMS_THRESHOLD = 0.3
FACE_TOP_K = 5000


def _check_dependencies() -> None:
    missing_packages = []
    if cv2 is None:
        missing_packages.append("opencv-python-headless")
    if np is None:
        missing_packages.append("numpy")
    if missing_packages:
        raise RuntimeError(
            "Missing Python package(s): "
            f"{', '.join(missing_packages)}. Install them with "
            "'python3 -m pip install -r python/requirements.txt'."
        )

    missing = [
        name for name in ("FaceDetectorYN", "FaceRecognizerSF")
        if not hasattr(cv2, name)
    ]
    if missing:
        found = getattr(cv2, "__version__", "unknown")
        raise RuntimeError(
            "This OpenCV build lacks the required face APIs: "
            f"{', '.join(missing)} (found cv2 {found})."
        )


def _check_model_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Missing model file: {path}")
    if path.stat().st_size < 1024:
        raise RuntimeError(
            f"Model file is suspiciously small: {path}. "
            "It may be a Git LFS pointer instead of the ONNX model."
        )


def _create_detector(score_threshold: float):
    _check_model_file(FACE_DETECTOR_PATH)
    return cv2.FaceDetectorYN.create(
        str(FACE_DETECTOR_PATH), "", (320, 320), score_threshold,
        FACE_NMS_THRESHOLD, FACE_TOP_K,
    )


def _create_recognizer():
    _check_model_file(FACE_RECOGNIZER_PATH)
    return cv2.FaceRecognizerSF.create(str(FACE_RECOGNIZER_PATH), "")


def _iter_people(photos_dir: Path):
    for person_dir in sorted(path for path in photos_dir.iterdir() if path.is_dir()):
        yield person_dir.name, person_dir


def _iter_images(person_dir: Path):
    for path in sorted(person_dir.iterdir()):
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
            yield path


def enroll(photos_dir: Path | str = DEFAULT_PHOTOS_DIR,
           output_path: Path | str = DEFAULT_OUTPUT_PATH,
           detect_threshold: float = FACE_DETECT_CONF_THRESHOLD) -> dict[str, list[float]]:
    photos_dir = Path(photos_dir).expanduser().resolve()
    output_path = Path(output_path).expanduser().resolve()

    if not photos_dir.is_dir():
        raise NotADirectoryError(f"Known-faces folder does not exist: {photos_dir}")

    _check_dependencies()
    detector = _create_detector(detect_threshold)
    recognizer = _create_recognizer()

    db: dict[str, list[float]] = {}
    for name, person_dir in _iter_people(photos_dir):
        embeddings = []
        for path in _iter_images(person_dir):
            image = cv2.imread(os.fspath(path))
            if image is None:
                print(f"  skipping unreadable file: {path}")
                continue

            detector.setInputSize((image.shape[1], image.shape[0]))
            _, faces = detector.detect(image)
            if faces is None or len(faces) == 0:
                print(f"  no face found in {path}, skipping")
                continue

            largest = max(faces, key=lambda face: face[2] * face[3])
            aligned = recognizer.alignCrop(image, largest)
            embeddings.append(recognizer.feature(aligned).reshape(-1).astype(np.float32))
            print(f"  enrolled a face from {path}")

        if embeddings:
            db[name] = np.mean(embeddings, axis=0).astype(np.float32).tolist()
            print(f"{name}: {len(embeddings)} photo(s) averaged\n")
        else:
            print(f"{name}: no usable photos found, skipped entirely\n")

    if not db:
        raise RuntimeError(
            "No usable faces were enrolled. Use clear, front-facing photos "
            "under known_faces/<person-name>/ and try again."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output:
        json.dump(db, output, indent=2)
        output.write("\n")
    print(f"Saved {len(db)} known face(s) to {output_path}")
    return db


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Build known_faces_db.json for the RoboDog face gate."
    )
    parser.add_argument("photos_dir", nargs="?", type=Path, default=DEFAULT_PHOTOS_DIR)
    parser.add_argument("output_json_path", nargs="?", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument(
        "--detect-threshold", type=float, default=FACE_DETECT_CONF_THRESHOLD,
        help="YuNet face detector confidence threshold (default: 0.6).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    try:
        enroll(args.photos_dir, args.output_json_path, args.detect_threshold)
    except Exception as error:
        print(f"Enrollment failed: {error}", file=sys.stderr)
        sys.exit(1)
