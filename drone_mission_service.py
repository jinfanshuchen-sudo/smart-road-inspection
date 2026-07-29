"""Small Flask backend for starting the D0 landing mission from a dashboard.

Run this service on the same computer that is connected to the drone Wi-Fi.
The Vue dashboard can call POST /api/drone/start-task later.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import math
import os
from pathlib import Path
import socket
import threading
import time
from typing import Any

from flask import Flask, Response, jsonify, request, send_from_directory

from crack_detector import CrackAnalysisConfig, analyze_crack_image
from pyhulax import DroneAPI
from pyhulax.core import AIRecognitionTarget, CameraPitchMode, CommandResult, Direction, VelocityLevel, VisionMode
from pyhulax.core.exceptions import TelemetryUnavailable


app = Flask(__name__)
BASE_DIR = Path(__file__).resolve().parent
DASHBOARD_DIR = BASE_DIR / "dashboard"
MEDIA_DIR = BASE_DIR / "media"
PHOTO_DIR = MEDIA_DIR / "photos"
CRACK_RESULT_DIR = MEDIA_DIR / "crack_results"
MAX_SAVED_PHOTOS = 50
RECENT_PHOTOS_TO_SHOW = 3
DEFAULT_CRACK_SCALE_CM_PER_PX = 10.8 / 1440
D0_LANDING_COMPENSATION_X_CM = 0.0
D0_LANDING_COMPENSATION_Y_CM = 0.0
# Hula is in router/group mode, so its command endpoint is on the Hula-Battle LAN.
DEFAULT_DRONE_IP = os.environ.get("PYHULAX_DRONE_IP", "192.168.31.160")


@dataclass
class MissionState:
    running: bool = False
    phase: str = "idle"
    message: str = "等待任务"
    success: bool | None = None
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    battery: int | None = None
    altitude_cm: float | None = None
    altitude_updated_at: str | None = None
    position_xyz: dict[str, float] | None = None
    coordinate_origin: dict[str, float] | None = None
    coordinate_updated_at: str | None = None
    orientation: dict[str, Any] | None = None
    ai_result: dict[str, Any] | None = None
    crack_alert: dict[str, Any] | None = None
    params: dict[str, Any] = field(default_factory=dict)


state_lock = threading.Lock()
mission_state = MissionState()
mission_thread: threading.Thread | None = None
active_drone: DroneAPI | None = None
active_coordinate_origin: dict[str, float] | None = None

video_lock = threading.Lock()
video_drone: DroneAPI | None = None
video_stream: Any | None = None
video_streamer: Any | None = None
video_started_at: str | None = None
video_error: str | None = None
video_battery: int | None = None
video_altitude_cm: float | None = None
video_altitude_updated_at: str | None = None
video_position_xyz: dict[str, float] | None = None
video_coordinate_origin: dict[str, float] | None = None
video_coordinate_updated_at: str | None = None
video_orientation: dict[str, Any] | None = None
last_photo: dict[str, Any] | None = None


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def set_state(**updates: Any) -> None:
    with state_lock:
        for key, value in updates.items():
            setattr(mission_state, key, value)


def get_state_dict() -> dict[str, Any]:
    with state_lock:
        return asdict(mission_state)


def get_drone_preflight(drone_ip: str) -> dict[str, Any]:
    """Confirm that commands will leave through the Hula router LAN."""
    expected_prefix = ".".join(drone_ip.split(".")[:3]) + "."
    try:
        with socket.create_connection((drone_ip, 8888), timeout=2.0) as connection:
            local_ip = connection.getsockname()[0]
    except OSError as exc:
        return {
            "ready": False,
            "drone_ip": drone_ip,
            "message": f"无法连接无人机 {drone_ip}:8888：{exc}",
        }

    if not local_ip.startswith(expected_prefix):
        return {
            "ready": False,
            "drone_ip": drone_ip,
            "local_ip": local_ip,
            "message": (
                f"当前命令将从 {local_ip} 发出，不在 Hula-Battle 网段 {expected_prefix}0/24。"
                "请连接 Hula-Battle 并断开 VPN 后再执行总任务。"
            ),
        }

    return {
        "ready": True,
        "drone_ip": drone_ip,
        "local_ip": local_ip,
        "message": "无人机局域网检查通过，可以执行总任务。",
    }


def photo_record(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "filename": path.name,
        "path": str(path),
        "url": f"/api/drone/photos/{path.name}",
        "saved_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
    }


def list_saved_photos(limit: int | None = None) -> list[dict[str, Any]]:
    if not PHOTO_DIR.exists():
        return []

    photo_files = [
        path
        for path in PHOTO_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"}
    ]
    photo_files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    if limit is not None:
        photo_files = photo_files[:limit]
    return [photo_record(path) for path in photo_files]


def prune_saved_photos(max_count: int = MAX_SAVED_PHOTOS) -> None:
    if not PHOTO_DIR.exists():
        return

    photo_files = [
        path
        for path in PHOTO_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"}
    ]
    photo_files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for old_photo in photo_files[max_count:]:
        try:
            old_photo.unlink()
        except OSError:
            pass


def current_last_photo() -> dict[str, Any] | None:
    global last_photo

    recent_photos = list_saved_photos(1)
    if recent_photos:
        last_photo = recent_photos[0]
        return last_photo

    last_photo = None
    return None


def save_current_stream_frame(wait_sec: float = 0.0, retry_interval_sec: float = 0.2) -> tuple[dict[str, Any], int]:
    global last_photo

    with video_lock:
        streamer = video_streamer

    if streamer is None:
        return {
            "success": False,
            "error": "请先开启相机，看到实时画面后再拍照保存。",
        }, 400

    deadline = time.monotonic() + max(0.0, wait_sec)
    jpeg = streamer.get_frame()
    while jpeg is None and time.monotonic() < deadline:
        time.sleep(max(0.05, retry_interval_sec))
        jpeg = streamer.get_frame()
    if jpeg is None:
        return {
            "success": False,
            "error": "视频画面还没准备好，请等 1 秒再拍。",
        }, 503

    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    photo_path = PHOTO_DIR / f"{timestamp}_stream.jpg"
    photo_path.write_bytes(jpeg)

    prune_saved_photos()
    last_photo = photo_record(photo_path)
    return {
        "success": True,
        "photo": last_photo,
        "recent_photos": list_saved_photos(RECENT_PHOTOS_TO_SHOW),
        "max_saved_photos": MAX_SAVED_PHOTOS,
        "source": "video_stream",
    }, 200


def analyze_photo_record_for_cracks(
    photo: dict[str, Any],
    payload: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    metadata = metadata or {}
    config = CrackAnalysisConfig(
        area_threshold=int(payload.get("area_threshold", 350)),
        gray_threshold=int(payload.get("gray_threshold", 5)),
        scale_cm_per_px=float(payload.get("scale_cm_per_px", DEFAULT_CRACK_SCALE_CM_PER_PX)),
        kernel_size=int(payload.get("kernel_size", 3)),
        iterations=int(payload.get("iterations", 1)),
    )
    result = analyze_crack_image(Path(photo["path"]), CRACK_RESULT_DIR, config=config, metadata=metadata)
    result["input_photo"] = photo
    result["output_url"] = f"/api/crack/results/{result['output_filename']}"
    result["csv_url"] = "/api/crack/result.csv"
    result["altitude_cm"] = video_altitude_cm if video_altitude_cm is not None else mission_state.altitude_cm
    result["capture_position"] = metadata.get("position_xyz")
    result["capture_altitude_cm"] = metadata.get("tof_altitude_cm")
    result["capture_orientation"] = metadata.get("orientation")
    result["scale_note"] = "当前使用固定比例尺；如需高精度，需要用 ToF 高度和标定板重新校准。"
    return result


def capture_crack_photo_metadata(
    *,
    step_index: int | None = None,
    photo_count: int | None = None,
    crack_analysis_count: int | None = None,
) -> dict[str, Any]:
    with state_lock:
        position_xyz = dict(mission_state.position_xyz) if mission_state.position_xyz else None
        orientation = dict(mission_state.orientation) if mission_state.orientation else None
        tof_altitude_cm = mission_state.altitude_cm
        coordinate_origin = dict(mission_state.coordinate_origin) if mission_state.coordinate_origin else None

    return {
        "step_index": step_index,
        "photo_count": photo_count,
        "crack_analysis_count": crack_analysis_count,
        "position_xyz": position_xyz,
        "tof_altitude_cm": tof_altitude_cm,
        "orientation": orientation,
        "coordinate_origin": coordinate_origin,
        "position_note": "拍照时无人机相对起飞前位置的坐标，Y 为起飞时摄像头前方，Z 为相对起点高度。",
    }


def build_d0_result_data(result: Any, params: dict[str, Any]) -> dict[str, Any]:
    result_data = {
        "success": result.success,
        "target_type": result.target_type,
        "position": result.position.model_dump() if result.position else None,
        "angle": result.angle,
    }
    if result.success and result.position is not None:
        target_x = float(params["align_target_x_cm"])
        target_y = float(params["align_target_y_cm"])
        error_x = float(result.position.x) - target_x
        error_y = float(result.position.y) - target_y
        result_data.update(
            {
                "target_position": {"x": target_x, "y": target_y},
                "target_error": {"x": round(error_x, 1), "y": round(error_y, 1)},
                "aligned": abs(error_x) <= params["align_tolerance_cm"]
                and abs(error_y) <= params["align_tolerance_cm"],
            }
        )
    return result_data


def build_qr_result_data(result: Any, params: dict[str, Any]) -> dict[str, Any]:
    result_data = {
        "success": result.success,
        "qr_id": result.qr_id,
        "target_qr_id": params.get("return_qr_id"),
        "position": result.position.model_dump() if result.position else None,
        "angle": result.angle,
    }
    if result.success and result.position is not None and "return_qr_align_target_x_cm" in params:
        target_x = float(params["return_qr_align_target_x_cm"])
        target_y = float(params["return_qr_align_target_y_cm"])
        error_x = float(result.position.x) - target_x
        error_y = float(result.position.y) - target_y
        result_data.update(
            {
                "target_position": {"x": target_x, "y": target_y},
                "target_error": {"x": round(error_x, 1), "y": round(error_y, 1)},
                "aligned": abs(error_x) <= params["return_qr_align_tolerance_cm"]
                and abs(error_y) <= params["return_qr_align_tolerance_cm"],
            }
        )
    return result_data


def build_landing_qr_result_data(result: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Build landing-marker data using the same pose fields as the numbered QR markers."""
    result_data = {
        "success": result.success,
        "qr_id": result.qr_id,
        "landing_qr_id": params["landing_qr_id"],
        "position": result.position.model_dump() if result.position else None,
        "angle": result.angle,
    }
    if result.success and result.position is not None:
        target_x = float(params["align_target_x_cm"])
        target_y = float(params["align_target_y_cm"])
        error_x = float(result.position.x) - target_x
        error_y = float(result.position.y) - target_y
        result_data.update(
            {
                "target_position": {"x": target_x, "y": target_y},
                "target_error": {"x": round(error_x, 1), "y": round(error_y, 1)},
                "aligned": abs(error_x) <= params["align_tolerance_cm"]
                and abs(error_y) <= params["align_tolerance_cm"],
            }
        )
    return result_data


