import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config.config import settings


_bearer = HTTPBearer(auto_error=False)
_ALGORITHM = "HS256"


@dataclass
class JWTPayload:
    sub: str
    exp: int


@dataclass
class AuthContext:
    access_token: str
    claims: JWTPayload


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _sign(signing_input: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), signing_input.encode("utf-8"), hashlib.sha256).digest()
    return _b64url_encode(digest)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def decode_access_token(token: str) -> JWTPayload:
    parts = token.split(".")
    if len(parts) != 3:
        raise _unauthorized("Invalid token format")

    header_b64, payload_b64, signature = parts

    try:
        header = json.loads(_b64url_decode(header_b64))
        payload = json.loads(_b64url_decode(payload_b64))
    except Exception:
        raise _unauthorized("Invalid token encoding")

    if header.get("alg") != _ALGORITHM:
        raise _unauthorized("Unsupported token algorithm")

    expected_sig = _sign(f"{header_b64}.{payload_b64}", settings.jwt_secret_key)
    if not hmac.compare_digest(signature, expected_sig):
        raise _unauthorized("Invalid token signature")

    now = int(time.time())
    skew = max(int(settings.jwt_clock_skew_seconds), 0)

    exp = int(payload.get("exp", 0))
    if exp <= 0 or now > exp + skew:
        raise _unauthorized("Token expired")

    iat = int(payload.get("iat", 0))
    if iat > 0 and iat > now + skew:
        raise _unauthorized("Invalid issued-at time")

    iss = str(payload.get("iss", ""))
    if iss != settings.jwt_issuer:
        raise _unauthorized("Invalid token issuer")

    sub = str(payload.get("sub", "")).strip()
    if not sub:
        raise _unauthorized("Invalid token subject")

    return JWTPayload(sub=sub, exp=exp)


def get_auth_context(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> AuthContext:
    if credentials is None:
        raise _unauthorized("Missing bearer token")
    if credentials.scheme.lower() != "bearer":
        raise _unauthorized("Invalid authentication scheme")
    token = credentials.credentials
    return AuthContext(access_token=token, claims=decode_access_token(token))
