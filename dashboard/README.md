# 智慧路灯控制面板

这是当前原型用的单文件 Vue 控制面板。它不需要烧录到 ESP32，也不需要安装 Node/Vite；直接用浏览器打开 `index.html` 即可。

## 当前连接

- 浏览器控制面板使用 MQTT over WebSocket：`ws://broker.emqx.io:8083/mqtt`
- ESP32-S3 固件使用普通 MQTT TCP：`broker.emqx.io:1883`
- 设备 ID：`road-warning-001`

浏览器页面订阅：

- `road-warning/road-warning-001/telemetry`
- `road-warning/road-warning-001/status`

浏览器页面发布命令到：

- `road-warning/road-warning-001/cmd`

## 当前按钮命令

- `ping`
- `getStatus`
- `setUploadInterval`
- `setProximityEnabled`
- `setWaterEnabled`
- `setWaterThresholds`
- `setWaterCalibration`

水位档位命令示例：

```json
{"cmd":"setWaterThresholds","caution":20,"warning":40,"danger":70}
```

水位 ADC 标定命令示例：

```json
{"cmd":"setWaterCalibration","dry":90,"wet":500}
```

控制面板会实时接收 ESP32 telemetry。编辑水位调校输入框时，页面会暂时停止用实时数据覆盖输入框；点击“设置水位档位”或“设置 ADC 标定”后再同步设备返回值。

## 气象站显示

当前控制面板已经预留并显示一体式 RS485 气象站数据，包括风速、风向、雨量、光照、温度、湿度、气压等字段。字段能否稳定变化取决于 ESP32 固件是否成功从气象站 Modbus 寄存器读到数据。

当前气象站接线边界：

- 棕色：12V 正极。
- 黑色：12V 负极，并与 ESP32 GND 共地。
- 黄色：TTL485 D+ / A。
- 蓝色：TTL485 D- / B。

气象站默认参数按当前厂家说明处理：地址码 1，波特率 4800。若厂家后续修改地址、波特率或寄存器表，需要同步更新 ESP32 固件配置。
