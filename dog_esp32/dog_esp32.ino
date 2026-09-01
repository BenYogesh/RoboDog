// Khai báo các thư viện sử dụng

#include <Arduino.h>
#include <math.h>
#include <SCServo.h>
#include <MPU6050.h>
#include <Wire.h>
#include <ESP32Servo.h>
#include <BluetoothSerial.h>

// =============================================================================
// SETUP PHẦN CỨNG
// =============================================================================
MPU6050 mpu;
SMS_STS st;  // Servo
#define RX_PIN 18
#define TX_PIN 19
#define RX2_PIN 16
#define TX2_PIN 17
#if !defined(CONFIG_BT_ENABLED) || !defined(CONFIG_BLUEDROID_ENABLED)
#error Bluetooth is not enabled! Please run `make menuconfig` to and enable it
#endif
BluetoothSerial SerialBT;          // Bluetooth
HardwareSerial UnoQLink(2);        // UART2 kết nối tới Uno Q
Servo cameraTiltServo;

#define CAMERA_SERVO_PIN 27 // Servo quay camera
constexpr int CAMERA_SERVO_STOP_US = 1500;
constexpr int CAMERA_SERVO_UP_US = 1600;
constexpr int CAMERA_SERVO_DOWN_US = 1400;
constexpr unsigned long CAMERA_TILT_STEP_MS = 100;
constexpr unsigned long CAMERA_SCAN_MAX_MS = 500;
#define BOOT_GRACE_PERIOD_MS 7000  // ignore serial input right after power-up, \
                                   // when boot-time noise/log text is most \
                                   // likely to appear on the line
String unoFrame;
bool unoFrameOverflow = false;
unsigned long lastBluetoothCommandTime = 0;
constexpr unsigned long BLUETOOTH_PRIORITY_MS = 2000;
constexpr unsigned long MOTION_COMMAND_TIMEOUT_MS = 5000;

// =============================================================================
// KÍCH THƯỚC ROBOT (mm)
// =============================================================================
constexpr float Lc = 40.0f;   // Khớp hông
constexpr float Lf = 100.0f;  // Khớp đùi
constexpr float Lt = 100.0f;  // Khớp cẳng

// Các hằng số hỗ trợ tính toán
constexpr float Lf2 = Lf * Lf;
constexpr float Lt2 = Lt * Lt;
constexpr float LfLt2 = 2.0f * Lf * Lt;
constexpr float LfPlusLt = Lf + Lt;
constexpr float Lc2 = Lc * Lc;

// =============================================================================
// THÔNG SỐ BƯỚC CHÂN
// =============================================================================
constexpr float stepLength = 40.0f;                            // Độ dài bước
constexpr float stepHeight = 30.0f;                            // Độ cao bước: keeps the tibia within its loaded speed capability
constexpr float zRest = -150.0f;                               // Chiều cao khi đứng yên
constexpr float totalCycleDuration = 800.0f;                   // Thời gian cho 1 bước
constexpr float dutyFactor = 0.65f;                             // Tỉ lệ thời gian giậm chân
constexpr float swingDuration = 1.0f - dutyFactor;             // Tỉ lệ thời gian nhấc chân
constexpr float stanceDuration = dutyFactor;                   // Tỉ lệ thời gian giậm chân
constexpr float invCycleDuration = 1.0f / totalCycleDuration;  // Nghịch đảo thời gian 1 bước (hỗ trợ tính toán)
constexpr float crabStep = 30.0f;                              // Độ dài bước chân khi đi ngang

// Tọa độ x, z để tính toán quỹ đạo bước chân
constexpr float BEZ_X0 = -stepLength / 2.0f;            // Tọa độ x điểm 0
constexpr float BEZ_X1 = (-stepLength / 2.0f) - 15.0f;  // Tọa độ x điểm 1
constexpr float BEZ_X2 = (stepLength / 2.0f) + 15.0f;   // Tọa độ x điểm 2
constexpr float BEZ_X3 = stepLength / 2.0f;             // Tọa độ x điểm 3
constexpr float BEZ_Z0 = zRest;                         // Tọa độ z điểm 0
constexpr float BEZ_Z1 = zRest + stepHeight;            // Tọa độ z điểm 1
constexpr float BEZ_Z2 = zRest + stepHeight;            // Tọa độ z điểm 2
constexpr float BEZ_Z3 = zRest;                         // Tọa độ z điểm 3

// Tọa độ y để tính toán quỹ đạo khi bước ngang
constexpr float BEZ_CY0 = -(crabStep / 2.0f);          // -15
constexpr float BEZ_CY1 = -(crabStep / 2.0f) - 15.0f;  // -30 (kick away from landing side)
constexpr float BEZ_CY2 = (crabStep / 2.0f) + 15.0f;   // +30 (lunge toward landing)
constexpr float BEZ_CY3 = (crabStep / 2.0f);           //  +15

