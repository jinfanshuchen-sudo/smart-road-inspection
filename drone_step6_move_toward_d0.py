"""Step 6: take off, move forward a short distance, recognize D0, then land.

This is a real flight test. It verifies that the drone can move toward the
second-tower direction and still recognize the D0 marker afterward.
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
    parser = argparse.ArgumentParser(description="Move toward D0, recognize it, then land.")
    parser.add_argument("--ip", default="192.168.100.1", help="Drone IP address")
    parser.add_argument("--height-cm", type=int, default=80, help="Takeoff height in cm")
    parser.add_argument("--forward-cm", type=int, default=50, help="Forward movement distance in cm")
    parser.add_argument("--settle-sec", type=float, default=1.0, help="Seconds to hover before AI scan")
    parser.add_argument("--min-battery", type=int, default=20, help="Minimum battery percent")
    parser.add_argument(
        "--target",
        type=int,
        default=int(AIRecognitionTarget.DIGIT_0),
        help="AI target value. D0 should normally be digit 0.",
    )
    args = parser.parse_args()

    print("Step 6: real flight test. The drone will move forward, recognize D0, then land.")
    print(f"Target drone IP: {args.ip}")
    print(f"Takeoff height: {args.height_cm} cm")
    print(f"Forward distance: {args.forward_cm} cm")
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

        try:
            print("Sending takeoff command...")
            expect_success(drone.takeoff(height_cm=args.height_cm), "takeoff")

            print("Moving forward...")
            expect_success(
                drone.move(Direction.FORWARD, args.forward_cm, speed=VelocityLevel.SLOW),
                "move forward",
            )

            print("Hovering before AI scan...")
            time.sleep(args.settle_sec)

            print("Recognizing D0 with onboard AI camera...")
            result = drone.recognize_target(args.target)
            print(f"AI success: {result.success}")
            print(f"AI target_type: {result.target_type}")
            print(f"AI position: {result.position}")
            print(f"AI angle: {result.angle}")

            print("Sending land command...")
            expect_success(drone.land(), "land")

            if not result.success:
                print("Step 6 failed: D0 was not recognized after moving forward.")
                return 2
        except BaseException:
            print("Trying safe landing before exiting...")
            try:
                drone.land(blocking=False)
            finally:
                raise

    print("Step 6 passed: moved forward and recognized D0.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
