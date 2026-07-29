"""Step 1: non-flight connectivity check for the Hula drone.

Run this before any takeoff or mission test. It only connects to the drone and
tries to read basic telemetry; it does not send flight commands.
"""

from __future__ import annotations

import argparse
import time

from pyhulax import DroneAPI
from pyhulax.core.exceptions import TelemetryUnavailable


def wait_for_battery(drone: DroneAPI, timeout_sec: float) -> int | None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            return drone.get_battery()
        except TelemetryUnavailable:
            time.sleep(0.5)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Check drone connection without flying.")
    parser.add_argument("--ip", default="192.168.100.1", help="Drone IP address")
    parser.add_argument(
        "--telemetry-timeout",
        type=float,
        default=8.0,
        help="Seconds to wait for telemetry after connection",
    )
    args = parser.parse_args()

    print("Step 1: checking drone connection only. No takeoff command will be sent.")
    print(f"Target drone IP: {args.ip}")

    with DroneAPI(
        enable_logging=False,
        enable_file_logging=False,
        enable_command_logging=False,
    ) as drone:
        if not drone.robust_connect(args.ip, verbose=True):
            print("Result: connection failed.")
            print("Check that the computer is connected to the drone Wi-Fi.")
            return 1

        print("Result: SDK connection established.")
        print(f"Drone ID: {drone.get_drone_id()}")

        battery = wait_for_battery(drone, args.telemetry_timeout)
        if battery is None:
            print("Telemetry: connected, but battery data was not received yet.")
            print("This can happen briefly after connection; retry once before flying.")
            return 2

        print(f"Battery: {battery}%")
        if battery < 20:
            print("Warning: battery is below 20%; do not run the flight mission yet.")
            return 3

        print("Step 1 passed: connection and basic telemetry are available.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
