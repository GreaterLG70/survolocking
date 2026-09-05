"""拦截日志上传 / 事后标记 / 数据导出。"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.core.security import truncate_to_hour
from app.database import get_db, session_scope
from app.models import InterceptLog, User
from app.schemas.common import fail, ok
from app.schemas.schemas import LogUploadRequest, MarkRequest

router = APIRouter(prefix="/logs", tags=["拦截日志"])


def _current_family_id(user_id: int) -> int | None:
    from app.models import FamilyMember

    with session_scope() as s:
        row = (
            s.query(FamilyMember.family_id)
            .filter(FamilyMember.user_id == user_id, FamilyMember.is_active.is_(True))
            .first()
        )
        return row[0] if row else None


@router.post("/upload")
async def upload_logs(
    req: LogUploadRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    批量上传脱敏拦截日志。

    合规处理：通话时间强制截断到小时级，避免精确时间反推出个人行踪。
    """
    family_id = _current_family_id(user.id)
    accepted = 0
    rejected = 0

    with session_scope() as s:
        for item in req.logs:
            # 上传的哈希长度必须是 SHA-256 的 64 位十六进制
            if len(item.phone_hash) != 64:
                rejected += 1
                continue
            s.add(
                InterceptLog(
                    phone_hash=item.phone_hash,
                    family_id=family_id,
                    user_id=user.id,
                    call_time=truncate_to_hour(item.call_time),
                    ring_duration=item.ring_duration,
                    action=item.action,
                    decision_source=item.decision_source,
                )
            )
            accepted += 1

    return ok({"accepted": accepted, "rejected": rejected})


@router.post("/mark")
async def mark_call(
    req: MarkRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    通话结束后用户标记（1骚扰/2客户/3不确定）。

    标记结果会更新最近一条同号码日志，作为 DeepSeek 分析与家庭聚合的输入。
    """
    with session_scope() as s:
        log = (
            s.query(InterceptLog)
            .filter(
                InterceptLog.user_id == user.id,
                InterceptLog.phone_hash == req.phone_hash,
            )
            .order_by(InterceptLog.call_time.desc())
            .first()
        )
        if log is None:
            # 日志尚未上传（离线场景），补建一条
            log = InterceptLog(
                phone_hash=req.phone_hash,
                user_id=user.id,
                family_id=_current_family_id(user.id),
                call_time=truncate_to_hour(datetime.now()),
                action=2,
                decision_source=4,
            )
            s.add(log)
            s.flush()
        log.user_mark = req.user_mark
        s.add(log)
    return ok({"marked": True, "user_mark": req.user_mark})


@router.get("/export")
async def export_logs(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """导出本人拦截日志，用于申诉或备份（合规要求：用户可导出个人数据）。"""
    rows = (
        db.query(InterceptLog)
        .filter(InterceptLog.user_id == user.id)
        .order_by(InterceptLog.call_time.desc())
        .limit(5000)
        .all()
    )
    data = [
        {
            "phone_hash": r.phone_hash,
            "call_time": r.call_time.isoformat(),
            "ring_duration": r.ring_duration,
            "action": r.action,
            "decision_source": r.decision_source,
            "user_mark": r.user_mark,
            "is_false_positive": r.is_false_positive,
        }
        for r in rows
    ]
    return ok({"count": len(data), "logs": data})


@router.get("/stats")
async def stats(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """个人拦截统计概览。"""
    from sqlalchemy import func

    total = db.query(func.count(InterceptLog.id)).filter(
        InterceptLog.user_id == user.id
    ).scalar() or 0
    blocked = (
        db.query(func.count(InterceptLog.id))
        .filter(InterceptLog.user_id == user.id, InterceptLog.action == 1)
        .scalar()
        or 0
    )
    false_positive = (
        db.query(func.count(InterceptLog.id))
        .filter(
            InterceptLog.user_id == user.id, InterceptLog.is_false_positive.is_(True)
        )
        .scalar()
        or 0
    )
    return ok(
        {
            "total": total,
            "blocked": blocked,
            "allowed": total - blocked,
            "false_positive": false_positive,
            "block_rate": round(blocked / total, 4) if total else 0,
        }
    )
