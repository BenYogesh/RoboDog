# DogVision

Arduino UNO Q vision app for the RoboDog quadruped.

For the Vietnamese project guide, see
[`README.vi.md`](README.vi.md).

For a guided explanation of the code layout and runtime dataflow, see
[`docs/system-structure-dataflow.md`](docs/system-structure-dataflow.md).

For compact pseudocode of the vision/control path, see
[`docs/project-pseudocode.md`](docs/project-pseudocode.md) or the Vietnamese
version at [`docs/project-pseudocode.vi.md`](docs/project-pseudocode.vi.md).

For the SG90 continuous-servo camera scan feature, including wiring, tuning,
and board-side testing, see
[`docs/camera-scan-instructions.md`](docs/camera-scan-instructions.md) or the
Vietnamese version at
[`docs/camera-scan-instructions.vi.md`](docs/camera-scan-instructions.vi.md).

For the ESP32 leg kinematics, gait tuning, balancing, and safe gait tests, see
[`docs/esp32-gait-control.md`](docs/esp32-gait-control.md).

For manual Bluetooth control with camera/microphone streaming and optional
laptop speaker audio, see
[`docs/manual-control.md`](docs/manual-control.md) or the Vietnamese version at
[`docs/manual-control.vi.md`](docs/manual-control.vi.md).

For the speech-recognition and audio test workflow, see
[`docs/speech-test.md`](docs/speech-test.md) or the Vietnamese version at
[`docs/speech-test.vi.md`](docs/speech-test.vi.md).

The first responsive browser dashboard is served by the UNO Q at
`http://<uno-q-ip>:8080/` while the DogVision app is running. It provides the
camera view, dashboard-owned manual movement, posture/action/camera buttons,
mode switching, and live status.

The Python app is `python/main.py` and uses this camera path by default (override
with `UNO_Q_CAMERA_PATH` if needed):

```text
/dev/v4l/by-id/usb-HX-MT9M114-201012_Integrated_Camera-video-index0
```

The integrated webcam microphone is captured directly by the UNO Q Linux side.
See [`docs/speech-test.md`](docs/speech-test.md) for device discovery and the
`python/usb_mic_test.py` microphone test.

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
