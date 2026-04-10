#!/usr/bin/env python3
# ═══════════════════════════════════════════════════════════════════════
#  KardPad — server.py  v1.0
#  Convierte un móvil en mando de Mario Kart para Dolphin Emulator.
#  Basado en la arquitectura de SmashPad (HTTP + WebSocket en paralelo).
#
#  Dependencias:  pip install websockets pynput
#  Opcional:      pip install qrcode pillow   (muestra QR en terminal)
# ═══════════════════════════════════════════════════════════════════════

import asyncio
import http.server
import json
import os
import socket
import threading
import time
import struct
import zlib
from collections import defaultdict, deque

import websockets
from pynput import keyboard, mouse
# ───────────────────────────────────────────────────────────────────────
#  ESTADO DEL MANDO (acelerómetro / giroscopio)
# ───────────────────────────────────────────────────────────────────────

controller_state = {
    "accel_x": 0.0,
    "accel_y": 0.0,
    "accel_z": 1.0,
    "gyro_pitch": 0.0,
    "gyro_roll": 0.0,
    "gyro_yaw": 0.0,
}

# ───────────────────────────────────────────────────────────────────────
#  SOCKET UDP (DSU / cemuhook)
# ───────────────────────────────────────────────────────────────────────

UDP_IP   = "127.0.0.1"
UDP_PORT = 26760

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", 26760))

# Windows puede convertir un ICMP "port unreachable" en WinError 10054
# sobre recvfrom() en sockets UDP. Lo desactivamos para que el hilo DSU
# no muera cuando un cliente deje de escuchar.
if os.name == "nt" and hasattr(socket, "SIO_UDP_CONNRESET"):
    try:
        sock.ioctl(socket.SIO_UDP_CONNRESET, struct.pack("I", 0))
    except (OSError, ValueError):
        pass

DSU_PROTOCOL_VERSION = 1001
DSU_SERVER_ID = 0x4B504144  # "KPAD"
DSU_MESSAGE_VERSION = 0x100000
DSU_MESSAGE_PORTS = 0x100001
DSU_MESSAGE_DATA = 0x100002
DSU_SLOT = 0
DSU_MAC = b"\x4B\x50\x41\x44\x00\x01"
DSU_CLIENT_TIMEOUT = 60.0

dsu_clients = {}
dsu_clients_lock = threading.Lock()
# ───────────────────────────────────────────────────────────────────────
#  CONFIGURACIÓN DE PUERTOS Y RUTAS
# ───────────────────────────────────────────────────────────────────────

HTTP_PORT = 3000
WS_PORT   = 8000
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# ───────────────────────────────────────────────────────────────────────
#  MAPA DE CONTROLES POR JUGADOR  (Dolphin: Wiimote horizontal)
# ───────────────────────────────────────────────────────────────────────

KART_KEY_MAP = {
    1: {
        "ACCELERATE": "w",
        "BRAKE":      "s",
        "DRIFT":      "a",
        "ITEM":       "d",
        "USE_ITEM":   keyboard.Key.space,
        "START":      keyboard.Key.enter,
    },
    2: {
        "ACCELERATE": "i",
        "BRAKE":      "k",
        "DRIFT":      "j",
        "ITEM":       "l",
        "USE_ITEM":   keyboard.Key.tab,
        "START":      keyboard.Key.backspace,
    },
    3: {
        "ACCELERATE": keyboard.Key.up,
        "BRAKE":      keyboard.Key.down,
        "DRIFT":      keyboard.Key.left,
        "ITEM":       keyboard.Key.right,
        "USE_ITEM":   keyboard.Key.ctrl_l,
        "START":      keyboard.Key.shift_l,
    },
    4: {
        "ACCELERATE": keyboard.Key.f5,
        "BRAKE":      keyboard.Key.f6,
        "DRIFT":      keyboard.Key.f7,
        "ITEM":       keyboard.Key.f8,
        "USE_ITEM":   keyboard.Key.f9,
        "START":      keyboard.Key.f10,
    },
}

TILT_KEYS = {
    1: { "LEFT": keyboard.Key.left,  "RIGHT": keyboard.Key.right },
    2: { "LEFT": keyboard.Key.left,  "RIGHT": keyboard.Key.right },
    3: { "LEFT": keyboard.Key.left,  "RIGHT": keyboard.Key.right },
    4: { "LEFT": keyboard.Key.left,  "RIGHT": keyboard.Key.right },
}

