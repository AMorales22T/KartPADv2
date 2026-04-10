#!/usr/bin/env python3
from __future__ import annotations

import asyncio

from kardpad.config import APP_NAME, HTTP_PORT, WS_PORT
from kardpad.controller import ControllerHub
from kardpad.dsu import DSUServer
from kardpad.web import MobileGateway, get_local_ip, print_qr, start_http_server, start_websocket_server


def print_banner(local_ip: str, dsu_port: int) -> None:
    url = f"http://{local_ip}:{HTTP_PORT}"
    print("+" + "-" * 58 + "+")
    print(f"| {APP_NAME:<56} |")
    print("+" + "-" * 58 + "+")
    print(f"| Mobile UI : {url:<44} |")
    print(f"| WebSocket : ws://{local_ip}:{WS_PORT:<37} |")
    print(f"| DSU      : 127.0.0.1:{dsu_port:<42} |")
    print("+" + "-" * 58 + "+")
    print("| 1. Connect the phone to the same Wi-Fi.                |")
    print("| 2. Open the mobile UI and choose a player slot.        |")
    print("| 3. In Dolphin, add DSUClient on udp://127.0.0.1:26760. |")
    print("| 4. Map buttons and motion from DSUClient/<slot> once.  |")
    print("+" + "-" * 58 + "+")


async def main() -> None:
    hub = ControllerHub()
    gateway = MobileGateway(hub)
    dsu_server = DSUServer(hub)

    local_ip = get_local_ip()
    print_banner(local_ip, dsu_server.port)
    print_qr(f"http://{local_ip}:{HTTP_PORT}")

    dsu_server.start()
    start_http_server(HTTP_PORT)
    await start_websocket_server(gateway, WS_PORT)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n[{APP_NAME}] Stopped.")