// =============================================================================
// CẤU TRÚC CHÂN
// =============================================================================
struct Leg {                          // Khởi tạo cấu trúc chân
  int coxaID, femurID, tibiaID;       // ID Servo các khớp
  float phaseOffset;                  // Độ lệch thời gian
  bool isFrontLeg;                    // Có phải chân trước?
  bool isRightSide;                   // Có phải chân phải?
  float coxaDir, femurDir, tibiaDir;  // Dấu để tính toán
};

Leg legs[4] = {
  // Tạo biến chân theo cấu trúc
  { 2, 6, 10, 0.0f, true, false, 1.0f, -1.0f, -1.0f },    // Trái trước FL
  { 1, 5, 9, 0.5f, true, true, 1.0f, 1.0f, 1.0f },        // Phải trước FR
  { 3, 7, 11, 0.5f, false, false, -1.0f, -1.0f, -1.0f },  // Trái sau BL
  { 4, 8, 12, 0.0f, false, true, -1.0f, 1.0f, 1.0f }      // Phải sau BR
};

struct JointAngles {  // Khởi tạo cấu trúc góc servo
  float coxa, femur, tibia;
};
JointAngles lastValid[4] = {};               // Mảng lưu vị trí góc cuối cùng
float coxaTrim[4] = { 0.0, 0.0, 0.0, 0.0 };  // Mảng tinh chỉnh góc hông FL, FR, BL, BR

// =============================================================================
// PID
// =============================================================================
class BiquadFilter {  // Bộ lọc dữ liệu
private:
  float b0, b1, b2, a1, a2;
  float x1 = 0, x2 = 0, y1 = 0, y2 = 0;

public:
  // Hàm khởi tạo bộ lọc. Yêu cầu biến tần số lấy mẫu, tần số lọc thông thấp kết quả
  void configure(float sampleFreq, float cutoffFreq) {
    float omega = 2.0f * PI * cutoffFreq / sampleFreq;
    float sn = sin(omega);
    float cs = cos(omega);
    float alpha = sn / (2.0f * 0.707f);  // 0.707 is the Butterworth Q-factor

    float a0 = 1.0f + alpha;
    b0 = (1.0f - cs) / 2.0f / a0;
    b1 = (1.0f - cs) / a0;
    b2 = (1.0f - cs) / 2.0f / a0;
    a1 = -2.0f * cs / a0;
    a2 = (1.0f - alpha) / a0;
  }

  // Truyền bộ lọc vào tham số
  float apply(float input) {
    float output = b0 * input + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2;
    x2 = x1;
    x1 = input;
    y2 = y1;
    y1 = output;
    return output;
  }
};

struct PID {              // Cấu trúc PID
  float Kp, Ki, Kd;       // Hằng số PID
  float integral = 0.0f;  // Biến tổng sai số để tính I
  float prevErr = 0.0f;   // Biến lưu sai số cuối cùng để tính D

  float compute(float setpoint, float measured, float dt) {  // Hàm tính PID
    float error = setpoint - measured;
    if (fabsf(error) < 0.5f) return 0.0f;
    integral += error * dt;
    integral = constrain(integral, -20.0f, 20.0f);
    float derivative = (error - prevErr) / dt;
    prevErr = error;
    return (Kp * error) + (Ki * integral) + (Kd * derivative);
  }
};

float angleRoll = 0.0f, anglePitch = 0.0f, angleYaw = 0.0f, gyroZFiltered = 0.0f;  // Biến dữ liệu đọc cảm biến thăng bằng
float balX = 0.0f, balY = 0.0f, balZ_Roll = 0.0f, balZ_Pitch = 0.0f;               // Biến kết quả điều chỉnh theo cảm biến

// Khởi tạo biến PID theo từng chiều
PID pitchZ_PID = { 1.2f, 0.005f, 0.1f };
PID pitchX_PID = { 0.5f, 0.005f, 0.03f };
PID rollZ_PID = { 1.0f, 0.005f, 0.05f };
PID rollY_PID = { 0.7f, 0.005f, 0.05f };

// Khởi tạo biến bộ lọc theo từng chiều
BiquadFilter gyroFilterX;
BiquadFilter gyroFilterY;
BiquadFilter gyroFilterZ;

// Hàm kết hợp PID với số đo thăng bằng
void updateBalancing(float pitch, float roll, float dt) {
  balZ_Pitch = pitchZ_PID.compute(0.0f, pitch, dt);
  balZ_Roll = rollZ_PID.compute(0.0f, roll, dt);
  balX = pitchX_PID.compute(0.0f, pitch, dt);
  balY = rollY_PID.compute(0.0f, roll, dt);
}