# ───────────────────────────────────────────────────────────────────────
#  PARÁMETROS DE INCLINACIÓN
# ───────────────────────────────────────────────────────────────────────

TILT_DEADZONE    = 0.15
TILT_THRESHOLD   = 0.28
TILT_SMOOTH_LEN  = 4

# ───────────────────────────────────────────────────────────────────────
#  PARÁMETROS DE SHAKE
# ───────────────────────────────────────────────────────────────────────

SHAKE_DEBOUNCE_MS = 220

# ───────────────────────────────────────────────────────────────────────
#  INSTANCIAS DE pynput
# ───────────────────────────────────────────────────────────────────────

kb         = keyboard.Controller()
mouse_ctrl = mouse.Controller()

# ───────────────────────────────────────────────────────────────────────
#  ESTADO GLOBAL POR JUGADOR
# ───────────────────────────────────────────────────────────────────────

active_keys:     dict[int, set]      = defaultdict(set)
active_tilt_dir: dict[int, str|None] = defaultdict(lambda: None)
tilt_buffer:     dict[int, deque]    = defaultdict(lambda: deque(maxlen=TILT_SMOOTH_LEN))
last_shake_ts:   dict[int, float]    = defaultdict(float)
pynput_lock = threading.Lock()

# ───────────────────────────────────────────────────────────────────────
#  HELPERS DE TECLADO / RATÓN
# ───────────────────────────────────────────────────────────────────────

def _press(key):
    with pynput_lock:
        try:
            kb.press(key)
        except Exception as e:
            print(f"  [KB] Error al presionar {key}: {e}")

def _release(key):
    with pynput_lock:
        try:
            kb.release(key)
        except Exception as e:
            print(f"  [KB] Error al soltar {key}: {e}")

def _mouse_move(dx: float, dy: float):
    with pynput_lock:
        try:
            mouse_ctrl.move(int(dx), int(dy))
        except Exception as e:
            print(f"  [MOUSE] Error al mover: {e}")

def _mouse_click(action: str):
    with pynput_lock:
        try:
            if action == "press":
                mouse_ctrl.press(mouse.Button.left)
            else:
                mouse_ctrl.release(mouse.Button.left)
        except Exception as e:
            print(f"  [MOUSE] Error click {action}: {e}")

# ───────────────────────────────────────────────────────────────────────
#  LIBERACIÓN TOTAL DE INPUTS DE UN JUGADOR
# ───────────────────────────────────────────────────────────────────────

def _release_all(player_id: int):
    for key in list(active_keys[player_id]):
        _release(key)
    active_keys[player_id].clear()

    tilt_dir = active_tilt_dir[player_id]
    if tilt_dir:
        tilt_key = TILT_KEYS.get(player_id, {}).get(tilt_dir)
        if tilt_key:
            _release(tilt_key)
    active_tilt_dir[player_id] = None
    tilt_buffer[player_id].clear()
    print(f"  [P{player_id}] 🔓 Inputs liberados")

# ───────────────────────────────────────────────────────────────────────
#  PROCESADORES DE INPUT
# ───────────────────────────────────────────────────────────────────────

def handle_button(player_id: int, name: str, action: str):
    key_map = KART_KEY_MAP.get(player_id, {})
    key = key_map.get(name)
    if key is None:
        return

    if action == "press":
        if key not in active_keys[player_id]:
            active_keys[player_id].add(key)
            _press(key)
    elif action == "release":
        if key in active_keys[player_id]:
            active_keys[player_id].discard(key)
            _release(key)


def handle_tilt(player_id: int, value: float):
    tilt_buffer[player_id].append(value)
    buf = tilt_buffer[player_id]
    smoothed = sum(buf) / len(buf)

    if abs(smoothed) < TILT_DEADZONE:
        smoothed = 0.0

    new_dir: str | None = None
    if smoothed > TILT_THRESHOLD:
        new_dir = "RIGHT"
    elif smoothed < -TILT_THRESHOLD:
        new_dir = "LEFT"

    old_dir = active_tilt_dir[player_id]
    if new_dir == old_dir:
        return

    tilt_keys = TILT_KEYS.get(player_id, {})
    if old_dir:
        old_key = tilt_keys.get(old_dir)
        if old_key:
            _release(old_key)
    if new_dir:
        new_key = tilt_keys.get(new_dir)
        if new_key:
            _press(new_key)

    active_tilt_dir[player_id] = new_dir


