"""家庭组接口：创建/邀请/接受/成员管理/动态。"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.core.security import normalize_phone
from app.database import get_db, session_scope
from app.models import Family, FamilyInvitation, FamilyMember, User
from app.schemas.common import fail, ok
from app.schemas.schemas import (
    ActivityItem,
    FamilyAcceptRequest,
    FamilyCreateRequest,
    FamilyInviteRequest,
    MemberItem,
)
from app.services import push_service, rule_service

router = APIRouter(prefix="/family", tags=["家庭组"])


def _mask_phone(phone: str) -> str:
    if len(phone) >= 7:
        return f"{phone[:3]}****{phone[-4:]}"
    return "***"


def _get_family_or_404(db: Session, family_id: int) -> Family:
    f = db.query(Family).filter(Family.id == family_id, Family.is_active.is_(True)).first()
    if f is None:
        raise HTTPException(status_code=404, detail="家庭组不存在")
    return f


def _assert_member(db: Session, family_id: int, user_id: int) -> None:
    row = (
        db.query(FamilyMember)
        .filter(
            FamilyMember.family_id == family_id,
            FamilyMember.user_id == user_id,
            FamilyMember.is_active.is_(True),
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=403, detail="非该家庭成员")


@router.post("/create")
async def create_family(
    req: FamilyCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # 约束：每个用户只属于一个家庭组
    existing = (
        db.query(FamilyMember)
        .filter(FamilyMember.user_id == user.id, FamilyMember.is_active.is_(True))
        .first()
    )
    if existing:
        return fail(1004, "你已加入一个家庭组，请先退出再创建")

    family = Family(name=req.name, creator_id=user.id)
    db.add(family)
    db.flush()
    db.add(FamilyMember(family_id=family.id, user_id=user.id))
    db.commit()
    db.refresh(family)
    return ok({"family_id": family.id, "name": family.name})


@router.post("/invite")
async def invite_member(
    req: FamilyInviteRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    import re

    phone = normalize_phone(req.invitee_phone)
    family = _get_family_or_404(db, req.family_id)
    _assert_member(db, req.family_id, user.id)

    if phone == user.phone:
        return fail(1002, "不能邀请自己")

    pending = (
        db.query(FamilyInvitation)
        .filter(
            FamilyInvitation.family_id == req.family_id,
            FamilyInvitation.invitee_phone == phone,
            FamilyInvitation.status == 0,
            FamilyInvitation.expire_at > datetime.now(),
        )
        .first()
    )
    if pending:
        return fail(1004, "已存在待处理的邀请")

    invitee = db.query(User).filter(User.phone == phone).first()
    if invitee is None:
        return fail(1003, "该手机号尚未注册，请先让对方注册后再邀请")

    # 对方已在其他家庭组：现在发邀请对方也接受不了（accept 会拒绝），
    # 提前拦下并给出明确原因，避免用户发出永远无法生效的邀请。
    in_other = (
        db.query(FamilyMember)
        .filter(FamilyMember.user_id == invitee.id, FamilyMember.is_active.is_(True))
        .first()
    )
    if in_other:
        return fail(1004, "对方已加入其他家庭组，需先退出才能接受邀请")

    invitation = FamilyInvitation(
        inviter_id=user.id,
        family_id=req.family_id,
        invitee_phone=phone,
        expire_at=datetime.now() + timedelta(hours=48),
    )
    db.add(invitation)
    db.commit()
    db.refresh(invitation)

    push_service.notify(
        [invitee.id],
        notify_type="invitation",
        title="家庭组邀请",
        content=f"{user.nickname or '成员'} 邀请你加入「{family.name}」",
        payload={"invitation_id": invitation.id, "family_id": family.id},
        family_id=family.id,
    )

    return ok(
        {
            "invitation_id": invitation.id,
            "expire_hours": 48,
            "invitee_nickname": invitee.nickname or f"用户{invitee.phone[-4:]}",
        }
    )


@router.post("/accept")
async def accept_invitation(
    req: FamilyAcceptRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    invitation = (
        db.query(FamilyInvitation).filter(FamilyInvitation.id == req.invitation_id).first()
    )
    if invitation is None or invitation.status != 0:
        raise HTTPException(status_code=404, detail="邀请不存在或已处理")
    if invitation.invitee_phone != user.phone:
        raise HTTPException(status_code=403, detail="该邀请不属于当前用户")
    if invitation.expire_at < datetime.now():
        invitation.status = 3
        db.commit()
        return fail(1004, "邀请已过期")

    already = (
        db.query(FamilyMember)
        .filter(FamilyMember.user_id == user.id, FamilyMember.is_active.is_(True))
        .first()
    )
    if already:
        return fail(1004, "你已加入其他家庭组，请先退出")

    db.add(FamilyMember(family_id=invitation.family_id, user_id=user.id))
    invitation.status = 1
    db.commit()

    family = db.query(Family).filter(Family.id == invitation.family_id).first()
    others = rule_service.get_family_member_ids(
        invitation.family_id, exclude_user_id=user.id
    )
    if others:
        push_service.notify(
            others,
            notify_type="member_joined",
            title="新成员加入",
            content=f"{user.nickname or user.phone} 加入了「{family.name if family else ''}」",
            payload={"user_id": user.id},
            family_id=invitation.family_id,
        )

    return ok({"family_id": invitation.family_id, "joined": True})


@router.get("/invitations")
async def list_my_invitations(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """
    我收到的待处理邀请。

    之前只有 accept / reject 却无法列举邀请，受邀方根本拿不到 invitation_id，
    导致邀请永远停留在待处理状态。此接口补上闭环：
    家庭页拉取后展示邀请卡片，用户点接受/拒绝即可。
    """
    rows = (
        db.query(FamilyInvitation, Family, User)
        .join(Family, Family.id == FamilyInvitation.family_id)
        .join(User, User.id == FamilyInvitation.inviter_id)
        .filter(
            FamilyInvitation.invitee_phone == user.phone,
            FamilyInvitation.status == 0,
            FamilyInvitation.expire_at > datetime.now(),
        )
        .order_by(FamilyInvitation.created_at.desc())
        .all()
    )
    items = [
        {
            "invitation_id": inv.id,
            "family_id": fam.id,
            "family_name": fam.name,
            "inviter_nickname": inviter.nickname or f"用户{inviter.phone[-4:]}",
            "created_at": inv.created_at,
            "expire_at": inv.expire_at,
        }
        for inv, fam, inviter in rows
    ]
    return ok({"invitations": items})


@router.get("/sent-invitations")
async def list_sent_invitations(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """我发出的邀请（含状态），便于发起方看到邀请是否被处理。"""
    rows = (
        db.query(FamilyInvitation, Family)
        .join(Family, Family.id == FamilyInvitation.family_id)
        .filter(FamilyInvitation.inviter_id == user.id)
        .order_by(FamilyInvitation.created_at.desc())
        .limit(50)
        .all()
    )
    items = [
        {
            "invitation_id": inv.id,
            "family_id": fam.id,
            "family_name": fam.name,
            "invitee_phone": inv.invitee_phone,
            # 0 待处理 / 1 已接受 / 2 已拒绝 / 3 已过期
            "status": inv.status,
            "created_at": inv.created_at,
        }
        for inv, fam in rows
    ]
    return ok({"invitations": items})


@router.post("/reject")
async def reject_invitation(
    req: FamilyAcceptRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    invitation = (
        db.query(FamilyInvitation).filter(FamilyInvitation.id == req.invitation_id).first()
    )
    if invitation is None or invitation.invitee_phone != user.phone:
        raise HTTPException(status_code=404, detail="邀请不存在")
    invitation.status = 2
    db.commit()
    return ok({"rejected": True})


@router.get("/members")
async def list_members(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    row = (
        db.query(FamilyMember)
        .filter(FamilyMember.user_id == user.id, FamilyMember.is_active.is_(True))
        .first()
    )
    if row is None:
        return ok({"family_id": None, "members": []})

    family = db.query(Family).filter(Family.id == row.family_id).first()
    members = (
        db.query(FamilyMember, User)
        .join(User, User.id == FamilyMember.user_id)
        .filter(FamilyMember.family_id == row.family_id, FamilyMember.is_active.is_(True))
        .all()
    )
    items = [
        MemberItem(
            user_id=u.id,
            nickname=u.nickname,
            phone_masked=_mask_phone(u.phone),
            joined_at=m.joined_at,
            is_creator=(family is not None and family.creator_id == u.id),
        )
        for m, u in members
    ]
    return ok(
        {
            "family_id": row.family_id,
            "family_name": family.name if family else None,
            "members": [i.model_dump(mode="json") for i in items],
        }
    )


@router.get("/activities")
async def list_activities(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """家庭动态：成员加入 + 近期拦截概况。"""
    from app.models import InterceptLog

    row = (
        db.query(FamilyMember)
        .filter(FamilyMember.user_id == user.id, FamilyMember.is_active.is_(True))
        .first()
    )
    if row is None:
        return ok({"activities": []})

    joined = (
        db.query(FamilyMember, User)
        .join(User, User.id == FamilyMember.user_id)
        .filter(FamilyMember.family_id == row.family_id)
        .order_by(FamilyMember.joined_at.desc())
        .limit(20)
        .all()
    )
    activities = [
        ActivityItem(
            type="member_joined",
            actor=u.nickname,
            target=None,
            timestamp=m.joined_at,
        ).model_dump(mode="json")
        for m, u in joined
    ]

    recent = (
        db.query(InterceptLog)
        .filter(InterceptLog.family_id == row.family_id)
        .order_by(InterceptLog.call_time.desc())
        .limit(20)
        .all()
    )
    for log in recent:
        activities.append(
            ActivityItem(
                type="intercept" if log.action == 1 else "allow",
                actor=None,
                target=log.phone_hash[:8],
                timestamp=log.call_time,
            ).model_dump(mode="json")
        )

    activities.sort(key=lambda x: x["timestamp"], reverse=True)
    return ok({"activities": activities[:40]})


@router.post("/leave")
async def leave_family(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """退出家庭组。历史数据保留，用户可另行删除。"""
    row = (
        db.query(FamilyMember)
        .filter(FamilyMember.user_id == user.id, FamilyMember.is_active.is_(True))
        .first()
    )
    if row is None:
        return fail(1003, "你当前不在任何家庭组")

    family_id = row.family_id
    row.is_active = False
    db.add(row)

    family = db.query(Family).filter(Family.id == family_id).first()
    if family and family.creator_id == user.id:
        # 创建者退出则解散家庭组
        family.is_active = False
        db.add(family)

    db.commit()

    others = rule_service.get_family_member_ids(family_id, exclude_user_id=user.id)
    if others:
        push_service.notify(
            others,
            notify_type="member_left",
            title="成员退出",
            content=f"{user.nickname or user.phone} 退出了家庭组",
            family_id=family_id,
        )
    return ok({"left": True, "family_id": family_id})


@router.post("/remove")
async def remove_member(
    user_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """创建者移除成员。"""
    row = (
        db.query(FamilyMember)
        .filter(FamilyMember.user_id == user.id, FamilyMember.is_active.is_(True))
        .first()
    )
    if row is None:
        return fail(1003, "你当前不在任何家庭组")

    family = db.query(Family).filter(Family.id == row.family_id).first()
    if family is None or family.creator_id != user.id:
        return fail(1003, "仅创建者可移除成员")
    if user_id == user.id:
        return fail(1002, "不能移除自己，请使用退出接口")

    target = (
        db.query(FamilyMember)
        .filter(
            FamilyMember.family_id == row.family_id,
            FamilyMember.user_id == user_id,
            FamilyMember.is_active.is_(True),
        )
        .first()
    )
    if target is None:
        return fail(1003, "成员不存在")
    target.is_active = False
    db.add(target)
    db.commit()

    push_service.notify(
        [user_id],
        notify_type="member_left",
        title="已被移出家庭组",
        content=f"你已被移出「{family.name}」",
        family_id=row.family_id,
    )
    return ok({"removed": True})
