"""Emergency landing helper.

Run this if the drone is connected but a test script is not stopping quickly
enough. It sends a non-blocking land command.
"""

from __future__ import annotations

import argparse

from pyhulax import DroneAPI


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a land command to the drone.")
    parser.add_argument("--ip", default="192.168.100.1", help="Drone IP address")
    args = parser.parse_args()

    print("Connecting to drone for emergency landing...")
    with DroneAPI(
        enable_logging=False,
        enable_file_logging=False,
        enable_command_logging=False,
    ) as drone:
        if not drone.robust_connect(args.ip, verbose=True):
            print("Connection failed. Use the drone/app/manual emergency stop if needed.")
            return 1

        print("Sending non-blocking land command...")
        drone.land(blocking=False)
        print("Land command sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