def handle_shake(player_id: int, intensity: float):
    now = time.time()
    elapsed_ms = (now - last_shake_ts[player_id]) * 1000
    if elapsed_ms < SHAKE_DEBOUNCE_MS:
        return

    last_shake_ts[player_id] = now
    key_map = KART_KEY_MAP.get(player_id, {})
    key = key_map.get("USE_ITEM")
    if key:
        _press(key)
        threading.Timer(0.08, _release, args=(key,)).start()
        print(f"  [P{player_id}] 💥 Shake! (intensidad {intensity:.2f})")


def handle_pointer_move(player_id: int, x: float, y: float, screen_w: int, screen_h: int):
    abs_x = int(x * screen_w)
    abs_y = int(y * screen_h)
    with pynput_lock:
        try:
            mouse_ctrl.position = (abs_x, abs_y)
        except Exception:
            pass

# ───────────────────────────────────────────────────────────────────────
#  DETECCIÓN DE IP LOCAL
# ───────────────────────────────────────────────────────────────────────

def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

# ───────────────────────────────────────────────────────────────────────
#  MANEJADOR WEBSOCKET
# ───────────────────────────────────────────────────────────────────────

SCREEN_W = 1920
SCREEN_H = 1080

async def handle_connection(websocket):
    player_id = None
    remote = websocket.remote_address
    print(f"\n[WS] 🔌 Conexión nueva desde {remote[0]}:{remote[1]}")

    try:
        raw = await asyncio.wait_for(websocket.recv(), timeout=10)
        msg = json.loads(raw)
        player_id = int(msg.get("player", 1))
        if player_id not in range(1, 5):
            player_id = 1

        await websocket.send(json.dumps({
            "status": "connected",
            "player": player_id,
            "mode":   "kart",
        }))
        print(f"  [P{player_id}] 🎮 Conectado ({remote[0]})")

        async for raw in websocket:
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                continue

            msg_type = data.get("type")

            if msg_type == "button":
                handle_button(player_id, data.get("name", ""), data.get("action", ""))

            elif msg_type == "tilt":
                # Actualiza el estado de giroscopio para DSU/UDP
                value = float(data.get("value", 0))
                controller_state["gyro_yaw"] = value
                # _press() con teclas izq/der desactivado — ahora se usa DSU/UDP
                # handle_tilt(player_id, value)

            elif msg_type == "shake":
                handle_shake(player_id, float(data.get("intensity", 1.0)))

            elif msg_type == "pointer_move":
                handle_pointer_move(
                    player_id,
                    float(data.get("x", 0.5)),
                    float(data.get("y", 0.5)),
                    data.get("screen_w", SCREEN_W),
                    data.get("screen_h", SCREEN_H),
                )

            elif msg_type == "pointer_click":
                _mouse_click(data.get("action", "press"))

    except asyncio.TimeoutError:
        print(f"  [WS] ⏱ Timeout esperando handshake de {remote[0]}")
    except websockets.exceptions.ConnectionClosedOK:
        pass
    except websockets.exceptions.ConnectionClosedError as e:
        print(f"  [P{player_id}] ⚠ Conexión cerrada con error: {e}")
    except Exception as e:
        print(f"  [P{player_id}] ❌ Error inesperado: {e}")
    finally:
        if player_id is not None:
            _release_all(player_id)
            print(f"  [P{player_id}] 👋 Desconectado")

# ───────────────────────────────────────────────────────────────────────
#  SERVIDOR HTTP (archivos estáticos)
# ───────────────────────────────────────────────────────────────────────

class StaticHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def log_message(self, format, *args):
        pass


def start_http_server():
    httpd = http.server.HTTPServer(("0.0.0.0", HTTP_PORT), StaticHandler)
    print(f"[HTTP] Servidor estático en puerto {HTTP_PORT}")
    httpd.serve_forever()

# ───────────────────────────────────────────────────────────────────────
#  QR OPCIONAL EN TERMINAL
# ───────────────────────────────────────────────────────────────────────

def print_qr(url: str):
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except ImportError:
        pass

# ───────────────────────────────────────────────────────────────────────
#  DSU / CEMUHOOK — envío UDP de datos de movimiento a Dolphin
# ───────────────────────────────────────────────────────────────────────

