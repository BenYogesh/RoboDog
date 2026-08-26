# DogVision

Arduino UNO Q vision app for the RoboDog quadruped.

For a guided explanation of the code layout and runtime dataflow, see
[`docs/system-structure-dataflow.md`](docs/system-structure-dataflow.md).

For the SG90 continuous-servo camera scan feature, including wiring, tuning,
and board-side testing, see
[`docs/camera-scan-instructions.md`](docs/camera-scan-instructions.md).

For the ESP32 leg kinematics, gait tuning, balancing, and safe gait tests, see
[`docs/esp32-gait-control.md`](docs/esp32-gait-control.md).

For manual Bluetooth control with camera/microphone streaming and optional
laptop speaker audio, see
[`docs/manual-control.md`](docs/manual-control.md).

The Python app is `python/main.py` and uses this camera path:

```text
/dev/v4l/by-id/usb-046d_C270_HD_WEBCAM_E21C4540-video-index0
```

## Enroll Known Faces

Put clear photos under `python/known_faces/<person-name>/`, preferably taken
from the RoboDog camera at the same distance and lighting used during control.
Then run this from the project root:

```bash
python3 -m pip install -r python/requirements.txt
python3 python/enroll_faces.py
```

This creates `python/known_faces_db.json`, which `python/face_gate.py` loads
when the vision app starts.
