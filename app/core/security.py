import base64
import hashlib
import hmac
import json

import jwt
from cryptography.fernet import Fernet

from app.config import settings


def create_token(sub: int, role: str, mid: int | None = None) -> str:
    payload = {"sub": str(sub), "role": role}
    if mid is not None:
        payload["mid"] = mid
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])


def _fernet() -> Fernet:
    if settings.FERNET_KEY:
        key = settings.FERNET_KEY.encode()
    else:
        digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
        key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_secret(plain: str) -> str:
    if not plain:
        return ""
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_secret(token: str) -> str:
    if not token:
        return ""
    return _fernet().decrypt(token.encode()).decode()


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sim_sign(payload: dict) -> str:
    return hmac.new(settings.SIM_SECRET.encode(), _canonical(payload), hashlib.sha256).hexdigest()


def sim_verify(payload: dict, sign: str) -> bool:
    expect = sim_sign(payload)
    return hmac.compare_digest(expect, sign or "")