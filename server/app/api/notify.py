"""站内通知（DEV 推送模式下的主通道）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.database import get_db
from app.models import User
from app.schemas.common import ok
from app.services import rule_service

router = APIRouter(prefix="/notify", tags=["通知"])


@router.get("/list")
async def list_notify(
    limit: int = 50, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    rows = rule_service.list_notifications(user.id, limit)
    return ok(
        {
            "notifications": [
                {
                    "id": r.id,
                    "notify_type": r.notify_type,
                    "title": r.title,
                    "content": r.content,
                    "payload": r.payload,
                    "is_read": r.is_read,
                    "created_at": r.created_at,
                }
                for r in rows
            ]
        }
    )


@router.post("/read")
async def mark_read(
    ids: list[int] | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    count = rule_service.mark_notifications_read(user.id, ids)
    return ok({"marked": count})
