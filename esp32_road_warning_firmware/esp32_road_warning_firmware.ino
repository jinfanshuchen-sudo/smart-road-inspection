/*
  智慧道路巡检：ESP32-S3 综合现场固件

  功能：Hula-Battle WiFi、本地 MQTT、LD06 人车接近、积水、WS2812 灯带、
  RS485 七要素气象站，以及与 dashboard/index.html 对应的命令/遥测协议。

  首次使用：复制 secrets.h.example 为 secrets.h，填写 WiFi 密码后再烧录。
*/

#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Adafruit_NeoPixel.h>
#include "config.h"
#include "secrets.h"

HardwareSerial Ld06Serial(1);
HardwareSerial WeatherSerial(2);
WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);
Adafruit_NeoPixel strip(LED_COUNT, LED_PIN, NEO_GRB + NEO_KHZ800);

struct Ld06State {
  bool enabled = ENABLE_LD06;
  bool online = false;
  int nearestDistanceMm = -1;
  uint32_t crcOk = 0;
  uint32_t crcErr = 0;
  uint32_t lastFrameMs = 0;
} ld06;

struct WaterState {
  bool enabled = ENABLE_WATER;
  int adc = 0;
  float percent = 0;
  int dryAdc = WATER_DRY_ADC;
  int wetAdc = WATER_WET_ADC;
  int caution = WATER_CAUTION_PERCENT;
  int warning = WATER_WARNING_PERCENT;
  int danger = WATER_DANGER_PERCENT;
} water;

struct WeatherState {
  bool enabled = ENABLE_WEATHER;
  bool online = false;
  bool dataValid = false;
  float windSpeedMps = 0;
  float windDirectionDeg = 0;
  float rainfallMm = 0;
  uint32_t lightLuxRaw = 0;
  float temperatureC = 0;
  float humidityPercent = 0;
  float pressureKpa = 0;
  uint32_t lastReadMs = 0;
} weather;

uint32_t uploadIntervalMs = MQTT_UPLOAD_INTERVAL_MS;
uint32_t lastUploadMs = 0;
uint8_t activeBrightness = LED_BRIGHTNESS;
String activeLightPattern = "green";

String telemetryTopic;
String statusTopic;
String commandTopic;

const char* levelFromDistance(int mm) {
  if (mm < 0) return "safe";
  if (mm <= LD06_DANGER_MM) return "danger";
  if (mm <= LD06_WARNING_MM) return "warning";
  if (mm <= LD06_CAUTION_MM) return "caution";
  return "safe";
}

const char* levelFromWater(float percent) {
  if (percent >= water.danger) return "danger";
  if (percent >= water.warning) return "warning";
  if (percent >= water.caution) return "caution";
  return "safe";
}

int levelRank(const char* level) {
  if (strcmp(level, "danger") == 0) return 3;
  if (strcmp(level, "warning") == 0) return 2;
  if (strcmp(level, "caution") == 0) return 1;
  return 0;
}

void setStrip(const char* pattern) {
  uint32_t color = strip.Color(0, 255, 0);
  if (strcmp(pattern, "off") == 0) color = 0;
  else if (strcmp(pattern, "red") == 0 || strcmp(pattern, "danger") == 0) color = strip.Color(255, 0, 0);
  else if (strcmp(pattern, "yellow") == 0 || strcmp(pattern, "warning") == 0) color = strip.Color(255, 160, 0);
  else if (strcmp(pattern, "blue") == 0 || strcmp(pattern, "caution") == 0) color = strip.Color(0, 0, 255);
  else if (strcmp(pattern, "white") == 0) color = strip.Color(255, 255, 255);
  strip.setBrightness(activeBrightness);
  strip.fill(color);
  strip.show();
  activeLightPattern = pattern;
}

void refreshAlarmLight() {
  const char* proximity = ld06.enabled && ld06.online ? levelFromDistance(ld06.nearestDistanceMm) : "safe";
  const char* waterLevel = water.enabled ? levelFromWater(water.percent) : "safe";
  const char* finalLevel = levelRank(proximity) >= levelRank(waterLevel) ? proximity : waterLevel;
  setStrip(finalLevel);
}

