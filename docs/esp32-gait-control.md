# ESP32 Gait-Control Instructions

This guide describes the leg-control part of the ESP32 firmware in
`dog_esp32.ino`. That sketch lives outside this UNO Q repository, but its
constants and commands must stay coordinated with the UNO Q project.

## Control Path

The ESP32 receives one-character commands from the UNO Q as `CMD:<char>`, or
directly through Bluetooth. Bluetooth has priority for two seconds after a
valid Bluetooth command. The gait loop then follows this path:

```text
command -> gait trajectory -> IMU balance offsets -> inverse kinematics
        -> ST3215 position commands -> synchronous serial write -> telemetry
```

The main loop delays for 5 ms at its end. MPU6050 attitude updates run at 50 Hz.
All 12 leg positions are sent together through `SyncWritePosEx`.

## Leg Layout And Gait Phasing

Each leg has coxa, femur, and tibia ST3215 servos. Direction multipliers convert
the common calculated joint angle into the installed direction for each servo.

| Leg | Coxa / femur / tibia IDs | Phase | Coxa / femur / tibia directions |
| --- | --- | ---: | --- |
| Front-left (FL) | 2 / 6 / 10 | 0.0 | + / - / - |
| Front-right (FR) | 1 / 5 / 9 | 0.5 | + / + / + |
| Rear-left (BL) | 3 / 7 / 11 | 0.5 | - / - / - |
| Rear-right (BR) | 4 / 8 / 12 | 0.0 | - / + / + |

The normal gait is a diagonal-pair trot:

- FL and BR move together.
- FR and BL move together half a cycle later.

With `dutyFactor = 0.65`, each pair has 65% of its cycle in stance and 35% in
swing. This leaves the other diagonal pair supporting the body during swing.

## Geometry And Default Gait Values

All dimensions are millimetres; timing values are milliseconds.

```cpp
constexpr float Lc = 40.0f;     // coxa link length
constexpr float Lf = 100.0f;    // femur link length
constexpr float Lt = 100.0f;    // tibia link length

constexpr float stepLength = 40.0f;
constexpr float stepHeight = 30.0f;
constexpr float zRest = -150.0f;
constexpr float totalCycleDuration = 800.0f;
constexpr float dutyFactor = 0.65f;
constexpr float crabStep = 30.0f;
```

The default cycle rate is 1.25 Hz. During swing, the foot follows a cubic
Bezier arc. During stance, it travels on a straight path to propel the body.

Coordinate intent in the sketch is:

- `x`: fore/aft foot position.
- `y`: lateral foot position around the coxa offset.
- `z`: vertical foot position; negative is below the body.

`calculateIK_Mammal()` rejects unreachable targets. The firmware then keeps the
last valid angles and prints `[IK] ... OOB`, rather than commanding a new pose.

## Inverse Kinematics: From Foot Target To Joint Angles

Inverse kinematics (IK) answers this question: *given the desired foot position
`(x, y, z)`, what coxa, femur, and tibia angles place the foot there?*
The calculation in `calculateIK_Mammal()` uses a geometric two-stage solution.
It does not use a numerical solver.

```text
                    foot target (x, y, z)
                           o
                          / \
                         /   \   femur / tibia plane
                        /  d  \
          femur link Lf o-------o tibia link Lt
                       ^
                       femur pivot, after coxa offset Lc
```

The diagram is conceptual. In the code's body coordinate system, `x` is
fore/aft, `y` is lateral, and negative `z` is below the body. The coxa link is
first removed from the lateral/vertical view; then the femur and tibia are
solved as a planar two-link arm.

### 1. Remove the coxa offset

The coxa joint moves the femur/tibia plane sideways. The foot's radial distance
from that coxa-rotation axis is:

```text
R² = y² + z²
R  = sqrt(R²)
```

The target is invalid when `R < Lc`, because it is inside the coxa link's
offset circle. The code then projects the target into the femur/tibia plane:

```text
z' = sqrt(R² - Lc²)
```

The coxa angle is the difference of two right-triangle angles:

```text
coxa = atan2(y, |z|) - atan2(Lc, z')
```

