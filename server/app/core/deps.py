"""FastAPI 公共依赖：鉴权、当前用户解析。"""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """
    从 Authorization: Bearer <token> 解析当前用户。

    注意：不依赖 OAuth2PasswordBearer，避免未授权时自动跳转登录页，
    移动端需要的是 401 JSON 而非 302。
    """
    from app.core.security import decode_token_claims

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少访问令牌"
        )

    token = authorization.split(" ", 1)[1].strip()
    user_id, sid = decode_token_claims(token)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="令牌无效或已过期"
        )

    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")

    # 单设备登录：令牌内的会话标识与库中不一致 → 账号已在别处登录，本端被挤下线。
    # 仅当库里已有会话时才校验，避免老令牌（无 sid）被误杀。
    if sid and user.session_id and sid != user.session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="账号已在其他设备登录",
            # 客户端据此弹窗提示并强制回登录页，区别于普通的令牌过期
            headers={"X-Kicked": "1"},
        )
    return user


def get_current_family_id(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> int | None:
    """返回当前用户所属家庭组 ID，未加入返回 None。"""
    from app.models import FamilyMember

    row = (
        db.query(FamilyMember.family_id)
        .filter(FamilyMember.user_id == user.id, FamilyMember.is_active.is_(True))
        .first()
    )
    return row[0] if row else None
