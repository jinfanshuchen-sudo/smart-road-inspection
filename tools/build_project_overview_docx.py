from __future__ import annotations

import html
import zipfile
from datetime import date
from pathlib import Path


OUT = Path("智慧道路巡检与路灯环境感知系统综合介绍.docx")

NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def esc(text: object) -> str:
    return html.escape(str(text), quote=False)


def r(text: str, *, bold: bool = False, color: str | None = None, size: int | None = None) -> str:
    props: list[str] = []
    if bold:
        props.append("<w:b/>")
    if color:
        props.append(f'<w:color w:val="{color}"/>')
    if size:
        props.append(f'<w:sz w:val="{size * 2}"/><w:szCs w:val="{size * 2}"/>')
    props.append('<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft YaHei"/>')
    rpr = f"<w:rPr>{''.join(props)}</w:rPr>"
    preserve = ' xml:space="preserve"' if text.startswith(" ") or text.endswith(" ") else ""
    return f"<w:r>{rpr}<w:t{preserve}>{esc(text)}</w:t></w:r>"


def p(
    text: str = "",
    *,
    style: str | None = None,
    runs: list[str] | None = None,
    align: str | None = None,
    before: int | None = None,
    after: int | None = None,
    num_id: int | None = None,
    level: int = 0,
) -> str:
    ppr: list[str] = []
    if style:
        ppr.append(f'<w:pStyle w:val="{style}"/>')
    if align:
        ppr.append(f'<w:jc w:val="{align}"/>')
    if before is not None or after is not None:
        attrs = []
        if before is not None:
            attrs.append(f'w:before="{before}"')
        if after is not None:
            attrs.append(f'w:after="{after}"')
        ppr.append(f"<w:spacing {' '.join(attrs)}/>")
    if num_id is not None:
        ppr.append(
            f"<w:numPr><w:ilvl w:val=\"{level}\"/><w:numId w:val=\"{num_id}\"/></w:numPr>"
        )
    ppr_xml = f"<w:pPr>{''.join(ppr)}</w:pPr>" if ppr else ""
    body = "".join(runs) if runs is not None else r(text)
    return f"<w:p>{ppr_xml}{body}</w:p>"


def heading(text: str, level: int) -> str:
    return p(text, style=f"Heading{level}")


def bullet(text: str) -> str:
    return p(text, num_id=2)


def numbered(text: str) -> str:
    return p(text, num_id=1)


def page_break() -> str:
    return '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'


def cell(content: str | list[str], *, fill: str | None = None, width: int | None = None) -> str:
    tcpr = []
    if width is not None:
        tcpr.append(f'<w:tcW w:w="{width}" w:type="dxa"/>')
    if fill is not None:
        tcpr.append(f'<w:shd w:fill="{fill}"/>')
    tcpr.append(
        '<w:tcMar><w:top w:w="100" w:type="dxa"/><w:left w:w="140" w:type="dxa"/>'
        '<w:bottom w:w="100" w:type="dxa"/><w:right w:w="140" w:type="dxa"/></w:tcMar>'
    )
    if isinstance(content, str):
        paras = p(content)
    else:
        paras = "".join(content)
    return f"<w:tc><w:tcPr>{''.join(tcpr)}</w:tcPr>{paras}</w:tc>"


