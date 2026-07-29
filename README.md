# 智慧道路巡检与路灯环境感知系统

面向道路巡检演示的本地化系统，整合无人机自动任务、裂缝识别、气象站、LD06 人车接近、积水检测、ESP32 灯带状态和 Vue 实时看板。

## 功能

- Hula 无人机总任务：起飞、巡航、裂缝告警、识别 7 号返航码、识别 2 号降落码并降落。
- 浏览器控制页面：任务状态、视频状态、紧急降落与网络预检。
- 本地 MQTT Broker：无互联网环境下接收 ESP32 的气象、LD06 与水位数据。
- Vue 看板：显示气象站、人车接近、积水状态和综合告警。
- 裂缝识别：使用 OpenCV 对无人机拍摄画面进行轻量级裂缝检测。

## 架构

```text
ESP32 + LD06 + 气象站 + 水位模块 -- MQTT --> 本机离线 Broker
                                                |
Vue Dashboard <--- Flask 后端 <--- Hula 无人机（网组局域网）
```

系统可在无外网的路由器局域网内运行。执行无人机任务前，电脑、无人机和 ESP32 必须在同一局域网，且电脑不能通过 VPN 转发指令。

## 本地运行

运行环境：Windows 10/11 x64、Python 3.11。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

启动离线 MQTT Broker 与控制后端：

```powershell
powershell -ExecutionPolicy Bypass -File .\start_offline_hula_demo.ps1
```

浏览器打开 `http://127.0.0.1:5055`。

## 设备配置

- 无人机地址默认从 `PYHULAX_DRONE_IP` 读取；未设置时为 `192.168.31.160`。
- 总任务启动前会检查命令是否从无人机所在局域网发出。
- ESP32 的 Wi-Fi 与 MQTT 主机地址属于设备侧配置，不提交到仓库。换电脑或换路由器时需要按现场网络重新烧录或配置。

## 安全说明

该项目包含真实无人机控制能力。仅应在安全、空旷并完成低风险验证的场地执行任务；飞行时保留遥控器/App 作为紧急处置手段。

## 仓库范围

仓库包含电脑端源代码、Vue 页面、Python SDK 与运行脚本。虚拟环境、运行日志、生成图片、历史飞行备份、交付压缩包和本地密钥文件已通过 `.gitignore` 排除。
