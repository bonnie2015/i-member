"""测试用 JWT Token 生成工具。

复用 app/security/jwt_auth.py 相同的 HS256 签名逻辑。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time


def generate_test_jwt(
    user_id: str = "test_user_001",
    expiry_minutes: int = 60,
) -> str:
    """生成测试用 HS256 JWT，1 小时有效。"""
    from app.config.config import settings

    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = {
        "sub": user_id,
        "exp": now + expiry_minutes * 60,
        "iat": now,
        "iss": settings.jwt_issuer,
    }

    header_b64 = (
        base64.urlsafe_b64encode(json.dumps(header, separators=(",", ":")).encode())
        .rstrip(b"=")
        .decode()
    )
    payload_b64 = (
        base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode())
        .rstrip(b"=")
        .decode()
    )
    signing_input = f"{header_b64}.{payload_b64}"
    signature = (
        base64.urlsafe_b64encode(
            hmac.new(
                settings.jwt_secret_key.encode(),
                signing_input.encode(),
                hashlib.sha256,
            ).digest()
        )
        .rstrip(b"=")
        .decode()
    )
    return f"{signing_input}.{signature}"
