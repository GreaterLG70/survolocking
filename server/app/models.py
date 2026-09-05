"""
Survolocking 数据模型。

MySQL 8 为主目标；APP_ENV=dev 且 MySQL 不可达时自动降级到 SQLite，
便于本地联调（SQLite 下自动跳过按月分区 DDL，功能完全一致）。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.config import settings

# SQLite 不支持 BIGINT 自增，做类型变体兼容
PKType = BigInteger().with_variant(Integer, "sqlite")


def _t(name: str) -> str:
    """拼装表名，支持无建库权限时的表前缀模式。"""
    return f"{settings.MYSQL_TABLE_PREFIX}{name}"


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class User(Base, TimestampMixin):
    __tablename__ = _t("users")

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    phone: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    nickname: Mapped[str | None] = mapped_column(String(50))
    # 设备推送令牌，多端时以逗号分隔存储
    push_token: Mapped[str | None] = mapped_column(String(512))
    # 账号密码登录：PBKDF2 哈希；为空表示仅支持验证码登录（老用户）
    password_hash: Mapped[str | None] = mapped_column(String(255))
    # 头像 URL 或存储标识；为空时前端用昵称首字默认头像
    avatar: Mapped[str | None] = mapped_column(String(512))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # 当前有效会话标识：每次登录都会刷新，JWT 内携带同名 sid。
    # 校验不一致即判定为新设备登录，旧设备被踢下线（单设备登录，参考 QQ）。
    session_id: Mapped[str | None] = mapped_column(String(64))


class Family(Base, TimestampMixin):
    __tablename__ = _t("families")

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    creator_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(_t("users.id")), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class FamilyMember(Base):
    __tablename__ = _t("family_members")

    family_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(_t("families.id")), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(_t("users.id")), primary_key=True
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class FamilyInvitation(Base, TimestampMixin):
    __tablename__ = _t("family_invitations")

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    inviter_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(_t("users.id")), nullable=False
    )
    family_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(_t("families.id")), nullable=False
    )
    invitee_phone: Mapped[str] = mapped_column(String(20), nullable=False)
    # 0待处理 1已接受 2已拒绝 3已过期
    status: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    expire_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (Index("idx_invitee_status", "invitee_phone", "status"),)


class InterceptLog(Base):
    """
    拦截日志（按月 RANGE 分区）。

    MySQL 分区约束：分区键必须包含在所有唯一键中，
    因此主键设计为 (id, call_time) 而非单独的 id。
    """

    __tablename__ = _t("intercept_logs")

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    phone_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # 注意：本表按月分区，MySQL 禁止分区表带外键，故 family_id/user_id 仅作逻辑关联，
    # 引用完整性由应用层保证（分区日志表常见做法）。
    family_id: Mapped[int | None] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    call_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ring_duration: Mapped[int | None] = mapped_column(Integer)
    # 1拦截 2放行 3用户拒接
    action: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # 1本地黑名单 2起零数据 3微行为 4用户标记
    decision_source: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_false_positive: Mapped[bool] = mapped_column(Boolean, default=False)
    # 1骚扰 2客户 3不确定
    user_mark: Mapped[int | None] = mapped_column(SmallInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_log_family_time", "family_id", "call_time"),
        Index("idx_log_hash", "phone_hash"),
        # dict 形式的 dialect 参数必须放在元组最后一位
        {"mysql_engine": "InnoDB"},
    )


class RulePackage(Base, TimestampMixin):
    __tablename__ = _t("rule_packages")

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    family_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(_t("families.id"))
    )
    # 1黑名单 2号段 3行为规则 4白名单豁免
    rule_type: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    pattern: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Numeric(3, 2))
    # 1用户标记 2DeepSeek生成 3家庭聚合
    source: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # 0生效 1待人工审核 2已废弃
    status: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (
        Index("idx_rule_family_version", "family_id", "version"),
        Index("idx_rule_active", "family_id", "is_active", "status"),
    )


class SmsCode(Base):
    """短信验证码。Redis 不可用时落库，保证功能不中断。"""

    __tablename__ = _t("sms_codes")

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    code: Mapped[str] = mapped_column(String(10), nullable=False)
    expire_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("idx_sms_phone", "phone", "used"),)


class Notification(Base, TimestampMixin):
    """站内通知。DEV 模式下推送降级为落库 + APP 轮询。"""

    __tablename__ = _t("notifications")

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(_t("users.id")), nullable=False
    )
    family_id: Mapped[int | None] = mapped_column(BigInteger)
    # member_joined / member_left / rule_updated / invitation
    notify_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    content: Mapped[str] = mapped_column(String(512))
    payload: Mapped[str | None] = mapped_column(Text)  # JSON
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (Index("idx_notify_user", "user_id", "is_read"),)


class AnalysisJob(Base, TimestampMixin):
    """DeepSeek 分析任务审计表，同时用于每日调用限频。"""

    __tablename__ = _t("analysis_jobs")

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    family_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    log_count: Mapped[int] = mapped_column(Integer, default=0)
    rules_generated: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="success")
    error_message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("idx_job_family_created", "family_id", "created_at"),)
