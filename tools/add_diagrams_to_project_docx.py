from __future__ import annotations

import glob
import math
import re
import shutil
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from PIL import Image, ImageDraw, ImageFont


DOCX_PATH = Path("智慧道路巡检与路灯环境感知系统综合介绍.docx")
BACKUP_PATH = Path("智慧道路巡检与路灯环境感知系统综合介绍.before_diagrams.docx")
DIAGRAM_DIR = Path("docx_diagrams")
OUTPUT_PATH = Path("project_overview_with_diagrams.docx")

EMU_PER_INCH = 914400
DOC_IMAGE_WIDTH_EMU = int(6.5 * EMU_PER_INCH)


def locate_docx() -> Path:
    if DOCX_PATH.exists():
        return DOCX_PATH
    matches = [Path(p) for p in glob.glob("*.docx")]
    if not matches:
        raise FileNotFoundError("No .docx file found in the project root.")
    return max(matches, key=lambda p: p.stat().st_mtime)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


FONT_TITLE = font(42, True)
FONT_H2 = font(28, True)
FONT_BODY = font(24)
FONT_SMALL = font(20)


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.ImageFont, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for ch in text:
        test = current + ch
        width = draw.textbbox((0, 0), test, font=fnt)[2]
        if width <= max_width or not current:
            current = test
        else:
            lines.append(current)
            current = ch
    if current:
        lines.append(current)
    return lines


def box(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    title: str,
    body: str = "",
    *,
    fill: str = "#FFFFFF",
    outline: str = "#89A7C8",
) -> None:
    draw.rounded_rectangle(xy, radius=18, fill=fill, outline=outline, width=3)
    x1, y1, x2, y2 = xy
    draw.text((x1 + 18, y1 + 14), title, fill="#0B2545", font=FONT_H2)
    if body:
        y = y1 + 58
        for line in wrap(draw, body, FONT_SMALL, x2 - x1 - 36)[:4]:
            draw.text((x1 + 18, y), line, fill="#334155", font=FONT_SMALL)
            y += 28


def arrow(draw: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int], color: str = "#2E74B5") -> None:
    draw.line([start, end], fill=color, width=5)
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    length = 18
    a1 = angle + math.pi * 0.82
    a2 = angle - math.pi * 0.82
    p1 = (end[0] + length * math.cos(a1), end[1] + length * math.sin(a1))
    p2 = (end[0] + length * math.cos(a2), end[1] + length * math.sin(a2))
    draw.polygon([end, p1, p2], fill=color)


def title(draw: ImageDraw.ImageDraw, text: str, subtitle: str = "") -> None:
    draw.text((60, 36), text, fill="#0B2545", font=FONT_TITLE)
    if subtitle:
        draw.text((62, 92), subtitle, fill="#64748B", font=FONT_BODY)


def save_canvas(path: Path, size: tuple[int, int]) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", size, "#F8FAFC")
    draw = ImageDraw.Draw(image)
    return image, draw


