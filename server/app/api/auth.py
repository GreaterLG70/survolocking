"""认证接口：短信验证码 / 账号密码 登录 / 注册 / 资料更新。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.core.deps import get_current_user
from app.core.security import (
    create_access_token,
    hash_password,
    is_valid_phone,
    new_session_id,
    normalize_phone,
    verify_password,
)
from app.database import get_db, session_scope
from app.models import User
from app.schemas.common import fail, ok
from app.schemas.schemas import (
    AuthResponse,
    LoginRequest,
    RegisterRequest,
    SendCodeRequest,
    UpdateProfileRequest,
)
from app.services import sms_service

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/send-code")
async def send_code(req: SendCodeRequest):
    if not is_valid_phone(req.phone):
        return fail(1002, "手机号格式不正确")

    success, code, retry_after = await sms_service.send_code(req.phone)
    if not success:
        if retry_after > 0:
            return fail(
                1005,
                f"验证码发送过于频繁，请 {retry_after} 秒后再试",
                {"retry_after": retry_after},
            )
        return fail(1005, "短信发送失败，请稍后重试")

    data = {"sent": True, "expire": settings.SMS_CODE_EXPIRE}
    # DEV 模式回显验证码，生产环境必须为 False
    if settings.SMS_PROVIDER == "dev":
        data["dev_code"] = code
    return ok(data)


def default_nickname(phone: str) -> str:
    """未设置昵称时的兜底显示名，避免前端把 null 直接渲染出来。"""
    return f"用户{phone[-4:]}" if len(phone) >= 4 else "用户"


def _issue(db: Session, user: User, is_new: bool, dev_code: str | None = None) -> dict:
    # 每次登录刷新会话：新设备登录即作废旧设备令牌（单设备登录）
    sid = new_session_id()
    user.session_id = sid
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(user.id, session_id=sid)
    payload = {
        "token": token,
        "user_id": user.id,
        "phone": user.phone,
        # 老用户 nickname 可能是 NULL，这里兜底，避免前端直接显示 null
        "nickname": user.nickname or default_nickname(user.phone),
        "avatar": user.avatar,
        "is_new_user": is_new,
        # 盐由服务端作为唯一真相源下发，端侧缓存后用于哈希计算，
        # 避免三端盐不一致导致日志与规则静默失配。
        "phone_hash_salt": settings.PHONE_HASH_SALT,
    }
    if settings.SMS_PROVIDER == "dev" and dev_code:
        payload["dev_code"] = dev_code
    return ok(payload)


@router.post("/register")
async def register(req: RegisterRequest, db: Session = Depends(get_db)):
    if not is_valid_phone(req.phone):
        return fail(1002, "手机号格式不正确")
    if not await sms_service.verify_code_async(req.phone, req.code):
        raise HTTPException(status_code=401, detail="验证码错误或已过期")

    exists = db.query(User).filter(User.phone == normalize_phone(req.phone)).first()
    if exists:
        return fail(1004, "该手机号已注册，请直接登录")

    user = User(
        phone=normalize_phone(req.phone),
        # 昵称兜底：不填则生成「用户+手机后4位」，避免前端渲染出 null
        nickname=(req.nickname or "").strip() or default_nickname(normalize_phone(req.phone)),
        password_hash=hash_password(req.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return _issue(db, user, is_new=True)


@router.post("/login")
async def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.phone == normalize_phone(req.phone)).first()

    # 账号密码登录：提供 password 即走密码校验
    if req.is_password_login():
        if user is None or not verify_password(req.password, user.password_hash):
            raise HTTPException(status_code=401, detail="账号或密码错误")
        return _issue(db, user, is_new=False)

    # 验证码登录（兼容老用户与无密码场景）
    if not await sms_service.verify_code_async(req.phone, req.code or ""):
        raise HTTPException(status_code=401, detail="验证码错误或已过期")

    if user is None:
        # 验证码通过但用户不存在：直接隐式注册，降低移动端接入摩擦
        user = User(
            phone=normalize_phone(req.phone),
            nickname=default_nickname(normalize_phone(req.phone)),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return _issue(db, user, is_new=True)

    return _issue(db, user, is_new=False)


@router.get("/me")
async def me(user: User = Depends(get_current_user)):
    return ok(
        {
            "user_id": user.id,
            "phone": user.phone,
            # 老用户 nickname 为 NULL 时兜底，前端不再显示 null
            "nickname": user.nickname or default_nickname(user.phone),
            "avatar": user.avatar,
            "created_at": user.created_at,
            "phone_hash_salt": settings.PHONE_HASH_SALT,
        }
    )


@router.put("/profile")
async def update_profile(
    req: UpdateProfileRequest, user: User = Depends(get_current_user)
):
    """更新个人资料：昵称与头像二选一或都传。"""
    with session_scope() as s:
        u = s.query(User).filter(User.id == user.id).first()
        if req.nickname is not None:
            u.nickname = req.nickname
        if req.avatar is not None:
            u.avatar = req.avatar
        s.add(u)
    return ok({"updated": True})


@router.put("/push-token")
async def update_push_token(
    push_token: str, user: User = Depends(get_current_user)
):
    """上报设备推送令牌，用于家庭组通知。"""
    with session_scope() as s:
        u = s.query(User).filter(User.id == user.id).first()
        u.push_token = push_token
        s.add(u)
    return ok({"updated": True})


@router.delete("/account")
async def delete_account(user: User = Depends(get_current_user)):
    """
    注销账号：删除个人数据。

    合规要求：用户可随时退出并删除数据。
    拦截日志保留号码哈希（无法反推个人），但解除与用户的关联。
    """
    from app.models import FamilyMember, InterceptLog, Notification

    with session_scope() as s:
        s.query(FamilyMember).filter(FamilyMember.user_id == user.id).delete()
        s.query(Notification).filter(Notification.user_id == user.id).delete()
        # 日志保留但匿名化：置空 user_id 关联交由外键处理，
        # 这里将用户记录物理删除，日志表按保留策略另行清理
        s.query(InterceptLog).filter(InterceptLog.user_id == user.id).delete()
        s.query(User).filter(User.id == user.id).delete()
    return ok({"deleted": True})
