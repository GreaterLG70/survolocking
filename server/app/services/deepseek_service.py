"""
DeepSeek 离线分析引擎。

定位：不参与实时决策，仅每日凌晨批量分析日志并生成规则。

成本控制：
- 每家庭每次最多 ANALYSIS_BATCH_SIZE 条日志；
- 每家庭每天最多 ANALYSIS_DAILY_LIMIT_PER_FAMILY 次调用；
- 相同特征 7 天内不重复分析（Redis/内存缓存）；
- 只生成置信度 >= RULE_CONFIDENCE_THRESHOLD 的规则。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta

import httpx

from app.config import settings
from app.core.cache import cache
from app.database import session_scope
from app.models import AnalysisJob, InterceptLog, RulePackage

logger = logging.getLogger("survolocking.deepseek")

SYSTEM_PROMPT = """你是一个骚扰电话特征分析专家。你将收到一批拦截日志（JSON格式），
每条包含：号码哈希、呼叫时间（小时级）、振铃时长、拦截来源、用户事后标记。

请分析后返回JSON格式的新增拦截规则，仅输出规则，不要解释。

输出格式：
{
  "rules": [
    {
      "type": "prefix|time|behavior",
      "pattern": "规则模式",
      "confidence": 0.0-1.0,
      "reason": "简短生成理由"
    }
  ],
  "version": "规则包版本号"
}

规则类型说明：
- prefix: 号段前缀，pattern如"170"表示拦截所有170开头号码（仅数字，3-8位）
- time: 时间段，pattern格式固定为"周XHH-HH点"，如"周日14-16点"
- behavior: 行为特征，pattern如"ring<=3s"或"ring>=30s"

