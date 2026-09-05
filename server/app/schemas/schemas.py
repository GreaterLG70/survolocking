"""请求/响应数据模型。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.core.security import normalize_phone

# ------------------------------------------------------------------ 认证


class SendCodeRequest(BaseModel):
    phone: str = Field(..., min_length=6, max_length=20)

    @field_validator("phone")
    @classmethod
    def _normalize(cls, v: str) -> str:
        return normalize_phone(v)


class LoginRequest(BaseModel):
    phone: str
    code: str | None = Field(default=None, min_length=4, max_length=10)
    password: str | None = Field(default=None, min_length=6, max_length=64)

    @field_validator("phone")
    @classmethod
    def _normalize(cls, v: str) -> str:
        return normalize_phone(v)

    def is_password_login(self) -> bool:
        """密码登录模式：提供了 password 即走密码校验（忽略 code）。"""
        return bool(self.password)


class RegisterRequest(BaseModel):
    phone: str
    code: str = Field(..., min_length=4, max_length=10)
    password: str = Field(..., min_length=6, max_length=64)
    nickname: str | None = Field(default=None, max_length=50)

    @field_validator("phone")
    @classmethod
    def _normalize(cls, v: str) -> str:
        return normalize_phone(v)


class AuthResponse(BaseModel):
    token: str
    user_id: int
    phone: str
    nickname: str | None = None
    is_new_user: bool = False
    # 盐由服务端作为唯一真相源下发，端侧缓存后用于哈希计算
    phone_hash_salt: str = ""
    # 头像 URL 或存储标识
    avatar: str | None = None
    # DEV 模式下直接回显验证码，便于联调
    dev_code: str | None = None


class UpdateProfileRequest(BaseModel):
    """更新个人资料：昵称与头像二选一或都传。"""
    nickname: str | None = Field(default=None, max_length=50)
    avatar: str | None = Field(default=None, max_length=512)


# ------------------------------------------------------------------ 家庭组


class FamilyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)


class FamilyInviteRequest(BaseModel):
    family_id: int
    invitee_phone: str


class FamilyAcceptRequest(BaseModel):
    invitation_id: int


class MemberItem(BaseModel):
    user_id: int
    nickname: str | None
    phone_masked: str
    joined_at: datetime | None
    is_creator: bool = False


class FamilyInfo(BaseModel):
    family_id: int
    name: str
    creator_id: int
    member_count: int
    created_at: datetime | None = None


class ActivityItem(BaseModel):
    type: str
    actor: str | None
    target: str | None
    timestamp: datetime


# ------------------------------------------------------------------ 日志


class LogItem(BaseModel):
    phone_hash: str = Field(..., min_length=64, max_length=64)
    call_time: datetime
    ring_duration: int | None = None
    action: Literal[1, 2, 3]
    decision_source: Literal[1, 2, 3, 4]


class LogUploadRequest(BaseModel):
    logs: list[LogItem] = Field(..., max_length=2000)


class LogUploadResponse(BaseModel):
    accepted: int
    rejected: int
    reason: str | None = None


class MarkRequest(BaseModel):
    phone_hash: str = Field(..., min_length=64, max_length=64)
    user_mark: Literal[1, 2, 3]


# ------------------------------------------------------------------ 规则


class RuleItem(BaseModel):
    rule_id: int
    rule_type: int
    pattern: str
    confidence: float | None = None
    source: int
    version: int
    expires_at: datetime | None = None


class RuleSyncResponse(BaseModel):
    version: int
    rules: list[RuleItem]
    server_time: datetime


class FeedbackRequest(BaseModel):
    """误拦纠正：用户主动放行被拦截的号码。"""

    phone_hash: str = Field(..., min_length=64, max_length=64)
    # 1误拦放行 2确认骚扰
    feedback_type: Literal[1, 2]


# ------------------------------------------------------------------ 起零查询


class QilingQueryRequest(BaseModel):
    # 原始号码（起零按真实号匹配；服务端仅用于查询，不持久化）
    phone: str = Field(..., min_length=5, max_length=20)


class QilingQueryResponse(BaseModel):
    phone: str
    risk_level: str  # fraud / harassment / normal / unknown
    action: str  # block / analyze / allow
    cached: bool = False
    source: str = "qiling"


# ------------------------------------------------------------------ 通知


class NotificationItem(BaseModel):
    id: int
    notify_type: str
    title: str
    content: str | None
    payload: str | None
    is_read: bool
    created_at: datetime
