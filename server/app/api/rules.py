"""规则同步与误拦反馈。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.database import get_db, session_scope
from app.models import FamilyMember, RulePackage, User
from app.schemas.common import fail, ok
from app.schemas.schemas import FeedbackRequest, RuleItem
from app.services import rule_service

router = APIRouter(prefix="/rules", tags=["规则包"])


def _family_id(user_id: int) -> int | None:
    with session_scope() as s:
        row = (
            s.query(FamilyMember.family_id)
            .filter(FamilyMember.user_id == user_id, FamilyMember.is_active.is_(True))
            .first()
        )
        return row[0] if row else None


@router.get("/sync")
async def sync_rules(
    since_version: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    增量拉取规则包。

    APP 传入本地已有版本号，仅返回更新的部分，节省流量。
    """
    family_id = _family_id(user.id)
    if family_id is None:
        return ok({"version": 0, "rules": [], "server_time": __import__("datetime").datetime.now()})

    latest, rules = rule_service.get_incremental_rules(family_id, since_version)
    items = [
        RuleItem(
            rule_id=r.id,
            rule_type=r.rule_type,
            pattern=r.pattern,
            confidence=float(r.confidence) if r.confidence is not None else None,
            source=r.source,
            version=r.version,
            expires_at=r.expires_at,
        )
        for r in rules
    ]
    return ok(
        {
            "version": latest,
            "rules": [i.model_dump(mode="json") for i in items],
            "server_time": __import__("datetime").datetime.now(),
        }
    )


@router.post("/feedback")
async def feedback(
    req: FeedbackRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    误拦纠正。

    feedback_type=1：误拦放行 → 加入白名单豁免；
    feedback_type=2：确认骚扰 → 加入黑名单。
    """
    family_id = _family_id(user.id)
    if family_id is None:
        # 无家庭组时仅做个人标记，不生成共享规则
        rule_service.mark_logs_false_positive(user.id, req.phone_hash)
        return ok({"recorded": True, "shared": False})

    rule_service.apply_feedback(family_id, req.phone_hash, req.feedback_type)

    if req.feedback_type == 1:
        rule_service.mark_logs_false_positive(user.id, req.phone_hash)

    return ok({"recorded": True, "shared": True, "family_id": family_id})


@router.get("/pending")
async def pending_rules(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """待人工审核的低置信度规则，供管理界面确认。"""
    family_id = _family_id(user.id)
    if family_id is None:
        return ok({"rules": []})
    rows = (
        db.query(RulePackage)
        .filter(
            RulePackage.family_id == family_id,
            RulePackage.status == 1,
            RulePackage.is_active.is_(True),
        )
        .order_by(RulePackage.id.desc())
        .limit(100)
        .all()
    )
    return ok(
        {
            "rules": [
                {
                    "rule_id": r.id,
                    "rule_type": r.rule_type,
                    "pattern": r.pattern,
                    "confidence": float(r.confidence) if r.confidence else None,
                    "created_at": r.created_at,
                }
                for r in rows
            ]
        }
    )


@router.post("/review/{rule_id}")
async def review_rule(
    rule_id: int,
    approve: bool = True,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """审核低置信度规则：approve=True 生效，False 废弃。"""
    family_id = _family_id(user.id)
    if family_id is None:
        return fail(1003, "未加入家庭组")
    with session_scope() as s:
        rule = (
            s.query(RulePackage)
            .filter(RulePackage.id == rule_id, RulePackage.family_id == family_id)
            .first()
        )
        if rule is None:
            return fail(1003, "规则不存在")
        if approve:
            rule.status = 0
        else:
            rule.status = 2
            rule.is_active = False
        s.add(rule)
    return ok({"reviewed": True, "approved": approve})