// Hàm đọc cảm biến
void updateAttitude(float dt) {
  int16_t ax, ay, az, gx, gy, gz;
  mpu.getMotion6(&ax, &ay, &az, &gx, &gy, &gz);
  if (abs(gx) > 30000 || abs(gy) > 30000 || abs(gz) > 30000) {
    Serial.println("WARNING: IMU spike blocked");
    return;
  }
  float accX = -ax / 16384.0f, accY = -ay / 16384.0f, accZ = az / 16384.0f;
  float gyroX = gx / 131.0f, gyroY = gy / 131.0f, gyroZ = -gz / 131.0f;
  // float clean_gx = gyroFilterX.apply(gyroX);
  // float clean_gy = gyroFilterY.apply(gyroY);
  // float clean_gz = gyroFilterZ.apply(gyroZ);
  // Serial.printf("ax = %.1f, ay = %.1f, az = %.1f, gx = %.1f, gy = %.1f, gz = %.1f)\n",
  //               accX, accY, accZ, gyroX, gyroY, gyroZ);
  float accRoll = atan2f(accY, accZ) * (180.0f / PI);
  float accPitch = atan2f(-accX, sqrtf(accY * accY + accZ * accZ)) * (180.0f / PI);
  // angleRoll = 0.98f * (angleRoll + clean_gx * dt) + 0.02f * accRoll;
  // anglePitch = 0.98f * (anglePitch + clean_gy * dt) + 0.02f * accPitch;
  // angleYaw += clean_gz * dt;
  angleRoll = 0.98f * (angleRoll + gyroX * dt) + 0.02f * accRoll;
  anglePitch = 0.98f * (anglePitch + gyroY * dt) + 0.02f * accPitch;
  angleYaw += gyroZ * dt;
  if (angleYaw > 180.0f) angleYaw -= 360.0f;
  else if (angleYaw < -180.0f) angleYaw += 360.0f;
  gyroZFiltered = 0.9f * gyroZFiltered + 0.1f * gyroZ;
}

void initializeAttitude() {
  int16_t ax, ay, az, gx, gy, gz;
  mpu.getMotion6(&ax, &ay, &az, &gx, &gy, &gz);

  float accX = -ax / 16384.0f;
  float accY = -ay / 16384.0f;
  float accZ = az / 16384.0f;
  angleRoll = atan2f(accY, accZ) * (180.0f / PI);
  anglePitch = atan2f(-accX, sqrtf(accY * accY + accZ * accZ)) * (180.0f / PI);
  angleYaw = 0.0f;
  gyroZFiltered = 0.0f;
  balX = balY = balZ_Roll = balZ_Pitch = 0.0f;
}

// =============================================================================
// GỬI TÍN HIỆU TỚI SERVO
// =============================================================================
u8 syncIDs[12];         // Mảng ID servo
s16 syncPositions[12];  // Mảng góc quay servo
u16 syncSpeeds[12];     // Mảng tốc độ servo
u8 syncAccels[12];      // Mảng gia tốc servo
int syncCount = 0;      // Biến đếm tín hiệu đã gửi

// Hàm cài đặt thông số từng servo
constexpr uint8_t SERVO_COUNT = 12;
constexpr bool ENABLE_SERVO_TELEMETRY = true;
constexpr unsigned long SERVO_TELEMETRY_INTERVAL_MS = 100;
u16 lastCommandedPosition[SERVO_COUNT + 1] = {};
uint8_t nextTelemetryServoID = 1;
unsigned long lastTelemetryTime = 0;
bool servoTelemetryEnabled = ENABLE_SERVO_TELEMETRY;
uint8_t consecutiveTelemetryFailures = 0;

void prepareST3215(int servoID, float angleDegrees, float dirMult) {
  angleDegrees *= dirMult;
  int pos = constrain(2048 + (int)roundf(angleDegrees * 11.377f), 0, 4095);
  syncIDs[syncCount] = (u8)servoID;
  syncPositions[syncCount] = (s16)pos;
  syncSpeeds[syncCount] = 3073;
  syncAccels[syncCount] = 0;
  if (servoID >= 1 && servoID <= SERVO_COUNT) {
    lastCommandedPosition[servoID] = (u16)pos;
  }
  syncCount++;
}

void updateServoTelemetry(unsigned long now) {
  if (!servoTelemetryEnabled || now - lastTelemetryTime < SERVO_TELEMETRY_INTERVAL_MS) return;
  lastTelemetryTime = now;

  const uint8_t servoID = nextTelemetryServoID;
  nextTelemetryServoID = (nextTelemetryServoID % SERVO_COUNT) + 1;

  // FeedBack reads all status registers in one bus transaction. Passing -1 to
  // the Read* functions below uses that cached response rather than issuing
  // additional requests.
  if (st.FeedBack(servoID) < 0) {
    Serial.printf("[STS %u] feedback timeout\n", servoID);
    if (++consecutiveTelemetryFailures >= 3) {
      servoTelemetryEnabled = false;
      Serial.println("STS telemetry disabled after three consecutive timeouts.");
    }
    return;
  }
  consecutiveTelemetryFailures = 0;

  const int presentPosition = st.ReadPos(-1);
  const int presentSpeed = st.ReadSpeed(-1);
  const int presentLoad = st.ReadLoad(-1);
  const int voltageDecivolts = st.ReadVoltage(-1);
  const int temperatureC = st.ReadTemper(-1);
  const int presentCurrent = st.ReadCurrent(-1);
  const float positionErrorDeg = (lastCommandedPosition[servoID] - presentPosition) / 11.377f;

  Serial.printf("[STS %u] goal=%u pos=%d err=%+.1fdeg speed=%d load=%d V=%.1f temp=%dC current=%d\n",
                servoID, lastCommandedPosition[servoID], presentPosition, positionErrorDeg,
                presentSpeed, presentLoad, voltageDecivolts * 0.1f, temperatureC, presentCurrent);
}

