"""Local MQTT broker for the offline Hula-Battle demonstration network."""

from __future__ import annotations

import asyncio

from amqtt.broker import Broker


CONFIG = {
    "listeners": {
        "default": {"type": "tcp", "bind": "0.0.0.0:1883"},
        "websocket": {"type": "ws", "bind": "0.0.0.0:9001"},
    },
    "plugins": {"amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True}},
}


async def main() -> None:
    print("Starting offline MQTT broker...", flush=True)
    broker = Broker(CONFIG)
    await broker.start()
    print("Offline MQTT ready: TCP 1883, WebSocket 9001", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
