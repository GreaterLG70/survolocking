"""
消息推送服务。

provider = dev  : 仅落站内通知表，APP 轮询 /api/notify/list 拉取
provider = fcm  : Firebase Cloud Messaging HTTP v1
provider = apns : Apple Push Notification service (HTTP/2 + JWT ES256)
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from app.config import settings
from app.database import session_scope
from app.models import Notification

logger = logging.getLogger("survolocking.push")

_FCM_TOKEN_URL = "https://oauth2.googleapis.com/token"
_APNS_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"


def notify(
    user_ids: list[int],
    notify_type: str,
    title: str,
    content: str | None = None,
    payload: dict[str, Any] | None = None,
    family_id: int | None = None,
) -> int:
    """
    发送通知。站内记录始终写入，外推按 provider 决定。
    返回写入的通知条数。
    """
    payload_str = json.dumps(payload, ensure_ascii=False) if payload else None
    with session_scope() as s:
        for uid in user_ids:
            s.add(
                Notification(
                    user_id=uid,
                    family_id=family_id,
                    notify_type=notify_type,
                    title=title,
                    content=content,
                    payload=payload_str,
                )
            )

    if settings.PUSH_PROVIDER == "fcm":
        _push_fcm(user_ids, notify_type, title, content, payload)
    elif settings.PUSH_PROVIDER == "apns":
        _push_apns(user_ids, notify_type, title, content, payload)
    else:
        logger.info("[DEV] 通知落库 provider=dev type=%s users=%s", notify_type, user_ids)

    return len(user_ids)


# ------------------------------------------------------------------ FCM


def _fcm_access_token() -> str | None:
    """用服务账号 JSON 换取 OAuth2 access token（不依赖 google-auth 库）。"""
    import cryptography.hazmat.primitives.serialization as ser
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from jose import jwt as jose_jwt

    try:
        with open(settings.FCM_CREDENTIALS_JSON, "r", encoding="utf-8") as f:
            info = json.load(f)
    except Exception as e:  # pragma: no cover
        logger.error("读取 FCM 服务账号失败: %s", e)
        return None

    now = int(time.time())
    claim = {
        "iss": info["client_email"],
        "scope": _APNS_SCOPE,
        "aud": _FCM_TOKEN_URL,
        "iat": now,
        "exp": now + 3600,
    }
    private_key = ser.load_pem_private_key(
        info["private_key"].encode(), password=None
    )
    assertion = jose_jwt.encode(claim, private_key, algorithm="RS256")

    resp = httpx.post(
        _FCM_TOKEN_URL,
        data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion},
        timeout=10,
    )
    if resp.status_code != 200:
        logger.error("FCM token 获取失败: %s", resp.text)
        return None
    return resp.json().get("access_token")


def _push_fcm(
    user_ids: list[int],
    notify_type: str,
    title: str,
    content: str | None,
    payload: dict[str, Any] | None,
) -> None:
    if not settings.FCM_CREDENTIALS_JSON:
        logger.warning("FCM 未配置凭据，跳过外推")
        return
    token = _fcm_access_token()
    if not token:
        return

    from app.models import User

    with session_scope() as s:
        users = (
            s.query(User)
            .filter(User.id.in_(user_ids), User.push_token.isnot(None))
            .all()
        )
        targets = [u.push_token for u in users if u.push_token]

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    for device_token in targets:
        body = {
            "message": {
                "token": device_token,
                "notification": {"title": title, "body": content or ""},
                "data": {
                    "type": notify_type,
                    "payload": json.dumps(payload or {}, ensure_ascii=False),
                },
            }
        }
        try:
            httpx.post(
                f"https://fcm.googleapis.com/v1/projects/{_fcm_project_id()}/messages:send",
                headers=headers,
                json=body,
                timeout=10,
            )
        except Exception as e:  # pragma: no cover
            logger.error("FCM 推送异常: %s", e)


def _fcm_project_id() -> str:
    try:
        with open(settings.FCM_CREDENTIALS_JSON, "r", encoding="utf-8") as f:
            return json.load(f).get("project_id", "")
    except Exception:
        return ""


# ------------------------------------------------------------------ APNs


def _push_apns(
    user_ids: list[int],
    notify_type: str,
    title: str,
    content: str | None,
    payload: dict[str, Any] | None,
) -> None:
    if not (settings.APNS_KEY_PATH and settings.APNS_KEY_ID and settings.APNS_TEAM_ID):
        logger.warning("APNs 未配置凭据，跳过外推")
        return
    try:
        from cryptography.hazmat.primitives import serialization
        from jose import jwt as jose_jwt

        with open(settings.APNS_KEY_PATH, "rb") as f:
            key = serialization.load_pem_private_key(f.read(), password=None)
        token = jose_jwt.encode(
            {"iss": settings.APNS_TEAM_ID, "iat": int(time.time())},
            key,
            algorithm="ES256",
            headers={"kid": settings.APNS_KEY_ID},
        )
    except Exception as e:  # pragma: no cover
        logger.error("APNs 签名失败: %s", e)
        return

    from app.models import User

    with session_scope() as s:
        users = s.query(User).filter(User.id.in_(user_ids)).all()
        targets = [u.push_token for u in users if u.push_token]

    for device_token in targets:
        body = {
            "aps": {"alert": {"title": title, "body": content or ""}, "sound": "default"},
            "type": notify_type,
            "payload": payload or {},
        }
        try:
            httpx.post(
                f"https://api.push.apple.com/3/device/{device_token}",
                headers={
                    "authorization": f"bearer {token}",
                    "apns-topic": settings.APNS_BUNDLE_ID,
                    "apns-push-type": "alert",
                },
                json=body,
                timeout=10,
            )
        except Exception as e:  # pragma: no cover
            logger.error("APNs 推送异常: %s", e)
