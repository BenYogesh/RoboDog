# Timed Camera Scan Instructions

This guide covers the camera-lift feature shared by the ESP32 motor controller
and the Arduino UNO Q vision app. It is written for the 360-degree SG90-style
continuous-rotation servo connected to ESP32 GPIO 27.

## What The Feature Does

When the robot is standing and the camera sees a person whose bounding box
contains only their legs, the UNO Q asks the ESP32 to rotate the camera upward.
The vision app then looks for a face or hand:

1. A face stops the scan immediately.
2. A hand stops the scan after two consecutive hand detections, which reduces
   false stops.
3. The camera holds its position while the face or hand remains visible.
4. When that target has been missing for one second, the ESP32 rotates the
   servo in the reverse direction for the saved upward-scan time.
5. If no target is found, the UNO Q starts the return after 1.5 seconds.

The ESP32 also stops an uninterrupted upward scan after 1.8 seconds. This is
a safety limit in case the UNO Q app or UART link stops responding.

Because the SG90 is a continuous-rotation servo, it has no position feedback.
The return is time-based, not an absolute angle measurement. A small drift is
normal and should be corrected by tuning the servo stop and speed values.

## Wiring

| Connection | Connect to |
| --- | --- |
| SG90 signal | ESP32 GPIO 27 |
| SG90 power | Stable external 5 V supply |
| SG90 ground | Supply ground and ESP32 ground |
| UNO Q TX (D1) | ESP32 UART2 RX, GPIO 16 |
| UNO Q RX (D0) | ESP32 UART2 TX, GPIO 17 |
| UNO Q ground | ESP32 ground |

Do **not** power the SG90 from the ESP32 3.3 V pin. Use a suitable 5 V supply
and ensure that its ground is common with the ESP32. Before connecting the UNO
Q UART pins, confirm that the board UART logic levels are compatible with the
ESP32's 3.3 V inputs.

## Files And Responsibilities

| File | Responsibility |
| --- | --- |
| `python/main.py` | Detects legs, hands, and faces; owns the scan state machine; sends commands through Arduino Bridge. |
| `sketch/sketch.ino` | Validates one-character commands and forwards them as `CMD:<char>` frames over `Serial1`. |
| ESP32 `dog_esp32.ino` | Drives GPIO 27, records scan duration, performs the reverse return, and limits an unattended scan to 1.8 seconds. |

## Camera Commands

All camera commands travel from the UNO Q to the ESP32 as `CMD:<character>`
followed by a newline.

| Command | ESP32 action | Normal producer |
| --- | --- | --- |
| `h` | Timed upward fixed-view step | Manual/fixed camera control |
| `l` | Timed downward fixed-view step | Ball-mode camera control |
| `n` | Return using saved scan time; otherwise request logical neutral | Target lost, scan timeout |
| `r` | Start continuous upward scan and start recording time | Legs-only person detection |
| `x` | Stop scan, retain recorded time, and hold position | Face or confirmed hand detection |

`h`, `l`, and `n` are logical views only. A continuous-rotation servo cannot
know its physical angle, so they are short timed movements rather than absolute
positions.

## Settings To Tune

### ESP32: servo motion

Edit these values in `dog_esp32.ino`:

```cpp
constexpr int CAMERA_SERVO_STOP_US = 1500;
constexpr int CAMERA_SERVO_UP_US = 1700;
constexpr int CAMERA_SERVO_DOWN_US = 1300;
constexpr unsigned long CAMERA_TILT_STEP_MS = 250;
constexpr unsigned long CAMERA_SCAN_MAX_MS = 1800;
```

- `CAMERA_SERVO_STOP_US`: Adjust until the servo is fully still. Start with
  1500 microseconds, then change in small steps such as 5–10 microseconds.
- `CAMERA_SERVO_UP_US` and `CAMERA_SERVO_DOWN_US`: Set equal-size offsets on
  opposite sides of the stop value. If scanning moves the lens down, swap these
  two values.
- `CAMERA_TILT_STEP_MS`: Length of a manual `h` or `l` movement. Keep it small
  because it is not position-controlled.
- `CAMERA_SCAN_MAX_MS`: Physical travel safety limit. Set it below the time
  that would force the camera mount into a hard stop.

### UNO Q: vision behavior

Edit these values near the camera command constants in `python/main.py`:

```python
CAMERA_SCAN_TIMEOUT_S = 1.5
CAMERA_TARGET_LOST_S = 1.0
CAMERA_HAND_CONFIRMATIONS = 2
CAMERA_SCAN_FACE_CHECK_PERIOD_S = 0.25
```

- Keep `CAMERA_SCAN_TIMEOUT_S` shorter than `CAMERA_SCAN_MAX_MS / 1000`.
- Increase `CAMERA_HAND_CONFIRMATIONS` if a false hand detection stops scans.
  Decrease it to `1` only if the hand detector misses targets too often.
- Increase `CAMERA_TARGET_LOST_S` if the lens returns while a person is still
  present but briefly occluded.
- Lower `CAMERA_SCAN_FACE_CHECK_PERIOD_S` only if the UNO Q has enough CPU for
  more frequent face recognition.

## Deployment And Test Procedure

1. With the robot supported so its legs cannot walk, flash the ESP32 firmware.
2. Deploy the UNO Q project containing both `python/main.py` and
   `sketch/sketch.ino` using the normal App Lab workflow.
3. Power the camera servo from its external 5 V supply and confirm the common
   ground connection.
4. Start the vision app. Check that the OLED does not report `CAM FAILED`.
5. Stand where the camera sees only your legs. The OLED should report
   `Scanning for hand/face`, and the lens should move upward.
6. Raise a hand or let your face enter the frame. The lens should stop and the
   OLED should report `Hand found` or `Face found`.
7. Move out of view. After approximately one second, the OLED should report
   `Target lost; returning`, and the servo should reverse for the saved scan
   time.
8. Repeat with no hand or face visible. It should report
   `Scan timed out; returning` after about 1.5 seconds.
9. If the direction is reversed, swap `CAMERA_SERVO_UP_US` and
   `CAMERA_SERVO_DOWN_US`, flash the ESP32 again, and repeat the test.

## Troubleshooting

| Symptom | Likely cause and action |
| --- | --- |
| Servo creeps while it should hold | Tune `CAMERA_SERVO_STOP_US` in 5–10 microsecond steps. |
| Servo moves in the wrong direction | Swap the UP and DOWN pulse-width values. |
| Servo buzzes, resets, or makes the ESP32 reboot | Use a stronger external 5 V supply and verify common ground. |
| Camera reaches its mechanical stop | Reduce `CAMERA_SCAN_MAX_MS` and `CAMERA_SCAN_TIMEOUT_S` immediately. |
| Scan does not start | Confirm the person detector sees a legs-only box, the robot is standing, and the UNO Q UART frame reaches the ESP32. |
| Scan starts but never stops on a target | Check camera focus/lighting, hand/face models, and lower the hand-confirmation count if needed. |
| ESP32 prints `Camera scan safety timeout.` | The UNO Q did not send a stop or return in time; inspect the vision app, Bridge connection, and UART wiring. |

When adjusting servo timings, change one value at a time and re-test with the
robot supported. Avoid increasing any duration until you have verified the
camera mount has enough mechanical travel.
