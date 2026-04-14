#!/usr/bin/env python3
from __future__ import annotations

import asyncio

from kardpad.config import APP_NAME, HTTP_PORT, WS_PORT
from kardpad.controller import ControllerHub
from kardpad.dsu import DSUServer
from kardpad.ssl_cert import get_ssl_context
from kardpad.web import (
    HTTPS_PORT,
    WSS_PORT,
    MobileGateway,
    get_local_ips,
    print_qr,
    start_http_server,
    start_https_server,
    start_websocket_server,
    start_wss_server,
)


def print_banner(local_ip: str, dsu_port: int, https_enabled: bool) -> None:
    http_url  = f"http://{local_ip}:{HTTP_PORT}"
    https_url = f"https://{local_ip}:{HTTPS_PORT}"
    ws_url    = f"ws://{local_ip}:{WS_PORT}"
    wss_url   = f"wss://{local_ip}:{WSS_PORT}"

    print("+" + "-" * 62 + "+")
    print(f"| {APP_NAME} - Red: {local_ip:<36} |")
    print("+" + "-" * 62 + "+")
    if https_enabled:
        print(f"| Android/Web (HTTPS) : {https_url:<39} |")
        print(f"| Fallback HTTP       : {http_url:<39} |")
        print(f"| WebSocket (WSS)     : {wss_url:<39} |")
        print(f"| WebSocket (WS)      : {ws_url:<39} |")
    else:
        print(f"| Mobile UI  : {http_url:<48} |")
        print(f"| WebSocket  : {ws_url:<48} |")
    print(f"| DSU        : 127.0.0.1:{dsu_port:<46} |")
    print("+" + "-" * 62 + "+")


async def main() -> None:
    hub     = ControllerHub()
    gateway = MobileGateway(hub)
    dsu_server = DSUServer(hub)

    local_ips = get_local_ips()
    # ── TLS: intentar generar/reutilizar certificado auto-firmado ──
    # Usamos la primera IP (o la de eduroam) para el cert si se requiere
    server_ssl, ws_ssl = get_ssl_context(local_ips[0])
    https_enabled = server_ssl is not None

    for ip in local_ips:
        print_banner(ip, dsu_server.port, https_enabled)
        # QR: preferir la URL HTTPS (activa el giroscopio en Android)
        qr_url = f"https://{ip}:{HTTPS_PORT}" if https_enabled else f"http://{ip}:{HTTP_PORT}"
        print_qr(qr_url)
        print()

    if https_enabled:
        print("★ ANDROID: Abre la URL HTTPS en Chrome.")
        print("  1a vez: pulsa 'Avanzado' → 'Continuar' (cert auto-firmado)")
        print("  Luego el giroscopio funcionará correctamente.\n")
    print("Conecta el móvil a la misma red (ej. Punto de acceso) que el PC.")
    print("En Dolphin añade DSUClient en udp://127.0.0.1:26760.")

    dsu_server.start()

    # HTTP plano siempre activo (APK Capacitor usa cleartext)
    start_http_server(HTTP_PORT)

    # HTTPS si el certificado está disponible
    if https_enabled:
        start_https_server(server_ssl, HTTPS_PORT)

    # WS plano siempre activo
    ws_task = asyncio.create_task(start_websocket_server(gateway, WS_PORT))

    # WSS si el certificado está disponible
    if https_enabled and ws_ssl:
        wss_task = asyncio.create_task(start_wss_server(gateway, ws_ssl, WSS_PORT))
        await asyncio.gather(ws_task, wss_task)
    else:
        await ws_task


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n[{APP_NAME}] Stopped.")
