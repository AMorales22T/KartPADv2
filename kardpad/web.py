from __future__ import annotations

import asyncio
import http.server
import json
import socket
import ssl
import threading
import time
import uuid

import websockets

from .config import HTTP_PORT, STATIC_DIR, WS_PORT
from .controller import ControllerHub

# Puertos HTTPS/WSS (contexto seguro → giroscopio Android habilitado)
HTTPS_PORT = 3443
WSS_PORT   = 8001

# ── Debug: motion logging ────────────────────────────────────────
_motion_debug_ts: float = 0.0
_motion_debug_count: int = 0


class StaticHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        # Permissions-Policy: permite sensores de movimiento en contexto seguro
        self.send_header("Permissions-Policy", "accelerometer=*, gyroscope=*")
        super().end_headers()

    def log_message(self, format: str, *args) -> None:
        return


def get_local_ips() -> list[str]:
    ips = set()
    try:
        import psutil
        for interface, snics in psutil.net_if_addrs().items():
            for snic in snics:
                if snic.family == socket.AF_INET:
                    ips.add(snic.address)
    except Exception:
        pass
    
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        ips.add(probe.getsockname()[0])
        probe.close()
    except OSError:
        ips.add("127.0.0.1")
        
    # Remove link-local and loopback
    valid_ips = [ip for ip in ips if not ip.startswith("127.") and not ip.startswith("169.254.")]
    return sorted(valid_ips) if valid_ips else ["127.0.0.1"]


def print_qr(url: str) -> None:
    try:
        import qrcode
    except ImportError:
        return

    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    try:
        # invert=True usa caracteres de bloque Unicode: puede fallar en Windows cp1252
        import sys
        if sys.stdout.encoding and sys.stdout.encoding.lower() in ("utf-8", "utf-16"):
            qr.print_ascii(invert=True)
        else:
            # Fallback: imprimir en modo compatible (solo ASCII puro, sin bloques)
            qr.print_ascii(invert=False)
    except (UnicodeEncodeError, Exception):
        # Si falla, simplemente omitimos el QR en la consola; la URL ya se imprimió
        pass


def start_http_server(port: int = HTTP_PORT) -> threading.Thread:
    """Servidor HTTP plano (fallback / APK Capacitor con cleartext)."""
    httpd = http.server.ThreadingHTTPServer(("0.0.0.0", port), StaticHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    print(f"[HTTP] Serving {STATIC_DIR} on http://0.0.0.0:{port}")
    return thread


def start_https_server(ssl_ctx: ssl.SSLContext, port: int = HTTPS_PORT) -> threading.Thread:
    """
    Servidor HTTPS con certificado auto-firmado.
    Chrome Android requiere HTTPS para DeviceMotionEvent (giroscopio).
    El usuario debe aceptar la advertencia del cert la primera vez.
    """
    httpd = http.server.ThreadingHTTPServer(("0.0.0.0", port), StaticHandler)
    httpd.socket = ssl_ctx.wrap_socket(httpd.socket, server_side=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    print(f"[HTTPS] Serving {STATIC_DIR} on https://0.0.0.0:{port}")
    return thread


class MobileGateway:
    def __init__(self, hub: ControllerHub) -> None:
        self._hub = hub

    async def handle_connection(self, websocket) -> None:
        remote = websocket.remote_address
        player_id = None
        session_id = uuid.uuid4().hex

        print(f"[WS] Incoming connection from {remote[0]}:{remote[1]}")
        try:
            raw_message = await asyncio.wait_for(websocket.recv(), timeout=10)
            handshake = json.loads(raw_message)
            player_id = self._parse_player_id(handshake.get("player"))
            self._hub.attach(player_id, session_id)

            await websocket.send(
                json.dumps(
                    {
                        "status": "connected",
                        "player": player_id,
                        "slot": player_id - 1,
                        "mode": "dsu",
                    }
                )
            )
            print(f"[WS] Player {player_id} connected from {remote[0]}")

            async for raw_message in websocket:
                await self._handle_message(player_id, raw_message)
        except asyncio.TimeoutError:
            print(f"[WS] Handshake timeout from {remote[0]}")
        except websockets.exceptions.ConnectionClosed:
            pass
        except json.JSONDecodeError:
            print(f"[WS] Invalid JSON from {remote[0]}")
        except Exception as exc:
            print(f"[WS] Unexpected error for player {player_id}: {exc}")
        finally:
            if player_id is not None:
                self._hub.detach(player_id, session_id)
                print(f"[WS] Player {player_id} disconnected")

    async def _handle_message(self, player_id: int, raw_message: str) -> None:
        try:
            payload = json.loads(raw_message)
        except json.JSONDecodeError:
            return

        message_type = payload.get("type")
        if message_type == "button":
            name = str(payload.get("name", ""))
            action = str(payload.get("action", ""))
            if action == "press":
                self._hub.set_button(player_id, name, True)
            elif action == "release":
                self._hub.set_button(player_id, name, False)
            return

        if message_type == "motion":
            accel = payload.get("accel") or {}
            gyro = payload.get("gyro") or {}
            timestamp_ms = int(payload.get("timestamp", 0) or 0)
            motion_timestamp_us = timestamp_ms * 1000 if timestamp_ms else None

            ax = float(accel.get("x", 0.0))
            ay = float(accel.get("y", 0.0))
            az = float(accel.get("z", 1.0))
            gp = float(gyro.get("pitch", 0.0))
            gy = float(gyro.get("yaw", 0.0))
            gr = float(gyro.get("roll", 0.0))

            # ── Debug: print motion once/sec ──────────────────────
            global _motion_debug_ts, _motion_debug_count
            _motion_debug_count += 1
            now = time.monotonic()
            if now - _motion_debug_ts >= 1.0:
                _motion_debug_ts = now
                print(
                    f"[MOTION P{player_id}] "
                    f"accel=({ax:+.3f}, {ay:+.3f}, {az:+.3f})  "
                    f"gyro=({gp:+.1f}, {gy:+.1f}, {gr:+.1f})  "
                    f"({_motion_debug_count} pkt/s)"
                )
                _motion_debug_count = 0

            self._hub.update_motion(
                player_id,
                (ax, ay, az),
                (gp, gy, gr),
                motion_timestamp_us=motion_timestamp_us,
            )

    def _parse_player_id(self, raw_player_id: object) -> int:
        try:
            player_id = int(raw_player_id)
        except (TypeError, ValueError):
            return 1
        return player_id if 1 <= player_id <= 4 else 1


async def start_websocket_server(gateway: MobileGateway, port: int = WS_PORT) -> None:
    """WS plano (puerto 8000) — usado por la APK Capacitor (cleartext)."""
    print(f"[WS] Listening on ws://0.0.0.0:{port}")
    async with websockets.serve(gateway.handle_connection, "0.0.0.0", port):
        await asyncio.Future()


async def start_wss_server(
    gateway: MobileGateway,
    ssl_ctx: ssl.SSLContext,
    port: int = WSS_PORT,
) -> None:
    """WSS cifrado (puerto 8001) — usado por la web HTTPS (Android Chrome)."""
    print(f"[WSS] Listening on wss://0.0.0.0:{port}")
    async with websockets.serve(gateway.handle_connection, "0.0.0.0", port, ssl=ssl_ctx):
        await asyncio.Future()
