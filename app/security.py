"""Bao mat van hanh Pha K5: Ma hoa du lieu sinh trac hoc nghi AES-256-GCM & Phan quyen RBAC 3 vai tro (viewer / hr / admin)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from fastapi import HTTPException, Request
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC_PREFIX = b"FG1$"
ROLE_RANK = {"viewer": 1, "hr": 2, "admin": 3}


class BiometricCipher:
    """Ma hoa / giai ma AES-256-GCM cho vector embedding va file anh mau trong data/enroll/ (T030)."""

    def __init__(self, raw_key: bytes):
        if len(raw_key) != 32:
            raw_key = hashlib.sha256(raw_key).digest()
        self._aes = AESGCM(raw_key)

    @classmethod
    def from_env_or_cfg(cls, cfg: dict | None = None) -> "BiometricCipher | None":
        secret = os.environ.get("FACEGATE_BIOMETRIC_KEY", "").strip()
        if not secret and cfg:
            secret = str((cfg.get("security") or {}).get("biometric_key") or "").strip()
        if not secret:
            return None
        try:
            decoded = base64.urlsafe_b64decode(secret + "===")
            key_bytes = decoded if len(decoded) == 32 else hashlib.sha256(secret.encode("utf-8")).digest()
        except Exception:  # noqa: BLE001
            key_bytes = hashlib.sha256(secret.encode("utf-8")).digest()
        return cls(key_bytes)

    def encrypt_bytes(self, plaintext: bytes) -> bytes:
        nonce = os.urandom(12)
        ct = self._aes.encrypt(nonce, plaintext, None)
        return MAGIC_PREFIX + nonce + ct

    def decrypt_bytes(self, blob: bytes) -> bytes:
        if not blob.startswith(MAGIC_PREFIX):
            return blob  # Tuong thich nguoc voi embedding cu chua ma hoa truoc K5
        nonce = blob[len(MAGIC_PREFIX):len(MAGIC_PREFIX) + 12]
        ct = blob[len(MAGIC_PREFIX) + 12:]
        return self._aes.decrypt(nonce, ct, None)


def issue_rbac_token(username: str, role: str, secret: str, ttl_sec: int = 28800) -> str:
    if role not in ROLE_RANK:
        raise ValueError(f"Vai tro khong hop le: {role}")
    payload = {"sub": username, "role": role, "exp": int(time.time()) + ttl_sec}
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).rstrip(b"=")
    sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).rstrip(b"=")
    return f"{body.decode('ascii')}.{sig_b64.decode('ascii')}"


def verify_rbac_token(token: str, secret: str) -> dict:
    parts = (token or "").split(".")
    if len(parts) != 2:
        raise HTTPException(401, "Token xac thuc khong hop le")
    body_b64, sig_b64 = parts
    expected = hmac.new(secret.encode("utf-8"), body_b64.encode("ascii"), hashlib.sha256).digest()
    actual = base64.urlsafe_b64decode(sig_b64 + "===")
    if not hmac.compare_digest(expected, actual):
        raise HTTPException(401, "Chu ky token khong hop le")
    data = json.loads(base64.urlsafe_b64decode(body_b64 + "===").decode("utf-8"))
    if int(data.get("exp", 0)) < int(time.time()):
        raise HTTPException(401, "Phien dang nhap da het han")
    return data


def require_role(request: Request, cfg: dict, min_role: str = "hr") -> str:
    """Kiem tra quyen RBAC khi bat security.auth_enabled (bat buoc truoc khi mo 0.0.0.0 o K5)."""
    sec = (cfg or {}).get("security") or {}
    if not sec.get("auth_enabled", False):
        return request.headers.get("X-Actor", "local:dev")
    secret = os.environ.get("FACEGATE_AUTH_SECRET") or sec.get("jwt_secret") or ""
    if not secret:
        raise HTTPException(500, "Chua cau hinh FACEGATE_AUTH_SECRET khi auth_enabled=true")
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else request.cookies.get("fg_token", "")
    if not token:
        raise HTTPException(401, "Yeu cau dang nhap")
    claims = verify_rbac_token(token, secret)
    role = str(claims.get("role", "viewer"))
    if ROLE_RANK.get(role, 0) < ROLE_RANK.get(min_role, 2):
        raise HTTPException(403, f"Can quyen '{min_role}' tro len (hien tai: '{role}')")
    return f"{role}:{claims.get('sub', 'user')}"
