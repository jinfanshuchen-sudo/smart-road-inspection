/*
  ESP32-S3 WS2812 灯带独立控制程序

  用途：单独测试或控制路灯 WS2812 灯带。
  接线：GPIO48 -> 灯带 DIN；灯带使用外接 5V；灯带 GND 与 ESP32 GND 共地。

  重要：本程序只控制灯带。它不包含当前项目的 LD06、水位、气象站和 MQTT
  逻辑，不能在未备份原 ESP32 固件的情况下直接替代正式现场固件。
*/

#include <Adafruit_NeoPixel.h>
#include "config.h"

Adafruit_NeoPixel strip(LED_COUNT, LED_PIN, LED_PIXEL_TYPE);

void showColor(uint8_t red, uint8_t green, uint8_t blue) {
  strip.fill(strip.Color(red, green, blue));
  strip.show();
}

void printHelp() {
  Serial.println("Commands: off | red | yellow | green | blue | white | help");
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  strip.begin();
  strip.setBrightness(LED_BRIGHTNESS);
  strip.clear();
  strip.show();

  Serial.println();
  Serial.println("ESP32 WS2812 light controller started.");
  Serial.printf("LED_COUNT=%d, LED_BRIGHTNESS=%d, LED_PIN=%d\n", LED_COUNT, LED_BRIGHTNESS, LED_PIN);
  printHelp();

  // 上电默认显示绿色，便于确认接线和灯珠数量。
  showColor(0, 255, 0);
}

void loop() {
  if (!Serial.available()) {
    return;
  }

  String command = Serial.readStringUntil('\n');
  command.trim();
  command.toLowerCase();

  if (command == "off") {
    strip.clear();
    strip.show();
  } else if (command == "red") {
    showColor(255, 0, 0);
  } else if (command == "yellow") {
    showColor(255, 160, 0);
  } else if (command == "green") {
    showColor(0, 255, 0);
  } else if (command == "blue") {
    showColor(0, 0, 255);
  } else if (command == "white") {
    showColor(255, 255, 255);
  } else if (command == "help") {
    printHelp();
  } else if (command.length() > 0) {
    Serial.println("Unknown command.");
    printHelp();
  }
}