// =============================================================================
// BIẾN TRẠNG THÁI ROBOT
// =============================================================================
char currentState = 's';
unsigned long jumpStartTime = 0;
unsigned long lastIMUTime = 0;
unsigned long lastMotionCommandTime = 0;
bool imuAvailable = false;

enum ControlMode : uint8_t {
  CONTROL_AUTOMATIC,
  CONTROL_MANUAL,
};
ControlMode controlMode = CONTROL_AUTOMATIC;

constexpr uint8_t CALIBRATION_SERVO_COUNT = 12;
constexpr unsigned long CALIBRATION_STEP_MS = 500;
const int16_t calibrationTrim[CALIBRATION_SERVO_COUNT + 1] = {
  0, 0, 0, 11, 0, 0, 0, 0, 0, 0, 0, 0, 0
};
bool calibrationActive = false;
uint8_t calibrationServoID = 1;
unsigned long lastCalibrationStepTime = 0;

bool isSupportedCommand(char command) {
  switch (command) {
    case 'w':
    case 'b':
    case 'a':
    case 'd':
    case 'p':
    case 'c':
    case 'g':
    case 'u':
    case 'q':
    case 'j':
    case 's':
    case 'z':
    case 'e':
    case 'f':
    case 'k':
      return true;
    default:
      return false;
  }
}

enum CameraView : int8_t {
  CAMERA_VIEW_DOWN = -1,
  CAMERA_VIEW_NEUTRAL = 0,
  CAMERA_VIEW_UP = 1,
};

CameraView currentCameraView = CAMERA_VIEW_NEUTRAL;
CameraView targetCameraView = CAMERA_VIEW_NEUTRAL;
bool cameraTiltMoving = false;
int8_t cameraTiltDirection = 0;
unsigned long cameraTiltStepEndTime = 0;
bool cameraScanActive = false;
bool cameraReturnActive = false;
int8_t cameraScanDirection = 0;  // +1 = up, -1 = down
unsigned long cameraScanStartTime = 0;
int32_t cameraOffsetDurationMs = 0;  // Signed timed offset from neutral.
int8_t cameraReturnDirection = 0;
unsigned long cameraReturnStartTime = 0;
unsigned long cameraReturnDurationMs = 0;

bool isCameraCommand(char command) {
  return command == 'h' || command == 'l' || command == 'n' || command == 'r' || command == 'v' || command == 'x';
}

bool isRecognizedCommand(char command) {
  return isSupportedCommand(command) || isCameraCommand(command);
}

void startCameraTiltStep(unsigned long now) {
  if (cameraScanActive || cameraReturnActive) return;
  if (currentCameraView == targetCameraView) return;

  cameraTiltDirection = (targetCameraView > currentCameraView) ? 1 : -1;
  cameraTiltServo.writeMicroseconds(cameraTiltDirection > 0 ? CAMERA_SERVO_UP_US : CAMERA_SERVO_DOWN_US);
  cameraTiltStepEndTime = now + CAMERA_TILT_STEP_MS;
  cameraTiltMoving = true;
}

void stopCameraTiltServo() {
  cameraTiltServo.writeMicroseconds(CAMERA_SERVO_STOP_US);
}

void stopCameraScan(unsigned long now) {
  if (!cameraScanActive) return;

  cameraOffsetDurationMs += cameraScanDirection * (int32_t)(now - cameraScanStartTime);
  cameraScanActive = false;
  cameraScanDirection = 0;
  stopCameraTiltServo();
}

void stopCameraReturn(unsigned long now) {
  if (!cameraReturnActive) return;

  const unsigned long elapsed = now - cameraReturnStartTime;
  const unsigned long moved = elapsed < cameraReturnDurationMs ? elapsed : cameraReturnDurationMs;
  cameraOffsetDurationMs += cameraReturnDirection * (int32_t)moved;
  cameraReturnActive = false;
  cameraReturnDirection = 0;
  stopCameraTiltServo();
}

void startCameraScan(unsigned long now, int8_t direction) {
  if (cameraScanActive) stopCameraScan(now);
  if (cameraReturnActive) stopCameraReturn(now);

  cameraTiltMoving = false;
  cameraScanStartTime = now;
  cameraScanDirection = direction;
  cameraScanActive = true;
  cameraTiltServo.writeMicroseconds(direction > 0 ? CAMERA_SERVO_UP_US : CAMERA_SERVO_DOWN_US);
}

