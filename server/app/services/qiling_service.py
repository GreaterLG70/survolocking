"""
起零（iStero）骚扰号码数据集成。

关键约束：
1. 向起零传【原始号码】（第三方骚扰库按真实号匹配，本地代码实测参数名为 number）；
   本服务永不持久化原始号码——业务库 intercept_logs 只存 HMAC 哈希。
2. 严格超时（可配置，默认 6s——起零实测响应 ~3.7s），超时立即降级。
   本服务不在端侧实时决策链路上：端侧第2层只读本地缓存并后台预热，
   调用方为端侧后台任务或手动查询界面，均允许等待数秒；
3. 成功响应 data 为列表：[{"name":数据源,"msg":标记}, ...]，按最高严重度聚合；
4. 结果缓存 5 分钟，避免重复计费与延迟。
"""
from __future__ import annotations

import logging
from typing import Literal

import httpx

from app.config import settings
from app.core.cache import cache

logger = logging.getLogger("survolocking.qiling")

RiskLevel = Literal["fraud", "harassment", "normal", "unknown", "unavailable"]

# 风险等级 → 端侧动作
_ACTION_MAP = {
    "fraud": "block",
    "harassment": "analyze",
    "normal": "analyze",
    "unknown": "analyze",
    "unavailable": "analyze",
}

# 严重度排序，用于多数据源聚合（数值越大越危险）
_SEVERITY = {"unknown": 0, "normal": 1, "harassment": 2, "fraud": 3}


def _normalize_msg(msg: str | None) -> RiskLevel:
    """将起零单条 msg（如 '诈骗' / '正常号码' / '未知号码' / '广告推销'）映射为内部等级。"""
    if not msg:
        return "unknown"
    t = str(msg).strip()
    if "诈骗" in t or "欺诈" in t:  # 优先判定诈骗/欺诈
        return "fraud"
    if any(k in t for k in ("骚扰", "广告", "推销", "营销", "中介", "贷款", "催收", "博彩", "赌博")):
        return "harassment"
    if "正常" in t:
        return "normal"
    if "未知" in t:
        return "unknown"
    return "unknown"


def _aggregate(items: list[dict]) -> RiskLevel:
    """聚合多个数据源（360/搜狗/百度手机卫士等）的标记，取最高严重度。"""
    if not items:
        return "unknown"
    best: RiskLevel = "unknown"
    for it in items:
        msg = it.get("msg") if isinstance(it, dict) else str(it)
        lvl = _normalize_msg(msg)
        if _SEVERITY[lvl] > _SEVERITY[best]:
            best = lvl
    return best


def _extract_level(body: dict) -> RiskLevel:
    """
    从起零响应提取风险等级。

    实测成功返回：{"code":200, "data":[{"name":"360手机卫士","msg":"正常号码"}, ...]}
    这里做多结构兜底：list(data) → 聚合；dict(data) 或顶层字段 → 单条归一。
    """
    for key in ("risk_level", "level", "type", "tag", "label", "category", "result"):
        if isinstance(body.get(key), str):
            return _normalize_msg(body[key])
    data = body.get("data")
    if isinstance(data, list):
        return _aggregate(data)
    if isinstance(data, dict):
        for key in ("risk_level", "level", "type", "tag", "label", "category", "result"):
            if isinstance(data.get(key), str):
                return _normalize_msg(data[key])
    return "unknown"


async def query(phone: str) -> dict:
    """
    查询号码风险。phone 为【原始号码】（起零按真实号匹配；本服务不持久化原始号）。

    返回结构：
    {
      "phone": str,
      "risk_level": RiskLevel,
      "action": "block" | "analyze",
      "cached": bool,
      "source": "qiling" | "cache" | "unavailable"
    }
    """
    cache_key = f"qiling:{phone}"

    if not settings.qiling_ready:
        return {
            "phone": phone,
            "risk_level": "unavailable",
            "action": "analyze",
            "cached": False,
            "source": "unavailable",
        }

    cached = cache.get_json(cache_key)
    if cached:
        cached["cached"] = True
        cached["source"] = "cache"
        return cached

    result: dict
    try:
        async with httpx.AsyncClient(timeout=settings.QILING_TIMEOUT) as client:
            resp = await client.get(
                settings.QILING_BASE_URL,
                headers={"Authorization": f"Bearer {settings.QILING_TOKEN}"},
                params={settings.QILING_PHONE_PARAM: phone},
            )
            resp.raise_for_status()
            body = resp.json()
        if settings.DEBUG:
            logger.debug("起零原始响应 phone=%s -> %s", phone, body)
        # 起零成功码为 200；非成功（如 400 号码非法）一律降级，避免阻塞决策
        if body.get("code") != 200:
            logger.warning("起零返回非成功码 %s: %s", body.get("code"), body.get("message"))
            raise ValueError(f"qiling non-200: {body.get('code')}")
        level = _extract_level(body)
        result = {
            "phone": phone,
            "risk_level": level,
            "action": _ACTION_MAP[level],
            "cached": False,
            "source": "qiling",
        }
    except Exception as e:
        # 超时或异常一律降级，绝不向上抛出阻塞决策
        logger.warning("起零查询失败，降级处理: %s", e)
        result = {
            "phone": phone,
            "risk_level": "unavailable",
            "action": "analyze",
            "cached": False,
            "source": "unavailable",
        }
        # 失败结果短缓存，避免同一号码反复触发超时
        cache.setex_json(cache_key, 60, result)
        return result

    cache.setex_json(cache_key, settings.QILING_CACHE_TTL, result)
    return result