注意：
1. 只生成置信度>=0.7的规则
2. 避免与用户手动标记冲突（user_mark=2 表示客户，不得拦截）
3. 若无可生成的新规则，返回{"rules": []}
4. 月度内相同规则不重复生成
5. 号码已是哈希值，不要试图从中推断具体号码"""


def _build_user_prompt(logs: list[dict], existing_patterns: list[str]) -> str:
    header = {
        "说明": "以下为某家庭组最近24小时的拦截日志（已脱敏）",
        "日志总数": len(logs),
        "已有规则": existing_patterns[:50],
        "logs": logs,
    }
    return json.dumps(header, ensure_ascii=False, separators=(",", ":"))


async def call_deepseek(system: str, user: str) -> tuple[dict | None, dict | None]:
    """
    调用 DeepSeek Chat Completions。
    返回 (解析后的JSON, token用量)。失败返回 (None, None)。
    """
    if not settings.deepseek_ready:
        logger.warning("DeepSeek 未配置 API Key，跳过分析")
        return None, None

    # DeepSeek 官方约束：response_format=json_object 时，prompt 中必须出现 "json" 字样，
    # 否则返回 400 invalid_request_error（2026-09 实测）。这里自动兜底追加提示。
    if "json" not in f"{system}{user}".lower():
        user = f"{user}\n（请以 JSON 格式输出）"

    url = f"{settings.DEEPSEEK_BASE_URL.rstrip('/')}/chat/completions"
    payload = {
        "model": settings.DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=settings.DEEPSEEK_TIMEOUT) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            body = resp.json()
    except Exception as e:
        logger.error("DeepSeek 调用失败: %s", e)
        return None, None

    usage = body.get("usage", {})
    content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
    return _safe_parse_json(content), usage


def _safe_parse_json(content: str) -> dict | None:
    """
    容错解析：模型偶尔会用 ```json 代码块包裹输出。
    """
    if not content:
        return None
    text = content.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 兜底：截取首个 { 到最后一个 }
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
    logger.error("DeepSeek 返回内容无法解析为 JSON: %s", content[:200])
    return None


# ------------------------------------------------------------------ 规则校验


_PREFIX_RE = re.compile(r"^\d{3,8}$")
_TIME_RE = re.compile(r"^周[一二三四五六日天](\d{1,2})-(\d{1,2})点$")
_BEHAVIOR_RE = re.compile(r"^ring(<=|>=|<|>|==)(\d{1,4})s$")


def validate_rule(rule: dict) -> tuple[int, str] | None:
    """
    校验单条规则。返回 (rule_type, normalized_pattern)，非法返回 None。

    rule_type: 1黑名单 2号段 3行为规则 4白名单豁免
    """
    rtype = str(rule.get("type", "")).strip().lower()
    pattern = str(rule.get("pattern", "")).strip()
    confidence = float(rule.get("confidence", 0) or 0)

    if confidence < settings.RULE_CONFIDENCE_THRESHOLD:
        return None

    if rtype == "prefix":
        if not _PREFIX_RE.match(pattern):
            return None
        return 2, pattern

    if rtype == "time":
        m = _TIME_RE.match(pattern)
        if not m:
            return None
        start, end = int(m.group(1)), int(m.group(2))
        if not (0 <= start <= 23 and 0 <= end <= 23 and start != end):
            return None
        return 3, f"time:{pattern}"

    if rtype == "behavior":
        if not _BEHAVIOR_RE.match(pattern):
            return None
        return 3, f"behavior:{pattern}"

    return None


# ------------------------------------------------------------------ 主流程


def _fetch_recent_logs(family_id: int, hours: int = 24) -> list[InterceptLog]:
    since = datetime.now() - timedelta(hours=hours)
    with session_scope() as s:
        logs = (
            s.query(InterceptLog)
            .filter(
                InterceptLog.family_id == family_id,
                InterceptLog.call_time >= since,
            )
            .order_by(InterceptLog.call_time.desc())
            .limit(settings.ANALYSIS_BATCH_SIZE)
            .all()
        )
        s.expunge_all()
        return list(logs)


def _existing_patterns(family_id: int) -> list[str]:
    with session_scope() as s:
        rows = (
            s.query(RulePackage.pattern)
            .filter(RulePackage.family_id == family_id, RulePackage.is_active.is_(True))
            .all()
        )
        return [r[0] for r in rows]


def _feature_signature(logs: list[InterceptLog]) -> str:
    """生成日志特征签名，用于 7 天内去重。"""
    import hashlib

    parts = [
        f"{l.action}:{l.decision_source}:{l.ring_duration or 0}:{l.user_mark or 0}"
        for l in logs
    ]
    return hashlib.sha256("|".join(sorted(parts)).encode()).hexdigest()


def _daily_call_count(family_id: int) -> int:
    today = datetime.now().strftime("%Y%m%d")
    with session_scope() as s:
        return (
            s.query(AnalysisJob)
            .filter(
                AnalysisJob.family_id == family_id,
                AnalysisJob.created_at >= datetime.now() - timedelta(days=1),
            )
            .count()
        )


async def analyze_family(family_id: int) -> dict:
    """
    对单个家庭组执行一次离线分析。返回结果统计。

    全流程失败均被捕获并记录，不会中断定时任务对其他家庭的处理。
    """
    result = {
        "family_id": family_id,
        "status": "skipped",
        "log_count": 0,
        "rules_created": 0,
        "reason": "",
    }

    if _daily_call_count(family_id) >= settings.ANALYSIS_DAILY_LIMIT_PER_FAMILY:
        result["reason"] = "达到每日调用上限"
        return result

    logs = _fetch_recent_logs(family_id)
    if not logs:
        result["reason"] = "无新增日志"
        return result

    sig = _feature_signature(logs)
    dedup_key = f"analysis:sig:{family_id}:{sig}"
    if cache.get_json(dedup_key):
        result["reason"] = "特征未变化，7天内已分析"
        return result

    log_dicts = [
        {
            "phone_hash": l.phone_hash[:12],
            "hour": l.call_time.hour,
            "ring": l.ring_duration,
            "action": l.action,
            "source": l.decision_source,
            "user_mark": l.user_mark,
        }
        for l in logs
    ]

    parsed, usage = await call_deepseek(
        SYSTEM_PROMPT, _build_user_prompt(log_dicts, _existing_patterns(family_id))
    )

    if parsed is None:
        with session_scope() as s:
            s.add(
                AnalysisJob(
                    family_id=family_id,
                    log_count=len(logs),
                    status="failed",
                    error_message="DeepSeek 调用或解析失败",
                )
            )
        result["status"] = "failed"
        result["reason"] = "DeepSeek 调用或解析失败"
        result["log_count"] = len(logs)
        return result

    rules = parsed.get("rules") or []
    created = _persist_rules(family_id, rules)

    cache.setex_json(dedup_key, 7 * 24 * 3600, 1)

    with session_scope() as s:
        s.add(
            AnalysisJob(
                family_id=family_id,
                log_count=len(logs),
                rules_generated=len(created),
                prompt_tokens=(usage or {}).get("prompt_tokens"),
                completion_tokens=(usage or {}).get("completion_tokens"),
                status="success",
            )
        )

    result.update(
        status="success", log_count=len(logs), rules_created=len(created)
    )
    return result


def _persist_rules(family_id: int, rules: list[dict]) -> list[RulePackage]:
    """校验并写入规则。低置信度规则进入人工审核区（status=1）。"""
    created: list[RulePackage] = []
    with session_scope() as s:
        existing = {
            r[0]
            for r in s.query(RulePackage.pattern)
            .filter(RulePackage.family_id == family_id, RulePackage.is_active.is_(True))
            .all()
        }
        max_version = (
            s.query(RulePackage.version)
            .filter(RulePackage.family_id == family_id)
            .order_by(RulePackage.version.desc())
            .first()
        )
        version = (max_version[0] if max_version else 0) + 1

        for raw in rules:
            if not isinstance(raw, dict):
                continue
            validated = validate_rule(raw)
            if validated is None:
                continue
            rule_type, pattern = validated
            if pattern in existing:
                continue

            confidence = float(raw.get("confidence", 0) or 0)
            rule = RulePackage(
                family_id=family_id,
                rule_type=rule_type,
                pattern=pattern,
                confidence=min(confidence, 0.99),
                source=2,  # DeepSeek 生成
                status=0 if confidence >= settings.RULE_CONFIDENCE_THRESHOLD else 1,
                version=version,
            )
            s.add(rule)
            existing.add(pattern)
            created.append(rule)
    return created


async def analyze_all_families() -> list[dict]:
    """每日定时入口：遍历所有活跃家庭组。"""
    from app.models import Family

    with session_scope() as s:
        families = [f[0] for f in s.query(Family.id).filter(Family.is_active.is_(True)).all()]

    results = []
    for fid in families:
        try:
            results.append(await analyze_family(fid))
        except Exception as e:  # pragma: no cover
            logger.exception("家庭 %s 分析异常", fid)
            results.append(
                {"family_id": fid, "status": "error", "reason": str(e)}
            )
    logger.info("DeepSeek 每日分析完成: %s", results)
    return results
