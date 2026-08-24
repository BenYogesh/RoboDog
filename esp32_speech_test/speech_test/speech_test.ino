// ESP32 audio test for one INMP441 microphone + one MAX98357A.
//
// The microphone is sent to the UNO Q as 24 kHz mono PCM16 over TCP.
// Sound commands open 16 kHz mono PCM WAV files from LittleFS. In manual
// mode, the UNO Q can also forward 16 kHz mono PCM from a laptop to the
// MAX98357A over a second TCP stream.

#include <Arduino.h>
#include <ESP_I2S.h>
#include <LittleFS.h>
#include <WiFi.h>
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include <string.h>

#if __has_include("secrets.h")
#include "secrets.h"
#endif

#ifndef TEST_WIFI_SSID
#define TEST_WIFI_SSID "CHANGE_ME"
#endif

#ifndef TEST_WIFI_PASSWORD
#define TEST_WIFI_PASSWORD "CHANGE_ME"
#endif

#ifndef TEST_UNO_Q_HOST
#define TEST_UNO_Q_HOST "192.168.137.67"
#endif

#ifndef TEST_UNO_Q_AUDIO_PORT
#define TEST_UNO_Q_AUDIO_PORT 3333
#endif

#ifndef TEST_UNO_Q_SPEAKER_PORT
#define TEST_UNO_Q_SPEAKER_PORT 3336
#endif

// Change these if they conflict with the existing ESP32 gait/servo wiring.
constexpr int MIC_BCLK_PIN = 26;
constexpr int MIC_WS_PIN = 25;
constexpr int MIC_SD_PIN = 33;
constexpr int MIC_LR_PIN = 4; // LOW selects the left I2S slot.

constexpr int SPEAKER_BCLK_PIN = 14;
constexpr int SPEAKER_WS_PIN = 13;
constexpr int SPEAKER_DOUT_PIN = 32;

// Existing project wiring: UNO Q D1/TX -> ESP32 GPIO16 (RX2),
// UNO Q D0/RX <- ESP32 GPIO17 (TX2).
constexpr int ESP32_UART_RX_PIN = 16;
constexpr int ESP32_UART_TX_PIN = 17;

constexpr uint32_t MIC_SAMPLE_RATE = 24000;
constexpr uint32_t SPEAKER_SAMPLE_RATE = 16000;
constexpr size_t AUDIO_FRAME_SAMPLES = 480;  // 20 ms at 24 kHz.
constexpr size_t MIC_CHANNELS = 1;
constexpr int32_t MIC_SHIFT = 14;
constexpr size_t MAX_NETWORK_SPEAKER_FRAME_BYTES = 4096;

I2SClass micI2S(I2S_NUM_0);
I2SClass speakerI2S(I2S_NUM_1);
WiFiClient audioClient;
WiFiClient speakerClient;
String uartLine;
bool uartFrameOverflow = false;
HardwareSerial UnoQLink(2);

int16_t micPcm[AUDIO_FRAME_SAMPLES];
int32_t micRaw[AUDIO_FRAME_SAMPLES];
constexpr size_t WAV_READ_BYTES = 512;
uint8_t wavInput[WAV_READ_BYTES];
int16_t speakerPcm[WAV_READ_BYTES / sizeof(int16_t)];
File soundFile;
String currentSound;
uint32_t soundDataRemaining = 0;
uint32_t lastAudioConnectAttempt = 0;
uint32_t lastSpeakerConnectAttempt = 0;
uint32_t lastWiFiAttempt = 0;
SemaphoreHandle_t soundMutex = nullptr;

uint8_t speakerNetworkFrame[MAX_NETWORK_SPEAKER_FRAME_BYTES];
size_t speakerNetworkExpected = 0;
size_t speakerNetworkReceived = 0;
uint8_t speakerNetworkHeader[12];
size_t speakerNetworkHeaderReceived = 0;
bool speakerNetworkReady = false;

constexpr uint32_t WIFI_CONNECT_TIMEOUT_MS = 20000;
constexpr uint32_t WIFI_RETRY_INTERVAL_MS = 10000;

void writeBigEndian32(uint32_t value) {
  uint8_t header[4] = {
    static_cast<uint8_t>((value >> 24) & 0xff),
    static_cast<uint8_t>((value >> 16) & 0xff),
    static_cast<uint8_t>((value >> 8) & 0xff),
    static_cast<uint8_t>(value & 0xff),
  };
  audioClient.write(header, sizeof(header));
}

