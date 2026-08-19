// LittleFS + MAX98357A output-only test.
//
// This sketch does not use Wi-Fi, the microphone, UART, or OpenAI. It mounts
// the existing LittleFS image and plays the three saved WAV files in order:
// beep -> success -> error.

#include <Arduino.h>
#include <ESP_I2S.h>
#include <LittleFS.h>

constexpr int SPEAKER_BCLK_PIN = 14;
constexpr int SPEAKER_WS_PIN = 13;
constexpr int SPEAKER_DOUT_PIN = 32;
constexpr uint32_t SPEAKER_SAMPLE_RATE = 16000;
constexpr size_t WAV_READ_BYTES = 512;
constexpr uint32_t TONE_FREQUENCY = 1000;
constexpr uint32_t TONE_DURATION_MS = 2000;
constexpr size_t TONE_BUFFER_SAMPLES = 256;

I2SClass speakerI2S(I2S_NUM_1);
uint8_t wavBytes[WAV_READ_BYTES];
int16_t pcmSamples[WAV_READ_BYTES / sizeof(int16_t)];
int16_t toneSamples[TONE_BUFFER_SAMPLES];

uint16_t readLittleEndian16(const uint8_t *bytes) {
  return static_cast<uint16_t>(bytes[0]) |
         (static_cast<uint16_t>(bytes[1]) << 8);
}

uint32_t readLittleEndian32(const uint8_t *bytes) {
  return static_cast<uint32_t>(bytes[0]) |
         (static_cast<uint32_t>(bytes[1]) << 8) |
         (static_cast<uint32_t>(bytes[2]) << 16) |
         (static_cast<uint32_t>(bytes[3]) << 24);
}

bool readExactly(File &file, uint8_t *buffer, size_t length) {
  return file.read(buffer, length) == length;
}

bool failWav(const char *path, const char *reason) {
  Serial.printf("WAV_ERROR path=%s reason=%s\n", path, reason);
  return false;
}

bool playWav(const char *path) {
  Serial.printf("WAV_OPEN %s\n", path);
  File file = LittleFS.open(path, "r");
  if (!file) {
    return failWav(path, "FILE_NOT_FOUND");
  }

  uint8_t riffHeader[12];
  if (!readExactly(file, riffHeader, sizeof(riffHeader)) ||
      memcmp(riffHeader, "RIFF", 4) != 0 ||
      memcmp(riffHeader + 8, "WAVE", 4) != 0) {
    file.close();
    return failWav(path, "NOT_WAV");
  }

  bool foundFormat = false;
  bool foundData = false;
  uint16_t audioFormat = 0;
  uint16_t channels = 0;
  uint32_t sampleRate = 0;
  uint16_t bitsPerSample = 0;
  uint32_t dataBytes = 0;

  while (file.available()) {
    uint8_t chunkHeader[8];
    if (!readExactly(file, chunkHeader, sizeof(chunkHeader))) {
      file.close();
      return failWav(path, "TRUNCATED_CHUNK");
    }

    const uint32_t chunkSize = readLittleEndian32(chunkHeader + 4);
    const uint32_t chunkDataStart = file.position();

    if (memcmp(chunkHeader, "fmt ", 4) == 0) {
      if (chunkSize < 16) {
        file.close();
        return failWav(path, "BAD_FMT_CHUNK");
      }

      uint8_t formatBytes[16];
      if (!readExactly(file, formatBytes, sizeof(formatBytes))) {
        file.close();
        return failWav(path, "TRUNCATED_FMT_CHUNK");
      }
      audioFormat = readLittleEndian16(formatBytes);
      channels = readLittleEndian16(formatBytes + 2);
      sampleRate = readLittleEndian32(formatBytes + 4);
      bitsPerSample = readLittleEndian16(formatBytes + 14);
      foundFormat = true;
    } else if (memcmp(chunkHeader, "data", 4) == 0) {
      if (!foundFormat) {
        file.close();
        return failWav(path, "FMT_AFTER_DATA");
      }
      dataBytes = chunkSize;
      foundData = true;
      break;
    }

    const uint32_t nextChunk =
        chunkDataStart + chunkSize + (chunkSize & 1U);
    if (!file.seek(nextChunk)) {
      file.close();
      return failWav(path, "BAD_CHUNK_OFFSET");
    }
  }

  if (!foundFormat || !foundData) {
    file.close();
    return failWav(path, "MISSING_FMT_OR_DATA");
  }

  if (audioFormat != 1 || channels != 1 ||
      sampleRate != SPEAKER_SAMPLE_RATE || bitsPerSample != 16 ||
      (dataBytes & 1U) != 0) {
    file.close();
    return failWav(path, "EXPECTED_PCM16_MONO_16KHZ");
  }

  Serial.printf("WAV_START %s bytes=%lu\n", path,
                static_cast<unsigned long>(dataBytes));

  uint32_t remaining = dataBytes;
  while (remaining > 0) {
    size_t bytesToRead = min(static_cast<size_t>(remaining),
                             sizeof(wavBytes));
    bytesToRead -= bytesToRead % sizeof(int16_t);
    if (bytesToRead == 0 || !readExactly(file, wavBytes, bytesToRead)) {
      file.close();
      return failWav(path, "READ_ERROR");
    }

    const size_t sampleCount = bytesToRead / sizeof(int16_t);
    for (size_t index = 0; index < sampleCount; ++index) {
      const size_t byteIndex = index * sizeof(int16_t);
      pcmSamples[index] = static_cast<int16_t>(
          static_cast<uint16_t>(wavBytes[byteIndex]) |
          (static_cast<uint16_t>(wavBytes[byteIndex + 1]) << 8));
    }

    const size_t bytesWritten =
        speakerI2S.write(pcmSamples, bytesToRead);
    if (bytesWritten != bytesToRead) {
      file.close();
      return failWav(path, "I2S_WRITE_ERROR");
    }
    remaining -= bytesToRead;
  }

  file.close();
  Serial.printf("WAV_DONE %s\n", path);
  return true;
}

