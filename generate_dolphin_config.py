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

DSU protocol button -> Dolphin name mapping:
  Y  (mask 0x80 byte2) -> Triangle   <-- TRICK / Shake
  B  (mask 0x40 byte2) -> Cross
  A  (mask 0x20 byte2) -> Circle
  X  (mask 0x10 byte2) -> Square     <-- LOOKBACK (mirar atras)
  R1 (mask 0x08 byte2) -> R1         <-- DRIFT (derrapar)
  L1 (mask 0x04 byte2) -> L1
  R2 (mask 0x02 byte2) -> R2         <-- ACCELERATE (acelerar)
  L2 (mask 0x01 byte2) -> L2         <-- BRAKE (frenar)
  UP   (byte1 0x10)    -> Pad N      <-- ITEM / menu arriba
  DOWN (byte1 0x40)    -> Pad S
  LEFT (byte1 0x80)    -> Pad W
  RIGHT(byte1 0x20)    -> Pad E

KardPad button aliases (controller.py):
  ACCELERATE -> R2      BRAKE -> L2
  DRIFT      -> R1      ITEM  -> UP  (= Pad N)
  LOOKBACK   -> X  (= Square)
  START      -> OPTIONS
  TRICK      -> Y  (= Triangle -> Shake)
"""

from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

DOLPHIN_CONFIG_DIR = Path(os.environ["APPDATA"]) / "Dolphin Emulator" / "Config"

WIIMOTE_INI = DOLPHIN_CONFIG_DIR / "WiimoteNew.ini"
DSU_INI     = DOLPHIN_CONFIG_DIR / "DSUClient.ini"

# ──────────────────────────────────────────────────────────────────────
# WiimoteNew.ini
# ──────────────────────────────────────────────────────────────────────
WIIMOTE_CONTENT = """\
[Wiimote1]
Source = 1
Device = DSUClient/0/
; ── Botones del Wiimote (horizontal) MKWii ───────────────────────────
; Wiimote plano: 2=Acelerar, 1=Frenar, B=Derrapar, A=Mirar atras
; KardPad envia:  ACCELERATE->R2, BRAKE->L2, DRIFT->R1, LOOKBACK->X(Square)
Buttons/A = `Square`
Buttons/B = `R1`
Buttons/1 = `L2`
Buttons/2 = `R2`
Buttons/- = Q
Buttons/+ = `OPTIONS`
Buttons/Home = RETURN
; ── D-Pad — usar item + navegar menus ────────────────────────────────
; KardPad envia: ITEM/UP->Pad N, DOWN->Pad S, LEFT->Pad W, RIGHT->Pad E
; En MKWii con Wiimote plano: D-Pad = usar item en carrera / navegar menus
D-Pad/Up    = `Pad N`
D-Pad/Down  = `Pad S`
D-Pad/Left  = `Pad W`
D-Pad/Right = `Pad E`
; ── IR: puntero con el raton del PC ──────────────────────────────────
IR/Up    = `DInput/0/Keyboard Mouse:Cursor Y-`
IR/Down  = `DInput/0/Keyboard Mouse:Cursor Y+`
IR/Left  = `DInput/0/Keyboard Mouse:Cursor X-`
IR/Right = `DInput/0/Keyboard Mouse:Cursor X+`
; ── Shake (pirueta/truco) ────────────────────────────────────────────
; KardPad TRICK->Y->Triangle.
; Al agitar el movil, el JS envia boton TRICK (Triangle) + spike de aceleracion IMU.
; Mapeamos los tres ejes al mismo boton para maxima compatibilidad.
Shake/X = `Triangle`
Shake/Y = `Triangle`
Shake/Z = `Triangle`
; ── IMU — Acelerometro DSU -> Acelerometro Wiimote virtual ───────────
IMUAccelerometer/Up       = `Accel Up`
IMUAccelerometer/Down     = `Accel Down`
IMUAccelerometer/Left     = `Accel Left`
IMUAccelerometer/Right    = `Accel Right`
IMUAccelerometer/Forward  = `Accel Forward`
IMUAccelerometer/Backward = `Accel Backward`
; ── IMU — Giroscopio DSU -> Giroscopio Wiimote virtual ───────────────
IMUGyroscope/Pitch Up    = `Gyro Pitch Up`
IMUGyroscope/Pitch Down  = `Gyro Pitch Down`
IMUGyroscope/Roll Left   = `Gyro Roll Left`
IMUGyroscope/Roll Right  = `Gyro Roll Right`
IMUGyroscope/Yaw Left    = `Gyro Yaw Left`
IMUGyroscope/Yaw Right   = `Gyro Yaw Right`
; ── Opciones ─────────────────────────────────────────────────────────
Options/Sideways Wiimote = True
Extension/Attach MotionPlus = False
; Sin Extension = None -> MKWii detecta Wiimote plano y activa tilt steering
IRPassthrough/Enabled = True

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

