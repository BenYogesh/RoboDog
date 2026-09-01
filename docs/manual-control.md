# Manual control

The production ESP32 firmware owns the Bluetooth mode switch:

- Send uppercase `M` to enter manual mode.
- Send uppercase `O` to return to automatic mode.
- Send the lowercase movement and camera command bytes while manual mode is active.

When `M` is received, the ESP32 stops the current motion, accepts movement
from Bluetooth only, and reports `MODE:MANUAL` to the UNO Q. The UNO Q then
enables the webcam/dashboard listener and pauses autonomous movement decisions.
Hand inference remains active for monitoring; face and ball decisions resume
when automatic mode is restored.

## Browser dashboard

Open this page from a device on the same trusted LAN:

```text
http://<uno-q-ip>:8080/
```

The dashboard provides:

- live webcam video at `/camera.mjpg`;
- a button to take or release dashboard control;
- press-and-hold movement controls with a stop-on-release safeguard;
- posture, action, and camera commands;
- a **Restart camera** action for recovering from a USB power dip;
- keyboard shortcuts (`W/A/S/D`, `E/F`, `B`, `Q`, `C`, `Z`, and Space);
- bilingual English/Vietnamese labels and live control status.

Only one dashboard device is allowed at a time. The server identifies a device
by its LAN source address and renews a 15-second lease while the page is open.
The lease can be changed with `MANUAL_DASHBOARD_LEASE_S` (minimum 5 seconds).
The listener has no authentication or encryption, so keep it on a trusted
private LAN or behind a VPN/firewall.

The dashboard's **Take dashboard control** action pauses Python autonomy while
leaving the ESP32 in its UART-accepting automatic mode. Browser commands are
then sent as the same framed `CMD:<character>` messages used by the UNO Q.
If Bluetooth sends `M` while the dashboard is open, Bluetooth takes ownership;
the dashboard remains visible but its movement requests are rejected until
automatic mode is restored.

### Camera recovery

`CameraStream` watches for consecutive failed reads. After five failures it
releases and reopens the V4L2 device in its reader thread, retrying with a
short backoff if the webcam is still powered down. This recovery does not block
the vision/control loop. The dashboard's **Restart camera** button calls
`POST /api/camera/restart` to request the same release/reopen path manually.
The status endpoint reports `camera_live`, `camera_restart_count`, and the last
camera error.

## Firmware boundary

The ESP32 firmware is `dog_esp32/dog_esp32.ino`. It implements these UART
frames from the UNO Q:

```text
MODE:MANUAL\n   -> accept Bluetooth movement and reject Uno Q CMD frames
MODE:AUTO\n     -> restore the Uno Q command path
CMD:<char>\n   -> one recognized movement or camera command
```

Bluetooth mode changes are sent back over UART as `MODE:MANUAL`/`MODE:AUTO`,
and `sketch/sketch.ino` forwards them to Python through `Bridge.notify`.
The ESP32 keeps the local `MOTION_COMMAND_TIMEOUT_MS` watchdog, so a sustained
movement stops when Bluetooth commands stop arriving.

## Board test sequence

1. Flash `sketch/sketch.ino` to the UNO Q MCU.
2. Flash `dog_esp32/dog_esp32.ino` to the ESP32 and pair with
   `RoboDog_ESP32`.
3. Start DogVision. Confirm hand gestures, face recognition, and ball tracking
   operate in automatic mode.
4. Send Bluetooth `M`. Confirm `CONTROL_MODE=MANUAL`, the UNO Q reports
   `MANUAL_VIDEO_ACTIVE`, and automatic movement decisions stop.
5. Open `http://<uno-q-ip>:8080/` and confirm the webcam is live. The browser
   movement buttons remain disabled while Bluetooth owns the robot.
6. Send Bluetooth `O`, then use **Take dashboard control**. Test movement only
   with the robot lifted or in a clear, supervised area; release a movement
   button and confirm it sends stop.
7. Confirm the dashboard shows the last command and hand/face status. Press
   **Return to automatic**, then verify automatic vision behavior resumes.