void startCameraReturn(unsigned long now) {
  if (cameraScanActive) stopCameraScan(now);
  if (cameraReturnActive) return;

  if (cameraOffsetDurationMs == 0) {
    targetCameraView = CAMERA_VIEW_NEUTRAL;
    return;
  }

  cameraTiltMoving = false;
  cameraReturnDirection = cameraOffsetDurationMs > 0 ? -1 : 1;
  cameraReturnDurationMs = cameraOffsetDurationMs > 0
                             ? (unsigned long)cameraOffsetDurationMs
                             : (unsigned long)(-cameraOffsetDurationMs);
  cameraReturnStartTime = now;
  cameraReturnActive = true;
  cameraTiltServo.writeMicroseconds(cameraReturnDirection > 0 ? CAMERA_SERVO_UP_US : CAMERA_SERVO_DOWN_US);
}

void updateCameraTiltServo(unsigned long now) {
  if (cameraScanActive) {
    if (now - cameraScanStartTime >= CAMERA_SCAN_MAX_MS) {
      stopCameraScan(now);
      Serial.println("Camera scan safety timeout.");
    }
    return;
  }

  if (cameraReturnActive) {
    if (now - cameraReturnStartTime >= cameraReturnDurationMs) {
      stopCameraTiltServo();
      cameraReturnActive = false;
      cameraReturnDirection = 0;
      cameraOffsetDurationMs = 0;
      currentCameraView = CAMERA_VIEW_NEUTRAL;
      targetCameraView = CAMERA_VIEW_NEUTRAL;
    }
    return;
  }

  if (cameraTiltMoving && (long)(now - cameraTiltStepEndTime) >= 0) {
    stopCameraTiltServo();
    currentCameraView = static_cast<CameraView>(currentCameraView + cameraTiltDirection);
    cameraTiltMoving = false;
  }

  if (!cameraTiltMoving) startCameraTiltStep(now);
}

void requestCameraView(char command, unsigned long now) {
  switch (command) {
    case 'h': targetCameraView = CAMERA_VIEW_UP; break;
    case 'l': targetCameraView = CAMERA_VIEW_DOWN; break;
    case 'n': startCameraReturn(now); break;
    case 'r': startCameraScan(now, 1); break;
    case 'v': startCameraScan(now, -1); break;
    case 'x':
      if (cameraScanActive) stopCameraScan(now);
      else if (cameraReturnActive) stopCameraReturn(now);
      break;
  }
}

void setControlMode(ControlMode mode, bool notifyUnoQ) {
  if (mode == controlMode && !notifyUnoQ) return;

  const unsigned long now = millis();
  if (cameraScanActive) stopCameraScan(now);
  if (cameraReturnActive) stopCameraReturn(now);
  cameraTiltMoving = false;
  stopCameraTiltServo();
  targetCameraView = currentCameraView;
  currentState = 's';
  calibrationActive = false;
  lastMotionCommandTime = now;
  controlMode = mode;

  if (notifyUnoQ) {
    UnoQLink.print(mode == CONTROL_MANUAL ? "MODE:MANUAL\n" : "MODE:AUTO\n");
    UnoQLink.flush();
  }
  Serial.printf("CONTROL_MODE=%s\n",
                mode == CONTROL_MANUAL ? "MANUAL" : "AUTOMATIC");
}

bool isMotionState(char state) {
  switch (state) {
    case 'w':
    case 'b':
    case 'a':
    case 'd':
    case 'p':
    case 'g':
    case 'u':
    case 'j':
    case 'e':
    case 'f':
      return true;
    default:
      return false;
  }
}

void resetUnoCommandParser() {
  unoFrame = "";
  unoFrameOverflow = false;
}

void processUnoFrame(const String &frame, char &command, bool allowMotion) {
  // The UNO Q uses explicit mode frames for dashboard ownership. Bluetooth
  // M/O still uses the notify=true path above so Python receives the change.
  if (frame == "MODE:MANUAL") {
    setControlMode(CONTROL_MANUAL, false);
    return;
  }
  if (frame == "MODE:AUTO") {
    setControlMode(CONTROL_AUTOMATIC, false);
    return;
  }
  if (!frame.startsWith("CMD:") || frame.length() != 5) return;
  const char candidate = frame.charAt(4);
  if (allowMotion && controlMode == CONTROL_AUTOMATIC &&
      isRecognizedCommand(candidate)) {
    command = candidate;
  }
}

bool readUnoCommand(char &command, bool allowMotion) {
  bool handled = false;
  while (UnoQLink.available() > 0) {
    const char c = static_cast<char>(UnoQLink.read());
    if (c == '\r') continue;
    if (c == '\n') {
      if (!unoFrameOverflow) {
        processUnoFrame(unoFrame, command, allowMotion);
        handled = true;
      }
      resetUnoCommandParser();
      continue;
    }
    if (!unoFrameOverflow && unoFrame.length() < 32) {
      unoFrame += c;
    } else {
      unoFrameOverflow = true;
    }
  }
  return handled;
}

void beginCalibration() {
  calibrationActive = true;
  calibrationServoID = 1;
  lastCalibrationStepTime = 0;
}

void updateCalibration(unsigned long now) {
  if (!calibrationActive) return;
  if (lastCalibrationStepTime != 0 && now - lastCalibrationStepTime < CALIBRATION_STEP_MS) return;

  st.WritePosEx(calibrationServoID, 2048 + calibrationTrim[calibrationServoID], 3400, 50);
  lastCalibrationStepTime = now;
  calibrationServoID++;

  if (calibrationServoID > CALIBRATION_SERVO_COUNT) {
    calibrationActive = false;
    currentState = 's';
  }
}