void writeAudioStreamHeader() {
  audioClient.write(reinterpret_cast<const uint8_t *>("AUD0"), 4);
  uint8_t header[8] = {
    static_cast<uint8_t>((MIC_SAMPLE_RATE >> 24) & 0xff),
    static_cast<uint8_t>((MIC_SAMPLE_RATE >> 16) & 0xff),
    static_cast<uint8_t>((MIC_SAMPLE_RATE >> 8) & 0xff),
    static_cast<uint8_t>(MIC_SAMPLE_RATE & 0xff),
    0,
    static_cast<uint8_t>(MIC_CHANNELS),
    0,
    16,
  };
  audioClient.write(header, sizeof(header));
}

uint32_t readBigEndian32(const uint8_t *bytes) {
  return (static_cast<uint32_t>(bytes[0]) << 24) |
         (static_cast<uint32_t>(bytes[1]) << 16) |
         (static_cast<uint32_t>(bytes[2]) << 8) |
         static_cast<uint32_t>(bytes[3]);
}

bool readFileBytes(File &file, uint8_t *buffer, size_t length) {
  return file.read(buffer, length) == length;
}

uint16_t readLittleEndian16(const uint8_t *bytes) {
  return static_cast<uint16_t>(bytes[0]) | (static_cast<uint16_t>(bytes[1]) << 8);
}

uint32_t readLittleEndian32(const uint8_t *bytes) {
  return static_cast<uint32_t>(bytes[0]) | (static_cast<uint32_t>(bytes[1]) << 8) | (static_cast<uint32_t>(bytes[2]) << 16) | (static_cast<uint32_t>(bytes[3]) << 24);
}

void rejectSound(const String &sound, const char *reason) {
  if (soundFile) {
    soundFile.close();
  }
  soundDataRemaining = 0;
  currentSound = "";
  Serial.printf("PLAYBACK_STATUS ERROR sound=%s reason=%s\n",
                sound.c_str(), reason);
  Serial.printf("NACK:WAV:%s:%s\n", sound.c_str(), reason);
}

bool openWavSoundLocked(const String &sound) {
  if (sound != "beep" && sound != "success" && sound != "error") {
    rejectSound(sound, "UNSUPPORTED_SOUND");
    return false;
  }

  if (soundFile) {
    soundFile.close();
  }

  const String path = String("/sounds/") + sound + ".wav";
  Serial.printf("PLAYBACK_STATUS OPENING sound=%s file=%s\n",
                sound.c_str(), path.c_str());
  soundFile = LittleFS.open(path, "r");
  if (!soundFile) {
    rejectSound(sound, "FILE_NOT_FOUND");
    return false;
  }

  uint8_t riffHeader[12];
  if (!readFileBytes(soundFile, riffHeader, sizeof(riffHeader)) || memcmp(riffHeader, "RIFF", 4) != 0 || memcmp(riffHeader + 8, "WAVE", 4) != 0) {
    rejectSound(sound, "NOT_WAV");
    return false;
  }

  bool foundFormat = false;
  bool foundData = false;
  uint16_t audioFormat = 0;
  uint16_t channels = 0;
  uint32_t sampleRate = 0;
  uint16_t bitsPerSample = 0;
  uint32_t dataBytes = 0;

  while (soundFile.available()) {
    uint8_t chunkHeader[8];
    if (!readFileBytes(soundFile, chunkHeader, sizeof(chunkHeader))) {
      break;
    }

    const uint32_t chunkSize = readLittleEndian32(chunkHeader + 4);
    const uint32_t chunkDataStart = soundFile.position();

    if (memcmp(chunkHeader, "fmt ", 4) == 0) {
      if (chunkSize < 16) {
        rejectSound(sound, "BAD_FMT_CHUNK");
        return false;
      }

      uint8_t formatBytes[16];
      if (!readFileBytes(soundFile, formatBytes, sizeof(formatBytes))) {
        rejectSound(sound, "TRUNCATED_FMT_CHUNK");
        return false;
      }
      audioFormat = readLittleEndian16(formatBytes);
      channels = readLittleEndian16(formatBytes + 2);
      sampleRate = readLittleEndian32(formatBytes + 4);
      bitsPerSample = readLittleEndian16(formatBytes + 14);
      foundFormat = true;
    } else if (memcmp(chunkHeader, "data", 4) == 0) {
      if (!foundFormat) {
        rejectSound(sound, "FMT_AFTER_DATA");
        return false;
      }
      dataBytes = chunkSize;
      foundData = true;
      break;
    }

    // WAV chunks are padded to an even number of bytes.
    const uint32_t nextChunk = chunkDataStart + chunkSize + (chunkSize & 1U);
    if (!soundFile.seek(nextChunk)) {
      rejectSound(sound, "BAD_CHUNK_OFFSET");
      return false;
    }
  }

  if (!foundFormat || !foundData) {
    rejectSound(sound, "MISSING_FMT_OR_DATA");
    return false;
  }
  if (audioFormat != 1 || channels != 1 || bitsPerSample != 16 || sampleRate != SPEAKER_SAMPLE_RATE || (dataBytes & 1U) != 0) {
    rejectSound(sound, "EXPECTED_PCM16_MONO_16KHZ");
    return false;
  }

  currentSound = sound;
  soundDataRemaining = dataBytes;
  Serial.printf("PLAYBACK_STATUS STARTED sound=%s bytes=%lu\n",
                currentSound.c_str(),
                static_cast<unsigned long>(soundDataRemaining));
  Serial.printf("ACK:WAV_STARTED:%s bytes=%lu\n", currentSound.c_str(),
                static_cast<unsigned long>(soundDataRemaining));
  return true;
}