uint8_t ld06Crc8(const uint8_t* data, size_t length) {
  uint8_t crc = 0;
  while (length--) {
    crc ^= *data++;
    for (uint8_t bit = 0; bit < 8; bit++) {
      crc = (crc & 0x80) ? uint8_t((crc << 1) ^ 0x4D) : uint8_t(crc << 1);
    }
  }
  return crc;
}

uint16_t modbusCrc16(const uint8_t* data, size_t length) {
  uint16_t crc = 0xFFFF;
  while (length--) {
    crc ^= *data++;
    for (uint8_t bit = 0; bit < 8; bit++) {
      crc = (crc & 1) ? uint16_t((crc >> 1) ^ 0xA001) : uint16_t(crc >> 1);
    }
  }
  return crc;
}

void pollLd06() {
  static uint8_t frame[47];
  static size_t index = 0;
  while (Ld06Serial.available()) {
    uint8_t value = uint8_t(Ld06Serial.read());
    if (index == 0) {
      if (value == 0x54) frame[index++] = value;
      continue;
    }
    if (index == 1 && value != 0x2C) {
      index = 0;
      continue;
    }
    frame[index++] = value;
    if (index < sizeof(frame)) continue;
    index = 0;
    if (ld06Crc8(frame, 46) != frame[46]) {
      ld06.crcErr++;
      continue;
    }
    int nearest = -1;
    for (uint8_t point = 0; point < 12; point++) {
      size_t offset = 6 + point * 3;
      int distance = int(frame[offset]) | (int(frame[offset + 1]) << 8);
      if (distance > 0 && (nearest < 0 || distance < nearest)) nearest = distance;
    }
    ld06.nearestDistanceMm = nearest;
    ld06.lastFrameMs = millis();
    ld06.online = true;
    ld06.crcOk++;
  }
  if (millis() - ld06.lastFrameMs > LD06_OFFLINE_TIMEOUT_MS) ld06.online = false;
}

float clampPercent(float value) {
  if (value < 0) return 0;
  if (value > 100) return 100;
  return value;
}

void pollWater() {
  if (!water.enabled) return;
  water.adc = analogRead(WATER_ADC_PIN);
  float denominator = float(water.wetAdc - water.dryAdc);
  water.percent = denominator == 0 ? 0 : clampPercent((water.adc - water.dryAdc) * 100.0f / denominator);
}

bool readWeatherRegisters(uint16_t* registers, uint8_t count) {
  uint8_t request[8] = {
    WEATHER_SLAVE_ID, WEATHER_FUNCTION_CODE,
    uint8_t(WEATHER_START_REGISTER >> 8), uint8_t(WEATHER_START_REGISTER & 0xFF),
    0, count, 0, 0
  };
  uint16_t requestCrc = modbusCrc16(request, 6);
  request[6] = uint8_t(requestCrc & 0xFF);
  request[7] = uint8_t(requestCrc >> 8);
  while (WeatherSerial.available()) WeatherSerial.read();
  WeatherSerial.write(request, sizeof(request));
  WeatherSerial.flush();

  const size_t expected = 5 + count * 2;
  uint8_t response[32];
  size_t received = 0;
  uint32_t deadline = millis() + 500;
  while (received < expected && millis() < deadline) {
    if (WeatherSerial.available()) response[received++] = uint8_t(WeatherSerial.read());
    else delay(2);
  }
  if (received != expected || response[0] != WEATHER_SLAVE_ID || response[1] != WEATHER_FUNCTION_CODE || response[2] != count * 2) return false;
  uint16_t receivedCrc = uint16_t(response[expected - 2]) | (uint16_t(response[expected - 1]) << 8);
  if (modbusCrc16(response, expected - 2) != receivedCrc) return false;
  for (uint8_t index = 0; index < count; index++) {
    registers[index] = (uint16_t(response[3 + index * 2]) << 8) | response[4 + index * 2];
  }
  return true;
}

