#pragma once

// ===== 以后最常修改的两项 =====
// 灯带实际灯珠数量。例如 30 颗就填 30，60 颗就填 60。
#define LED_COUNT 30

// 全局亮度范围 0 ~ 255。
// 80 约为 31%，100 约为 39%，128 约为 50%，255 为最大亮度。
#define LED_BRIGHTNESS 80

// ===== 硬件固定参数 =====
// 交付接线：ESP32-S3 GPIO48 -> WS2812 灯带 DIN。
#define LED_PIN 48

// WS2812 常用的颜色排列和时序。若颜色红绿颠倒，再改为 NEO_RGB。
#define LED_PIXEL_TYPE (NEO_GRB + NEO_KHZ800)

// 串口监视器波特率。
#define SERIAL_BAUD 115200
