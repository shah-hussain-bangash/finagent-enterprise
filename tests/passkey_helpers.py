"""Test-only virtual ES256 authenticator; the production verifier is never mocked."""

import hashlib
import json
import secrets
import struct

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from webauthn.helpers import bytes_to_base64url as encode

RP = "desktop-48ai2l6.tail9482f2.ts.net"
ORIGIN = "https://" + RP


class VirtualAuthenticator:
    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = secrets.token_bytes(32)

    def client(self, challenge, kind, origin, **extra):
        return json.dumps(
            {"type": kind, "challenge": challenge, "origin": origin, "crossOrigin": False, **extra}
        ).encode()

    def registration(self, challenge, *, origin=ORIGIN, rp=RP, flags=0x45, **client_extra):
        public = self.key.public_key().public_numbers()
        cose = cbor2.dumps(
            {1: 2, 3: -7, -1: 1, -2: public.x.to_bytes(32, "big"), -3: public.y.to_bytes(32, "big")}
        )
        data = hashlib.sha256(rp.encode()).digest() + bytes([flags]) + struct.pack(">I", 0)
        data += bytes(16) + struct.pack(">H", len(self.credential_id)) + self.credential_id + cose
        return {
            "id": encode(self.credential_id),
            "rawId": encode(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": encode(self.client(challenge, "webauthn.create", origin, **client_extra)),
                "attestationObject": encode(cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": data})),
            },
        }

    def assertion(self, challenge, *, origin=ORIGIN, rp=RP, flags=5, count=1, handle=None, **client_extra):
        client = self.client(challenge, "webauthn.get", origin, **client_extra)
        data = hashlib.sha256(rp.encode()).digest() + bytes([flags]) + struct.pack(">I", count)
        signature = self.key.sign(data + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return {
            "id": encode(self.credential_id),
            "rawId": encode(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": encode(client),
                "authenticatorData": encode(data),
                "signature": encode(signature),
                "userHandle": handle,
            },
        }
