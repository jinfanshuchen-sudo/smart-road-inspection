"""Step 8: front camera video stream test without flying.

This script connects to the drone, enables the front RTP video stream, and
starts a small local MJPEG web page for browser viewing. It does not send
takeoff, land, move, or rotate commands.
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
    parser = argparse.ArgumentParser(
        description="Show the drone front camera in a browser without flying."
    )
    parser.add_argument("--ip", default="192.168.100.1", help="Drone IP address")
    parser.add_argument("--web-port", type=int, default=5056, help="Local web stream port")
    parser.add_argument("--min-battery", type=int, default=20, help="Minimum battery percent")
    parser.add_argument(
        "--telemetry-timeout",
        type=float,
        default=8.0,
        help="Seconds to wait for battery telemetry after connection",
    )
    parser.add_argument(
        "--duration-sec",
        type=float,
        default=0.0,
        help="Auto-stop after this many seconds. 0 means run until Ctrl+C.",
    )
    args = parser.parse_args()

    print("Step 8: front camera video stream test.")
    print("No takeoff, land, move, or rotate command will be sent.")
    print(f"Target drone IP: {args.ip}")
    print(f"Video web page: http://127.0.0.1:{args.web_port}/")
    print(f"Raw MJPEG feed: http://127.0.0.1:{args.web_port}/video_feed")

    stream = None
    with DroneAPI(
        enable_logging=False,
        enable_file_logging=False,
        enable_command_logging=False,
    ) as drone:
        if not drone.robust_connect(args.ip, verbose=True):
            print("Result: connection failed.")
            print("Check that the computer is connected to the drone Wi-Fi.")
            return 1

        battery = wait_for_battery(drone, args.telemetry_timeout)
        if battery is None:
            print("Telemetry: connected, but battery data was not received yet.")
            print("Retry once before testing the video stream.")
            return 2

        print(f"Battery: {battery}%")
        if battery < args.min_battery:
            print(f"Battery is below {args.min_battery}%; skip the video test for now.")
            return 3

        try:
            print("Starting front camera video stream...")
            stream = drone.start_video_stream(
                display=False,
                web_server=True,
                web_port=args.web_port,
            )
            print("Video stream is running.")
            print(f"Open http://127.0.0.1:{args.web_port}/ in the browser.")
            print("Wait 5-15 seconds for the first frame if the page is black.")
            print("Press Ctrl+C in this terminal to stop the stream.")

            start_time = time.monotonic()
            while True:
                if args.duration_sec > 0 and time.monotonic() - start_time >= args.duration_sec:
                    print("Duration reached. Stopping video stream.")
                    break
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("Stopping video stream after Ctrl+C...")
        finally:
            if stream is not None:
                try:
                    stream.stop()
                except Exception as exc:
                    print(f"Warning: stream.stop() failed: {exc}")
            try:
                drone.set_video_stream(False)
            except Exception as exc:
                print(f"Warning: disabling video stream failed: {exc}")

    print("Step 8 ended: video stream closed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
