"""Small, explicit crypto helpers (prompt §7 "encrypted payloads", §21 "authenticate producers").

* `Encryptor` - AES-256-GCM. The associated data binds a ciphertext to its tenant and record, so a
  blob copied into another tenant's or run's row fails authentication instead of decrypting.
* `sign` / `verify` - HMAC-SHA256 over canonical JSON for event envelopes.

Keys come from Settings (Key Vault in deployed environments). Rotation: the 1-byte key id in each
ciphertext lets a future key ring decrypt old data; R1 uses a single key (id 1).
"""

from __future__ import annotations

import hashlib
import hmac
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_VERSION = b"\x01"
_KEY_ID = b"\x01"
_NONCE = 12


class DecryptionError(Exception):
    """Ciphertext is corrupt, was tampered with, or belongs to another tenant/record."""


class Encryptor:
    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("AES-256-GCM needs a 32-byte key")
        self._aead = AESGCM(key)

    def encrypt(self, plaintext: bytes, aad: bytes) -> bytes:
        nonce = os.urandom(_NONCE)
        return _VERSION + _KEY_ID + nonce + self._aead.encrypt(nonce, plaintext, aad)

    def decrypt(self, blob: bytes, aad: bytes) -> bytes:
        if len(blob) < 2 + _NONCE + 16 or blob[:1] != _VERSION or blob[1:2] != _KEY_ID:
            raise DecryptionError("unsupported ciphertext format")
        nonce, body = blob[2 : 2 + _NONCE], blob[2 + _NONCE :]
        try:
            return self._aead.decrypt(nonce, body, aad)
        except InvalidTag as exc:
            raise DecryptionError("ciphertext failed authentication") from exc


def sign(key: bytes, data: bytes) -> str:
    return hmac.new(key, data, hashlib.sha256).hexdigest()


def verify(key: bytes, data: bytes, signature: str) -> bool:
    return hmac.compare_digest(sign(key, data), signature)
