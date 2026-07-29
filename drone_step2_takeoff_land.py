"""Step 2: short takeoff and landing safety test.

This sends real flight commands:
1. Connect to the drone.
2. Take off to a low height.
3. Hover briefly.
4. Land.
"""

from __future__ import annotations

import argparse
import time

from pyhulax import DroneAPI
from pyhulax.core import CommandResult
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
    parser = argparse.ArgumentParser(description="Take off briefly, then land.")
    parser.add_argument("--ip", default="192.168.100.1", help="Drone IP address")
    parser.add_argument("--height-cm", type=int, default=60, help="Takeoff height in cm")
    parser.add_argument("--hover-sec", type=float, default=3.0, help="Hover duration in seconds")
    parser.add_argument("--min-battery", type=int, default=20, help="Minimum battery percent")
    args = parser.parse_args()

    print("Step 2: real flight test. The drone will take off and land.")
    print(f"Target drone IP: {args.ip}")
    print(f"Takeoff height: {args.height_cm} cm")
    print(f"Hover duration: {args.hover_sec} sec")

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

            print("Hovering...")
            time.sleep(args.hover_sec)

            print("Sending land command...")
            expect_success(drone.land(), "land")
        except Exception:
            print("Trying emergency safe landing before exiting...")
            try:
                drone.land(blocking=False)
            finally:
                raise

    print("Step 2 passed: takeoff and landing completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
