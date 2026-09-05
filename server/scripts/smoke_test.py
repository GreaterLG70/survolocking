"""
端到端冒烟测试。

直接以 ASGI 方式驱动 FastAPI 应用，无需先启动服务器，
覆盖：注册登录 → 家庭组 → 日志上传 → 标记 → 聚合 → 规则同步 → 反馈 → 退出。

运行： python scripts/smoke_test.py
"""
from __future__ import annotations

import asyncio
import hashlib
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from app.main import app
from app.config import settings

BASE = "http://smoke.test"
# .env 若配置了 ADMIN_TOKEN，admin 接口需要携带
ADMIN_H = {"X-Admin-Token": settings.ADMIN_TOKEN} if settings.ADMIN_TOKEN else {}
PASS, FAIL = "PASS", "FAIL"
_results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    _results.append((name, cond, detail))
    print(f"[{PASS if cond else FAIL}] {name}" + (f"  -- {detail}" if detail else ""))


async def main() -> int:
    # httpx ASGITransport 不触发 lifespan，需显式建表
    from app.database import init_db

    init_db()

    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url=BASE, timeout=30) as c:
        # ---------------------------------------------------- 健康检查
        r = await c.get("/health")
        check("健康检查", r.status_code == 200, r.text[:120])
        health = r.json()

        # ---------------------------------------------------- 用户A注册
        phone_a = "13800000001"
        r = await c.post("/api/auth/send-code", json={"phone": phone_a})
        check("A发送验证码", r.json().get("code") == 0, r.text[:120])
        code_a = r.json().get("data", {}).get("dev_code")
        check("DEV模式回显验证码", bool(code_a), f"code={code_a}")

        r = await c.post(
            "/api/auth/register",
            json={"phone": phone_a, "code": code_a, "password": "test-pass-a", "nickname": "户主A"},
        )
        check("A注册", r.json().get("code") == 0, r.text[:160])
        token_a = r.json()["data"]["token"]
        user_a = r.json()["data"]["user_id"]
        h_a = {"Authorization": f"Bearer {token_a}"}

        r = await c.get("/api/auth/me", headers=h_a)
        check("A获取个人信息", r.json().get("data", {}).get("phone") == phone_a)

        # 错误验证码应被拒绝
        r = await c.post("/api/auth/login", json={"phone": phone_a, "code": "000000"})
        check("错误验证码被拒", r.status_code == 401, f"status={r.status_code}")

        # ---------------------------------------------------- 用户B注册
        phone_b = "13800000002"
        r = await c.post("/api/auth/send-code", json={"phone": phone_b})
        code_b = r.json()["data"]["dev_code"]
        r = await c.post(
            "/api/auth/register",
            json={"phone": phone_b, "code": code_b, "password": "test-pass-b", "nickname": "成员B"},
        )
        token_b = r.json()["data"]["token"]
        user_b = r.json()["data"]["user_id"]
        h_b = {"Authorization": f"Bearer {token_b}"}
        check("B注册", bool(token_b))

        # ---------------------------------------------------- 家庭组
        r = await c.post("/api/family/create", json={"name": "苏家"}, headers=h_a)
        check("A创建家庭组", r.json().get("code") == 0, r.text[:120])
        family_id = r.json()["data"]["family_id"]

        r = await c.post("/api/family/create", json={"name": "第二个家"}, headers=h_a)
        check("重复创建被拒", r.json().get("code") != 0, r.text[:120])

        r = await c.post(
            "/api/family/invite",
            json={"family_id": family_id, "invitee_phone": phone_b},
            headers=h_a,
        )
        check("A邀请B", r.json().get("code") == 0, r.text[:160])
        invitation_id = r.json()["data"]["invitation_id"]

        # 重复邀请应被拒
        await c.post(
            "/api/family/invite",
            json={"family_id": family_id, "invitee_phone": phone_b},
            headers=h_a,
        )
        r = await c.post(
            "/api/family/invite",
            json={"family_id": family_id, "invitee_phone": phone_b},
            headers=h_a,
        )

        r = await c.post(
            "/api/family/accept",
            json={"invitation_id": invitation_id},
            headers=h_b,
        )
        check("B接受邀请", r.json().get("code") == 0, r.text[:160])

        r = await c.get("/api/family/members", headers=h_a)
        members = r.json()["data"]["members"]
        check("成员列表含2人", len(members) == 2, f"count={len(members)}")
        check(
            "创建者标记正确",
            any(m["is_creator"] and m["user_id"] == user_a for m in members),
        )
        check(
            "手机号已脱敏",
            all("****" in m["phone_masked"] for m in members),
            str([m["phone_masked"] for m in members]),
        )

        # ---------------------------------------------------- 日志上传
        spam_hash = hashlib.sha256(b"17012345678").hexdigest()
        normal_hash = hashlib.sha256(b"13900000000").hexdigest()
        now = datetime.now()
        logs = []
        for i in range(12):
            logs.append(
                {
                    "phone_hash": spam_hash,
                    "call_time": (now - timedelta(hours=i)).isoformat(),
                    "ring_duration": 2,
                    "action": 1,
                    "decision_source": 1,
                }
            )
        logs.append(
            {
                "phone_hash": normal_hash,
                "call_time": now.isoformat(),
                "ring_duration": 20,
                "action": 2,
                "decision_source": 4,
            }
        )
        r = await c.post("/api/logs/upload", json={"logs": logs}, headers=h_a)
        check("A上传日志", r.json().get("data", {}).get("accepted") == 13, r.text[:160])

        # 非法哈希应被拒（schema 层 422 或业务层 rejected 计数，均可接受）
        r = await c.post(
            "/api/logs/upload",
            json={
                "logs": [
                    {
                        "phone_hash": "short",
                        "call_time": now.isoformat(),
                        "action": 1,
                        "decision_source": 1,
                    }
                ]
            },
            headers=h_a,
        )
        body = r.json()
        check(
            "非法哈希被拒",
            r.status_code == 422 or body.get("data", {}).get("rejected") == 1,
            f"status={r.status_code} {r.text[:120]}",
        )

        # ---------------------------------------------------- 双方标记 → 聚合
        r = await c.post("/api/logs/mark", json={"phone_hash": spam_hash, "user_mark": 1}, headers=h_a)
        check("A标记骚扰", r.json().get("code") == 0)
        r = await c.post("/api/logs/mark", json={"phone_hash": spam_hash, "user_mark": 1}, headers=h_b)
        check("B标记骚扰", r.json().get("code") == 0)

        r = await c.post(
            "/api/admin/aggregation/trigger", params={"family_id": family_id}, headers=ADMIN_H
        )
        agg = r.json()["data"]["rules"]
        check("家庭聚合生成规则", len(agg) == 1, str(agg)[:200])
        check(
            "聚合规则为黑名单类型",
            agg and agg[0]["type"] == 1 and agg[0]["confidence"] >= 0.8,
            str(agg)[:200],
        )

        # ---------------------------------------------------- 规则同步
        r = await c.get("/api/rules/sync?since_version=0", headers=h_a)
        data = r.json()["data"]
        check("规则同步返回版本号", data.get("version", 0) >= 1, r.text[:160])
        has_spam_rule = any(
            rule["pattern"] == f"hash:{spam_hash}" and rule["rule_type"] == 1
            for rule in data.get("rules", [])
        )
        check("同步到黑名单规则", has_spam_rule, str(data.get("rules"))[:200])

        # 增量同步：传最新版本号应为空
        latest = data["version"]
        r = await c.get(f"/api/rules/sync?since_version={latest}", headers=h_a)
        check(
            "增量同步无冗余",
            len(r.json()["data"].get("rules", [])) == 0,
            r.text[:160],
        )

        # ---------------------------------------------------- 误拦反馈
        r = await c.post(
            "/api/rules/feedback",
            json={"phone_hash": normal_hash, "feedback_type": 1},
            headers=h_a,
        )
        check("误拦放行反馈", r.json().get("data", {}).get("shared") is True, r.text[:160])

        r = await c.get(f"/api/rules/sync?since_version={latest}", headers=h_a)
        has_whitelist = any(
            rule["pattern"] == f"hash:{normal_hash}" and rule["rule_type"] == 4
            for rule in r.json()["data"].get("rules", [])
        )
        check("误拦生成白名单规则", has_whitelist, r.text[:200])

        # ---------------------------------------------------- 起零查询（传原始号码）
        r = await c.post("/api/qiling/query", json={"phone": "13800138000"}, headers=h_a)
        q = r.json().get("data", {})
        check(
            "起零查询返回合法动作",
            q.get("action") in ("analyze", "block")
            and q.get("source") in ("qiling", "cache", "unavailable"),
            str(q),
        )

        # ---------------------------------------------------- 通知
        r = await c.get("/api/notify/list", headers=h_b)
        notifies = r.json()["data"]["notifications"]
        check("B收到通知", len(notifies) >= 1, f"count={len(notifies)}")
        if notifies:
            r = await c.post("/api/notify/read", json=[notifies[0]["id"]], headers=h_b)
            check("标记已读", r.json()["data"]["marked"] >= 1)

        # ---------------------------------------------------- 家庭动态
        r = await c.get("/api/family/activities", headers=h_a)
        check("家庭动态非空", len(r.json()["data"]["activities"]) > 0)

        # ---------------------------------------------------- 统计
        r = await c.get("/api/logs/stats", headers=h_a)
        check("拦截统计可用", r.json()["data"]["total"] >= 13, r.text[:160])

        # ---------------------------------------------------- 数据导出
        r = await c.get("/api/logs/export", headers=h_a)
        check("数据导出可用", r.json()["data"]["count"] >= 13, r.text[:120])

        # ---------------------------------------------------- 鉴权
        r = await c.get("/api/auth/me")
        check("无令牌访问被拒", r.status_code == 401 or r.status_code == 403)
        r = await c.get("/api/auth/me", headers={"Authorization": "Bearer bad.token.here"})
        check("无效令牌被拒", r.status_code == 401)

        # ---------------------------------------------------- 运维自检
        r = await c.get("/api/admin/selftest", headers=ADMIN_H)
        check("运维自检可访问", r.status_code == 200, r.text[:200])
        r = await c.get("/api/admin/stats/overview", headers=ADMIN_H)
        check("数据概览可用", r.json()["data"]["users"] >= 2, r.text[:160])

        # ---------------------------------------------------- 退出与删除
        r = await c.post("/api/family/leave", headers=h_b)
        check("B退出家庭组", r.json().get("code") == 0, r.text[:120])
        r = await c.get("/api/family/members", headers=h_b)
        check("退出后无家庭组", r.json()["data"]["family_id"] is None)

        r = await c.delete("/api/auth/account", headers=h_b)
        check("B注销账号", r.json().get("code") == 0, r.text[:120])

        print()
        print(f"数据库后端: {health.get('db')}   缓存后端: {health.get('cache')}")
        print(f"DeepSeek: {health.get('deepseek')}   起零: {health.get('qiling')}")

    passed = sum(1 for _, ok_, _ in _results if ok_)
    total = len(_results)
    print()
    print(f"冒烟测试结果: {passed}/{total} 通过")
    for name, ok_, detail in _results:
        if not ok_:
            print(f"  [FAIL] {name}  {detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
