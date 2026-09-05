"""
Survolocking 云端服务配置中心。

设计要点：
1. 所有外部依赖（MySQL / Redis / DeepSeek / 起零 / 短信 / 推送）均为可插拔。
   未配置凭据时自动降级为安全默认值，服务仍可启动，不影响主线开发联调。
2. 敏感信息只从环境变量或 .env 文件读取，永不写死在代码中。
"""
from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Literal, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------------------------------------------------------------- 基础
    APP_NAME: str = "Survolocking"
    APP_ENV: Literal["dev", "staging", "prod"] = "dev"
    DEBUG: bool = True
    API_PREFIX: str = "/api"
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # ---------------------------------------------------------------- 安全
    # JWT 签名密钥；生产必须覆盖。留空时自动生成（重启后 token 失效）
    JWT_SECRET: str = ""
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 24 * 30  # 30 天，移动端免频繁登录

    # 号码哈希加盐值；生产必须覆盖且一旦上线不可更改，否则历史数据全部失配
    PHONE_HASH_SALT: str = ""

    # 管理接口保护令牌；生产必须设置，dev 可留空
    ADMIN_TOKEN: str = ""

    # ---------------------------------------------------------------- MySQL
    MYSQL_HOST: str = "127.0.0.1"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = ""
    MYSQL_DATABASE: str = "survolocking"
    MYSQL_TABLE_PREFIX: str = ""  # 无建库权限时启用，如 "sl_"
    MYSQL_SSL: bool = False
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_ECHO: bool = False

    # ---------------------------------------------------------------- Redis（可选）
    # 留空则使用进程内内存缓存，功能一致，仅多实例部署时缓存不共享
    REDIS_URL: str = ""  # 例：redis://:password@host:6379/0

    # ---------------------------------------------------------------- DeepSeek
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_MODEL: str = "deepseek-chat"
    DEEPSEEK_TIMEOUT: float = 60.0
    DEEPSEEK_ENABLED: bool = True  # 未配置 key 时会自动置 False

    # ---------------------------------------------------------------- 起零数据（iStero）
    # 鉴权：Authorization: Bearer <QILING_TOKEN>
    # 实测：参数名是 number（非 phone）；成功返回 data=list[{name,msg}]
    # 隐私：向起零传【原始号码】用于匹配（第三方骚扰库按真实号匹配），
    #       本服务永不持久化原始号码，库中只存 HMAC 哈希。
    QILING_TOKEN: str = ""
    QILING_BASE_URL: str = "https://api.istero.com/resource/v1/harassing/calls"
    QILING_PHONE_PARAM: str = "number"
    QILING_TIMEOUT: float = 1.0  # 严格超时，超时直接跳过该层，绝不阻塞来电决策
    QILING_ENABLED: bool = True
    QILING_CACHE_TTL: int = 300  # 起零结果缓存秒数

    # ---------------------------------------------------------------- 短信验证码
    # dev        : 不真实发送，验证码写日志并回显（联调用）
    # aliyun     : 阿里云「短信服务」SendSms（dysmsapi）——需企业资质申请签名，个人账号不可用
    # aliyun_pns : 阿里云「号码认证服务 - 短信认证」SendSmsVerifyCode（dypnsapi）
    #              免资质/免签名/免模板申请，用平台赠送的签名与模板，个人开发者可用。
    #              验证码由阿里云生成并保管，校验走 CheckSmsVerifyCode。
    # tencent    : 腾讯云短信
    SMS_PROVIDER: Literal["dev", "aliyun", "aliyun_pns", "tencent"] = "dev"
    SMS_CODE_EXPIRE: int = 300  # 5 分钟
    SMS_SIGN_NAME: str = ""
    SMS_TEMPLATE_ID: str = ""
    ALIYUN_ACCESS_KEY_ID: str = ""
    ALIYUN_ACCESS_KEY_SECRET: str = ""
    ALIYUN_SMS_REGION: str = "cn-hangzhou"
    # 号码认证服务短信认证：发送间隔频控（秒），与阿里云 Interval 参数一致
    SMS_SEND_INTERVAL: int = 60
    TENCENT_SECRET_ID: str = ""
    TENCENT_SECRET_KEY: str = ""
    TENCENT_SMS_APPID: str = ""

    # ---------------------------------------------------------------- 推送
    PUSH_PROVIDER: Literal["dev", "fcm", "apns"] = "dev"
    FCM_CREDENTIALS_JSON: str = ""  # 服务账号 JSON 路径
    APNS_KEY_PATH: str = ""
    APNS_KEY_ID: str = ""
    APNS_TEAM_ID: str = ""
    APNS_BUNDLE_ID: str = ""

    # ---------------------------------------------------------------- 业务参数
    # 单个家庭组每次 DeepSeek 分析的最大日志条数（防 Token 超限）
    ANALYSIS_BATCH_SIZE: int = 500
    # 规则置信度阈值，低于此值进入人工审核区而不自动下发
    RULE_CONFIDENCE_THRESHOLD: float = 0.7
    # 每个家庭组每天最多触发的 DeepSeek 调用次数
    ANALYSIS_DAILY_LIMIT_PER_FAMILY: int = 2
    # 家庭聚合：至少多少个不同成员标记才生成规则
    FAMILY_AGGREGATION_MIN_USERS: int = 2
    # 家庭聚合：骚扰标记占比达到多少判定为高可信骚扰
    FAMILY_SPAM_RATIO: float = 0.8
    # 家庭聚合：标记为"客户"且骚扰占比低于多少时加入豁免
    FAMILY_SAFE_RATIO: float = 0.3
    # 规则包版本号基数，每次下发自增
    RULE_SYNC_MIN_INTERVAL_HOURS: int = 1

    # ---------------------------------------------------------------- 定时任务
    # 每日离线分析触发时间
    ANALYSIS_CRON_HOUR: int = 2
    ANALYSIS_CRON_MINUTE: int = 0

    @field_validator("JWT_SECRET", "PHONE_HASH_SALT")
    @classmethod
    def _fill_secrets(cls, v: str) -> str:
        if not v:
            return secrets.token_hex(32)
        return v

    @property
    def db_url(self) -> str:
        driver = "mysql+pymysql"
        base = (
            f"{driver}://{self.MYSQL_USER}:{self.MYSQL_PASSWORD}"
            f"@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DATABASE}"
        )
        if self.MYSQL_SSL:
            base += "?ssl=true"
        return base

    @property
    def deepseek_ready(self) -> bool:
        return bool(self.DEEPSEEK_API_KEY) and self.DEEPSEEK_ENABLED

    @property
    def qiling_ready(self) -> bool:
        return bool(self.QILING_TOKEN) and self.QILING_ENABLED

    @property
    def sms_ready(self) -> bool:
        """短信通道是否具备真实发送条件（不含 dev）。"""
        if self.SMS_PROVIDER == "dev":
            return False
        # aliyun（短信服务）与 aliyun_pns（号码认证服务）共用同一对 AK
        if self.SMS_PROVIDER in ("aliyun", "aliyun_pns"):
            return bool(self.ALIYUN_ACCESS_KEY_ID and self.ALIYUN_ACCESS_KEY_SECRET)
        if self.SMS_PROVIDER == "tencent":
            return bool(self.TENCENT_SECRET_ID and self.TENCENT_SECRET_KEY and self.TENCENT_SMS_APPID)
        return False


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