bool openWavSound(const String &sound) {
  if (soundMutex != nullptr) {
    xSemaphoreTake(soundMutex, portMAX_DELAY);
  }
  const bool opened = openWavSoundLocked(sound);
  if (soundMutex != nullptr) {
    xSemaphoreGive(soundMutex);
  }
  return opened;
}

const char *soundForCommand(char command) {
  switch (command) {
    case 'B':
      return "beep";
    case 'S':
      return "success";
    case 'E':
      return "error";
    default:
      return nullptr;
  }
}

void readPlayCommands() {
  while (UnoQLink.available()) {
    const char character = static_cast<char>(UnoQLink.read());

    if (character == '\n') {
      uartLine.trim();
      if (!uartFrameOverflow && uartLine.startsWith("SND:") &&
          uartLine.length() == 5) {
        const char command = uartLine.charAt(4);
        const char *sound = soundForCommand(command);
        if (sound != nullptr) {
          Serial.printf("UART_COMMAND_RECEIVED frame=%s sound=%s\n",
                        uartLine.c_str(), sound);
          Serial.printf("COMMAND_RECEIVED %c -> PLAY:%s\n", command,
                        sound);
          Serial.printf("ACK:CMD_RECEIVED:%c:%s\n", command, sound);
          const bool started = openWavSound(String(sound));
          Serial.printf("COMMAND_RESULT %c sound=%s status=%s\n", command,
                        sound, started ? "PLAYBACK_STARTED"
                                       : "PLAYBACK_REJECTED");
        }
      }
      // Invalid/noisy frames are intentionally silent on the USB monitor.
      uartLine = "";
      uartFrameOverflow = false;
    } else if (character != '\r') {
      if (!uartFrameOverflow && uartLine.length() < 16) {
        uartLine += character;
      } else {
        // Drop an overlong/noisy frame until its terminator arrives.
        uartFrameOverflow = true;
      }
    }
  }
}

