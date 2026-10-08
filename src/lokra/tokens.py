from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

MAX_TTL_SECONDS = 8 * 3600


class TokenError(Exception):
    pass


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(key: bytes, body: str) -> str:
    return _b64(hmac.new(key, body.encode(), hashlib.sha256).digest())


def mint(key: bytes, agent: str, on_behalf_of: str, ttl_seconds: int = 3600) -> str:
    if not 0 < ttl_seconds <= MAX_TTL_SECONDS:
        raise TokenError(f"ttl must be between 1 and {MAX_TTL_SECONDS} seconds")
    now = int(time.time())
    payload = {"agent": agent, "sub": on_behalf_of, "iat": now,
               "exp": now + ttl_seconds, "jti": secrets.token_hex(8)}
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    return f"lokra1.{body}.{_sign(key, body)}"


def verify(key: bytes, token: str | None, now: float | None = None) -> dict:
    if not token:
        raise TokenError("no agent token supplied (set LOKRA_TOKEN)")
    parts = token.strip().split(".")
    if len(parts) != 3 or parts[0] != "lokra1":
        raise TokenError("malformed token")
    _, body, sig = parts
    if not hmac.compare_digest(_sign(key, body), sig):
        raise TokenError("token signature is invalid")
    try:
        payload = json.loads(_unb64(body))
    except ValueError:
        raise TokenError("malformed token payload") from None
    if (now if now is not None else time.time()) >= payload.get("exp", 0):
        raise TokenError("token has expired, mint a new one")
    return payload
