# Manual-control mode

Vietnamese version: [manual-control.vi.md](manual-control.vi.md)

The normal DogVision app now supports a `manual` voice command. It stops the
current autonomous motion, sends `MODE:MANUAL` to the ESP32 through the UNO Q
UART, and changes the UNO Q into a media relay. In this state, the Python app
does not run face, hand, ball, or voice movement decisions. A voice
`automatic` command is retained as a safety escape hatch; normal movement
voice commands are rejected while manual mode is active.
On the main ESP32 sketch, uppercase Bluetooth `M` enters manual mode and
uppercase `O` returns to automatic mode.

The media relay listens on the UNO Q LAN address:

These listeners have no authentication or encryption. Use them only on a
trusted private LAN for now, or put them behind a VPN/firewall before exposing
the UNO Q to a wider network.

| Port | Purpose | Wire format |
| ---: | --- | --- |
| 8080 | Dashboard and camera | `GET /`, `GET /camera.mjpg`, multipart MJPEG |
| 3334 | UNO Q USB microphone to laptop | `AUD0`, then 24 kHz, 1-channel, 16-bit PCM frames |
| 3335 | Laptop audio to robot | `AUD0`, then 16 kHz, 1-channel, 16-bit PCM frames |
| 3336 | ESP32 speaker receiver | The same 16 kHz mono stream sent by port 3335 |

For a quick laptop-side receiver, install `opencv-python` and `sounddevice`
on the laptop and run:

```bash
python python/manual_controller.py <uno-q-ip>
```

The first responsive browser dashboard is also served at:

```text
http://<uno-q-ip>:8080/
```

It works from a modern phone, tablet, laptop, or desktop browser on the same
private LAN. The camera feed is live in automatic mode as well as manual mode.
It provides dashboard manual-mode entry, return to automatic mode,
press-and-hold movement controls, posture/action/camera buttons, keyboard
shortcuts, language switching, and live status. The browser sends JSON
requests to `/api/mode` and `/api/command`, then polls `/api/status`.

Only one dashboard device is allowed at a time. The server identifies a device
by its LAN source address, renews the lease while the page is open, and releases
it when the page closes. A disconnected device's lease expires after 15 seconds
by default. Set `MANUAL_DASHBOARD_LEASE_S` to change the timeout, with a minimum
of 5 seconds.

The first version does not use the browser microphone. The optional
`manual_controller.py` client remains available for the audio relay.

With the supplied gait firmware, dashboard mode pauses the UNO Q vision loop
but leaves the ESP32 in its UART-accepting automatic mode. This is necessary
because that firmware accepts `CMD:<character>` from the UNO Q only in
`CONTROL_AUTOMATIC`. Bluetooth movement can still take priority; if Bluetooth
enters manual mode, the dashboard shows that Bluetooth owns the robot and
rejects browser movement until automatic mode is restored.

The helper displays the camera, plays the mono microphone stream, and sends the
laptop microphone to the robot speaker. Press `q` in the camera window to
stop. Use `--no-camera` or `--no-speaker` when testing only one direction.

Every PCM frame is preceded by a four-byte big-endian unsigned frame length.
The `AUD0` header is followed by three big-endian fields: sample rate (`uint32`),
channel count (`uint16`), and bits per sample (`uint16`). The UNO Q captures the
USB webcam microphone locally with ALSA, forwards the mono frame to Realtime
speech recognition, and relays it to port 3334 when manual mode is active.
Port 3333 remains only as an optional legacy receiver for old ESP32/INMP441
firmware.

## Hardware and firmware boundary

The main gait/Bluetooth sketch is maintained separately at
`Code/dog_esp32/dog_esp32.ino`. It implements these UART frames:

```text
MODE:MANUAL\n   -> accept Bluetooth movement commands; ignore Uno Q CMD frames
MODE:AUTO\n     -> restore the existing Uno Q/automatic command path
```

The gait firmware keeps Bluetooth mode commands and the movement watchdog local
to the ESP32. That makes “only Bluetooth commands” enforceable even if the UNO Q
network or Python process stops. Bluetooth mode changes are sent back over the
UART as `MODE:MANUAL`/`MODE:AUTO`, and the UNO Q forwards them to Linux through
`Bridge.notify` so the media relay follows either entry path.

The webcam microphone is connected to the UNO Q through USB. The main gait
sketch only needs the MAX98357A I2S output now; confirm its exact pins in the
gait firmware before wiring the robot.
The previous `esp32_speech_test` sketch remains a standalone diagnostic; do not
flash it as the robot's gait firmware.

## Test sequence

1. Set the UNO Q runtime's `OPENAI_API_KEY` only if voice recognition is wanted.
   The camera and USB microphone relay still starts without it. Use
   `python3 python/usb_mic_test.py --list` and the USB microphone test in
   `docs/speech-test.md` to select and verify the webcam capture device first.
2. Make sure the laptop and UNO Q are on the same LAN. Run the normal DogVision
   app and note the UNO Q IP address from its network settings or terminal.
3. Copy `Code/dog_esp32/secrets.example.h` to a local `secrets.h`, fill in the
   Wi-Fi values, and upload `Code/dog_esp32/data/sounds/` as the ESP32 LittleFS
   image. Flash the main `dog_esp32.ino` sketch. Confirm that its serial log
   reports the MAX98357A speaker as ready and the UNO Q terminal reports the
   selected USB microphone device. The old `mic=ready` ESP32 status is only
   expected if the legacy INMP441 path is still enabled.
4. Say “manual control”. The UNO Q log should show `MANUAL_CONTROL_ENTERED`
   and `MANUAL_MEDIA_ACTIVE`; the OLED should show `MANUAL CONTROL`.
5. Open `http://<uno-q-ip>:8080/` in a browser. The camera should already be
   live in automatic mode. Press **Take dashboard control** before testing
   movement buttons, and test only with the robot lifted or in a safe open
   area. Press and hold movement buttons; releasing them sends stop.
6. Open `http://<uno-q-ip>:8080/camera.mjpg` directly if a raw camera stream is
   needed. A media client must
   connect to TCP port 3334 and parse the `AUD0` header plus framed mono PCM
   to hear the microphone.
7. For speaker testing, connect the laptop to TCP port 3335, send the matching
   16 kHz mono `AUD0` header, then send framed PCM16. The ESP32 must already be
   connected to port 3336; audio is discarded unless manual mode is active.
8. Test Bluetooth `M`, movement commands, and the ESP32 watchdog. Press
   Bluetooth `O` or say “automatic”, then confirm `MODE:AUTO`,
   `MANUAL_MEDIA_INACTIVE`, and normal vision behavior.

Ports can be changed with `MANUAL_VIDEO_PORT`, `MANUAL_AUDIO_PORT`,
`MANUAL_SPEAKER_PORT`, and `MANUAL_ROBOT_SPEAKER_PORT`. The optional legacy
ESP32 speech input port remains configurable with `UNO_Q_AUDIO_PORT`.