void playTone() {
  Serial.printf("TONE_START %luHz %lums\n",
                static_cast<unsigned long>(TONE_FREQUENCY),
                static_cast<unsigned long>(TONE_DURATION_MS));

  const uint32_t totalSamples =
      SPEAKER_SAMPLE_RATE * TONE_DURATION_MS / 1000;
  const uint32_t samplesPerPeriod = SPEAKER_SAMPLE_RATE / TONE_FREQUENCY;
  uint32_t samplesPlayed = 0;

  while (samplesPlayed < totalSamples) {
    const size_t samplesThisBuffer = min(
        static_cast<size_t>(totalSamples - samplesPlayed),
        TONE_BUFFER_SAMPLES);
    for (size_t index = 0; index < samplesThisBuffer; ++index) {
      const uint32_t phase =
          (samplesPlayed + index) % samplesPerPeriod;
      toneSamples[index] = phase < samplesPerPeriod / 2 ? 16000 : -16000;
    }

    const size_t bytesToWrite = samplesThisBuffer * sizeof(int16_t);
    if (speakerI2S.write(toneSamples, bytesToWrite) != bytesToWrite) {
      Serial.println("TONE_ERROR I2S_WRITE_ERROR");
      return;
    }
    samplesPlayed += samplesThisBuffer;
  }

  Serial.println("TONE_DONE");
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println();
  Serial.println("LittleFS/MAX98357A output test starting.");

  if (!LittleFS.begin(false)) {
    Serial.println("LittleFS mount failed. Do not format; upload the existing data folder.");
    while (true) {
      delay(1000);
    }
  }

  Serial.printf("LittleFS mounted: %lu/%lu bytes used\n",
                static_cast<unsigned long>(LittleFS.usedBytes()),
                static_cast<unsigned long>(LittleFS.totalBytes()));

  speakerI2S.setPins(SPEAKER_BCLK_PIN, SPEAKER_WS_PIN, SPEAKER_DOUT_PIN, -1);
  if (!speakerI2S.begin(I2S_MODE_STD, SPEAKER_SAMPLE_RATE,
                        I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO,
                        I2S_STD_SLOT_LEFT)) {
    Serial.println("I2S speaker initialization failed.");
    while (true) {
      delay(1000);
    }
  }

  playTone();
  delay(1000);

  const char *sounds[] = {
      "/sounds/beep.wav",
      "/sounds/success.wav",
      "/sounds/error.wav",
  };

  for (const char *sound : sounds) {
    playWav(sound);
    delay(1000);
  }

  Serial.println("Output test complete. Reset the ESP32 to repeat.");
}

void loop() {
  delay(1000);
}