def table(rows: list[list[str]], widths: list[int] | None = None, header: bool = True) -> str:
    widths = widths or [int(9360 / len(rows[0]))] * len(rows[0])
    tblpr = (
        '<w:tblPr><w:tblW w:w="9360" w:type="dxa"/>'
        '<w:tblInd w:w="120" w:type="dxa"/>'
        '<w:tblBorders><w:top w:val="single" w:sz="4" w:color="D0D7E2"/>'
        '<w:left w:val="single" w:sz="4" w:color="D0D7E2"/>'
        '<w:bottom w:val="single" w:sz="4" w:color="D0D7E2"/>'
        '<w:right w:val="single" w:sz="4" w:color="D0D7E2"/>'
        '<w:insideH w:val="single" w:sz="4" w:color="D0D7E2"/>'
        '<w:insideV w:val="single" w:sz="4" w:color="D0D7E2"/></w:tblBorders>'
        '<w:tblCellMar><w:top w:w="80" w:type="dxa"/><w:left w:w="120" w:type="dxa"/>'
        '<w:bottom w:w="80" w:type="dxa"/><w:right w:w="120" w:type="dxa"/></w:tblCellMar></w:tblPr>'
    )
    grid = "<w:tblGrid>" + "".join(f'<w:gridCol w:w="{w}"/>' for w in widths) + "</w:tblGrid>"
    trs: list[str] = []
    for idx, row in enumerate(rows):
        fill = "F2F4F7" if header and idx == 0 else None
        cells = []
        for col_idx, value in enumerate(row):
            if header and idx == 0:
                cells.append(cell([p("", runs=[r(value, bold=True, color="0B2545")])], fill=fill, width=widths[col_idx]))
            else:
                cells.append(cell(value, width=widths[col_idx]))
        trs.append(f"<w:tr>{''.join(cells)}</w:tr>")
    return f"<w:tbl>{tblpr}{grid}{''.join(trs)}</w:tbl>"


def callout(title: str, body: str) -> str:
    rows = [[title], [body]]
    return table(rows, widths=[9360], header=False).replace("<w:tcPr>", '<w:tcPr><w:shd w:fill="F4F6F9"/>', 1)


def styles_xml() -> str:
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="{NS_W}">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
    <w:name w:val="Normal"/>
    <w:pPr><w:spacing w:after="120" w:line="264" w:lineRule="auto"/></w:pPr>
    <w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft YaHei"/><w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Title">
    <w:name w:val="Title"/>
    <w:pPr><w:spacing w:before="0" w:after="160"/><w:jc w:val="center"/></w:pPr>
    <w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft YaHei"/><w:b/><w:color w:val="0B2545"/><w:sz w:val="48"/><w:szCs w:val="48"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Subtitle">
    <w:name w:val="Subtitle"/>
    <w:pPr><w:spacing w:after="240"/><w:jc w:val="center"/></w:pPr>
    <w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft YaHei"/><w:color w:val="555555"/><w:sz w:val="26"/><w:szCs w:val="26"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/>
    <w:pPr><w:keepNext/><w:spacing w:before="320" w:after="160"/></w:pPr>
    <w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft YaHei"/><w:b/><w:color w:val="2E74B5"/><w:sz w:val="32"/><w:szCs w:val="32"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading2">
    <w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/>
    <w:pPr><w:keepNext/><w:spacing w:before="240" w:after="120"/></w:pPr>
    <w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft YaHei"/><w:b/><w:color w:val="2E74B5"/><w:sz w:val="26"/><w:szCs w:val="26"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading3">
    <w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/>
    <w:pPr><w:keepNext/><w:spacing w:before="160" w:after="80"/></w:pPr>
    <w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft YaHei"/><w:b/><w:color w:val="1F4D78"/><w:sz w:val="24"/><w:szCs w:val="24"/></w:rPr>
  </w:style>
</w:styles>'''


def numbering_xml() -> str:
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="{NS_W}">
  <w:abstractNum w:abstractNumId="0">
    <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/>
      <w:pPr><w:tabs><w:tab w:val="num" w:pos="720"/></w:tabs><w:ind w:left="720" w:hanging="360"/></w:pPr>
    </w:lvl>
  </w:abstractNum>
  <w:abstractNum w:abstractNumId="1">
    <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/>
      <w:pPr><w:tabs><w:tab w:val="num" w:pos="720"/></w:tabs><w:ind w:left="720" w:hanging="360"/></w:pPr>
      <w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/></w:rPr>
    </w:lvl>
  </w:abstractNum>
  <w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
  <w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>
</w:numbering>'''


