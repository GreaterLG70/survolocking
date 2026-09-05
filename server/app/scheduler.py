"""定时任务调度：DeepSeek 每日离线分析 + 分区维护 + 规则过期清理。"""
from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.database import drop_old_partitions, ensure_month_partitions, get_engine
from app.services import deepseek_service, rule_service

logger = logging.getLogger("survolocking.scheduler")

scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")


async def _daily_analysis() -> None:
    logger.info("开始每日 DeepSeek 离线分析")
    try:
        results = await deepseek_service.analyze_all_families()
        logger.info("每日分析完成，共处理 %s 个家庭组", len(results))
    except Exception:
        logger.exception("每日分析任务异常")


async def _daily_aggregation() -> None:
    """家庭聚合：基于多成员标记生成规则，不消耗 Token。"""
    from app.models import Family

    from app.database import session_scope

    logger.info("开始家庭聚合分析")
    try:
        with session_scope() as s:
            families = [
                f[0] for f in s.query(Family.id).filter(Family.is_active.is_(True)).all()
            ]
        for fid in families:
            rule_service.family_aggregation(fid)
        logger.info("家庭聚合完成，共处理 %s 个家庭组", len(families))
    except Exception:
        logger.exception("家庭聚合任务异常")


def _maintain_partitions() -> None:
    """维护按月分区：预建未来分区，清理超过 6 个月的旧分区。"""
    if not settings.APP_ENV == "dev":
        pass
    from app.database import is_sqlite_fallback

    if is_sqlite_fallback():
        return
    engine = get_engine()
    try:
        created = ensure_month_partitions(engine, months_ahead=2, months_back=1)
        if created:
            logger.info("新建分区: %s", created)
        dropped = drop_old_partitions(engine, keep_months=6)
        if dropped:
            logger.info("清理旧分区: %s", dropped)
    except Exception:
        logger.exception("分区维护异常")


def _expire_rules() -> None:
    try:
        count = rule_service.expire_old_rules()
        if count:
            logger.info("清理过期规则 %s 条", count)
    except Exception:
        logger.exception("规则过期清理异常")


def start_scheduler() -> None:
    if scheduler.running:
        return

    scheduler.add_job(
        _daily_analysis,
        CronTrigger(
            hour=settings.ANALYSIS_CRON_HOUR, minute=settings.ANALYSIS_CRON_MINUTE
        ),
        id="daily_deepseek_analysis",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # 聚合任务早于 DeepSeek 30 分钟执行，使其结果可被分析任务参考
    scheduler.add_job(
        _daily_aggregation,
        CronTrigger(
            hour=settings.ANALYSIS_CRON_HOUR,
            minute=(settings.ANALYSIS_CRON_MINUTE + 30) % 60,
        ),
        id="daily_family_aggregation",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    scheduler.add_job(
        _maintain_partitions,
        CronTrigger(hour=3, minute=30),
        id="partition_maintenance",
        replace_existing=True,
    )

    scheduler.add_job(
        _expire_rules,
        CronTrigger(hour=4, minute=0),
        id="expire_rules",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(
        "定时任务已启动：每日 %02d:%02d 执行 DeepSeek 分析",
        settings.ANALYSIS_CRON_HOUR,
        settings.ANALYSIS_CRON_MINUTE,
    )


def shutdown_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
