"""Step 3: take off, detect the 00 QR marker, then land.

This is a real flight test, but it does not fly toward the second tower.
Place the 00 marker on the floor where the downward camera can see it.
"""

from __future__ import annotations

import argparse
import time

from pyhulax import DroneAPI
from pyhulax.core import CommandResult, VisionMode
from pyhulax.core.exceptions import TelemetryUnavailable


def require_battery(drone: DroneAPI, minimum_percent: int, timeout_sec: float = 8.0) -> int:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            battery = drone.get_battery()
            if battery < minimum_percent:
                raise RuntimeError(
                    f"Battery is {battery}%, below the required {minimum_percent}%."
                )
            return battery
        except TelemetryUnavailable:
            time.sleep(0.5)
    raise RuntimeError("Battery telemetry was not available.")


def expect_success(result: CommandResult, command_name: str) -> None:
    if result != CommandResult.SUCCESS:
        raise RuntimeError(f"{command_name} failed: {result.name} ({int(result)})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect 00 marker, then land.")
    parser.add_argument("--ip", default="192.168.100.1", help="Drone IP address")
    parser.add_argument("--qr-id", type=int, default=0, help="QR marker ID, 00 means 0")
    parser.add_argument("--height-cm", type=int, default=60, help="Takeoff height in cm")
    parser.add_argument(
        "--method",
        choices=("detect", "safe-align"),
        default="detect",
        help="detect is passive; safe-align uses bounded recognize_qr()",
    )
    parser.add_argument("--scan-sec", type=float, default=5.0, help="Maximum QR scan seconds")
    parser.add_argument("--settle-sec", type=float, default=1.0, help="Seconds to wait before detection")
    parser.add_argument("--min-battery", type=int, default=20, help="Minimum battery percent")
    parser.add_argument(
        "--skip-qr-localization",
        action="store_true",
        help="Do not enable QR localization before scanning",
    )
    args = parser.parse_args()

    print("Step 3: real flight test. The drone will take off, detect 00, then land.")
    print("This version uses detect_qr(), not recognize_qr(), so it should not align/follow the board.")
    print(f"Target drone IP: {args.ip}")
    print(f"QR marker ID: {args.qr_id}")
    print(f"Takeoff height: {args.height_cm} cm")
    print(f"QR method: {args.method}")

    with DroneAPI(
        enable_logging=False,
        enable_file_logging=False,
        enable_command_logging=False,
    ) as drone:
        if not drone.robust_connect(args.ip, verbose=True):
            print("Result: connection failed.")
            return 1

        battery = require_battery(drone, args.min_battery)
        print(f"Battery: {battery}%")

        try:
            if not args.skip_qr_localization:
                print("Enabling QR localization mode...")
                drone.set_qr_localization(True)
                time.sleep(1.0)

            print("Sending takeoff command...")
            expect_success(drone.takeoff(height_cm=args.height_cm), "takeoff")

            time.sleep(args.settle_sec)
            if args.method == "safe-align":
                print("Recognizing QR marker with bounded optical-flow alignment...")
                result = drone.recognize_qr(
                    qr_id=args.qr_id,
                    mode=VisionMode.OPTICAL_FLOW,
                    timeout=args.scan_sec,
                    search_radius=0,
                )
            else:
                print("Detecting QR marker with downward optical-flow camera...")
                result = drone.detect_qr(
                    qr_id=args.qr_id,
                    mode=VisionMode.OPTICAL_FLOW,
                )

            print(f"QR success: {result.success}")
            print(f"QR id: {result.qr_id}")
            print(f"QR position: {result.position}")
            print(f"QR angle: {result.angle}")

            print("Sending land command...")
            expect_success(drone.land(), "land")

            if not result.success:
                print("Step 3 failed: QR marker was not recognized.")
                return 2
        except BaseException:
            print("Trying safe landing before exiting...")
            try:
                drone.land(blocking=False)
            finally:
                raise

    print("Step 3 passed: 00 marker recognition completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