def document_xml() -> str:
    body: list[str] = []
    body.append(p("智慧道路巡检与路灯环境感知系统综合介绍", style="Title"))
    body.append(p("项目综合说明书（初稿）", style="Subtitle"))
    body.append(p("", runs=[r("文档版本：", bold=True), r("V0.1  "), r("生成日期：", bold=True), r(str(date.today()))], align="center"))
    body.append(p("", runs=[r("适用范围：", bold=True), r("用于项目答辩、阶段汇报、系统交付说明和后续功能补充。")], align="center"))
    body.append(callout("文档定位", "本文档整理当前系统已经实现和正在集成的能力，覆盖固定感知端、网页控制端、无人机巡检端、裂缝识别与后续树莓派部署方向。后续新增功能可以继续在本说明书基础上扩展。"))

    body.append(heading("一、项目概述", 1))
    body.append(p("本项目面向道路、园区、智慧路灯和临时巡检场景，构建一套“固定监测 + 移动巡检 + 网页可视化”的综合感知系统。系统通过 ESP32-S3、LD06 雷达、水位检测模块、RS485 气象站、报警灯和无人机等设备，完成环境数据采集、人员靠近判断、积水风险识别、无人机巡航拍照、D0/7号标志识别、返航降落和路面裂缝分析。"))
    body.append(p("当前系统的核心目标不是单一设备演示，而是把多个子系统连接成一个可以在网页上观察、控制、记录和展示的综合平台。固定端负责持续采集道路环境和风险状态，无人机负责移动巡检、图像采集和标志点导航，Vue 控制面板负责统一显示状态、下发命令、启动任务和展示识别结果。"))

    body.append(heading("二、系统组成", 1))
    body.append(table([
        ["组成模块", "主要硬件/软件", "作用"],
        ["网页控制面板", "Vue 单页控制面板、本地 vendor 依赖、Flask 静态服务", "统一显示气象站、灯塔、积水、无人机、视频、裂缝识别和功能开关状态，并提供按钮触发任务。"],
        ["固定感知端", "ESP32-S3、MQTT、RGB/报警灯", "负责初始化传感器、采集数据、融合风险、上传状态并响应网页命令。"],
        ["人员靠近检测", "LD06 激光雷达", "持续扫描周围区域，解析距离点，判断指定方向或区域内是否有人/目标靠近。"],
        ["积水与水位检测", "水位模块、ADC 采样、阈值标定", "周期性读取水位数值，按标定阈值换算积水比例和风险等级。"],
        ["气象环境采集", "RS485 气象站", "采集风速、风向、雨量、光照、温湿度、气压等道路环境数据。"],
        ["无人机巡检端", "Hula 无人机、pyhulax SDK、Flask 后端", "执行起飞、分段飞行、视频回传、拍照、D0 识别、7号返航码识别、返航和降落。"],
        ["裂缝识别模块", "OpenCV 图像处理、结果 CSV、标注图", "对无人机拍摄图像进行裂缝检测，输出像素宽度、折算物理宽度、拍摄位置和结果图。"],
    ], widths=[1900, 2700, 4760]))

    body.append(heading("三、当前已实现功能", 1))
    body.append(heading("3.1 固定监测与环境感知", 2))
    for item in [
        "ESP32-S3 可作为固定端主控，初始化 Wi-Fi、MQTT、LD06 雷达、水位模块、RS485 气象站和报警灯。",
        "LD06 持续扫描周围环境，解析点云/距离点，判断指定区域内的最近目标，用于人车靠近或行人接近风险提示。",
        "水位模块周期性采样 ADC 数值，并根据调校阈值换算为积水比例、注意/较深/危险等状态。",
        "RS485 气象站可返回风速、风向、雨量、光照、温湿度、气压等数据，为道路环境判断提供背景条件。",
        "系统可综合人员靠近、积水风险、气象风险和灯塔状态，形成面向现场的风险等级与报警状态。"
    ]:
        body.append(bullet(item))

    body.append(heading("3.2 网页控制与数据展示", 2))
    for item in [
        "Vue 控制面板统一展示共享气象站、多灯塔、人车接近、积水检查、积水调校、功能开关和最近 MQTT 应答。",
        "网页可订阅 MQTT 数据，实时显示各模块上传的状态、传感器数据和风险结果。",
        "网页支持下发控制命令，例如刷新状态、修改上传间隔、设置积水档位、启用或关闭某个检测模块。",
        "无人机任务区可以设置后端接口、D0 目标参数，并触发正式任务、实时高度测试、D0 距离测试、下视 D0 对准降落、巡航裂缝识别、总任务和紧急降落。"
    ]:
        body.append(bullet(item))

    body.append(heading("3.3 无人机巡检与视觉任务", 2))
    body.append(table([
        ["功能", "当前实现方式", "输出结果"],
        ["基础连接与起降", "通过 pyhulax SDK 连接无人机 Wi-Fi，执行起飞、悬停、前进、转向和降落。", "电量、连接状态、任务阶段、成功/失败信息。"],
        ["实时视频流", "开启无人机前置摄像头视频流，在网页中显示“无人机实时视频流”。", "网页实时画面、视频状态、ToF 和姿态信息。"],
        ["拍照保存", "从当前视频帧保存照片到 media/photos，最多保留 50 张，网页显示最近照片路径。", "原始巡检照片。"],
        ["D0 识别降落", "前置镜头向下，通过 D0 板子识别位置；高处识别并校准后执行盲降。", "D0 是否识别、当前位置偏移、目标误差、降落结果。"],
        ["7号返航码", "使用 7 号二维码/定位码作为远端返航点，识别后先靠近校准，再转向返航。", "7号识别状态、7号位置、返航阶段。"],
        ["总任务", "起飞、巡航拍照与裂缝识别、寻找 7 号返航码、校准返航、寻找 D0、对准并降落。", "完整任务状态、裂缝结果、D0/7号状态和最终降落结果。"],
    ], widths=[1800, 4400, 3160]))

    body.append(heading("3.4 路面裂缝识别", 2))
    body.append(p("裂缝识别模块目前采用图像处理方式完成第一层检测：无人机在巡航过程中自动拍照，原始照片保存在照片目录；系统随后对照片进行灰度阈值、形态学处理和小噪声过滤，识别可能的暗色裂缝区域，并输出标注图和 CSV 记录。"))
    for item in [
        "原始照片保存在 media/photos，便于后续复核。",
        "识别后的标注图保存在 media/crack_results，网页可展示最新结果。",
        "result.csv 记录每次识别结果，包括是否检出、裂缝像素宽度、折算物理宽度、拍摄段数、无人机相对起点坐标、ToF 高度和姿态角。",
        "当前属于轻量级图像处理检测，适合演示和原型验证；正式现场应用还需要根据真实路面材质、光照、高度和镜头角度进一步标定。"
    ]:
        body.append(bullet(item))

    body.append(page_break())
    body.append(heading("四、完整工作流程", 1))
    body.append(heading("4.1 固定监测端工作流程", 2))
    fixed_steps = [
        "系统上电后，ESP32-S3 初始化 Wi-Fi、MQTT、LD06 雷达、水位模块、RS485 气象站和报警灯。",
        "LD06 持续扫描周围环境，解析距离点并筛选指定方向或区域内的最近目标。",
        "水位模块周期性采集 ADC 数值，并根据标定参数换算为积水比例或水位等级。",
        "气象站通过 RS485 定时返回风速、风向、雨量、光照、温湿度、气压等环境数据。",
        "ESP32-S3 将人员靠近、积水、气象等信息进行综合判断，形成当前风险等级。",
        "RGB 灯或外接报警灯显示当前风险状态，必要时进行现场提示。",
        "ESP32-S3 通过 MQTT 定时上传全部状态数据。",
        "网页控制面板订阅 MQTT 数据，实时显示气象站、灯塔、积水、人车靠近和功能状态。",
        "用户可在网页下发命令，例如刷新状态、调整上传间隔、设置积水阈值或启停某个模块。",
        "ESP32-S3 接收到命令后执行对应操作，并将执行结果作为最近应答返回网页。"
    ]
    for step in fixed_steps:
        body.append(numbered(step))

    body.append(heading("4.2 无人机巡检端工作流程", 2))
    drone_steps = [
        "操作人员连接无人机 Wi-Fi，启动本地 Flask 后端，并打开 Vue 控制面板。",
        "网页通过后端接口确认无人机连接、电量、ToF 高度、姿态角和任务状态。",
        "启动总任务后，无人机从起点起飞，并把起飞前位置作为三维坐标参考零点。",
        "无人机按分段方式前进巡航；每段短暂停留时自动保存视频帧照片。",
        "系统对每张巡航照片执行裂缝识别，原图和识别结果分别保存，并在网页报告区展示最新一次结果。",
        "巡航过程中无人机寻找 7 号返航码；识别后先靠近并校准位置，降低直接转向造成的偏差。",
        "无人机转向返航，分段返回起点方向，并寻找 D0 标志板。",
        "识别到 D0 后，系统根据目标偏移进行对准，确认后执行盲降。",
        "任务完成后，网页显示任务状态、D0 识别、7号识别、ToF、坐标、姿态、裂缝报告和最终降落结果。",
        "如出现异常，用户可点击紧急降落，后端向无人机发送降落指令。"
    ]
    for step in drone_steps:
        body.append(numbered(step))

    body.append(heading("4.3 网页端典型操作流程", 2))
    for step in [
        "打开控制面板，确认 MQTT 数据、灯塔状态、气象站和积水检查显示正常。",
        "如需查看无人机画面，先开启相机，确认视频流正常；拍照测试通过后停止相机。",
        "确认任务状态为 idle 或 completed，电量充足，现场 D0 和 7号码摆放正确。",
        "点击总任务按钮，观察任务阶段、实时高度、坐标、姿态角、D0/7号识别和裂缝识别报告。",
        "任务完成后检查照片目录、裂缝结果图和 result.csv；必要时根据测试结果调整飞行步长、识别次数或 D0 目标补偿。"
    ]:
        body.append(numbered(step))

    body.append(heading("五、应用场景", 1))
    body.append(table([
        ["场景", "系统价值", "典型部署方式"],
        ["智慧路灯与园区道路", "固定灯塔持续感知人车靠近、积水和气象状态，无人机补充移动巡检能力。", "每个灯塔布置 ESP32-S3、LD06、水位、报警灯；无人机在指定点执行巡航。"],
        ["道路积水监测点", "雨天或低洼区域可及时发现积水程度，结合气象数据判断风险。", "水位模块安装在低洼点，气象站提供降雨和风环境信息。"],
        ["地下车库出入口", "检测积水、人车接近和环境变化，适合低空巡检和报警提示。", "入口两侧布置固定监测点，网页集中显示。"],
        ["校园、园区和施工区域", "对临时道路、施工边界或人流通道进行风险提示和巡检记录。", "固定端监测 + 无人机定点巡航 + 网页统一展示。"],
        ["路面裂缝巡查演示", "无人机沿道路拍照并自动分析裂缝，形成带位置和高度信息的初步报告。", "D0/7号标志作为起点和返航参考，照片与结果图留档。"],
    ], widths=[2100, 4200, 3060]))

    body.append(heading("六、系统特点与项目价值", 1))
    for item in [
        "多源感知：同时接入气象、水位、LD06、人车靠近、无人机视觉和裂缝图像分析。",
        "固定与移动结合：固定灯塔负责连续监测，无人机负责巡航、拍照、返航和定点降落。",
        "网页统一控制：操作人员在同一个 Vue 面板中查看数据、控制模块、启动任务和查看结果。",
        "数据可追溯：无人机照片、裂缝结果图和 CSV 记录可用于后续复盘、答辩展示和算法改进。",
        "可迁移部署：当前在电脑端完成测试，后续可迁移到树莓派作为现场边缘主机，用户通过网页访问即可操作。",
        "模块化扩展：气象站、LD06、水位、无人机、裂缝识别和报警灯可独立调试，也可以组合成完整任务流程。"
    ]:
        body.append(bullet(item))

    body.append(heading("七、当前完成情况与后续完善方向", 1))
    body.append(heading("7.1 当前完成情况", 2))
    body.append(table([
        ["模块", "完成情况"],
        ["Vue 控制面板", "已完成主要布局和功能入口，包含固定监测、无人机任务、实时视频、裂缝报告、常用命令和功能开关。"],
        ["气象站 / LD06 / 水位", "已完成项目分支测试和网页侧展示逻辑，具备数据采集、状态上传和阈值调校能力。"],
        ["无人机基础任务", "已完成连接、起飞、分段前进、D0识别、降落、视频、拍照、紧急降落等功能。"],
        ["D0 对准降落", "已完成前置镜头向下识别 D0、目标补偿和盲降测试，精度已通过多轮调校提升。"],
        ["7号返航码", "已完成识别、靠近校准、转向返航和回到 D0 降落的总任务流程。"],
        ["裂缝识别", "已完成自动拍照后分析、结果图生成、CSV 记录和网页报告展示。"],
    ], widths=[2300, 7060]))

    body.append(heading("7.2 后续完善方向", 2))
    for item in [
        "将后端、Vue 控制面板和依赖迁移到树莓派，形成现场可独立运行的小型主机。",
        "根据真实道路高度、灯塔位置和 D0/7号码摆放方式，重新标定飞行高度、步长、识别距离和降落补偿。",
        "对裂缝识别算法进行现场数据采集和阈值优化，降低纹理地面、阴影和光照变化带来的误判。",
        "完善设备外壳、防水固定、电源保护、报警灯布线和多灯塔联动方式。",
        "整理面向甲方的简化操作手册，把复杂调试参数隐藏，只保留启动、巡检、查看报告和紧急降落等必要操作。"
    ]:
        body.append(bullet(item))

    body.append(heading("八、总结", 1))
    body.append(p("综上，当前系统已经从单一传感器演示扩展为一个包含固定端感知、网页端控制、无人机巡检、视觉标志导航和路面裂缝识别的综合平台。系统既能展示气象、积水、人车靠近等连续监测能力，也能通过无人机完成巡航拍照、识别返航和定点降落。后续重点应放在树莓派现场部署、真实场景标定、裂缝识别稳定性和交付操作流程简化上。"))

    sect = (
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" '
        'w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>'
    )
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="{NS_W}" xmlns:r="{NS_R}">
  <w:body>{''.join(body)}{sect}</w:body>