# ──────────────────────────────────────────────────────────────────────
# DSUClient.ini
# ──────────────────────────────────────────────────────────────────────
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
        print(f"[ERROR] No se encontro {DOLPHIN_CONFIG_DIR}")
        print("        Esta Dolphin instalado?")
        sys.exit(1)

    print("=" * 60)
    print("  KardPad — Generador de config Dolphin")
    print("=" * 60)

    bak1 = backup(WIIMOTE_INI)
    bak2 = backup(DSU_INI)
    if bak1:
        print(f"[BACKUP] {bak1.name}")
    if bak2:
        print(f"[BACKUP] {bak2.name}")

    WIIMOTE_INI.write_text(WIIMOTE_CONTENT, encoding="utf-8")
    print(f"[OK] Escrito: {WIIMOTE_INI}")

    DSU_INI.write_text(DSU_CONTENT, encoding="utf-8")
    print(f"[OK] Escrito: {DSU_INI}")

    print()
    print("Cambios aplicados:")
    print("  [OK] Extension = None  (sin Nunchuk -> tilt steering activo)")
    print("  [OK] Sideways Wiimote = True")
    print("  [OK] Buttons/A = Square   <- LOOKBACK  (mirar atras)")
    print("  [OK] Buttons/B = R1       <- DRIFT     (derrapar / hop)")
    print("  [OK] Buttons/2 = R2       <- ACCELERATE (acelerar / confirmar)")
    print("  [OK] Buttons/1 = L2       <- BRAKE     (frenar / cancelar)")
    print("  [OK] D-Pad = Pad N/S/W/E  <- ITEM + navegacion de menus")
    print("  [OK] Shake/X/Y/Z = Triangle <- TRICK  (pirueta al agitar)")
    print("  [OK] IMU Accel/Gyro -> DSU  (volante funciona)")
    print("  [OK] DSU Client -> 127.0.0.1:26760")
    print()
    print("IMPORTANTE: Cierra y vuelve a abrir Dolphin.")
    print()
    print("--- Mapeo completo KardPad -> Wiimote -> MKWii -------------")
    print("  Boton A UI  (ACCELERATE) -> R2       -> Wiimote 2 -> Acelerar")
    print("  Boton B UI  (BRAKE)      -> L2       -> Wiimote 1 -> Frenar")
    print("  Trigger Izq (DRIFT)      -> R1       -> Wiimote B -> Derrapar")
    print("  Boton Atras (LOOKBACK)   -> Square   -> Wiimote A -> Mirar atras")
    print("  Cruceta UP  (ITEM)       -> Pad N    -> D-Pad Up  -> Usar item")
    print("  Cruceta L/R/D            -> Pad W/E/S-> D-Pad     -> Navegar menus")
    print("  Boton +     (START)      -> OPTIONS  -> Wiimote + -> Pausa")
    print("  Agitar movil(TRICK)      -> Triangle -> Shake     -> Pirueta")
    print("  Inclinar    (volante)    -> IMU Accel-> Tilt      -> Girar")
    print("=" * 60)


if __name__ == "__main__":
    main()