void pollWeather() {
  if (!weather.enabled || millis() - weather.lastReadMs < WEATHER_POLL_INTERVAL_MS) return;
  weather.lastReadMs = millis();
  uint16_t value[WEATHER_REGISTER_COUNT];
  if (!readWeatherRegisters(value, WEATHER_REGISTER_COUNT)) {
    weather.online = false;
    weather.dataValid = false;
    return;
  }
  weather.online = true;
  weather.dataValid = true;
  weather.windSpeedMps = value[0] * WEATHER_WIND_SPEED_SCALE;
  weather.windDirectionDeg = value[1] * WEATHER_WIND_DIRECTION_SCALE;
  weather.rainfallMm = value[2] * WEATHER_RAINFALL_SCALE;
  weather.lightLuxRaw = uint32_t(value[3] * WEATHER_LIGHT_SCALE);
  weather.temperatureC = int16_t(value[4]) * WEATHER_TEMPERATURE_SCALE;
  weather.humidityPercent = value[5] * WEATHER_HUMIDITY_SCALE;
  weather.pressureKpa = value[6] * WEATHER_PRESSURE_SCALE;
}

void publishStatus(const char* command, bool ok = true, const char* message = "ok") {
  StaticJsonDocument<768> doc;
  doc["deviceId"] = DEVICE_ID;
  doc["cmd"] = command;
  doc["ok"] = ok;
  doc["message"] = message;
  doc["proximityEnabled"] = ld06.enabled;
  doc["waterEnabled"] = water.enabled;
  doc["weatherEnabled"] = weather.enabled;
  doc["uploadIntervalMs"] = uploadIntervalMs;
  doc["waterDryAdc"] = water.dryAdc;
  doc["waterWetAdc"] = water.wetAdc;
  JsonObject thresholds = doc.createNestedObject("waterThresholds");
  thresholds["caution"] = water.caution;
  thresholds["warning"] = water.warning;
  thresholds["danger"] = water.danger;
  String payload;
  serializeJson(doc, payload);
  mqtt.publish(statusTopic.c_str(), payload.c_str(), false);
}

void publishTelemetry() {
  const char* proximity = ld06.enabled && ld06.online ? levelFromDistance(ld06.nearestDistanceMm) : "safe";
  const char* waterLevel = water.enabled ? levelFromWater(water.percent) : "safe";
  const char* finalLevel = levelRank(proximity) >= levelRank(waterLevel) ? proximity : waterLevel;
  const char* source = levelRank(proximity) >= levelRank(waterLevel) ? "ld06" : "water";

  StaticJsonDocument<2048> doc;
  doc["deviceId"] = DEVICE_ID;
  doc["uptimeMs"] = millis();
  doc["wifiRssi"] = WiFi.RSSI();
  JsonObject lidar = doc.createNestedObject("ld06");
  lidar["enabled"] = ld06.enabled;
  lidar["online"] = ld06.online;
  lidar["nearestDistanceMm"] = ld06.nearestDistanceMm;
  lidar["level"] = proximity;
  lidar["crcOk"] = ld06.crcOk;
  lidar["crcErr"] = ld06.crcErr;

  JsonObject waterJson = doc.createNestedObject("water");
  waterJson["enabled"] = water.enabled;
  waterJson["adc"] = water.adc;
  waterJson["percent"] = water.percent;
  waterJson["level"] = waterLevel;
  waterJson["calibrated"] = water.dryAdc != water.wetAdc;
  waterJson["dryAdc"] = water.dryAdc;
  waterJson["wetAdc"] = water.wetAdc;
  JsonObject thresholds = waterJson.createNestedObject("thresholds");
  thresholds["caution"] = water.caution;
  thresholds["warning"] = water.warning;
  thresholds["danger"] = water.danger;

  JsonObject weatherJson = doc.createNestedObject("weather");
  weatherJson["enabled"] = weather.enabled;
  weatherJson["online"] = weather.online;
  weatherJson["dataValid"] = weather.dataValid;
  weatherJson["windSpeedMps"] = weather.windSpeedMps;
  weatherJson["windDirectionDeg"] = weather.windDirectionDeg;
  weatherJson["rainfallMm"] = weather.rainfallMm;
  weatherJson["lightLuxRaw"] = weather.lightLuxRaw;
  weatherJson["temperatureC"] = weather.temperatureC;
  weatherJson["humidityPercent"] = weather.humidityPercent;
  weatherJson["pressureKpa"] = weather.pressureKpa;

  JsonObject alarm = doc.createNestedObject("alarm");
  alarm["finalLevel"] = finalLevel;
  alarm["sources"] = strcmp(finalLevel, "safe") == 0 ? "-" : source;
  alarm["pattern"] = activeLightPattern;

  String payload;
  serializeJson(doc, payload);
  mqtt.publish(telemetryTopic.c_str(), payload.c_str(), false);
}

