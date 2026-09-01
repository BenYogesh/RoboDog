# DogVision

Arduino UNO Q vision app for the RoboDog quadruped.

Included capabilities:

- Hand-gesture detection and movement commands
- Face recognition and familiar-face gating
- Ball detection and autonomous tracking
- Bluetooth-only manual control on the ESP32
- Browser dashboard with live webcam feed and manual control

For a guided explanation of the code layout and runtime dataflow, see
[`docs/system-structure-dataflow.md`](docs/system-structure-dataflow.md).

For the SG90 continuous-servo camera scan feature, including wiring, tuning,
and board-side testing, see
[`docs/camera-scan-instructions.md`](docs/camera-scan-instructions.md).

For the ESP32 leg kinematics, gait tuning, balancing, and safe gait tests, see
[`docs/esp32-gait-control.md`](docs/esp32-gait-control.md).

For Bluetooth/manual dashboard control with a live webcam feed, see
[`docs/manual-control.md`](docs/manual-control.md).

The Python app is `python/main.py` and uses this camera path:

```text
/dev/v4l/by-id/usb-046d_C270_HD_WEBCAM_E21C4540-video-index0
```

The dashboard is served on port `8080` while DogVision is running. Bluetooth
manual mode remains owned by the ESP32; the browser can take control only after
Bluetooth has returned the robot to automatic mode.

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
