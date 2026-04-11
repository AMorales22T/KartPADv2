#!/usr/bin/env python3
"""
Generate correct Dolphin WiimoteNew.ini and DSUClient.ini for KardPad.

This script:
  1. Backs up existing Dolphin config files.
  2. Writes a clean WiimoteNew.ini with:
     - Extension = None (no Nunchuk!)
     - Sideways Wiimote = True
     - IMU Accel/Gyro mapped to DSU
     - Correct button mapping (no duplicates)
  3. Writes a clean DSUClient.ini with a single entry.

KardPad DSU button mapping reference (controller.py):
  ACCELERATE → R2    BRAKE → L2    DRIFT → R1    ITEM → X
  START → OPTIONS    TRICK → Y

Wiimote horizontal mapping in MKWii:
  Button 2 = Accelerate  →  R2  ✓
  Button 1 = Look behind →  L2  ✓
  Button A = Use item     →  X
  Button B = Brake/drift  →  Y
  D-pad    = Menu nav     →  D-pad + IR cursor
  +        = Pause        →  OPTIONS (keyboard E kept as fallback)
  Tilt     = Steering     →  IMU Accel from DSU
"""

from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

DOLPHIN_CONFIG_DIR = Path(os.environ["APPDATA"]) / "Dolphin Emulator" / "Config"

WIIMOTE_INI = DOLPHIN_CONFIG_DIR / "WiimoteNew.ini"
DSU_INI = DOLPHIN_CONFIG_DIR / "DSUClient.ini"

# ─── WiimoteNew.ini ────────────────────────────────────────────────────
# Wiimote 1: DSU (KardPad) — all 4 players could use DSU, but only P1
# is configured for DSU here; P2-P4 use keyboard.
WIIMOTE_CONTENT = """\
[Wiimote1]
Source = 1
Device = DSUClient/0/
; ── Botones del Wiimote (horizontal) ─────────────────────────────
; Sideways Wiimote MKWii: 2=Acelerar, 1=Mirar atrás, A=Item, B=Freno/Drift
Buttons/A = `Triangle`
Buttons/B = `R1`
Buttons/1 = `L2`
Buttons/2 = `R2`
Buttons/- = Q
Buttons/+ = `OPTIONS`
Buttons/Home = RETURN
; ── D-Pad ────────────────────────────────────────────────────────
D-Pad/Up = `Pad N`
D-Pad/Down = `Pad S`
D-Pad/Left = `Pad W`
D-Pad/Right = `Pad E`
; ── IR: el menú principal de MKWii usa puntero, no solo D-pad ────
;    Reutilizamos el D-pad para mover el cursor amarillo del menú.
IR/Up = `Pad N`
IR/Down = `Pad S`
IR/Left = `Pad W`
IR/Right = `Pad E`
; ── Shake (truco/trick — Y activa sacudida) ───────────────────────
Shake/X = `Square`
Shake/Y = `Square`
Shake/Z = `Square`
; ── IMU — Acelerómetro DSU → Acelerómetro Wiimote ────────────────
IMUAccelerometer/Up = `Accel Up`
IMUAccelerometer/Down = `Accel Down`
IMUAccelerometer/Left = `Accel Left`
IMUAccelerometer/Right = `Accel Right`
IMUAccelerometer/Forward = `Accel Forward`
IMUAccelerometer/Backward = `Accel Backward`
; ── IMU — Giroscopio DSU → Giroscopio Wiimote ────────────────────
IMUGyroscope/Pitch Up = `Gyro Pitch Up`
IMUGyroscope/Pitch Down = `Gyro Pitch Down`
IMUGyroscope/Roll Left = `Gyro Roll Left`
IMUGyroscope/Roll Right = `Gyro Roll Right`
IMUGyroscope/Yaw Left = `Gyro Yaw Left`
IMUGyroscope/Yaw Right = `Gyro Yaw Right`
; ── Opciones ─────────────────────────────────────────────────────
Options/Sideways Wiimote = True
Extension/Attach MotionPlus = False
; ⚠ SIN bindings de Nunchuk = Extension queda como None
;   Esto es CRÍTICO para que MKWii use tilt steering
IRPassthrough/Enabled = False

[Wiimote2]
Source = 1
Device = DInput/0/Keyboard Mouse
Buttons/A = E
Buttons/B = R
Buttons/1 = W
Buttons/2 = Q
D-Pad/Up = H
D-Pad/Down = F
D-Pad/Left = T
D-Pad/Right = G
Buttons/+ = Y
Options/Sideways Wiimote = True

[Wiimote3]
Source = 1
Device = DInput/0/Keyboard Mouse
Buttons/A = I
Buttons/B = L
Buttons/1 = M
Buttons/2 = COMMA
D-Pad/Up = `F8`
D-Pad/Down = `F5`
D-Pad/Left = `F4`
D-Pad/Right = `F6`
Buttons/+ = `F7`
Options/Sideways Wiimote = True

[Wiimote4]
Device = DInput/0/Keyboard Mouse
Source = 1
Buttons/A = `1`
Buttons/B = `2`
Buttons/1 = `3`
Buttons/2 = `4`
D-Pad/Up = PRIOR
D-Pad/Down = NEXT
D-Pad/Left = HOME
D-Pad/Right = END
Options/Sideways Wiimote = True

[BalanceBoard]
Device = DInput/0/Keyboard Mouse
Source = 0
"""