</w:document>'''


def content_types_xml() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>'''


def rels_xml() -> str:
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>'''


def document_rels_xml() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
</Relationships>'''


def core_xml() -> str:
    today = date.today().isoformat()
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:dcterms="http://purl.org/dc/terms/"
 xmlns:dcmitype="http://purl.org/dc/dcmitype/"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>智慧道路巡检与路灯环境感知系统综合介绍</dc:title>
  <dc:creator>Codex</dc:creator>
  <cp:lastModifiedBy>Codex</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{today}T00:00:00Z</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{today}T00:00:00Z</dcterms:modified>
</cp:coreProperties>'''


def app_xml() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
 xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Codex</Application>
</Properties>'''


def build() -> None:
    files = {
        "[Content_Types].xml": content_types_xml(),
        "_rels/.rels": rels_xml(),
        "word/_rels/document.xml.rels": document_rels_xml(),
        "word/document.xml": document_xml(),
        "word/styles.xml": styles_xml(),
        "word/numbering.xml": numbering_xml(),
        "docProps/core.xml": core_xml(),
        "docProps/app.xml": app_xml(),
    }
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as docx:
        for name, data in files.items():
            docx.writestr(name, data.encode("utf-8"))
    print(OUT.resolve())


if __name__ == "__main__":
    build()
