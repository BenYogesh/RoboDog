#include <Arduino_RouterBridge.h>
#include <Wire.h>
#include "SSD1306Ascii.h"
#include "SSD1306AsciiWire.h"
#include "Arduino_LED_Matrix.h" // Brought back!

#define I2C_ADDRESS 0x3C
SSD1306AsciiWire oled;
ArduinoLEDMatrix matrix;

// 13x8 frames: [row][col], row 0 = top, 1 = LED on.
const byte FACE_SMILEY[8][13] = {
  {0,0,1,1,1,1,1,1,1,1,1,0,0},
  {0,1,0,0,0,0,0,0,0,0,0,1,0},
  {1,0,1,0,0,1,1,0,0,1,0,1,0},
  {1,0,1,0,0,1,1,0,0,1,0,1,0},
  {1,0,0,0,0,0,0,0,0,0,0,1,0},
  {1,0,1,0,0,0,0,0,0,1,0,1,0},
  {0,1,0,1,1,1,1,1,1,0,1,0,0},
  {0,0,1,1,1,1,1,1,1,1,0,0,0},
};

const byte FACE_INDIFFERENT[8][13] = {
  {0,0,1,1,1,1,1,1,1,1,1,0,0},
  {0,1,0,0,0,0,0,0,0,0,0,1,0},
  {1,0,1,1,0,0,1,0,0,1,1,0,1},
  {1,0,1,1,0,1,1,1,0,1,1,0,1},
  {1,0,0,0,0,0,0,0,0,0,0,0,1},
  {1,0,0,1,1,1,1,1,1,1,0,0,1},
  {0,1,0,0,0,0,0,0,0,0,0,1,0},
  {0,0,1,1,1,1,1,1,1,1,1,0,0},
};

const byte FACE_NEUTRAL[8][13] = {
  {0,0,0,0,0,0,0,0,0,0,0,0,0},
  {0,0,0,0,0,0,0,0,0,0,0,0,0},
  {0,0,0,0,0,0,0,0,0,0,0,0,0},
  {0,0,0,0,0,0,0,0,0,0,0,0,0},
  {0,0,0,0,0,0,0,0,0,0,0,0,0},
  {0,0,0,0,0,0,0,0,0,0,0,0,0},
  {0,0,0,0,0,0,0,0,0,0,0,0,0},
  {0,0,0,0,0,0,0,0,0,0,0,0,0},
};

void drawMatrixFrame(const byte frame[8][13]) {
  // The UNO Q expects 104 pixels packed tightly into four 32-bit integers.
  // We dynamically compress your 2D array into this format here!
  uint32_t packedFrame[4] = {0, 0, 0, 0};
  int bitCount = 0;
  
  for (int r = 0; r < 8; r++) {
    for (int c = 0; c < 13; c++) {
      if (frame[r][c] == 1) {
        int wordIdx = bitCount / 32;
        int bitIdx = 31 - (bitCount % 32); // MSB first (Left justified)
        packedFrame[wordIdx] |= (1UL << bitIdx);
      }
      bitCount++;
    }
  }
  
  matrix.loadFrame(packedFrame);
}

void handle_gesture(String command) {
  oled.clear();
  oled.println(command);
}

void handle_face_expression(String expression) {
  if (expression == "smiley") {
    drawMatrixFrame(FACE_SMILEY);
  } else if (expression == "indifferent") {
    drawMatrixFrame(FACE_INDIFFERENT);
  } else {
    drawMatrixFrame(FACE_NEUTRAL);
  }
}

void send_motor_command(String command) {
  // Prefixed framing ("CMD:" + char) ensures the ESP32 doesn't trigger on noise
  Serial1.print("CMD:");
  Serial1.write(command.c_str());
  Serial1.write('\n');
}

void setup() {
  Serial1.begin(115200); // hardware UART on D0(RX)/D1(TX) -> ESP32

  Wire.begin();
  oled.begin(&Adafruit128x64, I2C_ADDRESS);
  oled.setFont(Adafruit5x7);
  oled.clear();

  // Initialize the UNO Q 13x8 matrix hardware
  matrix.begin();
  drawMatrixFrame(FACE_NEUTRAL);

  Bridge.begin();
  Bridge.provide_safe("update_oled", handle_gesture);
  Bridge.provide_safe("send_motor_command", send_motor_command);
  Bridge.provide_safe("update_face_matrix", handle_face_expression);
}

void loop() {
  // Bridge handles all background polling automatically
}