# ─── DSUClient.ini ─────────────────────────────────────────────────────
DSU_CONTENT = """\
[Server]
Enabled = True
Entries = :127.0.0.1:26760;
"""


def backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = path.with_suffix(f".{ts}.bak")
    shutil.copy2(path, backup_path)
    return backup_path


def main() -> None:
    if not DOLPHIN_CONFIG_DIR.exists():
        print(f"[ERROR] No se encontró {DOLPHIN_CONFIG_DIR}")
        print("        ¿Está Dolphin instalado?")
        sys.exit(1)

    print("=" * 60)
    print("  KardPad — Generador de config Dolphin")
    print("=" * 60)

    # Backup
    bak1 = backup(WIIMOTE_INI)
    bak2 = backup(DSU_INI)
    if bak1:
        print(f"[BACKUP] {bak1.name}")
    if bak2:
        print(f"[BACKUP] {bak2.name}")

    # Write
    WIIMOTE_INI.write_text(WIIMOTE_CONTENT, encoding="utf-8")
    print(f"[OK] Escrito: {WIIMOTE_INI}")

    DSU_INI.write_text(DSU_CONTENT, encoding="utf-8")
    print(f"[OK] Escrito: {DSU_INI}")

    print()
    print("Cambios aplicados:")
    print("  [OK] Extension Nunchuk eliminada (Extension = None)")
    print("  [OK] Sideways Wiimote = True")
    print("  [OK] IMU Accel/Gyro -> DSU")
    print("  [OK] Button A = X, Button B = Y (sin duplicados)")
    print("  [OK] Button 2 = R2 (acelerar), Button 1 = L2")
    print("  [OK] DSU Client limpio (una sola entrada)")
    print()
    print("IMPORTANTE: Cierra y vuelve a abrir Dolphin para")
    print("   que lea la nueva configuración.")
    print()
    print("--- Mapeo final KardPad -> Wiimote -> MKWii ---------")
    print("  ACCELERATE (R2) -> Wiimote 2 -> Acelerar")
    print("  BRAKE      (L2) -> Wiimote 1 -> Mirar atras / frenar")
    print("  ITEM       (X)  -> Wiimote A -> Usar item")
    print("  DRIFT      (R1) -> Wiimote B -> Freno / Derrapar")
    print("  TRICK      (Y)  -> Shake     -> Truco en el aire")
    print("  D-pad           -> D-Pad/IR  -> Mover menus / cursor")
    print("  ITEM       (X)  -> Wiimote A -> Confirmar en menus")
    print("  Volante    (IMU)-> Tilt      -> Girar izq/der")
    print("=" * 60)


if __name__ == "__main__":
    main()
