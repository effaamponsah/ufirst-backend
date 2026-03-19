"""
AES-256-GCM field encryption.

Used for storing sensitive PII (bank account numbers, IBANs, etc.) in the
database. The encrypted payload is stored as bytes:

    [12-byte nonce][ciphertext][16-byte GCM tag]

The nonce is randomly generated per encryption — reusing nonces with the same
key is catastrophic for GCM and MUST be avoided.
"""

from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings

_KEY_LEN = 32  # 256-bit


def _load_key() -> bytes:
    raw = settings.encryption_key.strip()
    key = bytes.fromhex(raw)
    if len(key) != _KEY_LEN:
        raise ValueError(
            f"ENCRYPTION_KEY must be a {_KEY_LEN * 2}-character hex string "
            f"({_KEY_LEN} bytes). Got {len(key)} bytes."
        )
    return key


def encrypt(plaintext: str) -> bytes:
    """Encrypt a UTF-8 string. Returns raw bytes suitable for a BYTEA column."""
    key = _load_key()
    nonce = os.urandom(12)          # 96-bit nonce — never reuse
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode(), None)
    # Layout: nonce (12) | ciphertext+tag (variable)
    return nonce + ciphertext


def decrypt(data: bytes) -> str:
    """Decrypt bytes produced by :func:`encrypt`. Returns the original string."""
    if len(data) < 12 + 16:         # nonce + minimum GCM tag
        raise ValueError("Encrypted data is too short to be valid.")
    key = _load_key()
    nonce, ciphertext = data[:12], data[12:]
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext, None).decode()
