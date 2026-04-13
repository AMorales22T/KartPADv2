"""
kardpad/ssl_cert.py
───────────────────
Genera (o reutiliza) un certificado TLS X.509 auto-firmado para la IP
local del servidor. Esto permite servir la web por HTTPS, lo que es
necesario para que Chrome en Android permita el acceso al giroscopio
(DeviceMotionEvent requiere un contexto seguro / isSecureContext).

Dependencia opcional: `cryptography>=38.0`  (pip install cryptography)
Si el paquete no está disponible, la función devuelve (None, None) y
el servidor continúa solo con HTTP/WS (sin giroscopio web en Android).
"""
from __future__ import annotations

import datetime
import ipaddress
import os
import ssl
from pathlib import Path
from typing import Optional, Tuple

# Rutas de los archivos de certificado (junto al proyecto raíz)
_CERT_FILE = Path(__file__).resolve().parent.parent / "kardpad_cert.pem"
_KEY_FILE  = Path(__file__).resolve().parent.parent / "kardpad_key.pem"

# ──────────────────────────────────────────────────────────────────────
# Función pública
# ──────────────────────────────────────────────────────────────────────

def get_ssl_context(local_ip: str) -> Tuple[Optional[ssl.SSLContext], Optional[ssl.SSLContext]]:
    """
    Devuelve (server_ctx, ws_ctx) listos para usar en HTTPServer y websockets.
    Si no se puede generar el certificado, devuelve (None, None).

    Los certificados se guardan en disco y se reutilizan entre ejecuciones
    siempre que sean válidos. Se regeneran si faltan o si la IP local cambia.
    """
    cert, key = _ensure_cert(local_ip)
    if not cert or not key:
        return None, None

    # Contexto HTTPS
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(certfile=str(cert), keyfile=str(key))

    # Contexto WSS (websockets acepta ssl.SSLContext directamente)
    ws_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ws_ctx.load_cert_chain(certfile=str(cert), keyfile=str(key))

    return server_ctx, ws_ctx


def cert_path() -> Path:
    return _CERT_FILE


# ──────────────────────────────────────────────────────────────────────
# Internals
# ──────────────────────────────────────────────────────────────────────

def _ensure_cert(local_ip: str) -> Tuple[Optional[Path], Optional[Path]]:
    """Regenera el cert si no existe o si la IP del SAN cambió."""
    if _CERT_FILE.exists() and _KEY_FILE.exists():
        if _cert_ip_matches(local_ip):
            return _CERT_FILE, _KEY_FILE
        # IP cambió → regenerar
        print(f"[TLS] IP cambió, regenerando certificado para {local_ip}…")

    return _generate_cert(local_ip)


def _cert_ip_matches(local_ip: str) -> bool:
    """Devuelve True si el cert existente tiene la IP en su SAN."""
    try:
        from cryptography import x509  # noqa: PLC0415
        cert = x509.load_pem_x509_certificate(_CERT_FILE.read_bytes())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        ips = san.value.get_values_for_type(x509.IPAddress)
        return ipaddress.ip_address(local_ip) in ips
    except Exception:
        return False


def _generate_cert(local_ip: str) -> Tuple[Optional[Path], Optional[Path]]:
    """Genera un certificado auto-firmado con la IP en el SAN."""
    try:
        from cryptography import x509                          # noqa: PLC0415
        from cryptography.hazmat.primitives import hashes, serialization  # noqa: PLC0415
        from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: PLC0415
        from cryptography.x509.oid import NameOID              # noqa: PLC0415
    except ImportError:
        print(
            "[TLS] ⚠  'cryptography' no instalado — el servidor correrá solo por HTTP.\n"
            "       Para habilitar HTTPS (giroscopio en Android):\n"
            "         pip install cryptography\n"
        )
        return None, None

    print(f"[TLS] Generando certificado TLS auto-firmado para {local_ip}…")

    # Clave privada RSA 2048
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # Atributos del sujeto
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, local_ip),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "KardPad"),
    ])

    # Validez: 10 años
    now     = datetime.datetime.utcnow()
    not_before = now - datetime.timedelta(seconds=30)
    not_after  = now + datetime.timedelta(days=3650)

    # SAN: IP + localhost
    san = x509.SubjectAlternativeName([
        x509.IPAddress(ipaddress.ip_address(local_ip)),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        x509.DNSName("localhost"),
    ])

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(san, critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    # Guardar PEM
    _CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    _KEY_FILE.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    # Permisos restrictivos en la clave privada (Unix)
    try:
        os.chmod(_KEY_FILE, 0o600)
    except OSError:
        pass

    print(f"[TLS] ✓ Certificado guardado: {_CERT_FILE.name}")
    return _CERT_FILE, _KEY_FILE