def build_post_turn_qr_result_data(result: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Evaluate QR 7 after the 180-degree turn in the turned body frame."""
    result_data = {
        "success": result.success,
        "qr_id": result.qr_id,
        "target_qr_id": params["return_qr_id"],
        "position": result.position.model_dump() if result.position else None,
        "angle": result.angle,
    }
    if result.success and result.position is not None:
        target_x = float(params["return_qr_post_turn_target_x_cm"])
        target_y = float(params["return_qr_post_turn_target_y_cm"])
        error_x = float(result.position.x) - target_x
        error_y = float(result.position.y) - target_y
        result_data.update(
            {
                "target_position": {"x": target_x, "y": target_y},
                "target_error": {"x": round(error_x, 1), "y": round(error_y, 1)},
                "aligned": abs(error_x) <= params["return_qr_post_turn_tolerance_cm"]
                and abs(error_y) <= params["return_qr_post_turn_tolerance_cm"],
            }
        )
    return result_data


def pose_spread_cm(results: list[Any]) -> float | None:
    """Return the largest axis spread across valid vision poses."""
    positions = [result.position for result in results if result.position is not None]
    if len(positions) < 2:
        return None
    return max(
        max(float(position.x) for position in positions) - min(float(position.x) for position in positions),
        max(float(position.y) for position in positions) - min(float(position.y) for position in positions),
        max(float(position.z) for position in positions) - min(float(position.z) for position in positions),
    )


def combine_crack_d0_result(
    crack_result: dict[str, Any] | None,
    d0_result: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if crack_result is None and d0_result is None:
        return None
    combined = dict(crack_result or {})
    if crack_result is not None:
        combined["crack_result"] = crack_result
    if d0_result is not None:
        if d0_result.get("landing_qr_id") is not None:
            combined["landing_qr_result"] = d0_result
            combined["landing_qr_success"] = d0_result.get("success") is True
        else:
            combined["d0_result"] = d0_result
            combined["d0_success"] = d0_result.get("success") is True
    return combined


def combine_return_qr_d0_result(
    return_qr_result: dict[str, Any] | None,
    d0_result: dict[str, Any] | None,
    crack_result: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if return_qr_result is None and d0_result is None and crack_result is None:
        return None
    combined: dict[str, Any] = {}
    if crack_result is not None:
        combined["crack_result"] = crack_result
    if return_qr_result is not None:
        combined["return_qr_result"] = return_qr_result
        combined["return_qr_success"] = return_qr_result.get("success") is True
    if d0_result is not None:
        combined["d0_result"] = d0_result
        combined["d0_success"] = d0_result.get("success") is True
    return combined


def get_video_state_dict_unlocked() -> dict[str, Any]:
    global video_battery, video_altitude_cm, video_altitude_updated_at, video_orientation
    global video_position_xyz, video_coordinate_origin, video_coordinate_updated_at

    running = video_stream is not None and video_drone is not None
    frame_count = getattr(video_streamer, "frame_count", 0) if video_streamer else 0
    client_count = getattr(video_streamer, "client_count", 0) if video_streamer else 0
    recent_photos = list_saved_photos(RECENT_PHOTOS_TO_SHOW)
    if running and video_drone is not None:
        try:
            video_altitude_cm = round(float(video_drone.get_altitude()), 1)
            video_altitude_updated_at = now_iso()
        except Exception:
            pass
        try:
            position = video_drone.get_position()
            if video_coordinate_origin is not None:
                video_position_xyz = {
                    "x": round(float(position.x) - video_coordinate_origin["x"], 1),
                    "y": round(float(position.y) - video_coordinate_origin["y"], 1),
                    "z": round(float(position.z) - video_coordinate_origin["z"], 1),
                }
                video_coordinate_updated_at = now_iso()
        except Exception:
            pass
        try:
            orientation = video_drone.get_orientation()
            video_orientation = {
                "yaw": round(float(orientation.yaw), 1),
                "pitch": round(float(orientation.pitch), 1),
                "roll": round(float(orientation.roll), 1),
                "updated_at": now_iso(),
            }
        except Exception:
            pass
        try:
            video_battery = int(video_drone.get_battery())
        except Exception:
            pass
    return {
        "running": running,
        "started_at": video_started_at,
        "battery": video_battery,
        "altitude_cm": video_altitude_cm,
        "altitude_updated_at": video_altitude_updated_at,
        "position_xyz": video_position_xyz,
        "coordinate_origin": video_coordinate_origin,
        "coordinate_updated_at": video_coordinate_updated_at,
        "orientation": video_orientation,
        "frame_count": frame_count,
        "client_count": client_count,
        "feed_url": "/api/drone/video-feed" if running else None,
        "frame_url": "/api/drone/frame.jpg" if running else None,
        "error": video_error,
        "last_photo": current_last_photo(),
        "recent_photos": recent_photos,
        "max_saved_photos": MAX_SAVED_PHOTOS,
    }


def get_video_state_dict() -> dict[str, Any]:
    with video_lock:
        return get_video_state_dict_unlocked()


def stop_video_stream_unlocked() -> None:
    global active_coordinate_origin
    global video_drone, video_stream, video_streamer, video_started_at, video_battery
    global video_altitude_cm, video_altitude_updated_at, video_orientation
    global video_position_xyz, video_coordinate_origin, video_coordinate_updated_at

    stream = video_stream
    drone = video_drone
    video_stream = None
    video_streamer = None
    video_started_at = None
    video_battery = None
    video_altitude_cm = None
    video_altitude_updated_at = None
    video_position_xyz = None
    video_coordinate_origin = None
    video_coordinate_updated_at = None
    video_orientation = None
    active_coordinate_origin = None

    if stream is not None:
        try:
            stream.stop()
        except Exception:
            pass

    if drone is not None:
        try:
            drone.set_video_stream(False)
        except Exception:
            pass
        try:
            drone.disconnect()
        except Exception:
            pass

    video_drone = None


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return response


@app.route("/", methods=["GET"])
def dashboard():
    return send_from_directory(DASHBOARD_DIR, "index.html")


@app.route("/vendor/<path:filename>", methods=["GET"])
def dashboard_vendor(filename: str):
    return send_from_directory(DASHBOARD_DIR / "vendor", filename)


def expect_success(result: CommandResult, command_name: str) -> None:
    if result != CommandResult.SUCCESS:
        raise RuntimeError(f"{command_name} failed: {result.name} ({int(result)})")


def move_with_retry(
    drone: DroneAPI,
    direction: Direction,
    distance_cm: int,
    speed: VelocityLevel,
    command_name: str,
    retries: int = 1,
) -> bool:
    attempts = max(1, retries + 1)
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            result = drone.move(direction, distance_cm, speed=speed)
            if result == CommandResult.SUCCESS:
                return True
            last_error = f"{result.name} ({int(result)})"
        except Exception as exc:
            last_error = str(exc)
        if attempt < attempts:
            set_state(error=f"{command_name} 返回异常：{last_error}，重试 {attempt}/{retries}")
            time.sleep(0.3)
    set_state(error=f"{command_name} 返回异常：{last_error}，跳过本段并继续返航扫描")
    return False


def normalize_angle_deg(angle: float) -> float:
    return (float(angle) + 180.0) % 360.0 - 180.0


def get_yaw_safe(drone: DroneAPI) -> float | None:
    try:
        return float(drone.get_orientation().yaw)
    except Exception:
        return None


def correct_yaw_after_turn(
    drone: DroneAPI,
    yaw_before_turn: float | None,
    turn_angle_deg: float,
    *,
    tolerance_deg: float,
    max_correction_deg: float,
) -> None:
    if yaw_before_turn is None:
        set_state(error="返航 yaw 修正跳过：转向前未读取到 yaw")
        return

    yaw_after_turn = get_yaw_safe(drone)
    if yaw_after_turn is None:
        set_state(error="返航 yaw 修正跳过：转向后未读取到 yaw")
        return

    target_yaw = normalize_angle_deg(yaw_before_turn + turn_angle_deg)
    yaw_error = normalize_angle_deg(target_yaw - yaw_after_turn)
    if abs(yaw_error) <= tolerance_deg:
        set_state(
            phase="turning_back",
            message=f"返航 yaw 已对准：误差 {yaw_error:.1f}°",
        )
        return

    correction = int(round(max(-max_correction_deg, min(max_correction_deg, yaw_error))))
    if correction == 0:
        return
    set_state(
        phase="turning_back",
        message=(
            f"返航 yaw 修正：目标 {target_yaw:.1f}°，当前 {yaw_after_turn:.1f}°，"
            f"补转 {correction:.1f}°"
        ),
    )
    try:
        result = drone.rotate(correction)
        if result != CommandResult.SUCCESS:
            set_state(error=f"返航 yaw 补转返回 {result.name}，继续分段返航")
    except Exception as exc:
        set_state(error=f"返航 yaw 补转异常，继续分段返航：{exc}")


def wait_for_battery(drone: DroneAPI, timeout_sec: float = 8.0) -> int:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            return drone.get_battery()
        except TelemetryUnavailable:
            time.sleep(0.5)
    raise RuntimeError("Battery telemetry was not available.")


def capture_coordinate_origin(
    drone: DroneAPI,
    *,
    update_video: bool = False,
    timeout_sec: float = 5.0,
) -> dict[str, float]:
    global active_coordinate_origin
    global video_coordinate_origin, video_position_xyz, video_coordinate_updated_at

    drone.set_qr_localization(False)
    time.sleep(0.2)
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            position = drone.get_position()
            origin = {
                "x": float(position.x),
                "y": float(position.y),
                "z": float(position.z),
            }
            active_coordinate_origin = origin
            updated_at = now_iso()
            set_state(
                coordinate_origin=origin,
                position_xyz={"x": 0.0, "y": 0.0, "z": 0.0},
                coordinate_updated_at=updated_at,
            )
            if update_video:
                with video_lock:
                    video_coordinate_origin = origin
                    video_position_xyz = {"x": 0.0, "y": 0.0, "z": 0.0}
                    video_coordinate_updated_at = updated_at
            return origin
        except TelemetryUnavailable:
            time.sleep(0.2)

    raise RuntimeError("Position telemetry was not available for coordinate origin.")


def update_drone_telemetry(drone: DroneAPI, *, update_video: bool = False) -> None:
    global active_coordinate_origin
    global video_battery, video_altitude_cm, video_altitude_updated_at, video_orientation
    global video_position_xyz, video_coordinate_updated_at

    altitude_cm: float | None = None
    position_xyz: dict[str, float] | None = None
    orientation_data: dict[str, Any] | None = None
    battery: int | None = None
    updated_at = now_iso()

    try:
        altitude_cm = round(float(drone.get_altitude()), 1)
    except Exception:
        altitude_cm = None

    try:
        position = drone.get_position()
        if active_coordinate_origin is not None:
            position_xyz = {
                "x": round(float(position.x) - active_coordinate_origin["x"], 1),
                "y": round(float(position.y) - active_coordinate_origin["y"], 1),
                "z": round(float(position.z) - active_coordinate_origin["z"], 1),
            }
    except Exception:
        position_xyz = None

    try:
        battery = int(drone.get_battery())
    except Exception:
        battery = None

    try:
        orientation = drone.get_orientation()
        orientation_data = {
            "yaw": round(float(orientation.yaw), 1),
            "pitch": round(float(orientation.pitch), 1),
            "roll": round(float(orientation.roll), 1),
            "updated_at": updated_at,
        }
    except Exception:
        orientation_data = None

    updates: dict[str, Any] = {
        "altitude_updated_at": updated_at,
        "coordinate_updated_at": updated_at,
    }
    if altitude_cm is not None:
        updates["altitude_cm"] = altitude_cm
    if position_xyz is not None:
        updates["position_xyz"] = position_xyz
    if orientation_data is not None:
        updates["orientation"] = orientation_data
    if battery is not None:
        updates["battery"] = battery
    set_state(**updates)

    if update_video:
        with video_lock:
            if altitude_cm is not None:
                video_altitude_cm = altitude_cm
            if position_xyz is not None:
                video_position_xyz = position_xyz
            if orientation_data is not None:
                video_orientation = orientation_data
            if battery is not None:
                video_battery = battery
            video_altitude_updated_at = updated_at
            video_coordinate_updated_at = updated_at


def start_telemetry_polling(
    drone: DroneAPI,
    stop_event: threading.Event,
    *,
    update_video: bool = False,
) -> threading.Thread:
    def poll() -> None:
        while not stop_event.is_set():
            update_drone_telemetry(drone, update_video=update_video)
            stop_event.wait(0.5)

    thread = threading.Thread(target=poll, daemon=True)
    thread.start()
    return thread


def mission_worker(params: dict[str, Any]) -> None:
    global active_drone

    drone = DroneAPI(
        enable_logging=False,
        enable_file_logging=False,
        enable_command_logging=False,
    )
    active_drone = drone

    found_d0 = False
    telemetry_stop = threading.Event()
    telemetry_thread: threading.Thread | None = None
    try:
        set_state(phase="connecting", message="正在连接无人机")
        if not drone.robust_connect(params["ip"], verbose=False):
            raise RuntimeError("无人机连接失败")

        capture_coordinate_origin(drone)
        battery = wait_for_battery(drone)
        set_state(battery=battery)
        telemetry_thread = start_telemetry_polling(drone, telemetry_stop)
        if battery < params["min_battery"]:
            raise RuntimeError(f"电量过低：{battery}%")

        set_state(phase="preparing", message="关闭 QR 定位，准备相对前进")
        drone.set_qr_localization(False)
        time.sleep(0.5)

        set_state(phase="takeoff", message=f"起飞到 {params['height_cm']} cm")
        expect_success(drone.takeoff(height_cm=params["height_cm"]), "takeoff")

        for step_index in range(1, params["max_steps"] + 1):
            set_state(
                phase="moving",
                message=f"向前搜索 D0：第 {step_index}/{params['max_steps']} 段",
            )
            expect_success(
                drone.move(
                    Direction.FORWARD,
                    params["step_cm"],
                    speed=VelocityLevel.MEDIUM,
                ),
                "move forward",
            )

            time.sleep(params["settle_sec"])

            set_state(phase="scanning", message="识别 D0")
            result = drone.recognize_target(params["target"])
            result_data = {
                "success": result.success,
                "target_type": result.target_type,
                "position": result.position.model_dump() if result.position else None,
                "angle": result.angle,
            }
            set_state(ai_result=result_data)

            if result.success:
                found_d0 = True
                set_state(phase="landing", message="识别到 D0，正在降落")
                break

        if not found_d0:
            set_state(phase="landing", message="未识别到 D0，执行安全降落")

        expect_success(drone.land(), "land")

        set_state(
            running=False,
            phase="completed" if found_d0 else "not_found",
            message="任务完成" if found_d0 else "未找到 D0，已降落",
            success=found_d0,
            finished_at=now_iso(),
        )
    except BaseException as exc:
        set_state(phase="landing", message="异常，尝试安全降落", error=str(exc))
        try:
            drone.land(blocking=False)
        except Exception:
            pass
        set_state(
            running=False,
            phase="failed",
            message="任务失败，已尝试降落",
            success=False,
            error=str(exc),
            finished_at=now_iso(),
        )
    finally:
        telemetry_stop.set()
        if telemetry_thread is not None:
            telemetry_thread.join(timeout=1.0)
        try:
            drone.disconnect()
        finally:
            active_drone = None


def video_hover_worker(params: dict[str, Any]) -> None:
    global active_drone, video_drone, video_stream, video_streamer
    global video_started_at, video_error, video_battery

    drone = DroneAPI(
        enable_logging=False,
        enable_file_logging=False,
        enable_command_logging=False,
    )
    stream = None
    telemetry_stop = threading.Event()
    telemetry_thread: threading.Thread | None = None

    try:
        active_drone = drone
        set_state(phase="connecting", message="正在连接无人机并启动视频")
        if not drone.robust_connect(params["ip"], verbose=False):
            raise RuntimeError("无人机连接失败")

        capture_coordinate_origin(drone, update_video=True)
        battery = wait_for_battery(drone)
        set_state(battery=battery)
        if battery < params["min_battery"]:
            raise RuntimeError(f"电量过低：{battery}%")

        from pyhulax.video import MJPEGStreamer

        streamer = MJPEGStreamer(
            quality=params["quality"],
            max_fps=params["max_fps"],
            draw_detections=False,
        )
        expect_success(drone.set_video_stream(True), "set video stream")
        stream = drone.create_video_stream()
        stream.add_callback(streamer)
        stream.start()

        with video_lock:
            video_drone = drone
            video_stream = stream
            video_streamer = streamer
            video_started_at = now_iso()
            video_error = None
            video_battery = battery

        telemetry_thread = start_telemetry_polling(drone, telemetry_stop, update_video=True)

        set_state(phase="takeoff", message=f"视频已开启，起飞到 {params['height_cm']} cm")
        expect_success(drone.takeoff(height_cm=params["height_cm"]), "takeoff")

        set_state(phase="hovering", message=f"悬停 {params['hover_sec']} 秒，视频保持开启")
        time.sleep(params["hover_sec"])

        set_state(phase="landing", message="悬停测试完成，正在降落")
        expect_success(drone.land(), "land")

        set_state(
            running=False,
            phase="completed",
            message="带视频悬停测试完成，已降落",
            success=True,
            finished_at=now_iso(),
        )
    except BaseException as exc:
        set_state(phase="landing", message="异常，尝试安全降落", error=str(exc))
        with video_lock:
            video_error = str(exc)
        try:
            drone.land(blocking=False)
        except Exception:
            pass
        set_state(
            running=False,
            phase="failed",
            message="带视频悬停测试失败，已尝试降落",
            success=False,
            error=str(exc),
            finished_at=now_iso(),
        )
    finally:
        telemetry_stop.set()
        if telemetry_thread is not None:
            telemetry_thread.join(timeout=1.0)
        try:
            drone.set_camera_angle(CameraPitchMode.UP_ABSOLUTE, 0)
        except Exception:
            pass
        with video_lock:
            try:
                stop_video_stream_unlocked()
            except Exception as exc:
                video_error = str(exc)
        active_drone = None


def video_mission_worker(params: dict[str, Any]) -> None:
    global active_drone, video_drone, video_stream, video_streamer
    global video_started_at, video_error, video_battery

    drone = DroneAPI(
        enable_logging=False,
        enable_file_logging=False,
        enable_command_logging=False,
    )
    stream = None
    found_d0 = False
    telemetry_stop = threading.Event()
    telemetry_thread: threading.Thread | None = None

    try:
        active_drone = drone
        set_state(phase="connecting", message="正在连接无人机并启动视频")
        if not drone.robust_connect(params["ip"], verbose=False):
            raise RuntimeError("无人机连接失败")

        capture_coordinate_origin(drone, update_video=True)
        battery = wait_for_battery(drone)
        set_state(battery=battery)
        if battery < params["min_battery"]:
            raise RuntimeError(f"电量过低：{battery}%")

        from pyhulax.video import MJPEGStreamer

        streamer = MJPEGStreamer(
            quality=params["quality"],
            max_fps=params["max_fps"],
            draw_detections=False,
        )
        expect_success(drone.set_video_stream(True), "set video stream")
        stream = drone.create_video_stream()
        stream.add_callback(streamer)
        stream.start()

        with video_lock:
            video_drone = drone
            video_stream = stream
            video_streamer = streamer
            video_started_at = now_iso()
            video_error = None
            video_battery = battery

        set_state(phase="preparing", message="视频已开启，准备执行 D0 任务")
        drone.set_qr_localization(False)
        time.sleep(0.5)

        set_state(phase="takeoff", message=f"起飞到 {params['height_cm']} cm，视频保持开启")
        expect_success(drone.takeoff(height_cm=params["height_cm"]), "takeoff")

        for step_index in range(1, params["max_steps"] + 1):
            set_state(
                phase="moving",
                message=f"带视频向前搜索 D0：第 {step_index}/{params['max_steps']} 段",
            )
            expect_success(
                drone.move(
                    Direction.FORWARD,
                    params["step_cm"],
                    speed=VelocityLevel.MEDIUM,
                ),
                "move forward",
            )

            time.sleep(params["settle_sec"])

            if params.get("auto_photo"):
                set_state(
                    phase="auto_photo",
                    message=f"自动拍照：第 {step_index}/{params['max_steps']} 段",
                )
                photo_result, photo_status = save_current_stream_frame(
                    wait_sec=params.get("photo_ready_timeout_sec", 0.0),
                    retry_interval_sec=params.get("photo_retry_interval_sec", 0.2),
                )
                if not photo_result.get("success"):
                    set_state(error=f"自动拍照失败：{photo_result.get('error', photo_status)}")

            set_state(phase="scanning", message="带视频识别 D0")
            result = drone.recognize_target(params["target"])
            result_data = {
                "success": result.success,
                "target_type": result.target_type,
                "position": result.position.model_dump() if result.position else None,
                "angle": result.angle,
            }
            set_state(ai_result=result_data)

            if result.success:
                found_d0 = True
                set_state(phase="landing", message="识别到 D0，正在降落，视频保持开启")
                break

        if not found_d0:
            set_state(phase="landing", message="未识别到 D0，执行安全降落，视频保持开启")

        expect_success(drone.land(), "land")

        set_state(
            running=False,
            phase="completed" if found_d0 else "not_found",
            message="带视频任务完成，已降落" if found_d0 else "未找到 D0，已降落",
            success=found_d0,
            finished_at=now_iso(),
        )
    except BaseException as exc:
        set_state(phase="landing", message="异常，尝试安全降落", error=str(exc))
        with video_lock:
            video_error = str(exc)
        try:
            drone.land(blocking=False)
        except Exception:
            pass
        set_state(
            running=False,
            phase="failed",
            message="带视频任务失败，已尝试降落",
            success=False,
            error=str(exc),
            finished_at=now_iso(),
        )
    finally:
        with video_lock:
            try:
                stop_video_stream_unlocked()
            except Exception as exc:
                video_error = str(exc)
        active_drone = None


def video_mission_worker_v2(params: dict[str, Any]) -> None:
    global active_drone, video_drone, video_stream, video_streamer
    global video_started_at, video_error, video_battery

    drone = DroneAPI(
        enable_logging=False,
        enable_file_logging=False,
        enable_command_logging=False,
    )
    found_d0 = False
    aligned_d0 = False
    last_success_result_data: dict[str, Any] | None = None
    telemetry_stop = threading.Event()
    telemetry_thread: threading.Thread | None = None

    try:
        active_drone = drone
        set_state(phase="connecting", message="正在连接无人机并启动视频")
        if not drone.robust_connect(params["ip"], verbose=False):
            raise RuntimeError("无人机连接失败")

        capture_coordinate_origin(drone, update_video=True)
        battery = wait_for_battery(drone)
        set_state(battery=battery)
        if battery < params["min_battery"]:
            raise RuntimeError(f"电量过低：{battery}%")

        from pyhulax.video import MJPEGStreamer

        streamer = MJPEGStreamer(
            quality=params["quality"],
            max_fps=params["max_fps"],
            draw_detections=False,
        )
        expect_success(drone.set_video_stream(True), "set video stream")
        stream = drone.create_video_stream()
        stream.add_callback(streamer)
        stream.start()

        with video_lock:
            video_drone = drone
            video_stream = stream
            video_streamer = streamer
            video_started_at = now_iso()
            video_error = None
            video_battery = battery

        time.sleep(params.get("video_warmup_sec", 0.0))
        telemetry_thread = start_telemetry_polling(drone, telemetry_stop, update_video=True)
        update_drone_telemetry(drone, update_video=True)

        set_state(phase="preparing", message="视频已开启，准备执行 D0 任务")
        drone.set_qr_localization(False)
        time.sleep(0.5)

        set_state(phase="takeoff", message=f"起飞到 {params['height_cm']} cm，实时高度更新中")
        expect_success(drone.takeoff(height_cm=params["height_cm"]), "takeoff")

        for step_index in range(1, params["max_steps"] + 1):
            set_state(
                phase="moving",
                message=f"向前搜索 D0：第 {step_index}/{params['max_steps']} 段",
            )
            expect_success(
                drone.move(
                    Direction.FORWARD,
                    params["step_cm"],
                    speed=VelocityLevel.MEDIUM,
                ),
                "move forward",
            )

            time.sleep(params["settle_sec"])

            if params.get("auto_photo"):
                set_state(
                    phase="auto_photo",
                    message=f"自动拍照：第 {step_index}/{params['max_steps']} 段",
                )
                photo_result, photo_status = save_current_stream_frame(
                    wait_sec=params.get("photo_ready_timeout_sec", 0.0),
                    retry_interval_sec=params.get("photo_retry_interval_sec", 0.2),
                )
                if not photo_result.get("success"):
                    set_state(error=f"自动拍照失败：{photo_result.get('error', photo_status)}")

            set_state(phase="scanning", message="识别 D0")
            result = drone.recognize_target(params["target"])
            result_data = {
                "success": result.success,
                "target_type": result.target_type,
                "position": result.position.model_dump() if result.position else None,
                "angle": result.angle,
            }
            set_state(ai_result=result_data)

            if result.success:
                found_d0 = True
                set_state(phase="landing", message="识别到 D0，正在降落")
                break

        if not found_d0:
            set_state(phase="landing", message="未识别到 D0，执行安全降落")

        expect_success(drone.land(), "land")
        update_drone_telemetry(drone, update_video=True)

        set_state(
            running=False,
            phase="completed" if found_d0 else "not_found",
            message="任务完成，已降落" if found_d0 else "未找到 D0，已降落",
            success=found_d0,
            finished_at=now_iso(),
        )
    except BaseException as exc:
        set_state(phase="landing", message="异常，尝试安全降落", error=str(exc))
        with video_lock:
            video_error = str(exc)
        try:
            drone.land(blocking=False)
        except Exception:
            pass
        set_state(
            running=False,
            phase="failed",
            message="任务失败，已尝试降落",
            success=False,
            error=str(exc),
            finished_at=now_iso(),
        )
    finally:
        telemetry_stop.set()
        if telemetry_thread is not None:
            telemetry_thread.join(timeout=1.0)
        try:
            drone.set_camera_angle(CameraPitchMode.UP_ABSOLUTE, 0)
        except Exception:
            pass
        with video_lock:
            try:
                stop_video_stream_unlocked()
            except Exception as exc:
                video_error = str(exc)
        active_drone = None


def return_qr7_test_worker(params: dict[str, Any]) -> None:
    global active_drone, video_drone, video_stream, video_streamer
    global video_started_at, video_error, video_battery

    drone = DroneAPI(
        enable_logging=False,
        enable_file_logging=False,
        enable_command_logging=False,
    )
    telemetry_stop = threading.Event()
    telemetry_thread: threading.Thread | None = None
    found_qr = False
    found_d0 = False
    aligned_d0 = False
    return_steps = 0
    photo_count = 0
    crack_count = 0
    crack_errors: list[str] = []
    last_crack_result: dict[str, Any] | None = None
    last_qr_result_data: dict[str, Any] | None = None
    last_d0_result_data: dict[str, Any] | None = None

    try:
        active_drone = drone
        set_state(phase="connecting", message="正在连接无人机并启动视频")
        if not drone.robust_connect(params["ip"], verbose=False):
            raise RuntimeError("无人机连接失败")

        capture_coordinate_origin(drone, update_video=True)
        battery = wait_for_battery(drone)
        set_state(battery=battery)
        if battery < params["min_battery"]:
            raise RuntimeError(f"电量过低：{battery}%")

        from pyhulax.video import MJPEGStreamer

        streamer = MJPEGStreamer(
            quality=params["quality"],
            max_fps=params["max_fps"],
            draw_detections=False,
        )
        expect_success(drone.set_video_stream(True), "set video stream")
        stream = drone.create_video_stream()
        stream.add_callback(streamer)
        stream.start()

        with video_lock:
            video_drone = drone
            video_stream = stream
            video_streamer = streamer
            video_started_at = now_iso()
            video_error = None
            video_battery = battery

        time.sleep(params["video_warmup_sec"])
        telemetry_thread = start_telemetry_polling(drone, telemetry_stop, update_video=True)
        update_drone_telemetry(drone, update_video=True)

        set_state(phase="preparing", message="总任务测试：只识别 7 号返航码，不识别 D0")
        drone.set_qr_localization(False)
        expect_success(
            drone.set_camera_angle(CameraPitchMode.DOWN_ABSOLUTE, params["camera_angle"]),
            "set camera angle down",
        )
        time.sleep(params["camera_settle_sec"])

        set_state(phase="takeoff", message=f"起飞到 {params['height_cm']} cm，开始寻找 7 号返航码")
        expect_success(drone.takeoff(height_cm=params["height_cm"]), "takeoff")
        time.sleep(params["takeoff_stabilize_sec"])
        update_drone_telemetry(drone, update_video=True)

        for step_index in range(1, params["max_steps"] + 1):
            set_state(
                phase="moving",
                message=f"总任务测试：向前搜索 7 号二维码，第 {step_index}/{params['max_steps']} 段",
            )
            expect_success(
                drone.move(Direction.FORWARD, params["step_cm"], speed=VelocityLevel.SLOW),
                "move forward",
            )
            if step_index == 1:
                # The demo uses a fixed warning once the drone has left the launch point.
                with state_lock:
                    mission_started_at = mission_state.started_at
                set_state(
                    crack_alert={
                        "id": f"{mission_started_at or now_iso()}-outbound-crack",
                        "task": "return_qr7_test",
                        "started_at": mission_started_at,
                        "step_index": step_index,
                        "message": "无人机已完成去程第一段，正在继续执行任务。",
                    }
                )
            if step_index <= params["departure_buffer_steps"]:
                set_state(
                    phase="departure_stabilizing",
                    message=(
                        f"Leaving takeoff platform: segment {step_index}/"
                        f"{params['departure_buffer_steps']}; holding steady before patrol"
                    ),
                    ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                )
                time.sleep(params["departure_buffer_settle_sec"])
                update_drone_telemetry(drone, update_video=True)
                continue

            time.sleep(params["settle_sec"])
            update_drone_telemetry(drone, update_video=True)

            if params.get("auto_crack"):
                set_state(
                    phase="auto_photo",
                    message=f"总任务裂缝拍照：第 {step_index}/{params['max_steps']} 段",
                    ai_result=combine_return_qr_d0_result(
                        last_qr_result_data,
                        last_d0_result_data,
                        last_crack_result,
                    ),
                )
                photo_result, photo_status = save_current_stream_frame(
                    wait_sec=params["photo_ready_timeout_sec"],
                    retry_interval_sec=params["photo_retry_interval_sec"],
                )
                if not photo_result.get("success"):
                    error_text = f"总任务第 {step_index} 段自动拍照失败：{photo_result.get('error', photo_status)}"
                    crack_errors.append(error_text)
                    set_state(error=error_text)
                else:
                    photo_count += 1
                    capture_metadata = capture_crack_photo_metadata(
                        step_index=step_index,
                        photo_count=photo_count,
                        crack_analysis_count=crack_count + 1,
                    )
                    set_state(
                        phase="crack_analysis",
                        message=f"总任务裂缝识别：第 {step_index}/{params['max_steps']} 张照片",
                        ai_result=combine_return_qr_d0_result(
                            last_qr_result_data,
                            last_d0_result_data,
                            last_crack_result,
                        ),
                    )
                    try:
                        crack_result = analyze_photo_record_for_cracks(
                            photo_result["photo"],
                            params.get("crack_config"),
                            capture_metadata,
                        )
                        crack_count += 1
                        crack_result["step_index"] = step_index
                        crack_result["photo_count"] = photo_count
                        crack_result["crack_analysis_count"] = crack_count
                        last_crack_result = crack_result
                        set_state(
                            ai_result=combine_return_qr_d0_result(
                                last_qr_result_data,
                                last_d0_result_data,
                                last_crack_result,
                            ),
                            message=f"第 {step_index}/{params['max_steps']} 段：已拍照并完成裂缝识别",
                        )
                    except Exception as exc:
                        error_text = f"总任务第 {step_index} 段裂缝识别失败：{exc}"
                        crack_errors.append(error_text)
                        set_state(error=error_text)

            for pause_scan_index in range(1, params["outbound_d0_pause_attempts"] + 1):
                set_state(
                    phase="outbound_d0_pause",
                    message=(
                        f"去程减速扫描 D0：第 {step_index}/{params['max_steps']} 段，"
                        f"第 {pause_scan_index}/{params['outbound_d0_pause_attempts']} 次"
                    ),
                    ai_result=combine_return_qr_d0_result(
                        last_qr_result_data,
                        last_d0_result_data,
                        last_crack_result,
                    ),
                )
                drone.recognize_target(params["target"])
                time.sleep(params["scan_interval_sec"])

            result = None
            for scan_index in range(1, params["scan_attempts"] + 1):
                set_state(
                    phase="scanning_return_qr",
                    message=(
                        f"识别 7 号返航码：第 {step_index}/{params['max_steps']} 段，"
                        f"第 {scan_index}/{params['scan_attempts']} 次"
                    ),
                )
                result = drone.detect_qr(params["return_qr_id"], VisionMode.FRONT_CAMERA)
                qr_result_data = build_qr_result_data(result, params)
                if result.success:
                    last_qr_result_data = qr_result_data
                set_state(ai_result=combine_return_qr_d0_result(qr_result_data, last_d0_result_data))
                if result.success:
                    break
                time.sleep(params["scan_interval_sec"])

            if result is not None and result.success:
                found_qr = True
                return_steps = step_index
                current_qr_result = result

                # QR poses can jump when the camera is still settling. Only allow
                # a small correction after several consistent observations.
                return_qr_confirmations = []
                set_state(
                    phase="confirming_return_qr",
                    message="已识别 7 号返航码，悬停 1.0 秒后进行两次位置确认",
                    ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                )
                time.sleep(params["return_qr_lock_sec"])
                for confirm_index in range(1, params["return_qr_confirmation_count"] + 1):
                    confirmed_result = drone.detect_qr(params["return_qr_id"], VisionMode.FRONT_CAMERA)
                    confirmed_data = build_qr_result_data(confirmed_result, params)
                    if confirmed_result.success:
                        last_qr_result_data = confirmed_data
                    set_state(
                        phase="confirming_return_qr",
                        message=f"7 号返航码位置确认 {confirm_index}/2",
                        ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                    )
                    if confirmed_result.success and confirmed_result.position is not None:
                        return_qr_confirmations.append((confirmed_result, confirmed_data))
                    if confirm_index < params["return_qr_confirmation_count"]:
                        time.sleep(params["return_qr_confirm_interval_sec"])

                # QR 7 is only the turnaround trigger. Keep the proven straight
                # return behavior: no position trim, yaw trim, or post-turn rescan.
                return_qr_position_alignment_enabled = False
                return_heading_correction_enabled = False
                return_qr_post_turn_verification_enabled = False

                alignment_attempts = 0
                if not return_qr_position_alignment_enabled:
                    set_state(
                        phase="returning",
                        message="已识别 7 号返航码，保持当前位置与航向准备原路返航",
                        ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                    )
                elif len(return_qr_confirmations) == params["return_qr_confirmation_count"]:
                    pose_delta_cm = pose_spread_cm([item[0] for item in return_qr_confirmations])
                    if pose_delta_cm <= params["return_qr_confirm_max_delta_cm"]:
                        current_qr_result, last_qr_result_data = return_qr_confirmations[-1]
                        alignment_attempts = 1
                    else:
                        set_state(
                            phase="returning",
                            message="7 号码两次位置差异较大，跳过位置校准，保持当前位置准备返航",
                            ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                        )
                else:
                    set_state(
                        phase="returning",
                        message="7 号码位置确认不足，跳过位置校准，保持当前位置准备返航",
                        ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                    )

                for align_index in range(1, alignment_attempts + 1):
                    qr_result_data = build_qr_result_data(current_qr_result, params)
                    last_qr_result_data = qr_result_data
                    set_state(ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data))

                    qr_error = qr_result_data.get("target_error") or {"x": 0, "y": 0}
                    qr_error_y = float(qr_error["y"])
                    if qr_result_data.get("aligned") or abs(qr_error_y) <= params["return_qr_align_tolerance_cm"]:
                        set_state(
                            phase="aligned_return_qr",
                            message="7号返航码前后距离已合适，准备转向返航",
                            ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                        )
                        break

                    position = current_qr_result.position
                    if position is None:
                        set_state(
                            phase="aligning_return_qr",
                            message="已识别7号返航码，但没有偏移数据，跳过7号对准",
                            ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                        )
                        break

                    direction = Direction.FORWARD if qr_error_y > 0 else Direction.BACK
                    direction_name = "前" if qr_error_y > 0 else "后"
                    offset = abs(qr_error_y)

                    correction_cm = int(
                        max(
                            params["return_qr_min_align_step_cm"],
                            min(
                                params["return_qr_safe_max_correction_cm"],
                                offset - params["return_qr_align_tolerance_cm"] / 2,
                            ),
                        )
                    )
                    set_state(
                        phase="aligning_return_qr",
                        message=f"7号返航码对准修正 {align_index}/{params['return_qr_max_align_steps']}：向{direction_name} {correction_cm} cm",
                        ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                    )
                    expect_success(
                        drone.move(direction, correction_cm, speed=VelocityLevel.SLOW),
                        "align return QR",
                    )
                    time.sleep(params["return_qr_align_settle_sec"])
                    update_drone_telemetry(drone, update_video=True)

                    set_state(phase="scanning_return_qr", message="7号返航码对准后重新识别")
                    current_qr_result = drone.detect_qr(params["return_qr_id"], VisionMode.FRONT_CAMERA)
                    current_qr_data = build_qr_result_data(current_qr_result, params)
                    if current_qr_result.success:
                        last_qr_result_data = current_qr_data
                    set_state(
                        ai_result=combine_return_qr_d0_result(
                            last_qr_result_data if last_qr_result_data is not None else current_qr_data,
                            last_d0_result_data,
                        )
                    )
                    if not current_qr_result.success:
                        set_state(
                            phase="aligning_return_qr",
                            message="7号返航码对准后暂时丢失，停止对准并开始返航",
                            ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                        )
                        break
                for fine_index in range(
                    1,
                    params["return_qr_fine_align_steps"] + 1
                    if return_qr_position_alignment_enabled
                    else 1,
                ):
                    set_state(
                        phase="scanning_return_qr",
                        message=f"7号返航码二次校准：重新识别 {fine_index}/{params['return_qr_fine_align_steps']}",
                        ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                    )
                    fine_result = drone.detect_qr(params["return_qr_id"], VisionMode.FRONT_CAMERA)
                    fine_qr_data = build_qr_result_data(fine_result, params)
                    if fine_result.success:
                        last_qr_result_data = fine_qr_data
                    set_state(ai_result=combine_return_qr_d0_result(last_qr_result_data or fine_qr_data, last_d0_result_data))

                    if not fine_result.success:
                        set_state(
                            phase="aligning_return_qr",
                            message="7号返航码二次校准未重新识别到，保留第一次校准结果并准备转向",
                            ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                        )
                        break

                    fine_error = fine_qr_data.get("target_error") or {"x": 0, "y": 0}
                    fine_error_y = float(fine_error["y"])
                    if fine_qr_data.get("aligned") or abs(fine_error_y) <= params["return_qr_align_tolerance_cm"]:
                        set_state(
                            phase="aligned_return_qr",
                            message="7号返航码二次前后校准完成，准备转向返航",
                            ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                        )
                        break

                    if fine_result.position is None:
                        set_state(
                            phase="aligning_return_qr",
                            message="7号返航码二次校准识别成功，但没有偏移数据，准备转向返航",
                            ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                        )
                        break

                    fine_direction = Direction.FORWARD if fine_error_y > 0 else Direction.BACK
                    fine_direction_name = "前" if fine_error_y > 0 else "后"
                    fine_offset = abs(fine_error_y)

                    fine_correction_cm = int(
                        max(
                            params["return_qr_min_align_step_cm"],
                            min(
                                params["return_qr_fine_max_align_step_cm"],
                                fine_offset - params["return_qr_align_tolerance_cm"] / 2,
                            ),
                        )
                    )
                    set_state(
                        phase="aligning_return_qr",
                        message=f"7号返航码二次校准 {fine_index}/{params['return_qr_fine_align_steps']}：向{fine_direction_name} {fine_correction_cm} cm",
                        ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                    )
                    expect_success(
                        drone.move(fine_direction, fine_correction_cm, speed=VelocityLevel.SLOW),
                        "fine align return QR",
                    )
                    time.sleep(params["return_qr_align_settle_sec"])
                    update_drone_telemetry(drone, update_video=True)

                set_state(
                    phase="settling_before_return_turn",
                    message=(
                        f"7号校准完成，悬停稳定 {params['return_turn_stabilize_sec']:.1f} 秒"
                        "后转向返航"
                    ),
                    ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                )
                time.sleep(params["return_turn_stabilize_sec"])
                try:
                    update_drone_telemetry(drone, update_video=True)
                except Exception as telemetry_exc:
                    set_state(error=f"转向前遥测更新异常，继续执行返航：{telemetry_exc}")

                set_state(
                    phase="turning_back",
                    message=f"已识别 7 号返航码，转向 {params['turn_angle_deg']}° 后按 {return_steps} 段返航",
                    ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                )
                yaw_before_turn = get_yaw_safe(drone)
                try:
                    turn_result = drone.rotate(params["turn_angle_deg"])
                    if turn_result != CommandResult.SUCCESS:
                        set_state(error=f"返航转向命令返回 {turn_result.name}，继续按分段返航")
                except Exception as turn_exc:
                    set_state(error=f"返航转向命令返回异常，继续按分段返航：{turn_exc}")
                time.sleep(params["settle_sec"])
                try:
                    update_drone_telemetry(drone, update_video=True)
                except Exception as telemetry_exc:
                    set_state(error=f"返航转向后遥测更新异常，继续按分段返航：{telemetry_exc}")
                if return_heading_correction_enabled:
                    correct_yaw_after_turn(
                        drone,
                        yaw_before_turn,
                        params["turn_angle_deg"],
                        tolerance_deg=params["yaw_correction_tolerance_deg"],
                        max_correction_deg=params["yaw_correction_max_deg"],
                    )
                set_state(
                    phase="settling_after_return_turn",
                    message="转向完成，停稳后按原路径返航",
                    ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                )
                time.sleep(params["return_post_turn_stabilize_sec"])
                try:
                    update_drone_telemetry(drone, update_video=True)
                except Exception as telemetry_exc:
                    set_state(error=f"返航转向后遥测更新异常，继续按分段返航：{telemetry_exc}")

                post_turn_qr_result = None
                post_turn_scan_attempts = (
                    params["return_qr_post_turn_scan_attempts"]
                    if return_qr_post_turn_verification_enabled
                    else 0
                )
                for post_turn_scan_index in range(1, post_turn_scan_attempts + 1):
                    set_state(
                        phase="verifying_return_qr_after_turn",
                        message=(
                            f"转向后复核 7 号返航码：第 {post_turn_scan_index}/"
                            f"{params['return_qr_post_turn_scan_attempts']} 次"
                        ),
                        ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                    )
                    post_turn_qr_result = drone.detect_qr(params["return_qr_id"], VisionMode.FRONT_CAMERA)
                    post_turn_qr_data = build_post_turn_qr_result_data(post_turn_qr_result, params)
                    if post_turn_qr_result.success:
                        last_qr_result_data = post_turn_qr_data
                    set_state(
                        ai_result=combine_return_qr_d0_result(
                            last_qr_result_data if last_qr_result_data is not None else post_turn_qr_data,
                            last_d0_result_data,
                        )
                    )
                    if post_turn_qr_result.success:
                        break
                    time.sleep(params["scan_interval_sec"])

                allow_post_turn_translation = False
                if (
                    allow_post_turn_translation
                    and post_turn_qr_result is not None
                    and post_turn_qr_result.success
                ):
                    post_turn_qr_data = build_post_turn_qr_result_data(post_turn_qr_result, params)
                    post_turn_error = post_turn_qr_data.get("target_error") or {"x": 0, "y": 0}
                    post_turn_error_x = float(post_turn_error["x"])
                    post_turn_error_y = float(post_turn_error["y"])
                    if not post_turn_qr_data.get("aligned") and post_turn_qr_result.position is not None:
                        if abs(post_turn_error_x) >= abs(post_turn_error_y):
                            post_turn_direction = Direction.RIGHT if post_turn_error_x > 0 else Direction.LEFT
                            post_turn_direction_name = "右" if post_turn_error_x > 0 else "左"
                            post_turn_offset = abs(post_turn_error_x)
                        else:
                            post_turn_direction = Direction.FORWARD if post_turn_error_y > 0 else Direction.BACK
                            post_turn_direction_name = "前" if post_turn_error_y > 0 else "后"
                            post_turn_offset = abs(post_turn_error_y)
                        post_turn_correction_cm = int(
                            max(
                                params["return_qr_min_align_step_cm"],
                                min(params["return_qr_post_turn_max_step_cm"], post_turn_offset),
                            )
                        )
                        set_state(
                            phase="aligning_return_qr_after_turn",
                            message=(
                                f"转向后 7 号码复核偏移，执行一次受限校正："
                                f"向{post_turn_direction_name} {post_turn_correction_cm} cm"
                            ),
                            ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                        )
                        expect_success(
                            drone.move(post_turn_direction, post_turn_correction_cm, speed=VelocityLevel.SLOW),
                            "post-turn return QR alignment",
                        )
                        time.sleep(params["return_qr_align_settle_sec"])
                        update_drone_telemetry(drone, update_video=True)
                else:
                    set_state(
                        phase="returning",
                        message="转向后未复核到 7 号返航码，保持当前姿态直线返航",
                        ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                    )

                if post_turn_qr_result is not None and post_turn_qr_result.success:
                    set_state(
                        phase="returning",
                        message="转向后已复核 7 号码，不做位置移动，保持当前航向直线分段返航",
                        ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                    )

                d0_result = None
                # First return using exactly the number of segments flown to QR 7.
                # Keep this leg straight. Only scan the landing QR on the last
                # three original return segments; do not make any correction until
                # a landing QR has actually been found.
                final_return_scan_segments = 3
                final_return_scan_attempts = 5
                final_return_scan_start = max(1, return_steps - final_return_scan_segments + 1)
                for return_index in range(1, return_steps + 1):
                    set_state(
                        phase="returning",
                        message=f"总任务返航测试：第 {return_index}/{return_steps} 段返航",
                        ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                    )
                    expect_success(
                        drone.move(Direction.FORWARD, params["step_cm"], speed=VelocityLevel.SLOW),
                        "return forward",
                    )
                    time.sleep(params["return_settle_sec"])
                    update_drone_telemetry(drone, update_video=True)

                    if return_index < final_return_scan_start:
                        continue

                    for d0_scan_index in range(1, final_return_scan_attempts + 1):
                        set_state(
                            phase="scanning_home_d0",
                            message=(
                                f"返程末段识别 {params['landing_qr_id']} 号降落码："
                                f"第 {return_index}/{return_steps} 段，第 "
                                f"{d0_scan_index}/{final_return_scan_attempts} 次"
                            ),
                            ai_result=combine_return_qr_d0_result(
                                last_qr_result_data,
                                last_d0_result_data,
                            ),
                        )
                        d0_result = drone.detect_qr(params["landing_qr_id"], VisionMode.FRONT_CAMERA)
                        d0_result_data = build_landing_qr_result_data(d0_result, params)
                        if d0_result.success:
                            found_d0 = True
                            last_d0_result_data = d0_result_data
                        set_state(
                            ai_result=combine_return_qr_d0_result(
                                last_qr_result_data,
                                last_d0_result_data or d0_result_data,
                            )
                        )
                        if d0_result.success:
                            break
                        if d0_scan_index < final_return_scan_attempts:
                            time.sleep(params["scan_interval_sec"])

                    if found_d0:
                        break

                # If the nominal return distance is a little short in the real
                # environment, extend it by at most five normal-sized segments.
                # A D0 hit stops the extension immediately.
                for extra_index in range(1, params["return_d0_search_segments"] + 1):
                    if found_d0:
                        break
                    extra_cm = params["return_search_step_cm"]
                    set_state(
                        phase="returning",
                        message=(
                            f"返程补搜 D0：第 {extra_index}/"
                            f"{params['return_d0_search_segments']} 段，向前 {extra_cm} cm"
                        ),
                        ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                    )
                    expect_success(
                        drone.move(Direction.FORWARD, int(extra_cm), speed=VelocityLevel.SLOW),
                        "return D0 search forward",
                    )
                    time.sleep(params["return_settle_sec"])
                    update_drone_telemetry(drone, update_video=True)
                    for d0_scan_index in range(1, params["return_d0_scan_each_segment_attempts"] + 1):
                        set_state(
                            phase="scanning_home_d0",
                            message=(
                                f"返程补搜 {params['landing_qr_id']} 号降落码：第 {extra_index}/"
                                f"{params['return_d0_search_segments']} 段，第 {d0_scan_index}/"
                                f"{params['return_d0_scan_each_segment_attempts']} 次"
                            ),
                            ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                        )
                        d0_result = drone.detect_qr(params["landing_qr_id"], VisionMode.FRONT_CAMERA)
                        d0_result_data = build_landing_qr_result_data(d0_result, params)
                        if d0_result.success:
                            found_d0 = True
                            last_d0_result_data = d0_result_data
                        set_state(ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data or d0_result_data))
                        if d0_result.success:
                            break
                        time.sleep(params["scan_interval_sec"])

                if d0_result is not None and d0_result.success:
                    found_d0 = True
                    current_result = d0_result
                    landing_alignment_limit = 0
                    landing_confirmations = []
                    set_state(
                        phase="confirming_home_landing_qr",
                        message="Landing marker found; holding before stable position confirmation",
                        ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                    )
                    time.sleep(params["landing_qr_lock_sec"])
                    for confirm_index in range(1, params["landing_qr_confirmation_count"] + 1):
                        confirmed_result = drone.detect_qr(params["landing_qr_id"], VisionMode.FRONT_CAMERA)
                        confirmed_data = build_landing_qr_result_data(confirmed_result, params)
                        if confirmed_result.success:
                            last_d0_result_data = confirmed_data
                        set_state(
                            phase="confirming_home_landing_qr",
                            message=(
                                f"Landing marker position confirmation {confirm_index}/"
                                f"{params['landing_qr_confirmation_count']}"
                            ),
                            ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                        )
                        if confirmed_result.success and confirmed_result.position is not None:
                            landing_confirmations.append((confirmed_result, confirmed_data))
                        if confirm_index < params["landing_qr_confirmation_count"]:
                            time.sleep(params["landing_qr_confirm_interval_sec"])

                    if len(landing_confirmations) == params["landing_qr_confirmation_count"]:
                        pose_delta_cm = pose_spread_cm([item[0] for item in landing_confirmations])
                        if pose_delta_cm is not None and pose_delta_cm <= params["landing_qr_confirm_max_delta_cm"]:
                            current_result, last_d0_result_data = landing_confirmations[-1]
                            landing_alignment_limit = min(2, params["max_align_steps"])
                        else:
                            set_state(
                                phase="landing_marker_unstable",
                                message="Landing marker position changed too much; skip correction and land safely",
                                ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                            )
                    else:
                        set_state(
                            phase="landing_marker_unstable",
                            message="Landing marker confirmation was incomplete; skip correction and land safely",
                            ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                        )

                    for align_index in range(1, landing_alignment_limit + 1):
                        position = current_result.position
                        if position is None:
                            set_state(
                                phase="aligning_home_d0",
                                message=f"返航后已识别 {params['landing_qr_id']} 号降落码，但没有偏移数据，执行当前位置盲降",
                                ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                            )
                            break

                        d0_result_data = build_landing_qr_result_data(current_result, params)
                        last_d0_result_data = d0_result_data
                        set_state(ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data))

                        if d0_result_data.get("aligned"):
                            aligned_d0 = True
                            set_state(
                                phase="aligned_home_d0",
                                message=f"返航后 {params['landing_qr_id']} 号降落码已到目标点，准备盲降",
                                ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                            )
                            break

                        error = d0_result_data.get("target_error") or {"x": 0, "y": 0}
                        error_x = float(error["x"])
                        error_y = float(error["y"])
                        if abs(error_x) >= abs(error_y):
                            direction = Direction.RIGHT if error_x > 0 else Direction.LEFT
                            direction_name = "右" if error_x > 0 else "左"
                            offset = abs(error_x)
                        else:
                            direction = Direction.FORWARD if error_y > 0 else Direction.BACK
                            direction_name = "前" if error_y > 0 else "后"
                            offset = abs(error_y)

                        correction_cm = int(
                            max(
                                params["min_align_step_cm"],
                                min(
                                    params["max_align_step_cm"],
                                    params["landing_safe_max_correction_cm"],
                                    offset - params["align_tolerance_cm"] / 2,
                                ),
                            )
                        )
                        set_state(
                            phase="aligning_home_d0",
                            message=(
                                f"返航 {params['landing_qr_id']} 号降落码对准修正 "
                                f"{align_index}/{params['max_align_steps']}：向{direction_name} {correction_cm} cm"
                            ),
                            ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                        )
                        expect_success(
                            drone.move(direction, correction_cm, speed=VelocityLevel.SLOW),
                            "align return D0",
                        )
                        time.sleep(max(1.0, params["align_settle_sec"]))
                        update_drone_telemetry(drone, update_video=True)

                        set_state(
                            phase="scanning_home_d0",
                            message=f"返航 {params['landing_qr_id']} 号降落码对准后重新识别",
                        )
                        current_result = drone.detect_qr(params["landing_qr_id"], VisionMode.FRONT_CAMERA)
                        current_result_data = build_landing_qr_result_data(current_result, params)
                        if current_result.success:
                            last_d0_result_data = current_result_data
                        set_state(
                            ai_result=combine_return_qr_d0_result(
                                last_qr_result_data,
                                last_d0_result_data if last_d0_result_data is not None else current_result_data,
                            )
                        )
                        if not current_result.success:
                            set_state(
                                phase="aligning_home_d0",
                                message="返航后已经识别过 D0，修正后暂时丢失，停止修正并执行当前位置盲降",
                                ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                            )
                            break

                if found_d0 and params.get("home_turn_after_d0_deg"):
                    found_d0 = False
                    aligned_d0 = False
                    set_state(
                        phase="turning_home_direction",
                        message=(
                            f"返航后{'已识别 D0，先校准后' if last_d0_result_data else '未识别到 D0，先'}"
                            f"转向 {params['home_turn_after_d0_deg']}° 让机头回到原始方向"
                        ),
                        ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                    )
                    expect_success(drone.rotate(params["home_turn_after_d0_deg"]), "turn home direction before landing")
                    time.sleep(params["home_turn_settle_sec"])
                    update_drone_telemetry(drone, update_video=True)

                    post_turn_result = None
                    for d0_scan_index in range(1, params["post_turn_d0_scan_attempts"] + 1):
                        set_state(
                            phase="scanning_home_d0_after_turn",
                            message=(
                                f"机头回正后重新搜索 D0：第 {d0_scan_index}/"
                                f"{params['post_turn_d0_scan_attempts']} 次"
                            ),
                            ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                        )
                        post_turn_result = drone.recognize_target(params["target"])
                        post_turn_result_data = build_d0_result_data(post_turn_result, params)
                        if post_turn_result.success:
                            last_d0_result_data = post_turn_result_data
                        set_state(
                            ai_result=combine_return_qr_d0_result(
                                last_qr_result_data,
                                last_d0_result_data if last_d0_result_data is not None else post_turn_result_data,
                            )
                        )
                        if post_turn_result.success:
                            break
                        time.sleep(params["scan_interval_sec"])

                    if post_turn_result is not None and post_turn_result.success:
                        found_d0 = True
                        current_result = post_turn_result
                        for align_index in range(1, params["max_align_steps"] + 1):
                            position = current_result.position
                            if position is None:
                                set_state(
                                    phase="aligning_home_d0_after_turn",
                                    message="机头回正后已识别 D0，但没有偏移数据，执行当前位置盲降",
                                    ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                                )
                                break

                            d0_result_data = build_d0_result_data(current_result, params)
                            last_d0_result_data = d0_result_data
                            set_state(ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data))

                            if d0_result_data.get("aligned"):
                                aligned_d0 = True
                                set_state(
                                    phase="aligned_home_d0_after_turn",
                                    message="机头回正后 D0 已到目标点，准备盲降",
                                    ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                                )
                                break

                            error = d0_result_data.get("target_error") or {"x": 0, "y": 0}
                            error_x = float(error["x"])
                            error_y = float(error["y"])
                            if abs(error_x) >= abs(error_y):
                                direction = Direction.RIGHT if error_x > 0 else Direction.LEFT
                                direction_name = "右" if error_x > 0 else "左"
                                offset = abs(error_x)
                            else:
                                direction = Direction.FORWARD if error_y > 0 else Direction.BACK
                                direction_name = "前" if error_y > 0 else "后"
                                offset = abs(error_y)

                            correction_cm = int(
                                max(
                                    params["min_align_step_cm"],
                                    min(params["max_align_step_cm"], offset - params["align_tolerance_cm"] / 2),
                                )
                            )
                            set_state(
                                phase="aligning_home_d0_after_turn",
                                message=(
                                    f"机头回正后 D0 对准修正 {align_index}/"
                                    f"{params['max_align_steps']}：向{direction_name} {correction_cm} cm"
                                ),
                                ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                            )
                            expect_success(
                                drone.move(direction, correction_cm, speed=VelocityLevel.SLOW),
                                "align return D0 after home turn",
                            )
                            time.sleep(params["align_settle_sec"])
                            update_drone_telemetry(drone, update_video=True)

                            set_state(phase="scanning_home_d0_after_turn", message="机头回正后对准完毕，重新识别 D0")
                            current_result = drone.recognize_target(params["target"])
                            current_result_data = build_d0_result_data(current_result, params)
                            if current_result.success:
                                last_d0_result_data = current_result_data
                            set_state(
                                ai_result=combine_return_qr_d0_result(
                                    last_qr_result_data,
                                    last_d0_result_data if last_d0_result_data is not None else current_result_data,
                                )
                            )
                            if not current_result.success:
                                set_state(
                                    phase="aligning_home_d0_after_turn",
                                    message="机头回正后已经识别过 D0，修正后暂时丢失，停止修正并执行当前位置盲降",
                                    ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                                )
                                break
                    else:
                        set_state(
                            phase="scanning_home_d0_after_turn",
                            message="机头回正后仍未识别到 D0，执行安全降落",
                            ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
                        )
                break

        if not found_qr:
            set_state(phase="landing", message="未识别到 7 号返航码，执行安全降落")
        elif not found_d0:
            set_state(
                phase="landing",
                message=f"已返航但未识别到起点 {params['landing_qr_id']} 号降落码，执行安全降落",
                ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
            )
        else:
            set_state(
                phase="landing",
                message=(
                    f"返航后 {params['landing_qr_id']} 号降落码已对准，执行盲降"
                    if aligned_d0
                    else f"返航后已识别 {params['landing_qr_id']} 号降落码，执行当前位置盲降"
                ),
                ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
            )
        expect_success(drone.land(), "land")
        update_drone_telemetry(drone, update_video=True)

        mission_complete = found_qr and found_d0
        crack_complete = True
        final_success = mission_complete and crack_complete
        if final_success:
            final_message = (
                f"总任务完成：已识别 7 号，返航 {return_steps} 段，"
                f"找到 {params['landing_qr_id']} 号降落码并降落"
            )
            final_error = None
        elif mission_complete and crack_errors:
            final_message = "总任务飞行完成，但裂缝识别未完成：" + "；".join(crack_errors)
            final_error = "裂缝识别未完成：" + "；".join(crack_errors)
        elif mission_complete:
            final_message = (
                f"总任务飞行完成：已识别 7 号，返航 {return_steps} 段，"
                f"找到 {params['landing_qr_id']} 号降落码并降落；"
                f"裂缝识别未完成（拍照 {photo_count} 张，识别 {crack_count} 张）"
            )
            final_error = f"裂缝识别未完成（拍照 {photo_count} 张，识别 {crack_count} 张）"
        elif found_qr:
            final_message = (
                f"总任务结束：已识别 7 号并返航 {return_steps} 段，"
                f"但未找到起点 {params['landing_qr_id']} 号降落码，已安全降落"
            )
            final_error = f"返航后未识别到起点 {params['landing_qr_id']} 号降落码"
        else:
            final_message = "总任务结束：未识别到 7 号，已安全降落"
            final_error = "未识别到 7 号返航码"
        set_state(
            running=False,
            phase="completed" if final_success else "crack_failed" if mission_complete else "not_found",
            message=final_message,
            success=final_success,
            error=final_error,
            ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data, last_crack_result),
            finished_at=now_iso(),
        )
    except BaseException as exc:
        exc_text = f"{type(exc).__name__}: {exc}"
        if found_d0:
            fail_message = f"总任务中断：{params['landing_qr_id']} 号降落码已识别，但后续校准或降落阶段异常，已尝试降落"
        elif found_qr:
            fail_message = (
                f"总任务中断：7号返航码已识别，但返航或 "
                f"{params['landing_qr_id']} 号降落码阶段异常，已尝试降落"
            )
        else:
            fail_message = "总任务中断：尚未完成 7号返航码识别，已尝试降落"
        set_state(phase="landing", message="总任务测试异常，尝试安全降落", error=exc_text)
        with video_lock:
            video_error = exc_text
        try:
            drone.land(blocking=False)
        except Exception:
            pass
        set_state(
            running=False,
            phase="failed",
            message=fail_message,
            success=False,
            error=exc_text,
            ai_result=combine_return_qr_d0_result(last_qr_result_data, last_d0_result_data),
            finished_at=now_iso(),
        )
        set_state(message=fail_message, error=exc_text)
    finally:
        telemetry_stop.set()
        if telemetry_thread is not None:
            telemetry_thread.join(timeout=1.0)
        with video_lock:
            try:
                stop_video_stream_unlocked()
            except Exception as exc:
                video_error = str(exc)
        active_drone = None


def cruise_crack_mission_worker(params: dict[str, Any]) -> None:
    global active_drone, video_drone, video_stream, video_streamer
    global video_started_at, video_error, video_battery

    drone = DroneAPI(
        enable_logging=False,
        enable_file_logging=False,
        enable_command_logging=False,
    )
    telemetry_stop = threading.Event()
    telemetry_thread: threading.Thread | None = None
    photo_count = 0
    crack_count = 0
    crack_errors: list[str] = []
    last_crack_result: dict[str, Any] | None = None
    found_d0 = False
    aligned_d0 = False
    last_d0_result_data: dict[str, Any] | None = None

    try:
        active_drone = drone
        set_state(phase="connecting", message="正在连接无人机并启动视频")
        if not drone.robust_connect(params["ip"], verbose=False):
            raise RuntimeError("无人机连接失败")

        capture_coordinate_origin(drone, update_video=True)
        battery = wait_for_battery(drone)
        set_state(battery=battery)
        if battery < params["min_battery"]:
            raise RuntimeError(f"电量过低：{battery}%")

        from pyhulax.video import MJPEGStreamer

        streamer = MJPEGStreamer(
            quality=params["quality"],
            max_fps=params["max_fps"],
            draw_detections=False,
        )
        expect_success(drone.set_video_stream(True), "set video stream")
        stream = drone.create_video_stream()
        stream.add_callback(streamer)
        stream.start()

        with video_lock:
            video_drone = drone
            video_stream = stream
            video_streamer = streamer
            video_started_at = now_iso()
            video_error = None
            video_battery = battery

        telemetry_thread = start_telemetry_polling(drone, telemetry_stop, update_video=True)
        update_drone_telemetry(drone, update_video=True)

        set_state(phase="preparing", message="视频已开启，准备执行巡航裂缝识别")
        drone.set_qr_localization(False)
        set_state(
            phase="camera_down",
            message=f"前置摄像头向下 {params['camera_angle']} 度，准备拍摄地面裂缝",
        )
        expect_success(
            drone.set_camera_angle(CameraPitchMode.DOWN_ABSOLUTE, params["camera_angle"]),
            "set camera angle down",
        )
        time.sleep(params["camera_settle_sec"])
        time.sleep(0.5)

        set_state(phase="takeoff", message=f"起飞到 {params['height_cm']} cm，准备巡航拍照")
        expect_success(drone.takeoff(height_cm=params["height_cm"]), "takeoff")

        for step_index in range(1, params["max_steps"] + 1):
            set_state(
                phase="moving",
                message=f"巡航前进：第 {step_index}/{params['max_steps']} 段",
            )
            expect_success(
                drone.move(
                    Direction.FORWARD,
                    params["step_cm"],
                    speed=VelocityLevel.MEDIUM,
                ),
                "move forward",
            )

            time.sleep(params["settle_sec"])
            update_drone_telemetry(drone, update_video=True)

            set_state(
                phase="auto_photo",
                message=f"自动拍照：第 {step_index}/{params['max_steps']} 段",
            )
            photo_result, photo_status = save_current_stream_frame()
            if not photo_result.get("success"):
                error_text = f"自动拍照失败：{photo_result.get('error', photo_status)}"
                crack_errors.append(error_text)
                set_state(error=error_text)
                continue

            photo_count += 1
            capture_metadata = capture_crack_photo_metadata(
                step_index=step_index,
                photo_count=photo_count,
                crack_analysis_count=crack_count + 1,
            )
            set_state(
                phase="crack_analysis",
                message=f"裂缝识别：第 {step_index}/{params['max_steps']} 张照片",
            )
            try:
                crack_result = analyze_photo_record_for_cracks(
                    photo_result["photo"],
                    params.get("crack_config"),
                    capture_metadata,
                )
                crack_count += 1
                crack_result["step_index"] = step_index
                crack_result["photo_count"] = photo_count
                crack_result["crack_analysis_count"] = crack_count
                last_crack_result = crack_result
                set_state(
                    ai_result=combine_crack_d0_result(last_crack_result, last_d0_result_data),
                    message=f"第 {step_index}/{params['max_steps']} 段：已拍照并完成裂缝识别",
                )
            except Exception as exc:
                error_text = f"第 {step_index} 段裂缝识别失败：{exc}"
                crack_errors.append(error_text)
                set_state(error=error_text)

            result = None
            for scan_index in range(1, params["scan_attempts"] + 1):
                set_state(
                    phase="scanning_d0",
                    message=(
                        f"裂缝识别后扫描 D0：第 {step_index}/{params['max_steps']} 段，"
                        f"第 {scan_index}/{params['scan_attempts']} 次"
                    ),
                )
                result = drone.recognize_target(params["target"])
                d0_result_data = build_d0_result_data(result, params)
                if result.success:
                    last_d0_result_data = d0_result_data
                set_state(ai_result=combine_crack_d0_result(last_crack_result, d0_result_data))
                if result.success:
                    break
                time.sleep(params["scan_interval_sec"])

            if result is not None and result.success:
                found_d0 = True
                current_result = result
                for align_index in range(1, params["max_align_steps"] + 1):
                    position = current_result.position
                    if position is None:
                        set_state(
                            phase="aligning_d0",
                            message="已识别 D0，但没有偏移数据，执行当前位置盲降",
                        )
                        break

                    d0_result_data = build_d0_result_data(current_result, params)
                    last_d0_result_data = d0_result_data
                    set_state(ai_result=combine_crack_d0_result(last_crack_result, last_d0_result_data))

                    if d0_result_data.get("aligned"):
                        aligned_d0 = True
                        set_state(phase="aligned_d0", message="D0 已到目标点，准备盲降")
                        break

                    error = d0_result_data.get("target_error") or {"x": 0, "y": 0}
                    error_x = float(error["x"])
                    error_y = float(error["y"])
                    if abs(error_x) >= abs(error_y):
                        direction = Direction.RIGHT if error_x > 0 else Direction.LEFT
                        direction_name = "右" if error_x > 0 else "左"
                        offset = abs(error_x)
                    else:
                        direction = Direction.FORWARD if error_y > 0 else Direction.BACK
                        direction_name = "前" if error_y > 0 else "后"
                        offset = abs(error_y)

                    correction_cm = int(
                        max(
                            params["min_align_step_cm"],
                            min(params["max_align_step_cm"], offset - params["align_tolerance_cm"] / 2),
                        )
                    )
                    set_state(
                        phase="aligning_d0",
                        message=(
                            f"D0 对准修正 {align_index}/{params['max_align_steps']}："
                            f"向{direction_name} {correction_cm} cm"
                        ),
                    )
                    expect_success(
                        drone.move(direction, correction_cm, speed=VelocityLevel.MEDIUM),
                        "align to D0",
                    )
                    time.sleep(params["align_settle_sec"])
                    update_drone_telemetry(drone, update_video=True)

                    set_state(phase="scanning_d0", message="对准后重新识别 D0")
                    current_result = drone.recognize_target(params["target"])
                    current_result_data = build_d0_result_data(current_result, params)
                    if current_result.success:
                        last_d0_result_data = current_result_data
                    set_state(
                        ai_result=combine_crack_d0_result(
                            last_crack_result,
                            last_d0_result_data if last_d0_result_data is not None else current_result_data,
                        )
                    )
                    if not current_result.success:
                        set_state(
                            phase="aligning_d0",
                            message="已经识别过 D0，修正后暂时丢失，停止修正并执行当前位置盲降",
                        )
                        break

                set_state(
                    phase="landing",
                    message="D0 已对准，执行盲降" if aligned_d0 else "已识别 D0，执行当前位置盲降",
                )
                break

        if not found_d0:
            set_state(phase="landing", message="巡航裂缝识别完成，未找到 D0，执行安全降落")
        expect_success(drone.land(), "land")
        update_drone_telemetry(drone, update_video=True)

        set_state(
            running=False,
            phase="completed" if found_d0 else "not_found",
            message=(
                f"巡航裂缝识别完成，拍照 {photo_count} 张，裂缝识别 {crack_count} 张，D0 已对准并降落"
                if aligned_d0
                else f"巡航裂缝识别完成，拍照 {photo_count} 张，裂缝识别 {crack_count} 张，D0 已识别并降落"
                if found_d0
                else f"巡航裂缝识别完成，拍照 {photo_count} 张，裂缝识别 {crack_count} 张，未找到 D0，已安全降落"
            ),
            success=found_d0 and photo_count > 0 and not crack_errors,
            error="；".join(crack_errors) if crack_errors else None,
            ai_result=combine_crack_d0_result(last_crack_result, last_d0_result_data),
            finished_at=now_iso(),
        )
    except BaseException as exc:
        set_state(phase="landing", message="异常，尝试安全降落", error=str(exc))
        with video_lock:
            video_error = str(exc)
        try:
            drone.land(blocking=False)
        except Exception:
            pass
        set_state(
            running=False,
            phase="failed",
            message="巡航裂缝识别失败，已尝试降落",
            success=False,
            error=str(exc),
            ai_result=combine_crack_d0_result(last_crack_result, last_d0_result_data),
            finished_at=now_iso(),
        )
    finally:
        telemetry_stop.set()
        if telemetry_thread is not None:
            telemetry_thread.join(timeout=1.0)
        with video_lock:
            try:
                stop_video_stream_unlocked()
            except Exception as exc:
                video_error = str(exc)
        active_drone = None


def downward_d0_blind_land_worker(params: dict[str, Any]) -> None:
    global active_drone, video_drone, video_stream, video_streamer
    global video_started_at, video_error, video_battery

    drone = DroneAPI(
        enable_logging=False,
        enable_file_logging=False,
        enable_command_logging=False,
    )
    found_d0 = False
    aligned_d0 = False
    last_success_result_data: dict[str, Any] | None = None
    telemetry_stop = threading.Event()
    telemetry_thread: threading.Thread | None = None

    try:
        active_drone = drone
        set_state(phase="connecting", message="正在连接无人机并启动下视视频测试")
        if not drone.robust_connect(params["ip"], verbose=False):
            raise RuntimeError("无人机连接失败")

        capture_coordinate_origin(drone, update_video=True)
        battery = wait_for_battery(drone)
        set_state(battery=battery)
        if battery < params["min_battery"]:
            raise RuntimeError(f"电量过低：{battery}%")

        from pyhulax.video import MJPEGStreamer

        streamer = MJPEGStreamer(
            quality=params["quality"],
            max_fps=params["max_fps"],
            draw_detections=False,
        )
        expect_success(drone.set_video_stream(True), "set video stream")
        stream = drone.create_video_stream()
        stream.add_callback(streamer)
        stream.start()

        with video_lock:
            video_drone = drone
            video_stream = stream
            video_streamer = streamer
            video_started_at = now_iso()
            video_error = None
            video_battery = battery

        telemetry_thread = start_telemetry_polling(drone, telemetry_stop, update_video=True)
        update_drone_telemetry(drone, update_video=True)

        set_state(
            phase="preparing",
            message=f"前置摄像头向下 {params['camera_angle']} 度，准备识别地面 D0",
        )
        drone.set_qr_localization(False)
        expect_success(
            drone.set_camera_angle(CameraPitchMode.DOWN_ABSOLUTE, params["camera_angle"]),
            "set camera angle down",
        )
        time.sleep(params["camera_settle_sec"])

        set_state(phase="takeoff", message=f"起飞到 {params['height_cm']} cm，下视搜索地面 D0")
        expect_success(drone.takeoff(height_cm=params["height_cm"]), "takeoff")

        for step_index in range(1, params["max_steps"] + 1):
            set_state(
                phase="moving",
                message=f"下视搜索 D0：向前第 {step_index}/{params['max_steps']} 段",
            )
            expect_success(
                drone.move(
                    Direction.FORWARD,
                    params["step_cm"],
                    speed=VelocityLevel.MEDIUM,
                ),
                "move forward",
            )

            time.sleep(params["settle_sec"])
            update_drone_telemetry(drone, update_video=True)

            if params.get("auto_photo"):
                set_state(
                    phase="auto_photo",
                    message=f"下视自动拍照：第 {step_index}/{params['max_steps']} 段",
                )
                photo_result, photo_status = save_current_stream_frame()
                if not photo_result.get("success"):
                    set_state(error=f"下视自动拍照失败：{photo_result.get('error', photo_status)}")

            result = None
            for scan_index in range(1, params["scan_attempts"] + 1):
                set_state(
                    phase="scanning",
                    message=(
                        f"下视识别地面 D0：第 {step_index}/{params['max_steps']} 段，"
                        f"第 {scan_index}/{params['scan_attempts']} 次"
                    ),
                )
                result = drone.recognize_target(params["target"])
                result_data = {
                    "success": result.success,
                    "target_type": result.target_type,
                    "position": result.position.model_dump() if result.position else None,
                    "angle": result.angle,
                }
                if result.success and result.position is not None:
                    target_x = float(params["align_target_x_cm"])
                    target_y = float(params["align_target_y_cm"])
                    error_x = float(result.position.x) - target_x
                    error_y = float(result.position.y) - target_y
                    result_data.update(
                        {
                            "target_position": {"x": target_x, "y": target_y},
                            "target_error": {"x": round(error_x, 1), "y": round(error_y, 1)},
                            "aligned": abs(error_x) <= params["align_tolerance_cm"]
                            and abs(error_y) <= params["align_tolerance_cm"],
                        }
                    )
                if result.success:
                    last_success_result_data = result_data
                set_state(ai_result=result_data)
                if result.success:
                    break
                time.sleep(params["scan_interval_sec"])

            if result is not None and result.success:
                found_d0 = True
                current_result = result
                for align_index in range(1, params["max_align_steps"] + 1):
                    position = current_result.position
                    if position is None:
                        set_state(
                            phase="aligning",
                            message="已识别地面 D0，但没有偏移数据，执行当前位置盲降",
                        )
                        break

                    offset_x = float(position.x)
                    offset_y = float(position.y)
                    target_x = float(params["align_target_x_cm"])
                    target_y = float(params["align_target_y_cm"])
                    error_x = offset_x - target_x
                    error_y = offset_y - target_y
                    result_data = {
                        "success": current_result.success,
                        "target_type": current_result.target_type,
                        "position": position.model_dump(),
                        "angle": current_result.angle,
                        "target_position": {"x": target_x, "y": target_y},
                        "target_error": {"x": round(error_x, 1), "y": round(error_y, 1)},
                        "aligned": abs(error_x) <= params["align_tolerance_cm"]
                        and abs(error_y) <= params["align_tolerance_cm"],
                    }
                    last_success_result_data = result_data
                    set_state(ai_result=result_data)

                    if result_data["aligned"]:
                        aligned_d0 = True
                        set_state(
                            phase="aligned",
                            message=(
                                f"D0 已到目标点：X={offset_x:.1f}/{target_x:.1f} cm, "
                                f"Y={offset_y:.1f}/{target_y:.1f} cm，准备盲降"
                            ),
                        )
                        break

                    if abs(error_x) >= abs(error_y):
                        direction = Direction.RIGHT if error_x > 0 else Direction.LEFT
                        direction_name = "右" if error_x > 0 else "左"
                        offset = abs(error_x)
                    else:
                        direction = Direction.FORWARD if error_y > 0 else Direction.BACK
                        direction_name = "前" if error_y > 0 else "后"
                        offset = abs(error_y)

                    correction_cm = int(
                        max(
                            params["min_align_step_cm"],
                            min(params["max_align_step_cm"], offset - params["align_tolerance_cm"] / 2),
                        )
                    )
                    set_state(
                        phase="aligning",
                        message=(
                            f"D0 对准修正 {align_index}/{params['max_align_steps']}："
                            f"向{direction_name} {correction_cm} cm"
                        ),
                    )
                    expect_success(
                        drone.move(direction, correction_cm, speed=VelocityLevel.MEDIUM),
                        "align to D0",
                    )
                    time.sleep(params["align_settle_sec"])
                    update_drone_telemetry(drone, update_video=True)

                    if params.get("auto_photo"):
                        photo_result, photo_status = save_current_stream_frame()
                        if not photo_result.get("success"):
                            set_state(error=f"D0 对准拍照失败：{photo_result.get('error', photo_status)}")

                    set_state(phase="scanning", message="对准后重新识别地面 D0")
                    current_result = drone.recognize_target(params["target"])
                    current_result_data = {
                        "success": current_result.success,
                        "target_type": current_result.target_type,
                        "position": current_result.position.model_dump()
                        if current_result.position
                        else None,
                        "angle": current_result.angle,
                        "aligned": False,
                    }
                    if current_result.success and current_result.position is not None:
                        target_x = float(params["align_target_x_cm"])
                        target_y = float(params["align_target_y_cm"])
                        error_x = float(current_result.position.x) - target_x
                        error_y = float(current_result.position.y) - target_y
                        current_result_data.update(
                            {
                                "target_position": {"x": target_x, "y": target_y},
                                "target_error": {"x": round(error_x, 1), "y": round(error_y, 1)},
                                "aligned": abs(error_x) <= params["align_tolerance_cm"]
                                and abs(error_y) <= params["align_tolerance_cm"],
                            }
                        )
                    if current_result.success:
                        last_success_result_data = current_result_data
                        set_state(ai_result=current_result_data)
                    elif last_success_result_data is not None:
                        set_state(ai_result=last_success_result_data)
                    else:
                        set_state(ai_result=current_result_data)
                    if not current_result.success:
                        set_state(
                            phase="aligning",
                            message="已经识别过 D0，修正后暂时丢失，停止修正并执行当前位置盲降",
                        )
                        break
                    if current_result.position is not None:
                        offset_x = float(current_result.position.x)
                        offset_y = float(current_result.position.y)
                        target_x = float(params["align_target_x_cm"])
                        target_y = float(params["align_target_y_cm"])
                        error_x = offset_x - target_x
                        error_y = offset_y - target_y
                        if (
                            abs(error_x) <= params["align_tolerance_cm"]
                            and abs(error_y) <= params["align_tolerance_cm"]
                        ):
                            aligned_d0 = True
                            set_state(
                                phase="aligned",
                                message=(
                                    f"D0 已到目标点：X={offset_x:.1f}/{target_x:.1f} cm, "
                                    f"Y={offset_y:.1f}/{target_y:.1f} cm，准备盲降"
                                ),
                                ai_result={
                                    "success": current_result.success,
                                    "target_type": current_result.target_type,
                                    "position": current_result.position.model_dump(),
                                    "angle": current_result.angle,
                                    "target_position": {"x": target_x, "y": target_y},
                                    "target_error": {"x": round(error_x, 1), "y": round(error_y, 1)},
                                    "aligned": True,
                                },
                            )
                            break

                set_state(
                    phase="landing",
                    message="D0 已对准，执行盲降" if aligned_d0 else "已识别地面 D0，未完全对准，执行当前位置盲降",
                )
                break

        if not found_d0:
            set_state(phase="landing", message="未识别到地面 D0，执行安全降落")

        expect_success(drone.land(), "land")
        update_drone_telemetry(drone, update_video=True)

        set_state(
            running=False,
            phase="completed" if found_d0 else "not_found",
            message=(
                "下视D0对准降落测试完成，已对准并降落"
                if aligned_d0
                else "下视D0对准降落测试完成，已识别并降落"
                if found_d0
                else "未找到地面D0，已安全降落"
            ),
            success=found_d0,
            ai_result=last_success_result_data if found_d0 else None,
            finished_at=now_iso(),
        )
    except BaseException as exc:
        set_state(phase="landing", message="异常，尝试安全降落", error=str(exc))
        with video_lock:
            video_error = str(exc)
        try:
            drone.land(blocking=False)
        except Exception:
            pass
        if found_d0:
            set_state(
                running=False,
                phase="completed",
                message="下视D0对准降落测试完成，已识别 D0 并尝试降落",
                success=True,
                error=str(exc),
                ai_result=last_success_result_data,
                finished_at=now_iso(),
            )
        else:
            set_state(
                running=False,
                phase="failed",
                message="下视D0对准降落测试失败，已尝试降落",
                success=False,
                error=str(exc),
                finished_at=now_iso(),
            )
    finally:
        telemetry_stop.set()
        if telemetry_thread is not None:
            telemetry_thread.join(timeout=1.0)
        try:
            drone.set_camera_angle(CameraPitchMode.UP_ABSOLUTE, 0)
        except Exception:
            pass
        with video_lock:
            try:
                stop_video_stream_unlocked()
            except Exception as exc:
                video_error = str(exc)
        active_drone = None


def height_test_worker(params: dict[str, Any]) -> None:
    global active_drone, video_drone, video_stream, video_streamer
    global video_started_at, video_error, video_battery

    drone = DroneAPI(
        enable_logging=False,
        enable_file_logging=False,
        enable_command_logging=False,
    )
    telemetry_stop = threading.Event()
    telemetry_thread: threading.Thread | None = None

    try:
        active_drone = drone
        set_state(phase="connecting", message="正在连接无人机并开启视频")
        if not drone.robust_connect(params["ip"], verbose=False):
            raise RuntimeError("无人机连接失败")

        capture_coordinate_origin(drone, update_video=True)
        battery = wait_for_battery(drone)
        set_state(battery=battery)
        if battery < params["min_battery"]:
            raise RuntimeError(f"电量过低：{battery}%")

        from pyhulax.video import MJPEGStreamer

        streamer = MJPEGStreamer(
            quality=params["quality"],
            max_fps=params["max_fps"],
            draw_detections=False,
        )
        expect_success(drone.set_video_stream(True), "set video stream")
        stream = drone.create_video_stream()
        stream.add_callback(streamer)
        stream.start()

        with video_lock:
            video_drone = drone
            video_stream = stream
            video_streamer = streamer
            video_started_at = now_iso()
            video_error = None
            video_battery = battery

        telemetry_thread = start_telemetry_polling(drone, telemetry_stop, update_video=True)
        update_drone_telemetry(drone, update_video=True)

        heights = params["heights_cm"]
        hover_sec = params["hover_sec"]

        set_state(phase="preparing", message="视频已开启，准备进行实时高度测试")
        drone.set_qr_localization(False)
        time.sleep(0.5)

        set_state(phase="takeoff", message=f"起飞到约 {heights[0]} cm")
        expect_success(drone.takeoff(height_cm=heights[0]), "takeoff")

        previous_height = heights[0]
        for index, target_height in enumerate(heights, start=1):
            if index > 1:
                climb_cm = max(5, int(target_height - previous_height))
                set_state(
                    phase="ascending",
                    message=f"上升到约 {target_height} cm（增加约 {climb_cm} cm）",
                )
                expect_success(
                    drone.move(
                        Direction.UP,
                        climb_cm,
                        speed=VelocityLevel.MEDIUM,
                    ),
                    "move up",
                )
                previous_height = target_height

            hover_deadline = time.monotonic() + hover_sec
            while time.monotonic() < hover_deadline:
                update_drone_telemetry(drone, update_video=True)
                set_state(
                    phase="hovering",
                    message=f"第 {index}/{len(heights)} 段悬停，目标约 {target_height} cm",
                )
                time.sleep(0.5)

        set_state(phase="landing", message="实时高度测试完成，正在降落")
        expect_success(drone.land(), "land")
        update_drone_telemetry(drone, update_video=True)

        set_state(
            running=False,
            phase="completed",
            message="实时高度测试完成，已降落",
            success=True,
            finished_at=now_iso(),
        )
    except BaseException as exc:
        set_state(phase="landing", message="异常，尝试安全降落", error=str(exc))
        with video_lock:
            video_error = str(exc)
        try:
            drone.land(blocking=False)
        except Exception:
            pass
        set_state(
            running=False,
            phase="failed",
            message="实时高度测试失败，已尝试降落",
            success=False,
            error=str(exc),
            finished_at=now_iso(),
        )
    finally:
        telemetry_stop.set()
        if telemetry_thread is not None:
            telemetry_thread.join(timeout=1.0)
        with video_lock:
            try:
                stop_video_stream_unlocked()
            except Exception as exc:
                video_error = str(exc)
        active_drone = None


def d0_distance_test_worker(params: dict[str, Any]) -> None:
    global active_drone, video_drone, video_stream, video_streamer
    global video_started_at, video_error, video_battery

    drone = DroneAPI(
        enable_logging=False,
        enable_file_logging=False,
        enable_command_logging=False,
    )
    telemetry_stop = threading.Event()
    telemetry_thread: threading.Thread | None = None
    found_d0 = False
    scan_count = 0

    try:
        active_drone = drone
        set_state(phase="connecting", message="正在连接无人机并开启视频")
        if not drone.robust_connect(params["ip"], verbose=False):
            raise RuntimeError("无人机连接失败")

        capture_coordinate_origin(drone, update_video=True)
        battery = wait_for_battery(drone)
        set_state(battery=battery)
        if battery < params["min_battery"]:
            raise RuntimeError(f"电量过低：{battery}%")

        from pyhulax.video import MJPEGStreamer

        streamer = MJPEGStreamer(
            quality=params["quality"],
            max_fps=params["max_fps"],
            draw_detections=False,
        )
        expect_success(drone.set_video_stream(True), "set video stream")
        stream = drone.create_video_stream()
        stream.add_callback(streamer)
        stream.start()

        with video_lock:
            video_drone = drone
            video_stream = stream
            video_streamer = streamer
            video_started_at = now_iso()
            video_error = None
            video_battery = battery

        telemetry_thread = start_telemetry_polling(drone, telemetry_stop, update_video=True)
        update_drone_telemetry(drone, update_video=True)

        set_state(phase="preparing", message="视频已开启，准备进行 D0 识别距离测试")
        drone.set_qr_localization(False)
        time.sleep(0.5)

        set_state(phase="takeoff", message=f"起飞到约 {params['height_cm']} cm 后悬停识别 D0")
        expect_success(drone.takeoff(height_cm=params["height_cm"]), "takeoff")

        deadline = time.monotonic() + params["scan_sec"]
        while time.monotonic() < deadline:
            scan_count += 1
            set_state(phase="scanning", message=f"D0 识别距离测试中：第 {scan_count} 次")
            result = drone.recognize_target(params["target"])
            result_data = {
                "success": result.success,
                "target_type": result.target_type,
                "position": result.position.model_dump() if result.position else None,
                "angle": result.angle,
                "scan_count": scan_count,
                "updated_at": now_iso(),
            }
            set_state(ai_result=result_data)

            if result.success:
                found_d0 = True
                pos = result.position
                if pos is not None:
                    set_state(
                        message=(
                            f"D0 已识别：x={pos.x:.1f} cm, "
                            f"y={pos.y:.1f} cm, z={pos.z:.1f} cm, "
                            f"angle={result.angle if result.angle is not None else '-'}"
                        )
                    )
                else:
                    set_state(message="D0 已识别，位置为空")
            else:
                set_state(message="D0 未识别，继续悬停扫描")

            update_drone_telemetry(drone, update_video=True)
            time.sleep(params["recognize_interval_sec"])

        set_state(phase="landing", message="D0 识别距离测试完成，正在降落")
        expect_success(drone.land(), "land")
        update_drone_telemetry(drone, update_video=True)

        set_state(
            running=False,
            phase="completed" if found_d0 else "not_found",
            message="D0 识别距离测试完成，已降落" if found_d0 else "测试结束，未识别到 D0，已降落",
            success=found_d0,
            finished_at=now_iso(),
        )
    except BaseException as exc:
        set_state(phase="landing", message="异常，尝试安全降落", error=str(exc))
        with video_lock:
            video_error = str(exc)
        try:
            drone.land(blocking=False)
        except Exception:
            pass
        set_state(
            running=False,
            phase="failed",
            message="D0 识别距离测试失败，已尝试降落",
            success=False,
            error=str(exc),
            finished_at=now_iso(),
        )
    finally:
        telemetry_stop.set()
        if telemetry_thread is not None:
            telemetry_thread.join(timeout=1.0)
        with video_lock:
            try:
                stop_video_stream_unlocked()
            except Exception as exc:
                video_error = str(exc)
        active_drone = None


@app.route("/api/drone/status", methods=["GET"])
def status():
    return jsonify(get_state_dict())


@app.route("/api/drone/preflight", methods=["GET"])
def drone_preflight():
    drone_ip = str(request.args.get("ip", DEFAULT_DRONE_IP))
    return jsonify(get_drone_preflight(drone_ip))


@app.route("/api/drone/video-status", methods=["GET"])
def video_status():
    return jsonify(get_video_state_dict())


@app.route("/api/drone/start-video", methods=["POST", "OPTIONS"])
def start_video():
    global video_drone, video_stream, video_streamer
    global video_started_at, video_error, video_battery
    global video_coordinate_origin, video_position_xyz, video_coordinate_updated_at

    if request.method == "OPTIONS":
        return ("", 204)

    with state_lock:
        if mission_state.running:
            return jsonify({"error": "飞行任务运行中，请先等待任务结束。"}), 400

    payload = request.get_json(silent=True) or {}
    ip = str(payload.get("ip", DEFAULT_DRONE_IP))
    min_battery = int(payload.get("min_battery", 20))
    quality = int(payload.get("quality", 75))
    max_fps = float(payload.get("max_fps", 15.0))

    with video_lock:
        video_error = None
        if video_stream is not None:
            return jsonify(get_video_state_dict_unlocked())

        drone = DroneAPI(
            enable_logging=False,
            enable_file_logging=False,
            enable_command_logging=False,
        )
        try:
            if not drone.robust_connect(ip, verbose=False):
                raise RuntimeError("无人机连接失败")

            origin = capture_coordinate_origin(drone)
            battery = wait_for_battery(drone)
            if battery < min_battery:
                raise RuntimeError(f"电量过低：{battery}%")

            from pyhulax.video import MJPEGStreamer

            streamer = MJPEGStreamer(quality=quality, max_fps=max_fps, draw_detections=False)
            expect_success(drone.set_video_stream(True), "set video stream")
            stream = drone.create_video_stream()
            stream.add_callback(streamer)
            stream.start()

            video_drone = drone
            video_stream = stream
            video_streamer = streamer
            video_started_at = now_iso()
            video_error = None
            video_battery = battery
            video_coordinate_origin = origin
            video_position_xyz = {"x": 0.0, "y": 0.0, "z": 0.0}
            video_coordinate_updated_at = now_iso()
        except Exception as exc:
            video_error = str(exc)
            video_drone = None
            video_stream = None
            video_streamer = None
            video_started_at = None
            video_battery = None
            video_coordinate_origin = None
            video_position_xyz = None
            video_coordinate_updated_at = None
            try:
                drone.set_video_stream(False)
            except Exception:
                pass
            try:
                drone.disconnect()
            except Exception:
                pass
            return jsonify(get_video_state_dict_unlocked() | {"error": str(exc)}), 500

        return jsonify(get_video_state_dict_unlocked()), 202


@app.route("/api/drone/stop-video", methods=["POST", "OPTIONS"])
def stop_video():
    global video_error

    if request.method == "OPTIONS":
        return ("", 204)

    with video_lock:
        try:
            stop_video_stream_unlocked()
            video_error = None
        except Exception as exc:
            video_error = str(exc)
            return jsonify(get_video_state_dict_unlocked() | {"error": str(exc)}), 500
        return jsonify(get_video_state_dict_unlocked())


@app.route("/api/drone/video-feed", methods=["GET"])
def drone_video_feed():
    with video_lock:
        streamer = video_streamer

    if streamer is None:
        return Response("Video stream is not running.\n", status=503, mimetype="text/plain")

    return Response(
        streamer.generate(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/api/drone/frame.jpg", methods=["GET"])
def drone_video_frame():
    with video_lock:
        streamer = video_streamer

    if streamer is None:
        return Response(status=503)

    jpeg = streamer.get_frame()
    if jpeg is None:
        return Response(status=204)
    return Response(jpeg, mimetype="image/jpeg")


@app.route("/api/drone/photos/<path:filename>", methods=["GET"])
def drone_photo_file(filename: str):
    return send_from_directory(PHOTO_DIR, filename)


@app.route("/api/drone/photos", methods=["GET"])
def drone_photo_list():
    return jsonify({
        "photos": list_saved_photos(RECENT_PHOTOS_TO_SHOW),
        "max_saved_photos": MAX_SAVED_PHOTOS,
    })


@app.route("/api/crack/results/<path:filename>", methods=["GET"])
def crack_result_file(filename: str):
    return send_from_directory(CRACK_RESULT_DIR, filename)


@app.route("/api/crack/result.csv", methods=["GET"])
def crack_result_csv():
    return send_from_directory(CRACK_RESULT_DIR, "result.csv")


@app.route("/api/crack/analyze-latest-photo", methods=["POST", "OPTIONS"])
def analyze_latest_crack_photo():
    if request.method == "OPTIONS":
        return ("", 204)

    latest_photo = current_last_photo()
    if latest_photo is None:
        return jsonify({"success": False, "error": "没有可分析的无人机照片，请先拍照保存。"}), 404

    payload = request.get_json(silent=True) or {}
    try:
        config = CrackAnalysisConfig(
            area_threshold=int(payload.get("area_threshold", 350)),
            gray_threshold=int(payload.get("gray_threshold", 5)),
            scale_cm_per_px=float(payload.get("scale_cm_per_px", DEFAULT_CRACK_SCALE_CM_PER_PX)),
            kernel_size=int(payload.get("kernel_size", 3)),
            iterations=int(payload.get("iterations", 1)),
        )
        result = analyze_crack_image(Path(latest_photo["path"]), CRACK_RESULT_DIR, config=config)
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500

    result["input_photo"] = latest_photo
    result["output_url"] = f"/api/crack/results/{result['output_filename']}"
    result["csv_url"] = "/api/crack/result.csv"
    result["altitude_cm"] = video_altitude_cm if video_altitude_cm is not None else mission_state.altitude_cm
    result["scale_note"] = "当前使用固定比例尺；如需高精度，需要用 ToF 高度和标定板重新校准。"
    return jsonify(result)


def capture_drone_photo() -> tuple[dict[str, Any], int]:
    return save_current_stream_frame()


@app.route("/api/drone/save-frame", methods=["POST", "OPTIONS"])
def save_frame():
    if request.method == "OPTIONS":
        return ("", 204)

    with state_lock:
        if mission_state.running:
            return jsonify({"error": "飞行任务运行中，暂不拍照。"}), 400

    payload, status_code = capture_drone_photo()
    return jsonify(payload), status_code


@app.route("/api/drone/take-photo", methods=["POST", "OPTIONS"])
def take_photo():
    if request.method == "OPTIONS":
        return ("", 204)

    with state_lock:
        if mission_state.running:
            return jsonify({"error": "飞行任务运行中，暂不拍照。"}), 400

    payload, status_code = capture_drone_photo()
    return jsonify(payload), status_code


@app.route("/api/drone/start-task", methods=["POST", "OPTIONS"])
def start_task():
    global mission_thread

    if request.method == "OPTIONS":
        return ("", 204)

    with state_lock:
        if mission_state.running:
            return jsonify(asdict(mission_state)), 409

    with video_lock:
        if video_stream is not None:
            return jsonify({"error": "请先停止相机预览，再开始飞行任务。"}), 400

    payload = request.get_json(silent=True) or {}
    params = {
        "ip": str(payload.get("ip", DEFAULT_DRONE_IP)),
        "height_cm": int(payload.get("height_cm", 80)),
        "step_cm": int(payload.get("step_cm", 40)),
        "max_steps": int(payload.get("max_steps", 6)),
        "settle_sec": float(payload.get("settle_sec", 0.8)),
        "min_battery": int(payload.get("min_battery", 20)),
        "target": int(payload.get("target", int(AIRecognitionTarget.DIGIT_0))),
    }

    set_state(
        running=True,
        phase="queued",
        message="任务已启动",
        success=None,
        error=None,
        started_at=now_iso(),
        finished_at=None,
        battery=None,
        altitude_cm=None,
        altitude_updated_at=None,
        position_xyz=None,
        coordinate_origin=None,
        coordinate_updated_at=None,
        orientation=None,
        ai_result=None,
        crack_alert=None,
        params=params,
    )

    mission_thread = threading.Thread(target=mission_worker, args=(params,), daemon=True)
    mission_thread.start()
    return jsonify(get_state_dict()), 202


@app.route("/api/drone/start-return-qr-test", methods=["POST", "OPTIONS"])
def start_return_qr_test():
    global mission_thread

    if request.method == "OPTIONS":
        return ("", 204)

    with state_lock:
        if mission_state.running:
            return jsonify(asdict(mission_state)), 409

    with video_lock:
        if video_stream is not None:
            return jsonify({"error": "请先停止相机预览，再启动总任务测试。"}), 400

    payload = request.get_json(silent=True) or {}
    display_target_x = float(payload.get("align_target_x_cm", 0.0))
    display_target_y = float(payload.get("align_target_y_cm", 0.0))
    params = {
        "ip": str(payload.get("ip", DEFAULT_DRONE_IP)),
        "height_cm": int(payload.get("height_cm", 25)),
        "step_cm": int(payload.get("step_cm", 20)),
        "max_steps": int(payload.get("max_steps", 15)),
        "settle_sec": float(payload.get("settle_sec", 0.6)),
        "takeoff_stabilize_sec": float(payload.get("takeoff_stabilize_sec", 1.0)),
        "departure_buffer_steps": int(payload.get("departure_buffer_steps", 2)),
        "departure_buffer_settle_sec": float(payload.get("departure_buffer_settle_sec", 1.0)),
        "return_settle_sec": float(payload.get("return_settle_sec", 0.6)),
        "scan_attempts": int(payload.get("scan_attempts", 2)),
        "outbound_d0_pause_attempts": int(payload.get("outbound_d0_pause_attempts", 0)),
        "return_d0_scan_each_segment_attempts": int(payload.get("return_d0_scan_each_segment_attempts", 2)),
        "return_d0_search_segments": int(payload.get("return_d0_search_segments", 5)),
        "return_search_step_cm": int(payload.get("return_search_step_cm", payload.get("step_cm", 20))),
        "scan_interval_sec": float(payload.get("scan_interval_sec", 0.3)),
        "min_battery": int(payload.get("min_battery", 20)),
        "quality": int(payload.get("quality", 75)),
        "max_fps": float(payload.get("max_fps", 15.0)),
        "photo_ready_timeout_sec": float(payload.get("photo_ready_timeout_sec", 3.0)),
        "photo_retry_interval_sec": float(payload.get("photo_retry_interval_sec", 0.2)),
        "video_warmup_sec": float(payload.get("video_warmup_sec", 1.0)),
        "camera_angle": int(payload.get("camera_angle", 90)),
        "camera_settle_sec": float(payload.get("camera_settle_sec", 1.0)),
        "return_qr_id": int(payload.get("return_qr_id", 7)),
        "return_qr_align_target_x_cm": float(payload.get("return_qr_align_target_x_cm", 0.0)),
        "return_qr_align_target_y_cm": float(payload.get("return_qr_align_target_y_cm", 60.0)),
        "return_qr_align_tolerance_cm": float(payload.get("return_qr_align_tolerance_cm", 15.0)),
        "return_qr_min_align_step_cm": int(payload.get("return_qr_min_align_step_cm", 5)),
        "return_qr_max_align_step_cm": int(payload.get("return_qr_max_align_step_cm", 25)),
        "return_qr_max_align_steps": int(payload.get("return_qr_max_align_steps", 3)),
        "return_qr_fine_align_steps": int(payload.get("return_qr_fine_align_steps", 2)),
        "return_qr_fine_max_align_step_cm": int(payload.get("return_qr_fine_max_align_step_cm", 12)),
        "return_qr_align_settle_sec": float(payload.get("return_qr_align_settle_sec", 0.5)),
        "return_qr_lock_sec": float(payload.get("return_qr_lock_sec", 1.0)),
        "return_qr_confirmation_count": int(payload.get("return_qr_confirmation_count", 3)),
        "return_qr_confirm_interval_sec": float(payload.get("return_qr_confirm_interval_sec", 0.2)),
        "return_qr_confirm_max_delta_cm": float(payload.get("return_qr_confirm_max_delta_cm", 12.0)),
        "return_qr_safe_max_correction_cm": int(payload.get("return_qr_safe_max_correction_cm", 8)),
        "return_turn_stabilize_sec": float(payload.get("return_turn_stabilize_sec", 1.0)),
        "return_post_turn_stabilize_sec": float(payload.get("return_post_turn_stabilize_sec", 1.0)),
        "return_qr_post_turn_scan_attempts": int(payload.get("return_qr_post_turn_scan_attempts", 3)),
        "return_qr_post_turn_target_x_cm": float(payload.get("return_qr_post_turn_target_x_cm", 0.0)),
        "return_qr_post_turn_target_y_cm": float(payload.get("return_qr_post_turn_target_y_cm", -60.0)),
        "return_qr_post_turn_tolerance_cm": float(payload.get("return_qr_post_turn_tolerance_cm", 15.0)),
        "return_qr_post_turn_max_step_cm": int(payload.get("return_qr_post_turn_max_step_cm", 12)),
        "turn_angle_deg": int(payload.get("turn_angle_deg", 180)),
        "yaw_correction_tolerance_deg": float(payload.get("yaw_correction_tolerance_deg", 8.0)),
        "yaw_correction_max_deg": float(payload.get("yaw_correction_max_deg", 25.0)),
        # Landing precision does not require restoring the aircraft nose direction.
        # Keep the camera/aircraft orientation unchanged after D0 is found.
        "home_turn_after_d0_deg": 0,
        "home_turn_settle_sec": float(payload.get("home_turn_settle_sec", 0.8)),
        "landing_qr_id": int(payload.get("landing_qr_id", 2)),
        "target": int(payload.get("target", int(AIRecognitionTarget.DIGIT_0))),
        "return_d0_scan_attempts": int(payload.get("return_d0_scan_attempts", 8)),
        "post_turn_d0_scan_attempts": int(payload.get("post_turn_d0_scan_attempts", 8)),
        "align_target_x_cm": display_target_x + D0_LANDING_COMPENSATION_X_CM,
        "align_target_y_cm": display_target_y + D0_LANDING_COMPENSATION_Y_CM,
        "align_display_target_x_cm": display_target_x,
        "align_display_target_y_cm": display_target_y,
        "align_tolerance_cm": float(payload.get("align_tolerance_cm", 5.0)),
        "min_align_step_cm": int(payload.get("min_align_step_cm", 3)),
        "max_align_step_cm": int(payload.get("max_align_step_cm", 18)),
        "max_align_steps": int(payload.get("max_align_steps", 6)),
        "align_settle_sec": float(payload.get("align_settle_sec", 0.5)),
        "landing_qr_lock_sec": float(payload.get("landing_qr_lock_sec", 1.0)),
        "landing_qr_confirmation_count": int(payload.get("landing_qr_confirmation_count", 3)),
        "landing_qr_confirm_interval_sec": float(payload.get("landing_qr_confirm_interval_sec", 0.2)),
        "landing_qr_confirm_max_delta_cm": float(payload.get("landing_qr_confirm_max_delta_cm", 12.0)),
        "landing_safe_max_correction_cm": int(payload.get("landing_safe_max_correction_cm", 8)),
        "auto_crack": bool(payload.get("auto_crack", True)),
        "crack_config": {
            "area_threshold": int(payload.get("area_threshold", 350)),
            "gray_threshold": int(payload.get("gray_threshold", 5)),
            "scale_cm_per_px": float(payload.get("scale_cm_per_px", DEFAULT_CRACK_SCALE_CM_PER_PX)),
            "kernel_size": int(payload.get("kernel_size", 3)),
            "iterations": int(payload.get("iterations", 1)),
        },
    }
    params["height_cm"] = max(20, min(200, params["height_cm"]))
    params["step_cm"] = max(10, min(60, params["step_cm"]))
    params["max_steps"] = max(1, min(30, params["max_steps"]))
    params["settle_sec"] = max(0.2, min(2.0, params["settle_sec"]))
    params["takeoff_stabilize_sec"] = max(0.5, min(3.0, params["takeoff_stabilize_sec"]))
    params["departure_buffer_steps"] = max(0, min(3, params["departure_buffer_steps"]))
    params["departure_buffer_settle_sec"] = max(0.5, min(2.0, params["departure_buffer_settle_sec"]))
    params["return_settle_sec"] = max(0.2, min(2.0, params["return_settle_sec"]))
    params["scan_attempts"] = max(1, min(8, params["scan_attempts"]))
    params["outbound_d0_pause_attempts"] = max(0, min(3, params["outbound_d0_pause_attempts"]))
    params["return_d0_scan_each_segment_attempts"] = max(1, min(5, params["return_d0_scan_each_segment_attempts"]))
    params["return_d0_search_segments"] = max(1, min(8, params["return_d0_search_segments"]))
    params["return_search_step_cm"] = max(5, min(params["step_cm"], params["return_search_step_cm"]))
    params["scan_interval_sec"] = max(0.1, min(1.0, params["scan_interval_sec"]))
    params["quality"] = max(30, min(95, params["quality"]))
    params["max_fps"] = max(3.0, min(25.0, params["max_fps"]))
    params["photo_ready_timeout_sec"] = max(0.0, min(8.0, params["photo_ready_timeout_sec"]))
    params["photo_retry_interval_sec"] = max(0.05, min(1.0, params["photo_retry_interval_sec"]))
    params["video_warmup_sec"] = max(0.0, min(5.0, params["video_warmup_sec"]))
    params["camera_angle"] = max(0, min(90, params["camera_angle"]))
    params["camera_settle_sec"] = max(0.2, min(5.0, params["camera_settle_sec"]))
    params["return_qr_id"] = max(0, min(9, params["return_qr_id"]))
    params["return_qr_align_target_x_cm"] = max(-120.0, min(120.0, params["return_qr_align_target_x_cm"]))
    params["return_qr_align_target_y_cm"] = max(20.0, min(160.0, params["return_qr_align_target_y_cm"]))
    params["return_qr_align_tolerance_cm"] = max(8.0, min(40.0, params["return_qr_align_tolerance_cm"]))
    params["return_qr_min_align_step_cm"] = max(3, min(20, params["return_qr_min_align_step_cm"]))
    params["return_qr_max_align_step_cm"] = max(
        params["return_qr_min_align_step_cm"],
        min(40, params["return_qr_max_align_step_cm"]),
    )
    params["return_qr_max_align_steps"] = max(1, min(6, params["return_qr_max_align_steps"]))
    params["return_qr_fine_align_steps"] = max(0, min(4, params["return_qr_fine_align_steps"]))
    params["return_qr_fine_max_align_step_cm"] = max(
        params["return_qr_min_align_step_cm"],
        min(params["return_qr_max_align_step_cm"], params["return_qr_fine_max_align_step_cm"]),
    )
    params["return_qr_align_settle_sec"] = max(0.2, min(2.0, params["return_qr_align_settle_sec"]))
    params["return_qr_lock_sec"] = max(0.5, min(2.0, params["return_qr_lock_sec"]))
    params["return_qr_confirmation_count"] = max(2, min(3, params["return_qr_confirmation_count"]))
    params["return_qr_confirm_interval_sec"] = max(0.1, min(0.5, params["return_qr_confirm_interval_sec"]))
    params["return_qr_confirm_max_delta_cm"] = max(5.0, min(25.0, params["return_qr_confirm_max_delta_cm"]))
    params["return_qr_safe_max_correction_cm"] = max(3, min(8, params["return_qr_safe_max_correction_cm"]))
    # The direct-return branch above disables QR 7 alignment explicitly.
    params["return_qr_max_align_steps"] = 1
    params["return_qr_fine_align_steps"] = 0
    params["return_turn_stabilize_sec"] = max(0.5, min(3.0, params["return_turn_stabilize_sec"]))
    params["return_post_turn_stabilize_sec"] = max(0.5, min(2.0, params["return_post_turn_stabilize_sec"]))
    params["return_qr_post_turn_scan_attempts"] = max(1, min(5, params["return_qr_post_turn_scan_attempts"]))
    params["return_qr_post_turn_target_x_cm"] = max(-120.0, min(120.0, params["return_qr_post_turn_target_x_cm"]))
    params["return_qr_post_turn_target_y_cm"] = max(-160.0, min(-20.0, params["return_qr_post_turn_target_y_cm"]))
    params["return_qr_post_turn_tolerance_cm"] = max(8.0, min(40.0, params["return_qr_post_turn_tolerance_cm"]))
    params["return_qr_post_turn_max_step_cm"] = max(3, min(20, params["return_qr_post_turn_max_step_cm"]))
    params["turn_angle_deg"] = max(-180, min(180, params["turn_angle_deg"]))
    params["yaw_correction_tolerance_deg"] = max(2.0, min(30.0, params["yaw_correction_tolerance_deg"]))
    params["yaw_correction_max_deg"] = max(5.0, min(45.0, params["yaw_correction_max_deg"]))
    params["home_turn_after_d0_deg"] = max(-180, min(180, params["home_turn_after_d0_deg"]))
    params["home_turn_settle_sec"] = max(0.2, min(3.0, params["home_turn_settle_sec"]))
    params["landing_qr_id"] = max(0, min(9, params["landing_qr_id"]))
    params["return_d0_scan_attempts"] = max(1, min(10, params["return_d0_scan_attempts"]))
    params["post_turn_d0_scan_attempts"] = max(1, min(10, params["post_turn_d0_scan_attempts"]))
    params["align_target_x_cm"] = max(-80.0, min(80.0, params["align_target_x_cm"]))
    params["align_target_y_cm"] = max(-80.0, min(80.0, params["align_target_y_cm"]))
    params["align_tolerance_cm"] = max(3.0, min(30.0, params["align_tolerance_cm"]))
    params["min_align_step_cm"] = max(3, min(20, params["min_align_step_cm"]))
    params["max_align_step_cm"] = max(params["min_align_step_cm"], min(30, params["max_align_step_cm"]))
    params["max_align_steps"] = max(1, min(8, params["max_align_steps"]))
    params["align_settle_sec"] = max(0.2, min(2.0, params["align_settle_sec"]))
    params["landing_qr_lock_sec"] = max(0.5, min(2.0, params["landing_qr_lock_sec"]))
    params["landing_qr_confirmation_count"] = max(2, min(3, params["landing_qr_confirmation_count"]))
    params["landing_qr_confirm_interval_sec"] = max(0.1, min(0.5, params["landing_qr_confirm_interval_sec"]))
    params["landing_qr_confirm_max_delta_cm"] = max(5.0, min(25.0, params["landing_qr_confirm_max_delta_cm"]))
    params["landing_safe_max_correction_cm"] = max(3, min(8, params["landing_safe_max_correction_cm"]))
    params["crack_config"]["area_threshold"] = max(1, min(20000, params["crack_config"]["area_threshold"]))
    params["crack_config"]["gray_threshold"] = max(0, min(255, params["crack_config"]["gray_threshold"]))
    params["crack_config"]["kernel_size"] = max(1, min(15, params["crack_config"]["kernel_size"]))
    params["crack_config"]["iterations"] = max(1, min(5, params["crack_config"]["iterations"]))

    set_state(
        running=True,
        phase="queued",
        message="总任务第一步：7号返航点识别测试已启动",
        success=None,
        error=None,
        started_at=now_iso(),
        finished_at=None,
        battery=None,
        altitude_cm=None,
        altitude_updated_at=None,
        position_xyz=None,
        coordinate_origin=None,
        coordinate_updated_at=None,
        orientation=None,
        ai_result=None,
        crack_alert=None,
        params=params,
    )

    mission_thread = threading.Thread(target=return_qr7_test_worker, args=(params,), daemon=True)
    mission_thread.start()
    return jsonify(get_state_dict()), 202


@app.route("/api/drone/start-photo-task", methods=["POST", "OPTIONS"])
def start_photo_task():
    global mission_thread

    if request.method == "OPTIONS":
        return ("", 204)

    with state_lock:
        if mission_state.running:
            return jsonify(asdict(mission_state)), 409

    with video_lock:
        if video_stream is not None:
            return jsonify({"error": "请先停止相机预览，再启动带视频悬停测试。"}), 400

    payload = request.get_json(silent=True) or {}
    display_target_x = float(payload.get("align_target_x_cm", 0.0))
    display_target_y = float(payload.get("align_target_y_cm", 0.0))
    params = {
        "ip": str(payload.get("ip", DEFAULT_DRONE_IP)),
        "height_cm": int(payload.get("height_cm", 80)),
        "step_cm": int(payload.get("step_cm", 40)),
        "max_steps": int(payload.get("max_steps", 6)),
        "settle_sec": float(payload.get("settle_sec", 0.8)),
        "scan_attempts": int(payload.get("scan_attempts", 2)),
        "scan_interval_sec": float(payload.get("scan_interval_sec", 0.3)),
        "min_battery": int(payload.get("min_battery", 20)),
        "target": int(payload.get("target", int(AIRecognitionTarget.DIGIT_0))),
        "quality": int(payload.get("quality", 75)),
        "max_fps": float(payload.get("max_fps", 15.0)),
        "camera_angle": int(payload.get("camera_angle", 90)),
        "camera_settle_sec": float(payload.get("camera_settle_sec", 1.0)),
        "align_target_x_cm": display_target_x + D0_LANDING_COMPENSATION_X_CM,
        "align_target_y_cm": display_target_y + D0_LANDING_COMPENSATION_Y_CM,
        "align_display_target_x_cm": display_target_x,
        "align_display_target_y_cm": display_target_y,
        "align_tolerance_cm": float(payload.get("align_tolerance_cm", 5.0)),
        "min_align_step_cm": int(payload.get("min_align_step_cm", 3)),
        "max_align_step_cm": int(payload.get("max_align_step_cm", 12)),
        "max_align_steps": int(payload.get("max_align_steps", 3)),
        "align_settle_sec": float(payload.get("align_settle_sec", 0.5)),
        "auto_photo": True,
        "crack_config": {
            "area_threshold": int(payload.get("area_threshold", 350)),
            "gray_threshold": int(payload.get("gray_threshold", 5)),
            "scale_cm_per_px": float(payload.get("scale_cm_per_px", DEFAULT_CRACK_SCALE_CM_PER_PX)),
            "kernel_size": int(payload.get("kernel_size", 3)),
            "iterations": int(payload.get("iterations", 1)),
        },
    }
    params["camera_angle"] = max(0, min(90, params["camera_angle"]))
    params["camera_settle_sec"] = max(0.2, min(5.0, params["camera_settle_sec"]))
    params["scan_attempts"] = max(1, min(10, params["scan_attempts"]))
    params["scan_interval_sec"] = max(0.1, min(1.0, params["scan_interval_sec"]))
    params["align_target_x_cm"] = max(-80.0, min(80.0, params["align_target_x_cm"]))
    params["align_target_y_cm"] = max(-80.0, min(80.0, params["align_target_y_cm"]))
    params["align_tolerance_cm"] = max(3.0, min(30.0, params["align_tolerance_cm"]))
    params["min_align_step_cm"] = max(3, min(20, params["min_align_step_cm"]))
    params["max_align_step_cm"] = max(params["min_align_step_cm"], min(30, params["max_align_step_cm"]))
    params["max_align_steps"] = max(1, min(8, params["max_align_steps"]))
    params["align_settle_sec"] = max(0.2, min(2.0, params["align_settle_sec"]))

    set_state(
        running=True,
        phase="queued",
        message="带视频悬停测试已启动",
        success=None,
        error=None,
        started_at=now_iso(),
        finished_at=None,
        battery=None,
        altitude_cm=None,
        altitude_updated_at=None,
        position_xyz=None,
        coordinate_origin=None,
        coordinate_updated_at=None,
        orientation=None,
        ai_result=None,
        params=params,
    )
    set_state(message="巡航裂缝识别任务已启动")

    mission_thread = threading.Thread(target=cruise_crack_mission_worker, args=(params,), daemon=True)
    mission_thread.start()
    return jsonify(get_state_dict()), 202


@app.route("/api/drone/start-video-task", methods=["POST", "OPTIONS"])
def start_video_task():
    global mission_thread

    if request.method == "OPTIONS":
        return ("", 204)

    with state_lock:
        if mission_state.running:
            return jsonify(asdict(mission_state)), 409

    with video_lock:
        if video_stream is not None:
            return jsonify({"error": "请先停止相机预览，再启动实时高度测试。"}), 400

    payload = request.get_json(silent=True) or {}
    raw_heights = payload.get("heights_cm", [80, 130, 160])
    if not isinstance(raw_heights, list):
        raw_heights = [80, 130, 160]
    heights_cm = []
    for value in raw_heights[:3]:
        try:
            heights_cm.append(max(20, min(200, int(value))))
        except (TypeError, ValueError):
            pass
    if len(heights_cm) != 3:
        heights_cm = [80, 130, 160]

    params = {
        "ip": str(payload.get("ip", DEFAULT_DRONE_IP)),
        "heights_cm": heights_cm,
        "hover_sec": float(payload.get("hover_sec", 5.0)),
        "min_battery": int(payload.get("min_battery", 20)),
        "quality": int(payload.get("quality", 75)),
        "max_fps": float(payload.get("max_fps", 15.0)),
    }

    set_state(
        running=True,
        phase="queued",
        message="实时高度测试已启动",
        success=None,
        error=None,
        started_at=now_iso(),
        finished_at=None,
        battery=None,
        altitude_cm=None,
        altitude_updated_at=None,
        position_xyz=None,
        coordinate_origin=None,
        coordinate_updated_at=None,
        orientation=None,
        ai_result=None,
        params=params,
    )

    mission_thread = threading.Thread(target=height_test_worker, args=(params,), daemon=True)
    mission_thread.start()
    return jsonify(get_state_dict()), 202


@app.route("/api/drone/start-d0-distance-test", methods=["POST", "OPTIONS"])
def start_d0_distance_test():
    global mission_thread

    if request.method == "OPTIONS":
        return ("", 204)

    with state_lock:
        if mission_state.running:
            return jsonify(asdict(mission_state)), 409

    with video_lock:
        if video_stream is not None:
            return jsonify({"error": "请先停止相机预览，再启动 D0 识别距离测试。"}), 400

    payload = request.get_json(silent=True) or {}
    params = {
        "ip": str(payload.get("ip", DEFAULT_DRONE_IP)),
        "height_cm": int(payload.get("height_cm", 80)),
        "scan_sec": float(payload.get("scan_sec", 30.0)),
        "recognize_interval_sec": float(payload.get("recognize_interval_sec", 0.5)),
        "min_battery": int(payload.get("min_battery", 20)),
        "target": int(payload.get("target", int(AIRecognitionTarget.DIGIT_0))),
        "quality": int(payload.get("quality", 75)),
        "max_fps": float(payload.get("max_fps", 15.0)),
    }

    set_state(
        running=True,
        phase="queued",
        message="D0 识别距离测试已启动",
        success=None,
        error=None,
        started_at=now_iso(),
        finished_at=None,
        battery=None,
        altitude_cm=None,
        altitude_updated_at=None,
        position_xyz=None,
        coordinate_origin=None,
        coordinate_updated_at=None,
        orientation=None,
        ai_result=None,
        params=params,
    )

    mission_thread = threading.Thread(target=d0_distance_test_worker, args=(params,), daemon=True)
    mission_thread.start()
    return jsonify(get_state_dict()), 202


@app.route("/api/drone/start-downward-d0-test", methods=["POST", "OPTIONS"])
def start_downward_d0_test():
    global mission_thread

    if request.method == "OPTIONS":
        return ("", 204)

    with state_lock:
        if mission_state.running:
            return jsonify(asdict(mission_state)), 409

    with video_lock:
        if video_stream is not None:
            return jsonify({"error": "请先停止相机预览，再启动下视D0对准降落测试。"}), 400

    payload = request.get_json(silent=True) or {}
    display_target_x = float(payload.get("align_target_x_cm", 0.0))
    display_target_y = float(payload.get("align_target_y_cm", 0.0))
    params = {
        "ip": str(payload.get("ip", DEFAULT_DRONE_IP)),
        "height_cm": int(payload.get("height_cm", 70)),
        "step_cm": int(payload.get("step_cm", 20)),
        "max_steps": int(payload.get("max_steps", 15)),
        "settle_sec": float(payload.get("settle_sec", 0.6)),
        "scan_attempts": int(payload.get("scan_attempts", 2)),
        "scan_interval_sec": float(payload.get("scan_interval_sec", 0.3)),
        "camera_angle": int(payload.get("camera_angle", 90)),
        "camera_settle_sec": float(payload.get("camera_settle_sec", 1.0)),
        "align_target_x_cm": display_target_x + D0_LANDING_COMPENSATION_X_CM,
        "align_target_y_cm": display_target_y + D0_LANDING_COMPENSATION_Y_CM,
        "align_tolerance_cm": float(payload.get("align_tolerance_cm", 5.0)),
        "min_align_step_cm": int(payload.get("min_align_step_cm", 3)),
        "max_align_step_cm": int(payload.get("max_align_step_cm", 12)),
        "max_align_steps": int(payload.get("max_align_steps", 5)),
        "align_settle_sec": float(payload.get("align_settle_sec", 0.8)),
        "min_battery": int(payload.get("min_battery", 20)),
        "target": int(payload.get("target", int(AIRecognitionTarget.DIGIT_0))),
        "quality": int(payload.get("quality", 75)),
        "max_fps": float(payload.get("max_fps", 15.0)),
        "auto_photo": True,
    }

    params["height_cm"] = max(60, min(200, params["height_cm"]))
    params["step_cm"] = max(5, min(60, params["step_cm"]))
    params["max_steps"] = max(1, min(20, params["max_steps"]))
    params["settle_sec"] = max(0.3, min(3.0, params["settle_sec"]))
    params["scan_attempts"] = max(1, min(10, params["scan_attempts"]))
    params["scan_interval_sec"] = max(0.1, min(1.0, params["scan_interval_sec"]))
    params["camera_angle"] = max(0, min(90, params["camera_angle"]))
    params["align_target_x_cm"] = max(-80.0, min(80.0, params["align_target_x_cm"]))
    params["align_target_y_cm"] = max(-80.0, min(80.0, params["align_target_y_cm"]))
    params["align_tolerance_cm"] = max(3.0, min(30.0, params["align_tolerance_cm"]))
    params["min_align_step_cm"] = max(3, min(20, params["min_align_step_cm"]))
    params["max_align_step_cm"] = max(params["min_align_step_cm"], min(30, params["max_align_step_cm"]))
    params["max_align_steps"] = max(1, min(10, params["max_align_steps"]))
    params["align_settle_sec"] = max(0.3, min(3.0, params["align_settle_sec"]))

    set_state(
        running=True,
        phase="queued",
        message="下视D0对准降落测试已启动",
        success=None,
        error=None,
        started_at=now_iso(),
        finished_at=None,
        battery=None,
        altitude_cm=None,
        altitude_updated_at=None,
        position_xyz=None,
        coordinate_origin=None,
        coordinate_updated_at=None,
        orientation=None,
        ai_result=None,
        params=params,
    )

    mission_thread = threading.Thread(target=downward_d0_blind_land_worker, args=(params,), daemon=True)
    mission_thread.start()
    return jsonify(get_state_dict()), 202


@app.route("/api/drone/emergency-land", methods=["POST", "OPTIONS"])
def emergency_land():
    if request.method == "OPTIONS":
        return ("", 204)

    set_state(phase="emergency_landing", message="收到紧急降落请求")
    try:
        if active_drone is not None:
            active_drone.land(blocking=False)
        else:
            with DroneAPI(
                enable_logging=False,
                enable_file_logging=False,
                enable_command_logging=False,
            ) as drone:
                if drone.robust_connect(DEFAULT_DRONE_IP, verbose=False):
                    drone.land(blocking=False)
        return jsonify(get_state_dict())
    except Exception as exc:
        set_state(error=str(exc), message="紧急降落命令发送失败")
        return jsonify(get_state_dict()), 500


if __name__ == "__main__":
    app_host = os.environ.get("PYHULAX_HOST", "127.0.0.1")
    app_port = int(os.environ.get("PYHULAX_PORT", "5055"))
    app.run(host=app_host, port=app_port, debug=False, threaded=True)
