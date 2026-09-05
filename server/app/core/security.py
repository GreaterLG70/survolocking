"""
安全与脱敏工具。

核心约束：原始号码永不上云。所有入库/传输使用加盐 SHA-256 哈希。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt

from app.config import settings

_PHONE_RE = re.compile(r"^\+?\d{6,20}$")

# 中国大陆常见异常号段：境外来电 / 虚拟运营商 / 高仿号
SUSPICIOUS_PREFIXES = (
    "00852",  # 中国香港
    "00853",  # 中国澳门
    "00886",  # 中国台湾
    "001",
    "0060",
    "0065",
    "0081",
    "0082",
    "170",
    "171",
    "162",
    "165",
    "167",
    "174",
    "149",
)


def normalize_phone(phone: str) -> str:
    """归一化号码：仅保留数字与前导 +，与服务端/Android/iOS 三端完全一致。

    必须与服务端 security.hash_phone、Android PhoneUtils.normalize、
    iOS PhoneUtils.normalize 保持同一套规则（只保留 [0-9] 与可选前导 +），
    否则同一号码在三端算出不同哈希，导致日志与规则静默失配。
    """
    if not phone:
        return ""
    s = phone.strip()
    plus = "+" if s.startswith("+") else ""
    digits = re.sub(r"\D", "", s)
    return plus + digits


def is_valid_phone(phone: str) -> bool:
    return bool(_PHONE_RE.match(normalize_phone(phone)))


def hash_phone(phone: str) -> str:
    """
    号码加盐哈希。

    使用 HMAC-SHA256 而非裸 sha256(salt+phone)，
    可避免长度扩展攻击，且盐值泄露时安全性更高。
    """
    normalized = normalize_phone(phone)
    return hmac.new(
        settings.PHONE_HASH_SALT.encode("utf-8"),
        normalized.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def is_suspicious_prefix(phone: str) -> bool:
    """端侧与云端共用的异常号段判定。"""
    normalized = normalize_phone(phone).lstrip("+")
    for prefix in SUSPICIOUS_PREFIXES:
        if normalized.startswith(prefix):
            return True
    return False


def new_session_id() -> str:
    """生成新的会话标识，用于单设备登录互踢。"""
    return secrets.token_hex(16)


def create_access_token(
    subject: str | int,
    expires_delta: timedelta | None = None,
    session_id: str | None = None,
) -> str:
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    )
    payload: dict[str, Any] = {"sub": str(subject), "exp": expire}
    if session_id:
        # 会话标识随令牌下发；服务端比对不一致即判定他处登录
        payload["sid"] = session_id
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> int | None:
    """解析 JWT 取出 user_id，失败返回 None。"""
    user_id, _ = decode_token_claims(token)
    return user_id


def decode_token_claims(token: str) -> tuple[int | None, str | None]:
    """解析 JWT，返回 (user_id, session_id)。失败时 user_id 为 None。"""
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
        sub = payload.get("sub")
        sid = payload.get("sid")
        user_id = int(sub) if sub is not None else None
        return user_id, (str(sid) if sid else None)
    except (JWTError, ValueError, TypeError):
        return None, None


def generate_sms_code(length: int = 6) -> str:
    """生成数字验证码。首位置 1-9，避免前导 0 引发输入歧义。"""
    first = secrets.choice("123456789")
    rest = "".join(secrets.choice("0123456789") for _ in range(length - 1))
    return first + rest


def truncate_to_hour(dt: datetime) -> datetime:
    """合规要求：通话时间仅保留到小时级。"""
    return dt.replace(minute=0, second=0, microsecond=0)


# ------------------------------------------------------------------ 密码哈希

_PBKDF2_ALGO = "sha256"
_PBKDF2_ITERATIONS = 100_000
_PWD_HASH_RE = re.compile(
    r"^pbkdf2_sha256\$(\d+)\$([A-Za-z0-9+/=]+)\$([A-Za-z0-9+/=]+)$"
)


def hash_password(password: str) -> str:
    """PBKDF2-SHA256 加盐哈希，返回自描述格式字符串（salt+hash 内嵌）。

    使用标准库实现，无需引入 bcrypt/passlib 等新依赖，降低部署成本。
    """
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac(
        _PBKDF2_ALGO, password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return "pbkdf2_sha256${}${}${}".format(
        _PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode(),
        base64.b64encode(dk).decode(),
    )


def verify_password(password: str, stored: str | None) -> bool:
    """校验明文密码与存储哈希是否匹配。stored 为空直接返回 False。"""
    if not stored:
        return False
    m = _PWD_HASH_RE.match(stored)
    if not m:
        return False
    try:
        iterations = int(m.group(1))
        salt = base64.b64decode(m.group(2))
        expected = base64.b64decode(m.group(3))
    except (ValueError, TypeError):
        return False
    dk = hashlib.pbkdf2_hmac(
        _PBKDF2_ALGO, password.encode("utf-8"), salt, iterations
    )
    return secrets.compare_digest(dk, expected)