// =============================================================================
// SETUP
// =============================================================================
void setup() {
  Serial.begin(115200);
  Wire.begin(21, 22);
  SerialBT.begin("RoboDog_ESP32");
  Serial.println("Ze Bluetooth devise iz readi to paer.");
  mpu.initialize();
  imuAvailable = mpu.testConnection();
  if (imuAvailable) {
    mpu.setFullScaleGyroRange(MPU6050_GYRO_FS_250);
    mpu.setFullScaleAccelRange(MPU6050_ACCEL_FS_2);
    initializeAttitude();
  } else {
    Serial.println("WARNING: MPU6050 not detected; balancing disabled.");
  }
  Serial1.begin(1000000, SERIAL_8N1, RX_PIN, TX_PIN);
  UnoQLink.begin(115200, SERIAL_8N1, RX2_PIN, TX2_PIN);  // link to UNO Q
  st.pSerial = &Serial1;
  cameraTiltServo.setPeriodHertz(50);
  cameraTiltServo.attach(CAMERA_SERVO_PIN, 500, 2400);
  cameraTiltServo.writeMicroseconds(CAMERA_SERVO_STOP_US);


  // Khởi chạy bộ lọc
  // gyroFilterX.configure(1000.0f, 80.0f);
  // gyroFilterY.configure(1000.0f, 80.0f);
  // gyroFilterZ.configure(1000.0f, 80.0f);

  // Khởi tạo các chân
  for (int i = 0; i < 4; i++) {
    JointAngles p;
    if (calculateIK_Mammal(0.0f, Lc, zRest, p)) {
      lastValid[i] = p;
      prepareST3215(legs[i].coxaID, p.coxa, legs[i].coxaDir);
      prepareST3215(legs[i].femurID, p.femur, legs[i].femurDir);
      prepareST3215(legs[i].tibiaID, p.tibia, legs[i].tibiaDir);
    }
  }
  if (syncCount > 0)
    st.SyncWritePosEx(syncIDs, syncCount, syncPositions, syncSpeeds, syncAccels);
  syncCount = 0;
  delay(2000);
  lastIMUTime = millis();
  lastMotionCommandTime = lastIMUTime;
}

// =============================================================================
// CÁC HÀM TÍNH TOÁN QUỸ ĐẠO BƯỚC CHÂN
// =============================================================================
// Hàm tính toán góc quay servo cho một chân dựa trên tư thế hiện tại
bool calculateIK_Mammal(float x, float y, float z, JointAngles &out) {
  float R2 = y * y + z * z;
  float R = sqrtf(R2);
  if (R < Lc) return false;
  float z_prime = sqrtf(R2 - Lc2);
  out.coxa = (atan2f(y, fabsf(z)) - atan2f(Lc, z_prime)) * (180.0f / PI);
  float d2 = x * x + z_prime * z_prime;
  float d = sqrtf(d2);
  if (d >= LfPlusLt || d < 1e-4f) return false;
  float alpha = atan2f(x, z_prime);
  float cosB = constrain((Lf2 + d2 - Lt2) / (2.0f * Lf * d), -1.0f, 1.0f);
  float cosG = constrain((Lf2 + Lt2 - d2) / LfLt2, -1.0f, 1.0f);
  out.femur = (alpha + acosf(cosB)) * (180.0f / PI);
  out.tibia = -(PI - acosf(cosG)) * (180.0f / PI);
  return true;
}

// Hàm tính toán tọa độ hiện tại cho một chân khi chuyển động dựa trên thời gian bước chân
void evalWalkTrajectory(float elapsed_norm, float phaseOffset,
                        float &x, float &z, bool &isStance) {
  float cp = fmodf(elapsed_norm + phaseOffset, 1.0f);
  if (cp < swingDuration) {
    float t = cp / swingDuration;
    float u = 1.0f - t;
    float uu = u * u;
    float uuu = uu * u;
    float tt = t * t;
    float ttt = tt * t;
    x = uuu * BEZ_X0 + 3.0f * uu * t * BEZ_X1 + 3.0f * u * tt * BEZ_X2 + ttt * BEZ_X3;
    z = uuu * BEZ_Z0 + 3.0f * uu * t * BEZ_Z1 + 3.0f * u * tt * BEZ_Z2 + ttt * BEZ_Z3;
    isStance = false;
  } else {
    float t = (cp - swingDuration) / stanceDuration;
    x = (stepLength / 2.0f) - (stepLength * t);
    z = zRest;
    isStance = true;
  }
}

