# ESP32-S3 道路巡检综合固件

此目录是根据当前树莓派本地 MQTT 服务和 Vue 监控页面协议补齐的 ESP32-S3 源码，覆盖：

- 连接现场 WiFi；
- 向树莓派本地 MQTT Broker 上报数据；
- LD06 人车接近检测；
- 水位 ADC 采集、标定与风险分级；
- WS2812 灯带风险颜色显示；
- RS485 七要素气象站（Modbus RTU）读取；
- 接收网页下发的开关、上报间隔与水位标定命令。

## 重要说明

原始 ESP32 固件不在最初交付包和压缩包内，因此这是一套**可维护的重建源码**，并不是从 ESP32 中反编译得到的旧程序。烧录前请先备份当前 ESP32 固件；首次应在备用 ESP32 或断开无人机任务的安全环境中验证。

气象站使用 Modbus RTU，但不同厂家寄存器地址/倍率可能不同。`config.h` 已将这些参数集中；若气象站数据异常，应以该气象站说明书为准调整，不要盲目修改主程序。

## 1. 准备本地网络配置

复制 `secrets.h.example` 并改名为 `secrets.h`，填写 WiFi 密码。`secrets.h` 已在 Git 忽略规则中，不会上传公开仓库。

```cpp
#define WIFI_SSID "Hula-Battle"
#define WIFI_PASSWORD "填写现场密码"
#define MQTT_HOST "192.168.31.66"
```

MQTT 端口默认是 `1883`，无需改动。

## 2. 灯带数量与亮度

打开 `config.h`，修改：

```cpp
#define LED_COUNT 30
#define LED_BRIGHTNESS 80
```

- `LED_COUNT` 是灯带实际 WS2812 灯珠数；修改后必须重新烧录。
- `LED_BRIGHTNESS` 范围是 `0 ~ 255`；建议从 `80 ~ 120` 开始。
- 灯带接线固定为 `GPIO48 -> DIN`，外接 5V，且灯带 GND 与 ESP32 GND 共地。

## 3. 接线

| 功能 | ESP32-S3 引脚 |
| --- | --- |
| WS2812 DIN | GPIO48 |
| LD06 TX 输入 | GPIO18 |
| 水位 AO | GPIO4 |
| 水位 DO | GPIO5 |
| RS485 模块 TXD -> ESP RX | GPIO16 |
| ESP TX -> RS485 模块 RXD | GPIO15 |

LD06 和 WS2812 必须使用外接稳定 5V；气象站使用其规定的 12V；所有低压模块按现场接线要求共地。12V 不可接入 ESP32、LD06 或灯带。

## 4. Arduino IDE 烧录

1. 在 Arduino IDE 的开发板管理器安装 **esp32 by Espressif Systems**。
2. 在库管理器安装 **PubSubClient**、**ArduinoJson**、**Adafruit NeoPixel**。
3. 使用可传数据的 USB 线连接 ESP32-S3；选择对应 ESP32-S3 板型和 COM 口。
4. 打开 `esp32_road_warning_firmware.ino`，点击上传。
5. 如遇上传连接失败，按住板载 `BOOT` 键后开始上传，出现写入提示后松开。

## MQTT 协议

设备 ID 默认 `road-warning-001`，主题为：

```text
road-warning/road-warning-001/telemetry
road-warning/road-warning-001/status
road-warning/road-warning-001/cmd
```

与当前 `dashboard/index.html` 的字段和命令保持对应。网页可下发 `ping`、`getStatus`、模块开关、上报间隔、水位阈值与 ADC 标定。

## 上线前最小测试顺序

1. 不接外设时先烧录，确认 ESP32 已连 WiFi、能向树莓派 MQTT 上报。
2. 接灯带，确认绿色、黄色、红色风险颜色变化。
3. 接 LD06，确认页面显示在线、最近距离和风险等级。
4. 接水位模块，完成干态/湿态 ADC 标定。
5. 最后接气象站，核对每个读数的单位与现场仪表是否一致。