`atan2` preserves the quadrant of the target. The `|z|` term matches the
sketch's convention that the nominal feet are below the body. This result is
converted from radians to degrees before it is returned.

### 2. Solve the femur and tibia as a two-link planar arm

After the coxa offset is removed, the femur pivot sees the target at `(x, z')`:

```text
d² = x² + z'²
d  = sqrt(d²)
```

`d` must be within the links' reachable annulus. For the current equal-length
links, the sketch explicitly rejects `d >= Lf + Lt` and an almost-zero `d`.
With unequal links, the general lower bound is `|Lf - Lt|` as well.

The code uses the cosine rule to find the inner triangle angles:

```text
cos(beta)  = (Lf² + d² - Lt²) / (2 Lf d)
cos(gamma) = (Lf² + Lt² - d²) / (2 Lf Lt)

beta  = acos(cos(beta))
gamma = acos(cos(gamma))
```

It also finds the target direction from the femur pivot:

```text
alpha = atan2(x, z')
```

The actual output conventions are therefore:

```text
femur = alpha + beta
tibia = -(pi - gamma)
```

The negative tibia sign is a deliberate joint-angle convention in this
firmware, not a mathematical requirement. Do not remove it to fix a reversed
physical joint; use the per-leg direction multipliers in `legs[]` only after
confirming the servo ID and mechanical assembly.

Before `acos`, the cosine values are constrained to `[-1, 1]`. This prevents a
very small floating-point rounding error from producing an invalid `acos` call;
it does not make an actually unreachable target safe. If any reach check fails,
the main loop reuses `lastValid[i]` for that leg.

### 3. Map calculated angles to ST3215 positions

For each leg, `prepareST3215()` first applies the installed-joint direction
multiplier (`+1` or `-1`) to the common IK angle. It then maps degrees to the
ST3215's 0–4095 position range:

```text
servo_position = clamp(2048 + round(angle_degrees x 11.377), 0, 4095)
```

`2048` is the logical centre. The `coxaTrim[i]` value is added before this
mapping; it is a mechanical calibration correction, not part of the IK
geometry. All 12 resulting positions are sent in one `SyncWritePosEx` packet
so the legs start each update together.

## Movement Commands

| Command | Firmware behavior | Readiness |
| --- | --- | --- |
| `w` | Forward trot | Normal gait |
| `b` | Backward trot | Normal gait; coxa trim is sign-reversed |
| `a` / `d` | Turning trot | Verify physical left/right after calibration |
| `e` / `f` | Opposite lateral crab trajectories | Test carefully; grip strongly affects straightness |
| `p` | Side-pair pace gait | Experimental; marked not working in the sketch |
| `s` | Neutral standing pose / stop | Normal pose |
| `z` | Neutral standing pose without fresh balance updates | Diagnostic pose |
| `q` | Sit pose | Static posture |
| `c` | Prone pose | Static posture |
| `g` | Front-right leg wave | Experimental |
| `u` | Front/rear body rocking | Test supported first |
| `j` | Short three-phase jump sequence | Experimental |
| `k` | One-shot servo-centre calibration | Service operation only |

The motion watchdog applies to `w`, `b`, `a`, `d`, `p`, `g`, `u`, `j`, `e`,
and `f`. If no renewed command arrives within `MOTION_COMMAND_TIMEOUT_MS`
(currently 5000 ms), the ESP32 changes to `s` and prints
`Motion command timeout; stopping.` Static postures do not need a keep-alive.

## MPU6050 Balancing

When the MPU6050 responds at boot, the ESP32 reads it on GPIO 21/22 and seeds
roll and pitch from gravity. A complementary filter combines accelerometer and
gyro data, then four PIDs generate body corrections.

| PID | Applies mainly to |
| --- | --- |
| `pitchZ_PID` | Front/rear stance-foot height difference |
| `rollZ_PID` | Left/right stance-foot height difference |
| `pitchX_PID` | Fore/aft foot shift |
| `rollY_PID` | Lateral foot shift |

Balance corrections are applied before inverse kinematics. Vertical corrections
are applied only to stance legs; swing legs retain their planned lift arc. State
`z` skips fresh balance updates, which helps inspect the uncompensated stand.