def make_topology(path: Path) -> None:
    image, draw = save_canvas(path, (1800, 1050))
    title(draw, "系统总体拓扑图", "固定监测端、无人机巡检端与网页控制端的连接关系")

    box(draw, (675, 160, 1125, 300), "Vue Web 控制面板", "统一显示状态、下发命令、启动任务、展示视频和裂缝报告", fill="#E8F1FB")
    box(draw, (675, 390, 1125, 520), "通信与后端服务", "MQTT 数据订阅 / Flask 无人机任务接口 / 本地网页服务", fill="#EEF6ED", outline="#8DBB8D")
    arrow(draw, (900, 300), (900, 390))
    arrow(draw, (900, 390), (900, 300), "#8DBB8D")

    left_boxes = [
        ((70, 650, 350, 780), "ESP32-S3 主控", "初始化设备、融合风险、上传状态"),
        ((395, 650, 675, 780), "LD06 雷达", "人车靠近 / 最近目标"),
        ((720, 650, 1000, 780), "水位模块", "积水比例 / 风险等级"),
        ((1045, 650, 1325, 780), "RS485 气象站", "风速、雨量、光照、温湿度"),
        ((1370, 650, 1650, 780), "报警灯 / RGB", "现场风险提示"),
    ]
    for xy, h, b in left_boxes:
        box(draw, xy, h, b, fill="#FFFFFF")
        arrow(draw, (900, 520), ((xy[0] + xy[2]) // 2, xy[1]))

    box(draw, (250, 850, 650, 990), "Hula 无人机", "起飞、巡航、视频、拍照、D0/7号识别、返航降落", fill="#FFF7ED", outline="#E6A15B")
    box(draw, (760, 850, 1160, 990), "裂缝识别模块", "照片分析、结果图、result.csv、拍摄位置记录", fill="#FEF2F2", outline="#D98C8C")
    box(draw, (1270, 850, 1670, 990), "数据留存", "照片、裂缝结果、CSV、任务状态", fill="#F5F3FF", outline="#9B8AD8")
    arrow(draw, (900, 520), (450, 850), "#E6A15B")
    arrow(draw, (650, 920), (760, 920), "#9B1C1C")
    arrow(draw, (1160, 920), (1270, 920), "#7C3AED")
    image.save(path, quality=95)


def make_fixed_flow(path: Path) -> None:
    image, draw = save_canvas(path, (1800, 780))
    title(draw, "固定监测端工作流程图", "环境感知、风险判断、报警显示与网页可视化")
    steps = [
        ("系统上电", "ESP32-S3 初始化 Wi-Fi / MQTT"),
        ("传感器采集", "LD06、水位、RS485 气象站"),
        ("数据解析", "距离点、ADC、水文气象数据"),
        ("风险融合", "人车靠近 + 积水 + 气象"),
        ("现场提示", "RGB / 报警灯显示风险"),
        ("MQTT 上传", "周期上报设备状态"),
        ("Vue 展示", "网页查看并下发命令"),
    ]
    x = 45
    y = 245
    w = 220
    h = 150
    gap = 35
    for idx, (h1, b) in enumerate(steps):
        box(draw, (x, y, x + w, y + h), h1, b, fill="#FFFFFF")
        if idx < len(steps) - 1:
            arrow(draw, (x + w, y + h // 2), (x + w + gap, y + h // 2))
        x += w + gap
    box(draw, (475, 520, 1325, 690), "网页命令回路", "用户可在面板中刷新状态、调整上传间隔、设置积水阈值、启停模块；ESP32-S3 执行后返回最近应答。", fill="#E8F1FB")
    arrow(draw, (1510, 395), (1325, 580), "#8DBB8D")
    arrow(draw, (475, 580), (155, 395), "#8DBB8D")
    image.save(path, quality=95)


def make_drone_flow(path: Path) -> None:
    image, draw = save_canvas(path, (1800, 930))
    title(draw, "无人机总任务工作流程图", "巡航裂缝识别、7号返航码、D0 对准降落的完整任务链路")
    steps = [
        ("连接无人机", "确认 Wi-Fi、电量、后端状态"),
        ("起飞建零点", "起飞前位置作为坐标参考"),
        ("分段巡航", "按步长前进并短暂停留"),
        ("拍照识别裂缝", "保存原图并生成结果图/CSV"),
        ("寻找 7 号码", "识别返航点并靠近校准"),
        ("转向返航", "转 180° 后分段返回"),
        ("寻找 D0", "前置镜头向下识别 D0"),
        ("对准盲降", "校准偏移后下降落点"),
    ]
    positions = [
        (55, 210, 255, 360),
        (290, 210, 490, 360),
        (525, 210, 725, 360),
        (760, 210, 1020, 360),
        (1055, 210, 1275, 360),
        (1310, 210, 1510, 360),
        (1190, 570, 1390, 720),
        (890, 570, 1090, 720),
    ]
    for idx, ((h1, b), xy) in enumerate(zip(steps, positions)):
        fill = "#FFF7ED" if idx in (0, 1, 2, 5) else "#EEF6ED" if idx in (4, 6, 7) else "#FEF2F2"
        outline = "#E6A15B" if idx in (0, 1, 2, 5) else "#8DBB8D" if idx in (4, 6, 7) else "#D98C8C"
        box(draw, xy, h1, b, fill=fill, outline=outline)
    for i in range(5):
        arrow(draw, (positions[i][2], 285), (positions[i + 1][0], 285))
    arrow(draw, (1410, 360), (1290, 570))
    arrow(draw, (1190, 645), (1090, 645))
    box(draw, (210, 565, 710, 735), "状态回传", "Vue 面板实时更新任务阶段、电量、ToF、坐标、姿态角、D0/7号识别和裂缝报告。", fill="#E8F1FB")
    arrow(draw, (900, 360), (570, 565), "#2E74B5")
    arrow(draw, (990, 570), (710, 650), "#2E74B5")
    image.save(path, quality=95)


def image_paragraph(rid: str, name: str, width_px: int, height_px: int, docpr_id: int) -> str:
    cx = DOC_IMAGE_WIDTH_EMU
    cy = int(cx * height_px / width_px)
    return f'''
<w:p>
  <w:pPr><w:jc w:val="center"/><w:spacing w:before="120" w:after="160"/></w:pPr>
  <w:r>
    <w:drawing>
      <wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" distT="0" distB="0" distL="0" distR="0">
        <wp:extent cx="{cx}" cy="{cy}"/>
        <wp:effectExtent l="0" t="0" r="0" b="0"/>
        <wp:docPr id="{docpr_id}" name="{name}"/>
        <wp:cNvGraphicFramePr>
          <a:graphicFrameLocks xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" noChangeAspect="1"/>
        </wp:cNvGraphicFramePr>
        <a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
          <a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">
            <pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">
              <pic:nvPicPr><pic:cNvPr id="{docpr_id}" name="{name}"/><pic:cNvPicPr/></pic:nvPicPr>
              <pic:blipFill><a:blip xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>
              <pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>
            </pic:pic>
          </a:graphicData>
        </a:graphic>
      </wp:inline>
    </w:drawing>
  </w:r>
</w:p>'''


def add_caption(text: str) -> str:
    return p_xml(text, color="555555", size=10, align="center")


def p_xml(text: str, *, color: str = "000000", size: int = 11, align: str | None = None) -> str:
    align_xml = f'<w:jc w:val="{align}"/>' if align else ""
    return (
        f"<w:p><w:pPr>{align_xml}<w:spacing w:after=\"120\"/></w:pPr>"
        f"<w:r><w:rPr><w:rFonts w:ascii=\"Calibri\" w:hAnsi=\"Calibri\" w:eastAsia=\"Microsoft YaHei\"/>"
        f"<w:color w:val=\"{color}\"/><w:sz w:val=\"{size * 2}\"/><w:szCs w:val=\"{size * 2}\"/></w:rPr>"
        f"<w:t>{text}</w:t></w:r></w:p>"
    )


def insert_before_heading(xml: str, heading_text: str, insertion: str) -> str:
    pos = xml.find(heading_text)
    if pos < 0:
        raise ValueError(f"Heading text not found: {heading_text}")
    start = xml.rfind("<w:p", 0, pos)
    if start < 0:
        raise ValueError(f"Paragraph start not found for: {heading_text}")
    return xml[:start] + insertion + xml[start:]


def insert_after_heading(xml: str, heading_text: str, insertion: str) -> str:
    pos = xml.find(heading_text)
    if pos < 0:
        raise ValueError(f"Heading text not found: {heading_text}")
    end = xml.find("</w:p>", pos)
    if end < 0:
        raise ValueError(f"Paragraph end not found for: {heading_text}")
    end += len("</w:p>")
    return xml[:end] + insertion + xml[end:]


def append_relationships(rels_xml: str, rels: list[tuple[str, str]]) -> str:
    additions = "".join(
        f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/{target}"/>'
        for rid, target in rels
    )
    return rels_xml.replace("</Relationships>", additions + "</Relationships>")


def ensure_png_content_type(content_types: str) -> str:
    if 'Extension="png"' in content_types:
        return content_types
    return content_types.replace(
        '<Default Extension="xml" ContentType="application/xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/>',
    )


def validate_xml(xml_text: str) -> None:
    ET.fromstring(xml_text.encode("utf-8"))


def main() -> None:
    docx_path = locate_docx()
    DIAGRAM_DIR.mkdir(parents=True, exist_ok=True)
    diagrams = [
        ("rIdDiagram1", "system_topology.png", DIAGRAM_DIR / "system_topology.png", make_topology, "图 1  系统总体拓扑图"),
        ("rIdDiagram2", "fixed_monitoring_flow.png", DIAGRAM_DIR / "fixed_monitoring_flow.png", make_fixed_flow, "图 2  固定监测端工作流程图"),
        ("rIdDiagram3", "drone_mission_flow.png", DIAGRAM_DIR / "drone_mission_flow.png", make_drone_flow, "图 3  无人机总任务工作流程图"),
    ]
    for _, _, path, maker, _ in diagrams:
        maker(path)

    if not BACKUP_PATH.exists():
        shutil.copy2(docx_path, BACKUP_PATH)

    with zipfile.ZipFile(docx_path, "r") as zin:
        entries = {name: zin.read(name) for name in zin.namelist()}

    document = entries["word/document.xml"].decode("utf-8")
    rels = entries["word/_rels/document.xml.rels"].decode("utf-8")
    content_types = entries["[Content_Types].xml"].decode("utf-8")

    # Remove old inserted diagram blocks if this script is re-run.
    document = re.sub(
        r"<w:p><w:pPr><w:jc w:val=\"center\"/><w:spacing w:after=\"120\"/></w:pPr><w:r>.*?图 [123].*?</w:r></w:p>",
        "",
        document,
        flags=re.S,
    )

    top_img = Image.open(diagrams[0][2])
    fixed_img = Image.open(diagrams[1][2])
    drone_img = Image.open(diagrams[2][2])

    topology_block = (
        image_paragraph("rIdDiagram1", "system_topology.png", *top_img.size, 101)
        + add_caption("图 1  系统总体拓扑图")
    )
    fixed_block = (
        image_paragraph("rIdDiagram2", "fixed_monitoring_flow.png", *fixed_img.size, 102)
        + add_caption("图 2  固定监测端工作流程图")
    )
    drone_block = (
        image_paragraph("rIdDiagram3", "drone_mission_flow.png", *drone_img.size, 103)
        + add_caption("图 3  无人机总任务工作流程图")
    )

    document = insert_before_heading(document, "三、当前已实现功能", topology_block)
    document = insert_after_heading(document, "4.1 固定监测端工作流程", fixed_block)
    document = insert_after_heading(document, "4.2 无人机巡检端工作流程", drone_block)

    # Avoid duplicate relationships when re-running after a failed write.
    rels = re.sub(r'<Relationship Id="rIdDiagram[123]".*?/>', "", rels)
    rels = append_relationships(rels, [(rid, target) for rid, target, *_ in diagrams])
    content_types = ensure_png_content_type(content_types)

    validate_xml(document)
    validate_xml(rels)
    validate_xml(content_types)

    entries["word/document.xml"] = document.encode("utf-8")
    entries["word/_rels/document.xml.rels"] = rels.encode("utf-8")
    entries["[Content_Types].xml"] = content_types.encode("utf-8")
    for _, target, path, _, _ in diagrams:
        entries[f"word/media/{target}"] = path.read_bytes()

    with zipfile.ZipFile(OUTPUT_PATH, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in entries.items():
            zout.writestr(name, data)
    print(OUTPUT_PATH.resolve())


if __name__ == "__main__":
    main()
