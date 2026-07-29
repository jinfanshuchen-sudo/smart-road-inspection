"""Step 4: ground test for the D0 visual marker.

This does not take off. Hold or place the D0 board in front of the drone's
front camera, then run this script to check whether onboard AI vision returns a
digit-0 result.
"""

from __future__ import annotations

import argparse

from pyhulax import DroneAPI
from pyhulax.core import AIRecognitionTarget


def main() -> int:
    parser = argparse.ArgumentParser(description="Ground AI recognition test for D0.")
    parser.add_argument("--ip", default="192.168.100.1", help="Drone IP address")
    parser.add_argument(
        "--target",
        type=int,
        default=int(AIRecognitionTarget.DIGIT_0),
        help="AI target value. D0 should normally be digit 0.",
    )
    args = parser.parse_args()

    print("Step 4: ground AI vision test. No takeoff command will be sent.")
    print("Put the D0 board in front of the drone camera, about 30-80 cm away.")
    print(f"Target drone IP: {args.ip}")
    print(f"AI target: {args.target}")

    with DroneAPI(
        enable_logging=False,
        enable_file_logging=False,
        enable_command_logging=False,
    ) as drone:
        if not drone.robust_connect(args.ip, verbose=True):
            print("Result: connection failed.")
            return 1

        print("Recognizing target with onboard AI camera...")
        result = drone.recognize_target(args.target)
        print(f"AI success: {result.success}")
        print(f"AI target_type: {result.target_type}")
        print(f"AI position: {result.position}")
        print(f"AI angle: {result.angle}")

        if not result.success:
            print("Step 4 failed: D0 was not recognized.")
            return 2

    print("Step 4 passed: D0 visual marker was recognized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
