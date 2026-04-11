from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Iterable

from .config import PLAYER_COUNT


BUTTON_ALIASES = {
    "ACCELERATE": "R2",
    "BRAKE": "L2",

    "DRIFT": "R1",
    "ITEM": "UP",
    "LOOKBACK": "X",
    "START": "OPTIONS",
    "TRICK": "Y",
    "A": "A",
    "B": "B",
    "X": "X",
    "Y": "Y",
    "R1": "R1",
    "L1": "L1",
    "R2": "R2",
    "L2": "L2",
    "OPTIONS": "OPTIONS",
    "HOME": "HOME",
    "UP": "UP",
    "DOWN": "DOWN",
    "LEFT": "LEFT",
    "RIGHT": "RIGHT",
}

BUTTON_ANALOG_ORDER = (
    "LEFT",
    "DOWN",
    "RIGHT",
    "UP",
    "Y",
    "B",
    "A",
    "X",
    "R1",
    "L1",
    "R2",
    "L2",
)

BUTTON_MASKS_1 = {
    "LEFT": 0x80,
    "DOWN": 0x40,
    "RIGHT": 0x20,
    "UP": 0x10,
    "OPTIONS": 0x08,
}

BUTTON_MASKS_2 = {
    "Y": 0x80,
    "B": 0x40,
    "A": 0x20,
    "X": 0x10,
    "R1": 0x08,
    "L1": 0x04,
    "R2": 0x02,
    "L2": 0x01,
}

HOME_MASK = 0x01
DEFAULT_ACCEL = (0.0, 0.0, 1.0)
DEFAULT_GYRO = (0.0, 0.0, 0.0)


@dataclass(slots=True)
class PlayerSnapshot:
    player_id: int
    slot: int
    connected: bool
    buttons: frozenset[str]
    accel: tuple[float, float, float]
    gyro: tuple[float, float, float]
    motion_timestamp_us: int
    mac_address: bytes

    @property
    def stick_center(self) -> tuple[int, int, int, int]:
        return (128, 128, 128, 128)

    def button_bytes(self) -> tuple[int, int, int, int]:
        mask_1 = 0
        mask_2 = 0
        home = 0
        touch = 0

        for name in self.buttons:
            mask_1 |= BUTTON_MASKS_1.get(name, 0)
            mask_2 |= BUTTON_MASKS_2.get(name, 0)
            if name == "HOME":
                home |= HOME_MASK

        return mask_1, mask_2, home, touch

    def analog_bytes(self) -> bytes:
        values = bytearray()
        for name in BUTTON_ANALOG_ORDER:
            values.append(0xFF if name in self.buttons else 0x00)
        return bytes(values)


@dataclass(slots=True)
class PlayerState:
    player_id: int
    slot: int
    mac_address: bytes
    connected: bool = False
    buttons: set[str] = field(default_factory=set)
    accel: tuple[float, float, float] = DEFAULT_ACCEL
    gyro: tuple[float, float, float] = DEFAULT_GYRO
    motion_timestamp_us: int = 0
    last_update_monotonic: float = field(default_factory=time.monotonic)

    def reset_runtime_state(self) -> None:
        self.buttons.clear()
        self.accel = DEFAULT_ACCEL
        self.gyro = DEFAULT_GYRO
        self.motion_timestamp_us = int(time.time_ns() // 1000)
        self.last_update_monotonic = time.monotonic()

    def snapshot(self) -> PlayerSnapshot:
        return PlayerSnapshot(
            player_id=self.player_id,
            slot=self.slot,
            connected=self.connected,
            buttons=frozenset(self.buttons),
            accel=self.accel,
            gyro=self.gyro,
            motion_timestamp_us=self.motion_timestamp_us or int(time.time_ns() // 1000),
            mac_address=self.mac_address,
        )


class ControllerHub:
    def __init__(self, player_count: int = PLAYER_COUNT) -> None:
        self._lock = threading.Lock()
        self._players = {
            player_id: PlayerState(
                player_id=player_id,
                slot=player_id - 1,
                mac_address=bytes((0x4B, 0x50, 0x41, 0x44, 0x00, player_id)),
            )
            for player_id in range(1, player_count + 1)
        }
        self._sessions: dict[int, str] = {}

    def attach(self, player_id: int, session_id: str) -> None:
        with self._lock:
            state = self._players[player_id]
            state.connected = True
            state.reset_runtime_state()
            self._sessions[player_id] = session_id

    def detach(self, player_id: int, session_id: str) -> None:
        with self._lock:
            if self._sessions.get(player_id) != session_id:
                return
            self._sessions.pop(player_id, None)
            state = self._players[player_id]
            state.connected = False
            state.reset_runtime_state()

    def set_button(self, player_id: int, raw_name: str, pressed: bool) -> None:
        name = BUTTON_ALIASES.get(raw_name.upper())
        if not name:
            return

        with self._lock:
            state = self._players[player_id]
            if pressed:
                state.buttons.add(name)
            else:
                state.buttons.discard(name)
            state.last_update_monotonic = time.monotonic()

    def update_motion(
        self,
        player_id: int,
        accel: Iterable[float],
        gyro: Iterable[float],
        motion_timestamp_us: int | None = None,
    ) -> None:
        accel_xyz = tuple(float(value) for value in accel)
        gyro_xyz = tuple(float(value) for value in gyro)
        if len(accel_xyz) != 3 or len(gyro_xyz) != 3:
            return

        with self._lock:
            state = self._players[player_id]
            state.accel = accel_xyz
            state.gyro = gyro_xyz
            state.motion_timestamp_us = motion_timestamp_us or int(time.time_ns() // 1000)
            state.last_update_monotonic = time.monotonic()

    def snapshots(self) -> list[PlayerSnapshot]:
        with self._lock:
            return [state.snapshot() for state in self._players.values()]

    def snapshot_for_slot(self, slot: int) -> PlayerSnapshot | None:
        player_id = slot + 1
        with self._lock:
            state = self._players.get(player_id)
            return state.snapshot() if state else None