void pumpWav() {
  if (soundMutex != nullptr) {
    xSemaphoreTake(soundMutex, portMAX_DELAY);
  }

  if (!soundFile || soundDataRemaining == 0) {
    if (soundMutex != nullptr) {
      xSemaphoreGive(soundMutex);
    }
    return;
  }

  size_t bytesToRead = min(
    static_cast<size_t>(soundDataRemaining), sizeof(wavInput));
  bytesToRead -= bytesToRead % sizeof(int16_t);
  if (bytesToRead == 0) {
    rejectSound(currentSound, "ODD_SAMPLE_DATA");
    if (soundMutex != nullptr) {
      xSemaphoreGive(soundMutex);
    }
    return;
  }

  const size_t bytesRead = soundFile.read(wavInput, bytesToRead);
  if (bytesRead != bytesToRead) {
    rejectSound(currentSound, "READ_ERROR");
    if (soundMutex != nullptr) {
      xSemaphoreGive(soundMutex);
    }
    return;
  }

  const size_t sampleCount = bytesRead / sizeof(int16_t);
  for (size_t index = 0; index < sampleCount; ++index) {
    const size_t byteIndex = index * sizeof(int16_t);
    speakerPcm[index] = static_cast<int16_t>(
      static_cast<uint16_t>(wavInput[byteIndex]) | (static_cast<uint16_t>(wavInput[byteIndex + 1]) << 8));
  }

  const size_t bytesWritten = speakerI2S.write(
    speakerPcm, sampleCount * sizeof(speakerPcm[0]));
  if (bytesWritten != bytesRead) {
    rejectSound(currentSound, "I2S_WRITE_ERROR");
    if (soundMutex != nullptr) {
      xSemaphoreGive(soundMutex);
    }
    return;
  }

  soundDataRemaining -= bytesRead;
  if (soundDataRemaining == 0) {
    Serial.printf("PLAYBACK_STATUS DONE sound=%s\n", currentSound.c_str());
    Serial.printf("SOUND_DONE:%s\n", currentSound.c_str());
    soundFile.close();
    currentSound = "";
  }

  if (soundMutex != nullptr) {
    xSemaphoreGive(soundMutex);
  }
}

void soundPlaybackTask(void *) {
  while (true) {
    pumpWav();
    bool idle = false;
    if (soundMutex != nullptr) {
      xSemaphoreTake(soundMutex, portMAX_DELAY);
      idle = soundDataRemaining == 0;
      xSemaphoreGive(soundMutex);
    } else {
      idle = soundDataRemaining == 0;
    }
    if (idle) {
      vTaskDelay(pdMS_TO_TICKS(2));
    }
  }
}

int16_t convertMicSample(int32_t rawSample) {
  int32_t sample = rawSample >> MIC_SHIFT;
  sample = constrain(sample, -32768L, 32767L);
  return static_cast<int16_t>(sample);
}

void connectAudioStreamIfNeeded() {
  if (WiFi.status() != WL_CONNECTED || audioClient.connected() || millis() - lastAudioConnectAttempt < 1000) {
    return;
  }

  lastAudioConnectAttempt = millis();
  Serial.printf("Connecting audio stream to %s:%d...\n", TEST_UNO_Q_HOST,
                TEST_UNO_Q_AUDIO_PORT);
  if (audioClient.connect(TEST_UNO_Q_HOST, TEST_UNO_Q_AUDIO_PORT)) {
    audioClient.setNoDelay(true);
    writeAudioStreamHeader();
    Serial.println("Audio stream connected.");
  } else {
    Serial.printf(
      "Audio stream connection failed. Target=%s:%d localIP=%s "
      "gateway=%s WiFiStatus=%d\n",
      TEST_UNO_Q_HOST, TEST_UNO_Q_AUDIO_PORT,
      WiFi.localIP().toString().c_str(), WiFi.gatewayIP().toString().c_str(),
      static_cast<int>(WiFi.status()));
  }
}

void resetSpeakerNetworkState() {
  speakerNetworkExpected = 0;
  speakerNetworkReceived = 0;
  speakerNetworkHeaderReceived = 0;
  speakerNetworkReady = false;
}

void connectSpeakerStreamIfNeeded() {
  if (WiFi.status() != WL_CONNECTED || speakerClient.connected() ||
      millis() - lastSpeakerConnectAttempt < 1000) {
    return;
  }

  lastSpeakerConnectAttempt = millis();
  if (speakerClient.connect(TEST_UNO_Q_HOST, TEST_UNO_Q_SPEAKER_PORT)) {
    speakerClient.setNoDelay(true);
    resetSpeakerNetworkState();
    Serial.printf("Speaker stream connected to %s:%d.\n", TEST_UNO_Q_HOST,
                  TEST_UNO_Q_SPEAKER_PORT);
  }
}

void streamMicFrame() {
  if (!audioClient.connected()) {
    return;
  }

  const size_t bytesRead = micI2S.readBytes(
    reinterpret_cast<char *>(micRaw), sizeof(micRaw));
  if (bytesRead != sizeof(micRaw)) {
    return;
  }

  for (size_t index = 0; index < AUDIO_FRAME_SAMPLES * MIC_CHANNELS; ++index) {
    micPcm[index] = convertMicSample(micRaw[index]);
  }

  const uint32_t payloadBytes = sizeof(micPcm);
  writeBigEndian32(payloadBytes);
  audioClient.write(reinterpret_cast<const uint8_t *>(micPcm), payloadBytes);
  if (!audioClient.connected()) {
    audioClient.stop();
  }
}

