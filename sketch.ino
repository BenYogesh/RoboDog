/*
 * UNO Q MCU (STM32) sketch.
 *
 * Three Bridge-exposed functions:
 *   - update_oled: unchanged from your original sketch, displays
 *     status text sent from the Python vision script.
 *   - send_motor_command: forwards motor commands out over the
 *     hardware UART (D0/D1, JDIGITAL header) to the ESP32.
 *   - update_face_matrix: new — shows a face expression on the UNO Q's
 *     built-in LED matrix based on face-recognition status.
 *
 * IMPORTANT: Serial1 here is the D0/D1 hardware UART pins, not the
 * separate internal MPU<->MCU bridge link — safe to use for the
 * ESP32 connection.
 *
 * ASSUMPTION on the LED matrix: this uses the standard Arduino_LED_Matrix
 * library, since I don't have your earlier "reusable library wrapper"
 * for this same matrix (mentioned only in passing from an earlier
 * project) in front of me. If your actual API differs, swap the
 * matrix.loadFrame(...) calls below for your own wrapper's equivalent —
 * the Bridge-facing function signature (a String expression name) can
 * stay the same either way.
 */

#include <Arduino_RouterBridge.h>
#include <Wire.h>
#include "SSD1306Ascii.h"
#include "SSD1306AsciiWire.h"
#include "Arduino_LED_Matrix.h"

#define I2C_ADDRESS 0x3C
SSD1306AsciiWire oled;
ArduinoLEDMatrix matrix;

// 12x8 bitmaps — ASSUMPTION on matrix size (you mentioned 13x8; the
// standard Arduino_LED_Matrix API works in 12x8 frames). If your matrix
// is genuinely 13 columns, this library isn't the right fit and your
// own wrapper from the earlier project should be used instead.
const uint32_t FACE_SMILEY[] = {
  0x1f81f8, 0x0f01e0, 0x00000c,
  0x00c000, 0x3f83f8, 0x000000
};
const uint32_t FACE_INDIFFERENT[] = {
  0x1f81f8, 0x00000e, 0x00000c,
  0x000000, 0x3f83f8, 0x000000
};
const uint32_t FACE_NEUTRAL[] = {
  0x000000, 0x000000, 0x000000,
  0x000000, 0x000000, 0x000000
};

void handle_gesture(String command) {
  oled.clear();
  oled.println(command);
}

void handle_face_expression(String expression) {
  // TODO: the bitmaps above are rough placeholders, not verified against
  // real hardware — adjust once you can see actual output on the matrix.
  if (expression == "smiley") {
    matrix.loadFrame(FACE_SMILEY);
  } else if (expression == "indifferent") {
    matrix.loadFrame(FACE_INDIFFERENT);
  } else {
    matrix.loadFrame(FACE_NEUTRAL);
  }
}

void send_motor_command(String command) {
  // Prefixed framing ("CMD:" + char) makes it extremely unlikely that
  // stray noise or boot-time log text on Serial1 gets misread as a
  // real command — the ESP32 only acts on a char immediately following
  // that exact 4-byte sequence.
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

  matrix.begin();
  matrix.loadFrame(FACE_NEUTRAL);

  Bridge.begin();
  Bridge.provide_safe("update_oled", handle_gesture);
  Bridge.provide_safe("send_motor_command", send_motor_command);
  Bridge.provide_safe("update_face_matrix", handle_face_expression);
}

void loop() {
}