// =============================================================================
// MAIN LOOP
// =============================================================================
void loop() {
  char command = '\0';
  unsigned long now = millis();

  // M enters manual mode and O returns to automatic mode. Movement and camera
  // bytes keep their existing Bluetooth behavior.
  while (SerialBT.available() > 0) {
    const char c = static_cast<char>(SerialBT.read());
    if (c == 'M') {
      setControlMode(CONTROL_MANUAL, true);
      lastBluetoothCommandTime = now;
    } else if (c == 'O') {
      setControlMode(CONTROL_AUTOMATIC, true);
      lastBluetoothCommandTime = now;
    } else if (isRecognizedCommand(c)) {
      command = c;
      lastBluetoothCommandTime = now;
    }
  }

  if (now >= BOOT_GRACE_PERIOD_MS) {
    // Uno Q motion is blocked in manual mode and during the existing
    // Bluetooth priority window.
    const bool allowUnoMotion =
      controlMode == CONTROL_AUTOMATIC &&
      now - lastBluetoothCommandTime > BLUETOOTH_PRIORITY_MS;
    readUnoCommand(command, allowUnoMotion);
  } else {
    while (UnoQLink.available() > 0) UnoQLink.read();
    resetUnoCommandParser();
  }

  if (isCameraCommand(command)) {
    requestCameraView(command, now);
  } else if (isSupportedCommand(command)) {
    currentState = command;
    lastMotionCommandTime = now;
    if (command == 'j') jumpStartTime = now;
    if (command == 'k') beginCalibration();
    else calibrationActive = false;
  }
  // Biến thời gian
  unsigned long currentTime = millis();
  unsigned long elapsed = currentTime;
  updateCameraTiltServo(currentTime);

  if (isMotionState(currentState) && currentTime - lastMotionCommandTime > MOTION_COMMAND_TIMEOUT_MS) {
    currentState = 's';
    Serial.println("Motion command timeout; stopping.");
  }

  if (currentState == 'k') {
    updateCalibration(currentTime);
    delay(5);
    return;
  }

  // Đọc cảm biến 50 lần mỗi giây
  if (imuAvailable && currentTime - lastIMUTime >= 20) {
    float dt = (float)(currentTime - lastIMUTime) * (1.0f / 1000.0f);
    lastIMUTime = currentTime;
    updateAttitude(dt);
    if (currentState != 'z') updateBalancing(anglePitch, angleRoll, dt);
  }

  float elapsed_norm = (float)elapsed * invCycleDuration;
  syncCount = 0;

  // Chạy liên tục từng chân
  for (int i = 0; i < 4; i++) {
    // Tọa độ mặc định (Đứng yên)
    float x = 0.0f, y = Lc, z = zRest;
    bool isStance = true;  // Có đang giậm chân không

    // -------------------------------------------------------------------------
    // Xử lý theo tư thế
    // -------------------------------------------------------------------------
    switch (currentState) {
      // Bước tiến
      case 'w':
      case 'a':
      case 'd':
        {
          evalWalkTrajectory(elapsed_norm, legs[i].phaseOffset, x, z, isStance);  // Tính toán tọa độ hiện tại của chân
          y = Lc;                                                                 // Tọa độ y không đổi
          x = -x;                                                                 // Lật tọa độ x để không đi giật lùi
          if (currentState == 'a' && !legs[i].isRightSide) x = -x;                // Nếu đang quay trái, lật tọa độ x với các chân bên trái
          else if (currentState == 'd' && legs[i].isRightSide) x = -x;            // Nếu đang quay phải, lật tọa độ x với các chân bên phải
          break;
        }

      // Bước lùi thẳng
      // Giống bước tiến thẳng, nhưng không lật tọa độ x
      case 'b':
        {
          evalWalkTrajectory(elapsed_norm, legs[i].phaseOffset, x, z, isStance);  // Tính toán tọa độ hiện tại của chân
          y = Lc;                                                                 // Tọa độ y không đổi
          // Không phải lật tọa độ x
          break;
        }

      // Bước hai hàng (chưa hoạt động)
      case 'p':
        {
          float paceOffset = legs[i].isRightSide ? 0.0f : 0.5f;
          evalWalkTrajectory(elapsed_norm, paceOffset, x, z, isStance);
          y = Lc;
          x = -x;

          // Lean body toward the stance side to keep CoM over the support polygon.
          // Without this, both stance legs are on one side and the robot tips toward
          // the swing side, consuming all propulsive force fighting the lateral fall.
          // constexpr float paceLean = 25.0f;  // mm — tune up if it still tips
          // if (isStance) {
          //   y += legs[i].isRightSide ? -paceLean : paceLean;
          // }
          break;
        }

      // Nằm
      case 'c':
        {
          x = 0.0f;
          y = legs[i].isRightSide ? Lc + 5.0f : Lc - 5.0f;  // Mở hông ra để xuống thấp hơn
          z = -80.0f;
          break;
        }

      //  Ngồi
      case 'q':
        {
          // Chân trước thằng, chân sau gập
          x = 0.0f;
          y = legs[i].isFrontLeg ? Lc : (legs[i].isRightSide ? Lc + 10.0f : Lc - 10.0f);
          z = legs[i].isFrontLeg ? zRest - 10.0f : -70.0f;
          break;
        }

      // Bắt tay (chưa hoạt động)
      case 'g':
        {
          if (i == 1) {  // Chân phải trước vẫy
            float t = fmodf((float)currentTime / 700.0f, 1.0f);
            x = -50.0f * sinf(t * 2.0f * PI);
            y = Lc;
            z = zRest + 80.0f + 20.0f * sinf(t * 4.0f * PI);
            isStance = false;
          } else {  // Các chân khác đứng
            x = 0.0f;
            y = legs[i].isRightSide ? Lc - 15.0f : Lc + 15.0f;
            z = zRest;
          }
          break;
        }

      // Nhún
      case 'u':
        {
          float t = fmodf((float)currentTime / 800.0f, 1.0f);
          x = 0.0f;
          y = Lc;
          z = zRest + (legs[i].isFrontLeg ? -1 : 1) * 80.0f * sinf(t * 2.0f * PI);  // Độ cao thay đổi theo thời gian
          break;
        }

      // Nhảy (chưa hoạt động)
      case 'j':
        {
          x = 0.0f;
          y = Lc;
          long jumpElapsed = (long)(millis() - jumpStartTime);
          if (jumpElapsed < 100) z = -70.0f;        // phase 1: hạ người
          else if (jumpElapsed < 200) z = -200.0f;  // phase 2: nâng lên nhanh
          else if (jumpElapsed < 300) z = -110.0f;  // phase 3: đứng
          else {
            currentState = 's';
            continue;
          }
          break;
        }

      // Đứng bất động, mặc kệ thăng bẳng
      case 'z':
        {
          x = 0.0f;
          y = Lc;
          z = zRest;
          break;
        }

      // Bước ngang
      case 'e':
      case 'f':
        {
          float sign = (currentState == 'e') ? 1.0f : -1.0f;
          float cp = fmodf(elapsed_norm + legs[i].phaseOffset, 1.0f);

          x = 0.0f;
          // Về cơ bản là giống bước thẳng, nhưng tính toán theo tọa độ y chứ không phải tọa độ x
          if (cp < swingDuration) {
            // --- SWING: foot steps laterally to the new position ---
            float t = cp / swingDuration;
            float u = 1.0f - t;
            float uu = u * u;
            float uuu = uu * u;
            float tt = t * t;
            float ttt = tt * t;

            // Bezier in Y, with the same kick-and-lunge shape as forward walking.
            // sign flips the whole arc so both directions use the same constants.
            float yOffset = uuu * BEZ_CY0 + 3.0f * uu * t * BEZ_CY1
                            + 3.0f * u * tt * BEZ_CY2 + ttt * BEZ_CY3;

            y = Lc + sign * yOffset;
            z = uuu * BEZ_Z0 + 3.0f * uu * t * BEZ_Z1 + 3.0f * u * tt * BEZ_Z2 + ttt * BEZ_Z3;
            isStance = false;

          } else {
            // --- STANCE: foot drags laterally, pushing the body sideways ---
            // For crab right: foot drags from Lc+15 → Lc-15 (left in body frame)
            // The body moves right over the planted feet.
            float t = (cp - swingDuration) / stanceDuration;
            y = Lc + sign * ((crabStep / 2.0f) - crabStep * t);
            z = zRest;
            isStance = true;
          }
          break;
        }

      // Đứng yên (tư thế mặc định)
      case 's':
      default:
        {
          x = 0.0f;
          y = Lc;
          z = zRest;
          break;
        }

    }  // hết xử lý tư thế

    // Đẩy kết quả đọc cảm biến vào tọa độ
    float finalX, finalY, finalZ;  // Kết quả cuối cùng của tọa độ
    if (currentState == 'z') {
      finalX = x;
      finalY = y;
      finalZ = z;
    } else {
      finalX = x - balX;
      finalY = y - balY + (isStance ? balY * 0.5f : 0.0f);
      finalZ = z;
      if (isStance) {
        finalZ += legs[i].isRightSide ? -balZ_Roll : balZ_Roll;
        finalZ += legs[i].isFrontLeg ? -balZ_Pitch : balZ_Pitch;
      }
    }

    // Tính toán góc quay theo kết quả tọa độ
    JointAngles angles;
    if (calculateIK_Mammal(finalX, finalY, finalZ, angles)) {
      lastValid[i] = angles;
    } else {
      angles = lastValid[i];
      Serial.printf("[IK] Leg %d OOB — holding pose (%.1f, %.1f, %.1f)\n",
                    i, finalX, finalY, finalZ);
    }
    float cTrim = coxaTrim[i];
    if (currentState == 'b') {
      cTrim = -cTrim;
    }
    prepareST3215(legs[i].coxaID, angles.coxa + cTrim, legs[i].coxaDir);
    prepareST3215(legs[i].femurID, angles.femur, legs[i].femurDir);
    prepareST3215(legs[i].tibiaID, angles.tibia, legs[i].tibiaDir);


  }  // hết vòng lặp chân

  // Gửi tín hiệu đồng loạt tới các chân
  if (syncCount > 0)
    st.SyncWritePosEx(syncIDs, syncCount, syncPositions, syncSpeeds, syncAccels);

  updateServoTelemetry(currentTime);


  delay(5);
}
