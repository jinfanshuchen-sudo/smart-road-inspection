"""Step 7: mission prototype - move forward in steps, land when D0 is seen.

This is a real flight test. It does not rely on the 00 QR marker yet because QR
recognition was not reliable in previous tests. Use the 00 board as the manual
start/orientation marker: place the drone at 00 and point its nose toward D0.
"""

from __future__ import annotations

import argparse
import time

from pyhulax import DroneAPI
from pyhulax.core import AIRecognitionTarget, CommandResult, Direction, VelocityLevel
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
    parser = argparse.ArgumentParser(description="Move forward stepwise and land when D0 is seen.")
    parser.add_argument("--ip", default="192.168.100.1", help="Drone IP address")
    parser.add_argument("--height-cm", type=int, default=80, help="Takeoff height in cm")
    parser.add_argument("--step-cm", type=int, default=40, help="Forward distance per step in cm")
    parser.add_argument("--max-steps", type=int, default=3, help="Maximum forward steps")
    parser.add_argument("--settle-sec", type=float, default=0.8, help="Hover seconds before each scan")
    parser.add_argument("--min-battery", type=int, default=20, help="Minimum battery percent")
    parser.add_argument(
        "--target",
        type=int,
        default=int(AIRecognitionTarget.DIGIT_0),
        help="AI target value. D0 should normally be digit 0.",
    )
    parser.add_argument(
        "--scan-before-move",
        action="store_true",
        help="Scan once before the first forward movement.",
    )
    args = parser.parse_args()

    print("Step 7: mission prototype. The drone will search forward for D0 and land when found.")
    print("Use 00 as the manual start/orientation marker; QR 00 is not used in this script.")
    print(f"Target drone IP: {args.ip}")
    print(f"Takeoff height: {args.height_cm} cm")
    print(f"Forward step: {args.step_cm} cm")
    print(f"Max steps: {args.max_steps}")
    print(f"AI target: {args.target}")

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

        found_d0 = False
        try:
            print("Disabling QR localization mode for relative forward movement...")
            drone.set_qr_localization(False)
            time.sleep(0.5)

            print("Sending takeoff command...")
            expect_success(drone.takeoff(height_cm=args.height_cm), "takeoff")

            for step_index in range(args.max_steps + 1):
                if step_index > 0:
                    print(f"Moving forward step {step_index}/{args.max_steps}...")
                    expect_success(
                        drone.move(Direction.FORWARD, args.step_cm, speed=VelocityLevel.MEDIUM),
                        "move forward",
                    )

                if step_index == 0 and not args.scan_before_move:
                    continue

                print("Hovering before AI scan...")
                time.sleep(args.settle_sec)
                print("Scanning for D0...")
                result = drone.recognize_target(args.target)
                print(f"AI success: {result.success}")
                print(f"AI target_type: {result.target_type}")
                print(f"AI position: {result.position}")
                print(f"AI angle: {result.angle}")

                if result.success:
                    found_d0 = True
                    print("D0 found. Landing now.")
                    break

            print("Sending land command...")
            expect_success(drone.land(), "land")
        except BaseException:
            print("Trying safe landing before exiting...")
            try:
                drone.land(blocking=False)
            finally:
                raise

    if found_d0:
        print("Step 7 passed: D0 was found and the drone landed.")
        return 0

    print("Step 7 ended: D0 was not found within the configured search distance.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
