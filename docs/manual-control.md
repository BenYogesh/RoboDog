# Manual-control mode

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
| 8080 | Camera | `GET /camera.mjpg`, multipart MJPEG |
| 3334 | Microphone to laptop | `AUD0`, then 24 kHz, 1-channel, 16-bit PCM frames |
| 3335 | Laptop audio to robot | `AUD0`, then 16 kHz, 1-channel, 16-bit PCM frames |
| 3336 | ESP32 speaker receiver | The same 16 kHz mono stream sent by port 3335 |

For a quick laptop-side receiver, install `opencv-python` and `sounddevice`
on the laptop and run:

```bash
python python/manual_controller.py <uno-q-ip>
```

The helper displays the camera, plays the mono microphone stream, and sends the
laptop microphone to the robot speaker. Press `q` in the camera window to
stop. Use `--no-camera` or `--no-speaker` when testing only one direction.

Every PCM frame is preceded by a four-byte big-endian unsigned frame length.
The `AUD0` header is followed by three big-endian fields: sample rate (`uint32`),
channel count (`uint16`), and bits per sample (`uint16`). The ESP32 microphone
stream to port 3333 also sends this header now; the UNO Q forwards the mono
frame to Realtime speech recognition and relays it to port 3334 when manual
mode is active.

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

The main gait sketch uses one INMP441 microphone on its I2S input. The
MAX98357A uses its separate I2S output. Confirm the exact pins in the gait
firmware before wiring the robot.
The previous `esp32_speech_test` sketch remains a standalone diagnostic; do not
flash it as the robot's gait firmware.

## Test sequence

1. Set the UNO Q runtime's `OPENAI_API_KEY` only if voice recognition is wanted.
   The camera and microphone relay still starts without it.
2. Make sure the laptop and UNO Q are on the same LAN. Run the normal DogVision
   app and note the UNO Q IP address from its network settings or terminal.
3. Copy `Code/dog_esp32/secrets.example.h` to a local `secrets.h`, fill in the
   Wi-Fi values, and upload `Code/dog_esp32/data/sounds/` as the ESP32 LittleFS
   image. Flash the main `dog_esp32.ino` sketch. Confirm that its serial log
   reports `Speech audio: mic=ready speaker=ready` and both network streams.
4. Say “manual control”. The UNO Q log should show `MANUAL_CONTROL_ENTERED`
   and `MANUAL_MEDIA_ACTIVE`; the OLED should show `MANUAL CONTROL`.
5. Open `http://<uno-q-ip>:8080/camera.mjpg` in a browser. A media client must
   connect to TCP port 3334 and parse the `AUD0` header plus framed mono PCM
   to hear the microphone.
6. For speaker testing, connect the laptop to TCP port 3335, send the matching
   16 kHz mono `AUD0` header, then send framed PCM16. The ESP32 must already be
   connected to port 3336; audio is discarded unless manual mode is active.
7. Test Bluetooth `M`, movement commands, and the ESP32 watchdog. Press
   Bluetooth `O` or say “automatic”, then confirm `MODE:AUTO`,
   `MANUAL_MEDIA_INACTIVE`, and normal vision behavior.

Ports can be changed with `MANUAL_VIDEO_PORT`, `MANUAL_AUDIO_PORT`,
`MANUAL_SPEAKER_PORT`, and `MANUAL_ROBOT_SPEAKER_PORT`. The existing ESP32
speech input port remains configurable with `UNO_Q_AUDIO_PORT`.