If the MPU6050 is missing, the sketch prints a warning and continues without
balancing. An ST3215 telemetry `err` is **not** an IMU error: it is the final
commanded servo position, including any balance correction, minus the servo's
reported position.

## ST3215 Commands And Telemetry

For each calculated servo angle, the firmware uses this conversion:

```text
position = 2048 + angle_degrees x 11.377
```

The result is constrained to 0-4095, adjusted by the individual servo direction,
and queued with speed `3073` and acceleration `0`. Keep ST3215 C018 servos on
their intended regulated 12 V supply with a shared controller ground.

Telemetry is enabled by default. Every 100 ms the ESP32 reads one servo ID,
then prints a line such as:

```text
[STS 6] goal=... pos=... err=...deg speed=... load=... V=... temp=...C current=...
```

One complete pass across 12 IDs takes about 1.2 seconds. After three consecutive
feedback timeouts, telemetry disables itself to protect the control loop.
Compare errors in a supported stand against walking errors, then correlate them
with load, voltage, and temperature before changing the gait.

## Safe Tuning Order

1. **Support the robot.** Keep feet off the ground or use a stand so it cannot
   walk, fall, or hit anything.
2. **Verify servo IDs and directions.** Power-up commands the neutral
   `x = 0`, `y = Lc`, `z = zRest` pose. Stop if a joint heads toward a hard stop.
3. **Calibrate only as a service procedure.** Command `k` moves IDs 1-12 one at
   a time every 500 ms to `2048 + calibrationTrim[id]`. It does not discover or
   save trim automatically; edit `calibrationTrim` and `coxaTrim` only after
   physically confirming the joint mapping.
4. **Check static standing first.** Use `s`; from a fresh boot, use `z` to
   inspect a stand without newly computed balance updates.
5. **Test a slow forward cycle.** First with the feet unloaded, then on a level,
   grippy surface.
6. **Tune only one gait value at a time.** Longer stride or shorter cycle time
   needs more servo speed. Higher steps demand more tibia speed.
7. **Tune PID last.** PID changes can hide a mechanical, trim, or traction issue.

## Practical Tuning Guide

| Symptom | First checks | Conservative adjustment |
| --- | --- | --- |
| Poor forward motion or slipping | Foot material, battery voltage, servo load | Improve grip, then reduce `stepLength` before shortening the cycle |
| One diagonal pair has large tracking error | Servo IDs/trim, supply, linkage friction | Compare telemetry in stand versus walk; inspect that pair mechanically |
| Tipping during trot | Centre of mass, duty factor, leg timing | Increase `dutyFactor` slightly or increase `totalCycleDuration` |
| Feet do not clear the floor | `stepHeight`, body height, mechanical range | Raise `stepHeight` slightly after checking tibia speed |
| Crab travel is diagonal | Coxa trims, lateral trajectory, foot grip | Verify `coxaTrim`, reduce `crabStep`, test `e` and `f` separately |
| Repeated `[IK] ... OOB` | Link lengths, body height, balance correction | Reduce stride/height or balance gains; do not ignore repeated messages |
| Oscillation while standing | IMU mounting/axis orientation, PID gains | Verify MPU axes first, then lower the relevant P/D gains |

## Board-Side Test Checklist

1. Verify the 12 V servo supply, common grounds, MPU6050 I2C wiring, and the
   1 Mbps ST3215 bus before powering the legs.
2. Open the ESP32 serial monitor at 115200 baud. Confirm MPU6050 status and STS
   telemetry messages.
3. Wait for the 7-second boot grace period, then send a framed UNO Q command
   such as `CMD:s`.
4. Test `s`, `q`, and `c` while supported before testing `w`.
5. Test one or two `w` cycles with unloaded feet, then on a grippy level surface.
6. Test turning and each crab direction separately. Mark directions from the
   robot's own viewpoint instead of a camera-mirrored view.
7. Stop if voltage sags, temperature rises quickly, a joint nears its mechanical
   limit, or repeated telemetry errors/OOB messages appear.

The ESP32 sketch is not stored in this repository. Flash it from the separate
`dog_esp32` project after pulling the matching UNO Q repository changes.