void pumpNetworkSpeaker() {
  if (!speakerClient.connected()) {
    resetSpeakerNetworkState();
    return;
  }

  while (speakerClient.available()) {
    if (!speakerNetworkReady) {
      const size_t remaining = sizeof(speakerNetworkHeader) -
                               speakerNetworkHeaderReceived;
      const size_t count = min(remaining,
                               static_cast<size_t>(speakerClient.available()));
      const size_t read = speakerClient.read(
        speakerNetworkHeader + speakerNetworkHeaderReceived, count);
      if (read == 0) {
        return;
      }
      speakerNetworkHeaderReceived += read;
      if (speakerNetworkHeaderReceived < sizeof(speakerNetworkHeader)) {
        return;
      }
      if (memcmp(speakerNetworkHeader, "AUD0", 4) != 0 ||
          readBigEndian32(speakerNetworkHeader + 4) != SPEAKER_SAMPLE_RATE ||
          speakerNetworkHeader[9] != 1 || speakerNetworkHeader[11] != 16) {
        Serial.println("Speaker stream header rejected.");
        speakerClient.stop();
        resetSpeakerNetworkState();
        return;
      }
      speakerNetworkReady = true;
    }

    if (speakerNetworkExpected == 0) {
      uint8_t frameHeader[4];
      const size_t available = speakerClient.available();
      if (available < sizeof(frameHeader)) {
        return;
      }
      if (speakerClient.read(frameHeader, sizeof(frameHeader)) !=
          sizeof(frameHeader)) {
        return;
      }
      speakerNetworkExpected = readBigEndian32(frameHeader);
      speakerNetworkReceived = 0;
      if (speakerNetworkExpected == 0 ||
          speakerNetworkExpected > MAX_NETWORK_SPEAKER_FRAME_BYTES ||
          (speakerNetworkExpected & 1U) != 0) {
        Serial.println("Speaker stream frame rejected.");
        speakerClient.stop();
        resetSpeakerNetworkState();
        return;
      }
    }

    const size_t remaining = speakerNetworkExpected - speakerNetworkReceived;
    const size_t count = min(remaining,
                             static_cast<size_t>(speakerClient.available()));
    if (count == 0) {
      return;
    }
    const size_t read = speakerClient.read(
      speakerNetworkFrame + speakerNetworkReceived, count);
    speakerNetworkReceived += read;
    if (speakerNetworkReceived < speakerNetworkExpected) {
      return;
    }

    // A local WAV has priority over network playback. Manual mode normally
    // has no local sound playing, and the mutex keeps both I2S writers safe.
    if (soundMutex != nullptr) {
      xSemaphoreTake(soundMutex, portMAX_DELAY);
    }
    if (soundDataRemaining == 0) {
      speakerI2S.write(speakerNetworkFrame, speakerNetworkExpected);
    }
    if (soundMutex != nullptr) {
      xSemaphoreGive(soundMutex);
    }
    speakerNetworkExpected = 0;
    speakerNetworkReceived = 0;
  }
}

const char *wifiStatusName(wl_status_t status) {
  switch (status) {
    case WL_NO_SSID_AVAIL:
      return "WL_NO_SSID_AVAIL";
    case WL_CONNECT_FAILED:
      return "WL_CONNECT_FAILED";
    case WL_CONNECTION_LOST:
      return "WL_CONNECTION_LOST";
    case WL_DISCONNECTED:
      return "WL_DISCONNECTED";
    case WL_CONNECTED:
      return "WL_CONNECTED";
    default:
      return "WL_OTHER";
  }
}