def send_dsu_packet():
    """
    Envía un paquete DSU (cemuhook) con el estado actual del giroscopio
    y acelerómetro.

    Formato: "<4sHHiIIBBBBBBffffffff"  →  20 valores, 58 bytes
      4s  magic         "DSUS"
      H   protocol      1001
      H   length        0  (relleno)
      i   crc           0  (relleno)
      I   server_id
      I   timestamp     uint32 (ms, recortado para evitar overflow)
      B   slot
      B   state
      B   model
      B   connection
      B   battery
      B   buttons
      fff accel x y z
      fff gyro  pitch roll yaw
      ff  padding
    """
    timestamp = int(time.time() * 1000) & 0xFFFFFFFF # recortar a uint32

    packet = struct.pack(
        "<4sHHiIIBBBBBBffffffff",
        b"DSUS",        # magic
        1001,           # protocol_version
        0,              # length  (relleno)
        0,              # crc     (relleno)
        0x12345678,     # server_id
        timestamp,      # timestamp uint32
        0,              # slot
        2,              # state
        2,              # model
        0,              # connection
        0x05,           # battery
        0,              # buttons
        controller_state["accel_x"],
        controller_state["accel_y"],
        controller_state["accel_z"],
        controller_state["gyro_pitch"],
        controller_state["gyro_roll"],
        controller_state["gyro_yaw"],
        0.0,            # padding
        0.0,            # padding
    )
    sock.sendto(packet, (UDP_IP, UDP_PORT))

def send_dsu_response(addr):
    timestamp = int(time.time() * 1000) & 0xFFFFFFFF

    fields = (
        b"DSUS",
        1001,
        84,
        0,
        0x12345678,
        timestamp,
        0,
        2,
        2,
        0,
        0x05,
        0,
        float(controller_state["accel_x"]),
        float(controller_state["accel_y"]),
        float(controller_state["accel_z"]),
        float(controller_state["gyro_pitch"]),
        float(controller_state["gyro_roll"]),
        float(controller_state["gyro_yaw"]),
        0.0,
        0.0,
    )

    assert len(fields) == 20, f"Esperaba 20 campos, tengo {len(fields)}"

    packet = struct.pack("<4sHHIIBBBBBBffffffff", *fields)
    sock.sendto(packet, addr)
def dsu_loop():
    send_dsu_handshake()  # 👈 IMPORTANTE

    while True:
        send_dsu_packet()
        time.sleep(1 / 60)
def dsu_server_loop():
    print("[DSU] Escuchando en UDP 26760...")

    while True:
        data, addr = sock.recvfrom(1024)

        if data.startswith(b"DSUC"):
            print(f"[DSU] Petición de {addr}")

            send_dsu_response(addr)
# ───────────────────────────────────────────────────────────────────────
#  PUNTO DE ENTRADA
# ───────────────────────────────────────────────────────────────────────

def dsu_build_packet(message_type, payload):
    header = struct.pack(
        "<4sHHII",
        b"DSUS",
        DSU_PROTOCOL_VERSION,
        len(payload) + 4,
        0,
        DSU_SERVER_ID,
    )
    packet = header + struct.pack("<I", message_type) + payload
    crc = zlib.crc32(packet) & 0xFFFFFFFF
    return packet[:8] + struct.pack("<I", crc) + packet[12:]


def dsu_port_header(slot, connected):
    if connected:
        state = 2
        model = 2
        connection = 0
        mac = DSU_MAC
        battery = 0x05
    else:
        state = 0
        model = 0
        connection = 0
        mac = b"\x00" * 6
        battery = 0x00

    return struct.pack("<BBBB6sB", slot, state, model, connection, mac, battery)


def send_dsu_version(addr):
    payload = struct.pack("<H", DSU_PROTOCOL_VERSION)
    sock.sendto(dsu_build_packet(DSU_MESSAGE_VERSION, payload), addr)


def send_dsu_port_info(addr, slot):
    connected = slot == DSU_SLOT
    payload = dsu_port_header(slot, connected) + b"\x00"
    sock.sendto(dsu_build_packet(DSU_MESSAGE_PORTS, payload), addr)


def send_dsu_data(addr, packet_number):
    motion_timestamp = time.time_ns() // 1000

    payload = bytearray()
    payload.extend(dsu_port_header(DSU_SLOT, True))
    payload.extend(struct.pack("<B", 1))
    payload.extend(struct.pack("<I", packet_number))
    payload.extend(
        bytes(
            [
                0,
                0,
                0,
                0,
                128,
                128,
                128,
                128,
                0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
            ]
        )
    )
    payload.extend(struct.pack("<BBHH", 0, 0, 0, 0))
    payload.extend(struct.pack("<BBHH", 0, 0, 0, 0))
    payload.extend(
        struct.pack(
            "<Qffffff",
            motion_timestamp,
            float(controller_state["accel_x"]),
            float(controller_state["accel_y"]),
            float(controller_state["accel_z"]),
            float(controller_state["gyro_pitch"]),
            float(controller_state["gyro_yaw"]),
            float(controller_state["gyro_roll"]),
        )
    )

    sock.sendto(dsu_build_packet(DSU_MESSAGE_DATA, bytes(payload)), addr)


