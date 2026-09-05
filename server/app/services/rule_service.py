"""
规则包服务：版本管理、增量下发、家庭聚合、误拦纠正。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import func as sa_func

from app.config import settings
from app.database import session_scope
from app.models import FamilyMember, InterceptLog, Notification, RulePackage

logger = logging.getLogger("survolocking.rule")


def next_version(family_id: int) -> int:
    with session_scope() as s:
        v = (
            s.query(sa_func.max(RulePackage.version))
            .filter(RulePackage.family_id == family_id)
            .scalar()
        )
        return (v or 0) + 1


def get_incremental_rules(family_id: int, since_version: int) -> tuple[int, list[RulePackage]]:
    """拉取自 since_version 之后的增量规则，返回 (当前最新版本号, 规则列表)。"""
    with session_scope() as s:
        latest = (
            s.query(sa_func.max(RulePackage.version))
            .filter(RulePackage.family_id == family_id)
            .scalar()
            or 0
        )
        rules = (
            s.query(RulePackage)
            .filter(
                RulePackage.family_id == family_id,
                RulePackage.version > since_version,
                RulePackage.is_active.is_(True),
                RulePackage.status == 0,  # 仅下发已生效规则
            )
            .order_by(RulePackage.version.asc())
            .all()
        )
        s.expunge_all()
        return latest, list(rules)


def expire_old_rules() -> int:
    """清理过期规则，返回失效条数。"""
    now = datetime.now()
    with session_scope() as s:
        rows = (
            s.query(RulePackage)
            .filter(
                RulePackage.expires_at.isnot(None),
                RulePackage.expires_at < now,
                RulePackage.is_active.is_(True),
            )
            .all()
        )
        for r in rows:
            r.is_active = False
            r.status = 2
            s.add(r)
        return len(rows)


# ------------------------------------------------------------------ 家庭聚合


def family_aggregation(family_id: int, days: int = 30) -> list[dict]:
    """
    家庭组内多成员标记聚合。

    规则：
    - 至少 FAMILY_AGGREGATION_MIN_USERS 个不同成员标记过才考虑；
    - 骚扰占比 >= FAMILY_SPAM_RATIO → 生成黑名单规则；
    - 客户标记 >= 2 且骚扰占比 < FAMILY_SAFE_RATIO → 生成白名单豁免；
    - 标记冲突 → 不生成规则。
    """
    since = datetime.now() - timedelta(days=days)
    stats: dict[str, dict] = {}

    with session_scope() as s:
        rows = (
            s.query(
                InterceptLog.phone_hash,
                InterceptLog.user_mark,
                InterceptLog.user_id,
            )
            .filter(
                InterceptLog.family_id == family_id,
                InterceptLog.user_mark.isnot(None),
                InterceptLog.call_time > since,
            )
            .all()
        )
        for phone_hash, user_mark, user_id in rows:
            item = stats.setdefault(
                phone_hash, {"marks": [], "users": set()}
            )
            item["marks"].append(user_mark)
            item["users"].add(user_id)

        existing = {
            r[0]
            for r in s.query(RulePackage.pattern)
            .filter(RulePackage.family_id == family_id, RulePackage.is_active.is_(True))
            .all()
        }
        version = next_version(family_id)

        created: list[dict] = []
        for phone_hash, data in stats.items():
            if len(data["users"]) < settings.FAMILY_AGGREGATION_MIN_USERS:
                continue

            marks = data["marks"]
            spam_count = marks.count(1)
            safe_count = marks.count(2)
            total = len(marks)
            spam_ratio = spam_count / total if total else 0

            pattern = f"hash:{phone_hash}"
            if pattern in existing:
                continue

            if spam_ratio >= settings.FAMILY_SPAM_RATIO:
                s.add(
                    RulePackage(
                        family_id=family_id,
                        rule_type=1,
                        pattern=pattern,
                        confidence=min(spam_ratio, 0.99),
                        source=3,
                        status=0,
                        version=version,
                    )
                )
                created.append({"pattern": pattern, "type": 1, "confidence": spam_ratio})
                existing.add(pattern)
            elif (
                safe_count >= settings.FAMILY_AGGREGATION_MIN_USERS
                and spam_ratio < settings.FAMILY_SAFE_RATIO
            ):
                s.add(
                    RulePackage(
                        family_id=family_id,
                        rule_type=4,
                        pattern=pattern,
                        confidence=min(1 - spam_ratio, 0.99),
                        source=3,
                        status=0,
                        version=version,
                    )
                )
                created.append(
                    {"pattern": pattern, "type": 4, "confidence": 1 - spam_ratio}
                )
                existing.add(pattern)
            # 标记冲突：不生成规则

    if created:
        logger.info("家庭 %s 聚合生成 %s 条规则", family_id, len(created))
    return created


# ------------------------------------------------------------------ 误拦纠正


def apply_feedback(family_id: int, phone_hash: str, feedback_type: int) -> bool:
    """
    用户误拦纠正。

    feedback_type=1 误拦放行：将该号码加入白名单豁免，并停用相关黑名单规则；
    feedback_type=2 确认骚扰：将该号码加入黑名单。
    """
    with session_scope() as s:
        target_pattern = f"hash:{phone_hash}"
        version = next_version(family_id)

        # 先停用同一号码的相反规则，避免规则打架
        opposite_type = 4 if feedback_type == 1 else 4
        s.query(RulePackage).filter(
            RulePackage.family_id == family_id,
            RulePackage.pattern == target_pattern,
            RulePackage.rule_type == opposite_type,
        ).update({RulePackage.is_active: False, RulePackage.status: 2})

        rule_type = 4 if feedback_type == 1 else 1
        confidence = 1.0 if feedback_type == 1 else 0.95

        s.add(
            RulePackage(
                family_id=family_id,
                rule_type=rule_type,
                pattern=target_pattern,
                confidence=confidence,
                source=1,  # 用户标记
                status=0,
                version=version,
            )
        )
    return True


def mark_logs_false_positive(user_id: int, phone_hash: str) -> int:
    """将历史日志标记为误拦，供后续分析修正。"""
    with session_scope() as s:
        count = (
            s.query(InterceptLog)
            .filter(
                InterceptLog.user_id == user_id,
                InterceptLog.phone_hash == phone_hash,
                InterceptLog.is_false_positive.is_(False),
            )
            .update({InterceptLog.is_false_positive: True})
        )
        return count


# ------------------------------------------------------------------ 通知


def list_notifications(user_id: int, limit: int = 50) -> list[Notification]:
    with session_scope() as s:
        rows = (
            s.query(Notification)
            .filter(Notification.user_id == user_id)
            .order_by(Notification.id.desc())
            .limit(limit)
            .all()
        )
        s.expunge_all()
        return list(rows)


def mark_notifications_read(user_id: int, ids: list[int] | None = None) -> int:
    with session_scope() as s:
        q = s.query(Notification).filter(
            Notification.user_id == user_id, Notification.is_read.is_(False)
        )
        if ids:
            q = q.filter(Notification.id.in_(ids))
        rows = q.all()
        for r in rows:
            r.is_read = True
            s.add(r)
        return len(rows)


def get_family_member_ids(family_id: int, exclude_user_id: int | None = None) -> list[int]:
    with session_scope() as s:
        q = s.query(FamilyMember.user_id).filter(
            FamilyMember.family_id == family_id, FamilyMember.is_active.is_(True)
        )
        if exclude_user_id is not None:
            q = q.filter(FamilyMember.user_id != exclude_user_id)
        return [r[0] for r in q.all()]
