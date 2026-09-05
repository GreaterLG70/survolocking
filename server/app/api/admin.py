"""运维接口：手动触发分析、连通性自检、分区维护。"""
from __future__ import annotations

import time
from datetime import datetime

from fastapi import APIRouter, Header, HTTPException
from sqlalchemy import text

from app.config import settings
from app.core.cache import cache
from app.database import get_engine, is_sqlite_fallback, session_scope
from app.models import AnalysisJob, Family, InterceptLog, RulePackage, User
from app.schemas.common import fail, ok
from app.services import deepseek_service, qiling_service, rule_service

router = APIRouter(prefix="/admin", tags=["运维"])


def _guard(x_admin_token: str | None) -> None:
    """管理员保护：生产必须配置 ADMIN_TOKEN；dev 环境允许空令牌。"""
    if settings.APP_ENV == "dev" and not settings.ADMIN_TOKEN:
        return
    if not x_admin_token or x_admin_token != settings.ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="无权访问")


@router.post("/analysis/trigger")
async def trigger_analysis(
    family_id: int | None = None, x_admin_token: str | None = Header(default=None)
):
    """手动触发离线分析，用于联调与补跑。"""
    _guard(x_admin_token)
    if family_id:
        result = await deepseek_service.analyze_family(family_id)
        return ok(result)
    results = await deepseek_service.analyze_all_families()
    return ok({"results": results})


@router.post("/aggregation/trigger")
async def trigger_aggregation(
    family_id: int | None = None, x_admin_token: str | None = Header(default=None)
):
    _guard(x_admin_token)
    if family_id:
        return ok({"rules": rule_service.family_aggregation(family_id)})

    with session_scope() as s:
        families = [f[0] for f in s.query(Family.id).filter(Family.is_active.is_(True)).all()]
    total = 0
    for fid in families:
        total += len(rule_service.family_aggregation(fid))
    return ok({"families": len(families), "rules_created": total})


@router.get("/selftest")
async def selftest(x_admin_token: str | None = Header(default=None)):
    """
    外部服务连通性自检。

    逐项验证 MySQL / 缓存 / DeepSeek / 起零 是否可用，
    用于在拿到凭据后快速确认配置正确性。
    """
    _guard(x_admin_token)
    report: dict[str, dict] = {}

    # 数据库
    t0 = time.perf_counter()
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        report["database"] = {
            "ok": True,
            "backend": "sqlite" if is_sqlite_fallback() else "mysql",
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }
    except Exception as e:
        report["database"] = {"ok": False, "error": str(e)}

    # 缓存
    try:
        cache.setex_json("__selftest__", 10, 1)
        ok_cache = cache.get_json("__selftest__") == 1
        report["cache"] = {"ok": ok_cache, "backend": cache.backend}
    except Exception as e:
        report["cache"] = {"ok": False, "error": str(e)}

    # DeepSeek
    if settings.deepseek_ready:
        t0 = time.perf_counter()
        parsed, usage = await deepseek_service.call_deepseek(
            "你是测试助手。", '请返回 {"ok": true}'
        )
        report["deepseek"] = {
            "ok": parsed is not None,
            "parsed": parsed,
            "usage": usage,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }
    else:
        report["deepseek"] = {"ok": False, "error": "未配置 DEEPSEEK_API_KEY"}

    # 起零
    if settings.qiling_ready:
        t0 = time.perf_counter()
        test_hash = "0" * 64
        result = await qiling_service.query("13800138000")
        report["qiling"] = {
            "ok": result.get("source") != "unavailable",
            "result": result,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }
    else:
        report["qiling"] = {"ok": False, "error": "未配置 QILING_TOKEN"}

    # 短信：校验模板/签名是否真实可用（探测到 isv.SMS_TEMPLATE_ILLEGAL /
    # isv.SMS_SIGNATURE_ILLEGAL 即视为不可用，提示去控制台核对）
    if settings.sms_ready:
        from app.services import sms_service

        probe_ok = await sms_service.probe_channel()
        report["sms"] = {
            "ok": probe_ok[0],
            "provider": settings.SMS_PROVIDER,
            "detail": probe_ok[1],
        }
    else:
        report["sms"] = {
            "ok": False,
            "provider": settings.SMS_PROVIDER,
            "detail": "dev 模式（不真实发送）或未配置 AK/SK",
        }

    return ok(report)


@router.get("/stats/overview")
async def overview(x_admin_token: str | None = Header(default=None)):
    """系统数据概览。"""
    _guard(x_admin_token)
    with session_scope() as s:
        data = {
            "users": s.query(User).count(),
            "families": s.query(Family).count(),
            "logs": s.query(InterceptLog).count(),
            "rules_active": s.query(RulePackage)
            .filter(RulePackage.is_active.is_(True))
            .count(),
            "analysis_jobs_today": s.query(AnalysisJob)
            .filter(AnalysisJob.created_at >= datetime.now().replace(hour=0, minute=0, second=0))
            .count(),
        }
    return ok(data)


@router.post("/partitions/maintain")
async def maintain_partitions(x_admin_token: str | None = Header(default=None)):
    """手动维护按月分区。"""
    _guard(x_admin_token)
    from app.database import drop_old_partitions, ensure_month_partitions

    if is_sqlite_fallback():
        return fail(1002, "SQLite 降级模式不支持分区")
    engine = get_engine()
    created = ensure_month_partitions(engine)
    dropped = drop_old_partitions(engine)
    return ok({"created": created, "dropped": dropped})
