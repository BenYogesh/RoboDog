# Manual Bluetooth control

Manual mode is entered only from the ESP32 Bluetooth connection:

- Send uppercase `M` to enter manual mode.
- Send uppercase `O` to return to automatic mode.
- Send the existing lowercase movement and camera command bytes while manual
  mode is active.

When `M` is received, the ESP32 immediately stops the current movement,
switches to Bluetooth-only command ownership, and reports `MODE:MANUAL` to
the UNO Q over UART. The UNO Q forwards that notification to the Python app.
The app pauses hand-gesture detection, face recognition, and ball tracking,
then enables the webcam endpoint.

When `O` is received, the ESP32 reports `MODE:AUTO`. The webcam endpoint is
disabled and the three automatic vision pipelines resume.

## Webcam feed

The UNO Q listens on port 8080. While manual mode is active, open:

```text
http://<uno-q-ip>:8080/camera.mjpg
```

The endpoint is an MJPEG stream and works in a browser or OpenCV. It returns
HTTP 503 while automatic mode is active. The port can be changed with the
`MANUAL_VIDEO_PORT` environment variable.

The listener has no authentication or encryption. Use it only on a trusted
private LAN or behind a VPN/firewall.

## Firmware boundary

The production ESP32 sketch is included at
`dog_esp32/dog_esp32.ino`. It owns the control-mode decision:

```text
Bluetooth M  -> stop, enter manual mode, notify MODE:MANUAL
Bluetooth O  -> stop, enter automatic mode, notify MODE:AUTO
```

In manual mode, the ESP32 continues parsing UNO Q UART frames so input cannot
accumulate, but it rejects every UNO Q movement command. Bluetooth movement
commands remain subject to the local `MOTION_COMMAND_TIMEOUT_MS` safety
watchdog.

The UNO Q sketch only forwards the ESP32 mode notification to Python. It
cannot enter manual mode itself, which keeps manual command ownership tied to
the Bluetooth connection.

## Board test sequence

1. Flash `sketch/sketch.ino` to the UNO Q MCU.
2. Flash `dog_esp32/dog_esp32.ino` to the ESP32 and pair with
   `RoboDog_ESP32`.
3. Start the DogVision app. Confirm gesture detection, face recognition, and
   ball tracking operate in automatic mode.
4. Send uppercase `M` over Bluetooth. Confirm the ESP32 prints
   `CONTROL_MODE=MANUAL`, the UNO Q app prints `MANUAL_VIDEO_ACTIVE`, and
   autonomous movement commands no longer reach the gait controller.
5. Open `http://<uno-q-ip>:8080/camera.mjpg` and verify a live webcam feed.
6. Send movement commands over Bluetooth and confirm the watchdog stops a
   sustained motion when commands are no longer refreshed.
7. Send uppercase `O`. Confirm `CONTROL_MODE=AUTOMATIC`, the webcam request
   returns HTTP 503, and automatic vision control resumes.
