#pragma once

// ===== 设备标识与硬件引脚 =====
#define DEVICE_ID "road-warning-001"
#define LED_PIN 48
#define LED_COUNT 60
#define LED_BRIGHTNESS 80  // 0 ~ 255；改灯珠数量和默认亮度只改上面两项。

#define LD06_RX_PIN 18
#define LD06_BAUD 230400

#define WATER_ADC_PIN 4
#define WATER_DIGITAL_PIN 5

// TTL-RS485 自动收发模块：ESP GPIO16 <- 模块 TXD，ESP GPIO15 -> 模块 RXD。
#define WEATHER_RX_PIN 16
#define WEATHER_TX_PIN 15
#define WEATHER_BAUD 9600
#define WEATHER_SLAVE_ID 1

// ===== 功能开关 =====
#define ENABLE_LD06 true
#define ENABLE_WATER true
#define ENABLE_WEATHER true

// ===== 本地 MQTT 协议 =====
#define MQTT_PORT 1883
#define MQTT_UPLOAD_INTERVAL_MS 1000UL

// ===== LD06 人车接近距离阈值（毫米） =====
#define LD06_CAUTION_MM 2500
#define LD06_WARNING_MM 1500
#define LD06_DANGER_MM 800

// ===== 水位 ADC 标定与风险阈值 =====
// 首次现场标定后可通过网页下发；这里是开机默认值。
// 湿态 ADC 可能大于或小于干态 ADC，程序均可处理。
#define WATER_DRY_ADC 3000
#define WATER_WET_ADC 1200
#define WATER_CAUTION_PERCENT 30
#define WATER_WARNING_PERCENT 60
#define WATER_DANGER_PERCENT 85

// ===== RS485 七要素气象站（Modbus RTU） =====
// 常见七要素站从输入寄存器 0 开始连续返回：风速、风向、雨量、照度、温度、湿度、气压。
// 不同厂家寄存器地址、倍率可能不同；与现场说明书不一致时只修改本区参数。
#define WEATHER_FUNCTION_CODE 0x03
#define WEATHER_START_REGISTER 0x0000
#define WEATHER_REGISTER_COUNT 7
#define WEATHER_WIND_SPEED_SCALE 0.1f
#define WEATHER_WIND_DIRECTION_SCALE 1.0f
#define WEATHER_RAINFALL_SCALE 0.1f
#define WEATHER_LIGHT_SCALE 1.0f
#define WEATHER_TEMPERATURE_SCALE 0.1f
#define WEATHER_HUMIDITY_SCALE 0.1f
#define WEATHER_PRESSURE_SCALE 0.1f

#define WEATHER_POLL_INTERVAL_MS 2000UL
#define LD06_OFFLINE_TIMEOUT_MS 3000UL