void onMqttMessage(char* topic, byte* bytes, unsigned int length) {
  if (String(topic) != commandTopic) return;
  StaticJsonDocument<512> doc;
  if (deserializeJson(doc, bytes, length)) {
    publishStatus("invalid", false, "invalid JSON command");
    return;
  }
  const char* cmd = doc["cmd"] | "";
  if (strcmp(cmd, "ping") == 0 || strcmp(cmd, "getStatus") == 0) {
    publishStatus(cmd);
  } else if (strcmp(cmd, "setUploadInterval") == 0) {
    uploadIntervalMs = constrain(doc["value"] | int(MQTT_UPLOAD_INTERVAL_MS), 250, 60000);
    publishStatus(cmd);
  } else if (strcmp(cmd, "setProximityEnabled") == 0) {
    ld06.enabled = doc["value"] | false;
    publishStatus(cmd);
  } else if (strcmp(cmd, "setWaterEnabled") == 0) {
    water.enabled = doc["value"] | false;
    publishStatus(cmd);
  } else if (strcmp(cmd, "setWeatherEnabled") == 0) {
    weather.enabled = doc["value"] | false;
    publishStatus(cmd);
  } else if (strcmp(cmd, "setWaterThresholds") == 0) {
    int caution = doc["caution"] | water.caution;
    int warning = doc["warning"] | water.warning;
    int danger = doc["danger"] | water.danger;
    if (caution >= 0 && caution < warning && warning < danger && danger <= 100) {
      water.caution = caution; water.warning = warning; water.danger = danger;
      publishStatus(cmd);
    } else publishStatus(cmd, false, "thresholds must satisfy 0 <= caution < warning < danger <= 100");
  } else if (strcmp(cmd, "setWaterCalibration") == 0) {
    int dry = doc["dry"] | water.dryAdc;
    int wet = doc["wet"] | water.wetAdc;
    if (dry >= 0 && dry <= 4095 && wet >= 0 && wet <= 4095 && dry != wet) {
      water.dryAdc = dry; water.wetAdc = wet;
      publishStatus(cmd);
    } else publishStatus(cmd, false, "ADC calibration must contain two different values from 0 to 4095");
  } else if (strcmp(cmd, "setLight") == 0) {
    const char* pattern = doc["pattern"] | "green";
    activeBrightness = constrain(doc["brightness"] | int(activeBrightness), 0, 255);
    setStrip(pattern);
    publishStatus(cmd);
  } else {
    publishStatus(cmd, false, "unsupported command");
  }
}

void connectWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);  // 局域网 MQTT 及时上报，避免 WiFi 省电造成的短时延迟。
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) delay(500);
}

void connectMqtt() {
  while (!mqtt.connected()) {
    String clientId = String(DEVICE_ID) + "-" + String((uint32_t)ESP.getEfuseMac(), HEX);
    if (mqtt.connect(clientId.c_str())) {
      mqtt.subscribe(commandTopic.c_str());
      publishStatus("boot");
    } else delay(2000);
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(WATER_DIGITAL_PIN, INPUT);
  analogReadResolution(12);

  strip.begin();
  setStrip("green");
  Ld06Serial.begin(LD06_BAUD, SERIAL_8N1, LD06_RX_PIN, -1);
  WeatherSerial.begin(WEATHER_BAUD, SERIAL_8N1, WEATHER_RX_PIN, WEATHER_TX_PIN);

  telemetryTopic = String("road-warning/") + DEVICE_ID + "/telemetry";
  statusTopic = String("road-warning/") + DEVICE_ID + "/status";
  commandTopic = String("road-warning/") + DEVICE_ID + "/cmd";
  connectWifi();
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onMqttMessage);
  mqtt.setBufferSize(2304);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) connectWifi();
  if (!mqtt.connected()) connectMqtt();
  mqtt.loop();
  pollLd06();
  pollWater();
  pollWeather();
  refreshAlarmLight();
  if (millis() - lastUploadMs >= uploadIntervalMs) {
    lastUploadMs = millis();
    publishTelemetry();
  }
}
