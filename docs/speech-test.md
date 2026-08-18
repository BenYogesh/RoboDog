# Speech-only test build

This test intentionally bypasses the existing camera, gait, and vision code.

```mermaid
flowchart LR
  M[INMP441] -->|24 kHz PCM16 over TCP| Q[UNO Q Linux]
  Q -->|Realtime WebSocket| O[gpt-realtime-2.1-mini]
  O -->|play_sound function call| Q
  Q -->|PLAY:beep\n over Serial1| E[ESP32]
  E --> A[MAX98357A]
```

## Files

- `python/speech_test.py`: UNO Q TCP receiver and Realtime client.
- `sketch/sketch.ino`: adds the safe `play_test_sound` Bridge function.
- `esp32_speech_test/speech_test.ino`: one-microphone capture and local tone playback.

## Wiring for this first test

Use one INMP441 and one MAX98357A. The pin numbers below are starting points;
check them against the existing ESP32 servo wiring before powering the board.

| Device | Signal | ESP32 pin |
| --- | --- | ---: |
| INMP441 | SCK/BCLK | GPIO26 |
| INMP441 | WS/LRCLK | GPIO25 |
| INMP441 | SD | GPIO33 |
| INMP441 | L/R | GND (left slot) |
| MAX98357A | BCLK | GPIO14 |
| MAX98357A | LRC/WS | GPIO13 |
| MAX98357A | DIN | GPIO32 |
| UNO Q ↔ ESP32 | UART2 RX/TX | GPIO16/GPIO17 |

Connect grounds together. Power the INMP441 from 3.3 V. Connect the MAX98357A
speaker only to `OUTP` and `OUTN`; do not connect either speaker lead to GND.
Set the MAX98357A `SD_MODE` for the left channel.

The ESP32 test firmware uses separate I2S controllers: the microphone runs at
24 kHz for Realtime input, while the MAX98357A test tone runs at 16 kHz. This
is deliberate because the MAX98357A does not support a 24 kHz LRCLK.

## Configure without committing secrets

1. Copy `esp32_speech_test/secrets.example.h` to `esp32_speech_test/secrets.h`.
2. Fill in Wi-Fi credentials and the UNO Q's LAN IP. `secrets.h` is ignored.
3. Confirm the I2S pins do not collide with the current ESP32 firmware.

On the UNO Q, provide the API key only in the process environment:

```sh
export OPENAI_API_KEY='your-key-here'
```

Do not put that value in this repository.

## Run

1. Flash the normal UNO Q sketch after the added Bridge function is compiled.
2. In `python/main.py`, temporarily set:

   ```python
   RUN_SPEECH_TEST_ONLY = True
   ```

   This makes App Lab launch the speech-only process inside its own Python
   runtime, where `arduino.app_utils.Bridge` is available. It does not run the
   normal camera/vision loop while enabled.

3. Press **Run** in App Lab. App Lab installs the Python dependency from
   `python/requirements.txt` and starts the speech test.

4. Confirm the App Lab console shows:

   ```text
   Listening for ESP32 PCM audio on 0.0.0.0:3333.
   ```

5. Flash and start `esp32_speech_test/speech_test/speech_test.ino`.
6. Say one of these commands near the INMP441:

   - English: `play beep`, `play success`, or `play error`.
   - Vietnamese: `phát tiếng bíp`, `báo thành công`, or `báo lỗi`.

7. Set `RUN_SPEECH_TEST_ONLY = False` and press **Run** again to restore the
   normal application.

Do not install packages into the system Python or a separate virtual
environment for this mode. App Lab installs `python/requirements.txt` into
the runtime that provides `arduino.app_utils.Bridge`.

The UNO Q terminal should show `UNO Q audio RX` with PCM frame counts and
microphone levels, followed by `UNO Q sent command to ESP32`. The ESP32 USB
serial monitor should show `ACK:CMD_RECEIVED`, `ACK:WAV_STARTED`, and
`SOUND_DONE`. These messages identify whether the fault is before or after
the command reaches the ESP32.

## Install WAV files

Place the files beside the ESP32 `.ino` file before uploading the filesystem:

```text
data/
└── sounds/
    ├── beep.wav
    ├── success.wav
    └── error.wav
```

Use PCM, 16-bit, mono, 16 kHz WAV files. In Arduino IDE, upload this folder
with the ESP32 LittleFS data uploader. The firmware opens the files from
`/sounds/<name>.wav`.

If the ESP32 connects to Wi-Fi but cannot connect to TCP port 3333, verify the
UNO Q IP address in `secrets.h`, that both devices are on the same LAN, that
the UNO Q process printed the listening message, and that no other process is
already using port 3333.