void scanForWiFiTarget() {
  Serial.println("Scanning nearby Wi-Fi networks...");
  const int networkCount = WiFi.scanNetworks();
  if (networkCount < 0) {
    Serial.println("Wi-Fi scan failed.");
    return;
  }
  if (networkCount == 0) {
    Serial.println("No Wi-Fi networks found.");
    return;
  }

  bool targetFound = false;
  for (int index = 0; index < networkCount; ++index) {
    const String scannedSsid = WiFi.SSID(index);
    Serial.printf("  %s  RSSI=%d dBm channel=%d\n", scannedSsid.c_str(),
                  WiFi.RSSI(index), WiFi.channel(index));
    if (scannedSsid == TEST_WIFI_SSID) {
      targetFound = true;
    }
  }
  Serial.printf("Configured SSID %s in scan results.\n",
                targetFound ? "was" : "was not");
  WiFi.scanDelete();
}

bool connectWiFi() {
  if (String(TEST_WIFI_SSID) == "CHANGE_ME" || String(TEST_WIFI_PASSWORD) == "CHANGE_ME") {
    Serial.println("Wi-Fi credentials are still CHANGE_ME.");
    return false;
  }

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  delay(100);
  Serial.printf("Connecting to Wi-Fi SSID: %s\n", TEST_WIFI_SSID);
  WiFi.begin(TEST_WIFI_SSID, TEST_WIFI_PASSWORD);

  const uint32_t startTime = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - startTime < WIFI_CONNECT_TIMEOUT_MS) {
    delay(500);
    Serial.print('.');
  }
  Serial.println();

  const wl_status_t status = WiFi.status();
  if (status == WL_CONNECTED) {
    Serial.printf("Wi-Fi connected: IP=%s RSSI=%d dBm channel=%d\n",
                  WiFi.localIP().toString().c_str(), WiFi.RSSI(),
                  WiFi.channel());
    return true;
  }

  Serial.printf("Wi-Fi connection failed: %s (%d)\n", wifiStatusName(status),
                static_cast<int>(status));
  scanForWiFiTarget();
  return false;
}

void maintainWiFi() {
  if (WiFi.status() == WL_CONNECTED) {
    return;
  }
  if (millis() - lastWiFiAttempt < WIFI_RETRY_INTERVAL_MS) {
    return;
  }
  lastWiFiAttempt = millis();
  connectWiFi();
}

void setup() {
  Serial.begin(115200);
  UnoQLink.begin(115200, SERIAL_8N1, ESP32_UART_RX_PIN, ESP32_UART_TX_PIN);
  if (!LittleFS.begin(false)) {
    Serial.println("LittleFS mount failed; upload the filesystem before testing.");
  } else {
    Serial.printf("LittleFS mounted: %lu/%lu bytes used\n",
                  static_cast<unsigned long>(LittleFS.usedBytes()),
                  static_cast<unsigned long>(LittleFS.totalBytes()));
  }
  pinMode(MIC_LR_PIN, OUTPUT);
  digitalWrite(MIC_LR_PIN, LOW);

  // Separate I2S controllers let the mic use Realtime's 24 kHz input rate
  // while the MAX98357A uses a supported 16 kHz local tone rate.
  micI2S.setPins(MIC_BCLK_PIN, MIC_WS_PIN, -1, MIC_SD_PIN);
  speakerI2S.setPins(SPEAKER_BCLK_PIN, SPEAKER_WS_PIN, SPEAKER_DOUT_PIN, -1);

  const bool micStarted = micI2S.begin(
    I2S_MODE_STD, MIC_SAMPLE_RATE, I2S_DATA_BIT_WIDTH_32BIT,
    I2S_SLOT_MODE_MONO, I2S_STD_SLOT_LEFT);
  const bool speakerStarted = speakerI2S.begin(
    I2S_MODE_STD, SPEAKER_SAMPLE_RATE, I2S_DATA_BIT_WIDTH_16BIT,
    I2S_SLOT_MODE_MONO, I2S_STD_SLOT_LEFT);
  if (!micStarted || !speakerStarted) {
    Serial.println("I2S initialization failed.");
    while (true) {
      delay(1000);
    }
  }

  soundMutex = xSemaphoreCreateMutex();
  if (soundMutex == nullptr || xTaskCreate(soundPlaybackTask, "wav_playback", 4096, nullptr, 1, nullptr) != pdPASS) {
    Serial.println("WAV playback task initialization failed.");
    while (true) {
      delay(1000);
    }
  }

  connectWiFi();
}

void loop() {
  readPlayCommands();
  maintainWiFi();
  connectAudioStreamIfNeeded();
  connectSpeakerStreamIfNeeded();
  streamMicFrame();
  pumpNetworkSpeaker();
}
