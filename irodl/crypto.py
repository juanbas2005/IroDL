"""Criptografía de tokens .irodl.

token = b64url( IV[16] || AES-256-CBC( PKCS7( ruta_relativa ) ) )
key   = SHA-256(seed)[32]

Misma mecánica que el protocolo invertido de UDYAT, con semilla propia de
IroDL (ver docs/FORMATO_IRODL.md). Sin HMAC ni capas extra: el token es
confidencialidad de la ruta, no autenticación.
"""
from __future__ import annotations

import base64
import hashlib
import os
import urllib.parse

from Crypto.Cipher import AES

#: Semilla ASCII propietaria de IroDL (viaja compilada en clientes, como en el diseño original).
IRODL_SEED = "eb4c353716dbf7e763149b2fa90981bb32614eceaba1ef930ebba3cf577ad9bf"

#: Clave AES-256 derivada (referencia cruzada: docs/FORMATO_IRODL.md).
IRODL_KEY = hashlib.sha256(IRODL_SEED.encode("ascii")).digest()

SCHEME = "irodl://"


class TokenError(ValueError):
    """Token malformado o no descifrable."""


def _pkcs7_pad(data: bytes) -> bytes:
    n = 16 - (len(data) % 16)
    return data + bytes([n]) * n


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data or len(data) % 16:
        raise TokenError("ciphertext no es múltiplo de bloque")
    n = data[-1]
    if not 1 <= n <= 16 or data[-n:] != bytes([n]) * n:
        raise TokenError("padding PKCS7 inválido")
    return data[:-n]


def encrypt_path(rel_path: str, *, key: bytes = IRODL_KEY, iv: bytes | None = None) -> str:
    """Cifra una ruta relativa y devuelve el token b64url (sin '=')."""
    if not rel_path or rel_path.startswith("/"):
        raise ValueError("la ruta debe ser relativa y no vacía")
    iv = iv if iv is not None else os.urandom(16)
    if len(iv) != 16:
        raise ValueError("IV debe ser de 16 bytes")
    ct = AES.new(key, AES.MODE_CBC, iv).encrypt(_pkcs7_pad(rel_path.encode("utf-8")))
    return base64.urlsafe_b64encode(iv + ct).decode("ascii").rstrip("=")


def decrypt_token(token: str, *, key: bytes = IRODL_KEY) -> str:
    """Descifra un token b64url y devuelve la ruta relativa."""
    tok = token.strip()
    if tok.startswith(SCHEME):
        tok = tok[len(SCHEME):]
    try:
        raw = base64.urlsafe_b64decode(tok + "=" * (-len(tok) % 4))
    except Exception as exc:  # binascii.Error
        raise TokenError(f"token no es b64url válido: {exc}") from exc
    if len(raw) < 32 or len(raw) % 16:
        raise TokenError("token demasiado corto o tamaño inválido")
    iv, ct = raw[:16], raw[16:]
    pt = AES.new(key, AES.MODE_CBC, iv).decrypt(ct)
    try:
        return _pkcs7_unpad(pt).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TokenError("clave incorrecta o token corrupto") from exc


def resolve_url(token_or_url: str, host: str | None = None, default_host: str | None = None) -> str:
    """Convierte `irodl://<token>` (o token pelado) en URL absoluta de descarga.

    Acepta también URLs `https://.../d/<token>` (portadores tipo los del
    protocolo original): se extrae el segmento final como token.
    """
    tok = token_or_url.strip()
    if tok.startswith(("http://", "https://")):
        tok = tok.rsplit("/d/", 1)[-1].split("?")[0]
    path = decrypt_token(tok)  # valida clave y padding
    base = (host or default_host or "https://s3.todus.cu/stream/")
    if not base.endswith("/"):
        base += "/"
    return base + urllib.parse.quote(path)
