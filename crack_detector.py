from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import csv

cv2 = None
np = None


SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


@dataclass(frozen=True)
class CrackAnalysisConfig:
    area_threshold: int = 350
    gray_threshold: int = 5
    scale_cm_per_px: float = 10.8 / 1440
    kernel_size: int = 3
    iterations: int = 1


def _load_cv() -> None:
    global cv2, np

    if cv2 is not None and np is not None:
        return

    try:
        import cv2 as cv2_module
        import numpy as np_module
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "裂缝识别需要 opencv-python 和 numpy，请先在当前 Python 环境安装依赖。"
        ) from exc

    cv2 = cv2_module
    np = np_module


def _remove_small_contours(mask: Any, area_threshold: int) -> Any:
    contours, _ = cv2.findContours(mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        if cv2.contourArea(contour) < area_threshold:
            cv2.fillPoly(mask, [contour], 0)
    return mask


def _average_crack_width_px(mask: Any) -> float:
    runs: list[int] = []
    height, width = mask.shape

    for row in range(height):
        count = 0
        for col in range(width):
            if mask[row, col] == 255:
                count += 1
            elif count:
                runs.append(count)
                count = 0
        if count:
            runs.append(count)

    if not runs:
        return 0.0
    return float(np.mean(runs))


def _append_result_csv(csv_path: Path, fieldnames: list[str], row: dict[str, Any]) -> None:
    existing_rows: list[dict[str, Any]] = []
    existing_fieldnames: list[str] | None = None

    if csv_path.exists():
        with csv_path.open("r", newline="", encoding="utf-8-sig") as csv_file:
            reader = csv.DictReader(csv_file)
            existing_fieldnames = reader.fieldnames
            existing_rows = list(reader)

    if existing_fieldnames == fieldnames:
        with csv_path.open("a", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            writer.writerow(row)
        return

    with csv_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for existing_row in existing_rows:
            writer.writerow({name: existing_row.get(name, "") for name in fieldnames})
        writer.writerow(row)


def analyze_crack_image(
    image_path: Path,
    output_dir: Path,
    config: CrackAnalysisConfig | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = config or CrackAnalysisConfig()
    metadata = metadata or {}
    image_path = Path(image_path)
    output_dir = Path(output_dir)
    _load_cv()

    if not image_path.exists():
        raise FileNotFoundError(f"image not found: {image_path}")
    if image_path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
        raise ValueError(f"unsupported image type: {image_path.suffix}")

    image = cv2.imread(str(image_path))
    if image is None:
        raise ValueError(f"failed to read image: {image_path}")

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    mask = np.zeros_like(gray, dtype=np.uint8)
    mask[gray <= config.gray_threshold] = 255

    kernel = np.ones((config.kernel_size, config.kernel_size), np.uint8)
    mask = cv2.dilate(mask, kernel, config.iterations)
    mask = _remove_small_contours(mask, config.area_threshold)

    overlay = np.zeros_like(image, dtype=np.uint8)
    overlay[mask == 255] = [0, 0, 255]
    result_image = cv2.addWeighted(image, 0.7, overlay, 0.3, 0)

    width_px = _average_crack_width_px(mask)
    width_cm = width_px * config.scale_cm_per_px

    cv2.putText(
        result_image,
        f"{width_px:.2f}px / {width_cm:.4f}cm",
        (30, 55),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.0,
        (0, 255, 0),
        2,
        cv2.LINE_AA,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_name = f"{image_path.stem}_crack.jpg"
    output_path = output_dir / output_name
    cv2.imwrite(str(output_path), result_image)

    csv_path = output_dir / "result.csv"
    fieldnames = [
        "time",
        "image",
        "result_image",
        "detected",
        "crack_pixels",
        "pixel_result",
        "result_cm",
        "step_index",
        "photo_count",
        "crack_analysis_count",
        "position_x_cm",
        "position_y_cm",
        "position_z_cm",
        "tof_altitude_cm",
        "yaw_deg",
        "pitch_deg",
        "roll_deg",
        "scale_cm_per_px",
        "gray_threshold",
        "area_threshold",
    ]

    crack_pixels = int(np.count_nonzero(mask == 255))
    position = metadata.get("position_xyz") or {}
    orientation = metadata.get("orientation") or {}
    row = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "image": image_path.name,
        "result_image": output_name,
        "detected": "yes" if crack_pixels > 0 else "no",
        "crack_pixels": crack_pixels,
        "pixel_result": f"{width_px:.6f}",
        "result_cm": f"{width_cm:.6f}",
        "step_index": metadata.get("step_index", ""),
        "photo_count": metadata.get("photo_count", ""),
        "crack_analysis_count": metadata.get("crack_analysis_count", ""),
        "position_x_cm": position.get("x", ""),
        "position_y_cm": position.get("y", ""),
        "position_z_cm": position.get("z", ""),
        "tof_altitude_cm": metadata.get("tof_altitude_cm", ""),
        "yaw_deg": orientation.get("yaw", ""),
        "pitch_deg": orientation.get("pitch", ""),
        "roll_deg": orientation.get("roll", ""),
        "scale_cm_per_px": f"{config.scale_cm_per_px:.10f}",
        "gray_threshold": config.gray_threshold,
        "area_threshold": config.area_threshold,
    }
    _append_result_csv(csv_path, fieldnames, row)

    return {
        "success": True,
        "input_path": str(image_path),
        "input_filename": image_path.name,
        "output_path": str(output_path),
        "output_filename": output_name,
        "csv_path": str(csv_path),
        "pixel_width": round(width_px, 3),
        "width_cm": round(width_cm, 6),
        "scale_cm_per_px": config.scale_cm_per_px,
        "crack_pixels": crack_pixels,
        "detected": crack_pixels > 0,
        "capture_metadata": metadata,
        "params": {
            "gray_threshold": config.gray_threshold,
            "area_threshold": config.area_threshold,
            "kernel_size": config.kernel_size,
            "iterations": config.iterations,
        },
    }
