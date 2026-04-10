from __future__ import annotations

import socket
import struct
import threading
import time
import zlib
from dataclasses import dataclass

from .config import (
    DSU_BROADCAST_HZ,
    DSU_CLIENT_TIMEOUT_SECONDS,
    DSU_MESSAGE_DATA,
    DSU_MESSAGE_PORTS,
    DSU_MESSAGE_VERSION,
    DSU_PROTOCOL_VERSION,
    DSU_SERVER_ID,
    PLAYER_COUNT,
    UDP_PORT,
)
from .controller import ControllerHub, PlayerSnapshot


@dataclass(slots=True)
class RegisteredClient:
    address: tuple[str, int]
    slots: frozenset[int] | None
    packet_number: int = 0
    last_seen: float = 0.0


class DSUServer:
    def __init__(self, hub: ControllerHub, host: str = "0.0.0.0", port: int = UDP_PORT) -> None:
        self._hub = hub
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.bind((host, port))
        if socket.getdefaulttimeout() is None:
            self._socket.settimeout(0.5)
        if (
            hasattr(socket, "SIO_UDP_CONNRESET")
            and hasattr(self._socket, "ioctl")
            and socket.gethostname()
        ):
            try:
                self._socket.ioctl(socket.SIO_UDP_CONNRESET, struct.pack("I", 0))
            except OSError:
                pass

        self._clients: dict[tuple[str, int], RegisteredClient] = {}
        self._clients_lock = threading.Lock()
        self._port = port

    def start(self) -> None:
        threading.Thread(target=self._request_loop, daemon=True).start()
        threading.Thread(target=self._broadcast_loop, daemon=True).start()

    @property
    def port(self) -> int:
        return self._port

    def _request_loop(self) -> None:
        print(f"[DSU] Listening on udp://0.0.0.0:{self._port}")
        while True:
            try:
                data, address = self._socket.recvfrom(1024)
            except socket.timeout:
                continue
            except ConnectionResetError:
                continue
            except OSError as exc:
                print(f"[DSU] Socket error: {exc}")
                continue

            if data.startswith(b"DSUC"):
                self._handle_request(data, address)

    def _broadcast_loop(self) -> None:
        frame_time = 1.0 / DSU_BROADCAST_HZ
        while True:
            start = time.perf_counter()
            snapshots = {snapshot.slot: snapshot for snapshot in self._hub.snapshots() if snapshot.connected}
            now = time.monotonic()

            with self._clients_lock:
                stale = [
                    address
                    for address, client in self._clients.items()
                    if now - client.last_seen > DSU_CLIENT_TIMEOUT_SECONDS
                ]
                for address in stale:
                    self._clients.pop(address, None)

                clients = list(self._clients.values())

            for client in clients:
                target_slots = snapshots.keys() if client.slots is None else client.slots
                for slot in target_slots:
                    snapshot = snapshots.get(slot)
                    if snapshot is None:
                        continue
                    packet_number = self._next_packet_number(client.address)
                    if packet_number is None:
                        continue
                    self._send_pad_data(client.address, snapshot, packet_number)

            elapsed = time.perf_counter() - start
            if elapsed < frame_time:
                time.sleep(frame_time - elapsed)

    def _handle_request(self, data: bytes, address: tuple[str, int]) -> None:
        if len(data) < 20:
            return

        try:
            magic, protocol, length, _crc, _client_id, message_type = struct.unpack(
                "<4sHHIII", data[:20]
            )
        except struct.error:
            return

        if magic != b"DSUC" or protocol != DSU_PROTOCOL_VERSION:
            return

        payload = data[20 : 16 + length]
        self._touch_client(address)

        if message_type == DSU_MESSAGE_VERSION:
            self._send_version(address)
            return

        if message_type == DSU_MESSAGE_PORTS:
            self._send_port_info_response(address, payload)
            return

        if message_type == DSU_MESSAGE_DATA:
            self._register_data_client(address, payload)

    def _touch_client(self, address: tuple[str, int]) -> None:
        with self._clients_lock:
            client = self._clients.get(address)
            if client:
                client.last_seen = time.monotonic()

    def _next_packet_number(self, address: tuple[str, int]) -> int | None:
        with self._clients_lock:
            client = self._clients.get(address)
            if client is None:
                return None
            client.packet_number += 1
            return client.packet_number

    def _register_data_client(self, address: tuple[str, int], payload: bytes) -> None:
        if len(payload) < 8:
            return

        registration_flags = payload[0]
        requested_slot = payload[1]
        requested_mac = payload[2:8]

        slots: set[int] | None
        if registration_flags == 0:
            slots = None
        else:
            slots = set()
            if registration_flags & 0x01 and 0 <= requested_slot < PLAYER_COUNT:
                slots.add(requested_slot)
            if registration_flags & 0x02:
                matched_slot = self._match_slot_by_mac(requested_mac)
                if matched_slot is not None:
                    slots.add(matched_slot)
            if not slots:
                return

        with self._clients_lock:
            client = self._clients.setdefault(address, RegisteredClient(address=address, slots=None))
            client.slots = None if slots is None else frozenset(slots)
            client.last_seen = time.monotonic()

        snapshots = self._hub.snapshots()
        for snapshot in snapshots:
            if not snapshot.connected:
                continue
            if client.slots is not None and snapshot.slot not in client.slots:
                continue
            packet_number = self._next_packet_number(address)
            if packet_number is None:
                return
            self._send_pad_data(address, snapshot, packet_number)

    def _match_slot_by_mac(self, requested_mac: bytes) -> int | None:
        for snapshot in self._hub.snapshots():
            if snapshot.mac_address == requested_mac:
                return snapshot.slot
        return None

    def _send_version(self, address: tuple[str, int]) -> None:
        payload = struct.pack("<H", DSU_PROTOCOL_VERSION)
        self._socket.sendto(self._build_packet(DSU_MESSAGE_VERSION, payload), address)

    def _send_port_info_response(self, address: tuple[str, int], payload: bytes) -> None:
        if len(payload) < 4:
            return

        try:
            slot_count = struct.unpack("<i", payload[:4])[0]
        except struct.error:
            return

        if slot_count <= 0:
            slots = range(PLAYER_COUNT)
        else:
            slots = payload[4 : 4 + slot_count]

        snapshots = {snapshot.slot: snapshot for snapshot in self._hub.snapshots()}
        for slot in slots:
            snapshot = snapshots.get(slot)
            if snapshot is None:
                continue
            port_payload = self._build_port_info_payload(snapshot)
            self._socket.sendto(self._build_packet(DSU_MESSAGE_PORTS, port_payload), address)

    def _send_pad_data(
        self,
        address: tuple[str, int],
        snapshot: PlayerSnapshot,
        packet_number: int,
    ) -> None:
        payload = self._build_pad_data_payload(snapshot, packet_number)
        self._socket.sendto(self._build_packet(DSU_MESSAGE_DATA, payload), address)

    def _build_packet(self, message_type: int, payload: bytes) -> bytes:
        packet = bytearray(
            struct.pack(
                "<4sHHII",
                b"DSUS",
                DSU_PROTOCOL_VERSION,
                len(payload) + 4,
                0,
                DSU_SERVER_ID,
            )
        )
        packet.extend(struct.pack("<I", message_type))
        packet.extend(payload)
        crc = zlib.crc32(packet) & 0xFFFFFFFF
        packet[8:12] = struct.pack("<I", crc)
        return bytes(packet)

    def _build_port_info_payload(self, snapshot: PlayerSnapshot) -> bytes:
        state = 2 if snapshot.connected else 0
        model = 2 if snapshot.connected else 0
        connection = 0 if snapshot.connected else 0
        battery = 0x05 if snapshot.connected else 0x00
        mac = snapshot.mac_address if snapshot.connected else b"\x00" * 6
        return struct.pack(
            "<BBBB6sBB",
            snapshot.slot,
            state,
            model,
            connection,
            mac,
            battery,
            0x01 if snapshot.connected else 0x00,
        )

    def _build_pad_data_payload(self, snapshot: PlayerSnapshot, packet_number: int) -> bytes:
        button_1, button_2, home, touch = snapshot.button_bytes()
        left_x, left_y, right_x, right_y = snapshot.stick_center

        payload = bytearray(self._build_port_info_payload(snapshot))
        payload.extend(struct.pack("<I", packet_number))
        payload.extend(
            bytes(
                (
                    button_1,
                    button_2,
                    home,
                    touch,
                    left_x,
                    left_y,
                    right_x,
                    right_y,
                )
            )
        )
        payload.extend(snapshot.analog_bytes())
        payload.extend(struct.pack("<BBHH", 0, 0, 0, 0))
        payload.extend(struct.pack("<BBHH", 0, 0, 0, 0))
        payload.extend(
            struct.pack(
                "<Qffffff",
                snapshot.motion_timestamp_us,
                float(snapshot.accel[0]),
                float(snapshot.accel[1]),
                float(snapshot.accel[2]),
                float(snapshot.gyro[0]),
                float(snapshot.gyro[1]),
                float(snapshot.gyro[2]),
            )
        )
        return bytes(payload)
