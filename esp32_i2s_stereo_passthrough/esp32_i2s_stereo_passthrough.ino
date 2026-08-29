/*
 * ESP32 stereo I2S microphone-to-speaker pass-through.
 *
 * Hardware assumed:
 *   - Two INMP441 microphones:
 *       Left  L/R -> GND
 *       Right L/R -> 3V3
 *       Both SD pins share MIC_SD_PIN because the microphones
 *       tristate the I2S data line outside their selected slot.
 *   - Two MAX98357A amplifiers:
 *       Left  SD_MODE -> 3V3 (left channel)
 *       Right SD_MODE -> 3V3 through approximately 220 kOhm
 *                       (right channel, with 3.3 V logic)
 *       Both GAIN_SLOT pins -> GND for approximately 12 dB gain.
 *
 * The microphone and speaker use separate I2S controllers and therefore
 * separate clock buses. Do not connect the microphone BCLK/WS wires to the
 * speaker BCLK/WS wires in this sketch.
 *
 * Speaker outputs are bridged/differential: connect each speaker between
 * its amplifier's OUTP/SPK+ and OUTN/SPK- terminals, never to GND.
 */

#include <Arduino.h>
#include <ESP_I2S.h>

// Speaker I2S bus: I2S_NUM_0
constexpr int SPEAKER_BCLK_PIN = 25;
constexpr int SPEAKER_LRC_PIN = 26;
constexpr int SPEAKER_DIN_PIN = 23;

// Microphone I2S bus: I2S_NUM_1
constexpr int MIC_SCK_PIN = 14;  // INMP441 SCK/BCLK
constexpr int MIC_WS_PIN = 13;  // INMP441 WS/LRCLK
constexpr int MIC_SD_PIN = 15;  // Shared SD from both microphones

constexpr uint32_t SAMPLE_RATE = 16000;
constexpr size_t STEREO_FRAMES_PER_BLOCK = 128;
constexpr size_t STEREO_SAMPLES_PER_BLOCK = STEREO_FRAMES_PER_BLOCK * 2;

I2SClass speakerI2S(I2S_NUM_0);
I2SClass micI2S(I2S_NUM_1);

// Each stereo frame contains [left, right], with one 32-bit slot per channel.
int32_t audioBuffer[STEREO_SAMPLES_PER_BLOCK];

[[noreturn]] void stopWithError(const char *message) {
  Serial.println(message);
  while (true) {
    delay(1000);
  }
}

void setup() {
  Serial.begin(115200);
  delay(200);

  Serial.println();
  Serial.println("Starting stereo I2S pass-through...");

  // The two microphones share the mic bus, while the two amplifiers share
  // the speaker bus. I2S_SLOT_MODE_STEREO preserves both L/R slots.
  micI2S.setPins(MIC_SCK_PIN, MIC_WS_PIN, -1, MIC_SD_PIN);
  speakerI2S.setPins(
    SPEAKER_BCLK_PIN,
    SPEAKER_LRC_PIN,
    SPEAKER_DIN_PIN,
    -1
  );

  const bool micStarted = micI2S.begin(
    I2S_MODE_STD,
    SAMPLE_RATE,
    I2S_DATA_BIT_WIDTH_32BIT,
    I2S_SLOT_MODE_STEREO,
    I2S_STD_SLOT_BOTH
  );

  const bool speakerStarted = speakerI2S.begin(
    I2S_MODE_STD,
    SAMPLE_RATE,
    I2S_DATA_BIT_WIDTH_32BIT,
    I2S_SLOT_MODE_STEREO,
    I2S_STD_SLOT_BOTH
  );

  if (!micStarted) {
    stopWithError("Microphone I2S initialization failed.");
  }
  if (!speakerStarted) {
    stopWithError("Speaker I2S initialization failed.");
  }

  Serial.println("I2S initialized.");
  Serial.println("Pass-through active: left microphone -> left amp, right microphone -> right amp.");
}

void loop() {
  const size_t bytesRead = micI2S.readBytes(
    reinterpret_cast<char *>(audioBuffer),
    sizeof(audioBuffer)
  );

  if (bytesRead == 0) {
    return;
  }

  // Keep the I2S write aligned to complete 32-bit slots if a read returns
  // a partial block.
  const size_t alignedBytes = bytesRead - (bytesRead % sizeof(int32_t));
  if (alignedBytes == 0) {
    return;
  }

  const size_t bytesWritten = speakerI2S.write(audioBuffer, alignedBytes);
  if (bytesWritten != alignedBytes) {
    Serial.printf(
      "I2S write short: expected %u, wrote %u bytes\n",
      static_cast<unsigned>(alignedBytes),
      static_cast<unsigned>(bytesWritten)
    );
  }
}