def dsu_register_client(addr):
    with dsu_clients_lock:
        client = dsu_clients.setdefault(addr, {"packet_number": 0})
        client["last_seen"] = time.monotonic()
        client["packet_number"] += 1
        packet_number = client["packet_number"]

    send_dsu_data(addr, packet_number)


def handle_dsu_request(data, addr):
    if len(data) < 20:
        return

    magic, protocol, length, _crc, _client_id, message_type = struct.unpack(
        "<4sHHIII", data[:20]
    )
    if magic != b"DSUC" or protocol != DSU_PROTOCOL_VERSION:
        return

    payload = data[20 : 16 + length]

    if message_type == DSU_MESSAGE_VERSION:
        send_dsu_version(addr)
        return

    if message_type == DSU_MESSAGE_PORTS:
        if len(payload) < 4:
            return
        requested = struct.unpack("<i", payload[:4])[0]
        slots = payload[4 : 4 + max(0, requested)]
        for slot in slots:
            send_dsu_port_info(addr, slot)
        return

    if message_type != DSU_MESSAGE_DATA or len(payload) < 8:
        return

    reg_flags = payload[0]
    slot = payload[1]
    mac = payload[2:8]

    wants_all = reg_flags == 0
    wants_slot = bool(reg_flags & 0x01) and slot == DSU_SLOT
    wants_mac = bool(reg_flags & 0x02) and mac == DSU_MAC

    if wants_all or wants_slot or wants_mac:
        dsu_register_client(addr)


def dsu_broadcast_loop():
    while True:
        now = time.monotonic()
        targets = []

        with dsu_clients_lock:
            stale = [
                addr
                for addr, client in dsu_clients.items()
                if now - client["last_seen"] > DSU_CLIENT_TIMEOUT
            ]
            for addr in stale:
                dsu_clients.pop(addr, None)

            for addr, client in dsu_clients.items():
                client["packet_number"] += 1
                targets.append((addr, client["packet_number"]))

        for addr, packet_number in targets:
            send_dsu_data(addr, packet_number)

        time.sleep(1 / 60)


def dsu_server_loop():
    print(f"[DSU] Servidor DSU escuchando en udp://0.0.0.0:{UDP_PORT}")

    while True:
        try:
            data, addr = sock.recvfrom(1024)
        except ConnectionResetError:
            continue
        if data.startswith(b"DSUC"):
            print(f"[DSU] Petición de {addr}")
            handle_dsu_request(data, addr)


async def main():
    local_ip = get_local_ip()
    url = f"http://{local_ip}:{HTTP_PORT}"

    print("╔══════════════════════════════════════════════╗")
    print("║          KardPad — Mario Kart Server         ║")
    print("╠══════════════════════════════════════════════╣")
    print(f"║  Mando:    {url:<34} ║")
    print(f"║  WS:       ws://{local_ip}:{WS_PORT:<27} ║")
    print("╠══════════════════════════════════════════════╣")
    print("║  1. Conecta el móvil a la misma Wi-Fi        ║")
    print("║  2. Abre la URL en el navegador del móvil    ║")
    print("║  3. Arranca Dolphin y carga Mario Kart Wii   ║")
    print("║  4. DSU motion: 127.0.0.1:26760 (slot 0)    ║")
    print("║  Ctrl+C para salir                           ║")
    print("╚══════════════════════════════════════════════╝\n")

    print_qr(url)

    # Hilos DSU (servidor y broadcast ~60 fps)
    threading.Thread(target=dsu_server_loop, daemon=True).start()
    threading.Thread(target=dsu_broadcast_loop, daemon=True).start()

    # Hilo HTTP
    http_thread = threading.Thread(target=start_http_server, daemon=True)
    http_thread.start()

    # Servidor WebSocket principal
    print(f"[WS]  Escuchando en ws://0.0.0.0:{WS_PORT}\n")
    async with websockets.serve(handle_connection, "0.0.0.0", WS_PORT):
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n[KardPad] Servidor detenido. ¡Hasta la próxima! 🏁")
