"""单设备登录（互踢）+ 家庭邀请闭环 专项验证。

覆盖：
  1. 同一账号先后登录两次 → 第一次的令牌失效（401 + X-Kicked）
  2. 新令牌仍可正常访问
  3. 默认昵称不再为 null
  4. 邀请闭环：邀请 → 受邀方可列举 → 接受 → 成为成员

运行： APP_ENV=dev SMS_PROVIDER=dev MYSQL_PORT=1 python scripts/test_session_kick.py
"""
from __future__ import annotations

import asyncio
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from app.database import init_db
from app.main import app

PASS, FAIL = "PASS", "FAIL"
_results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    _results.append((name, cond, detail))
    print(f"[{PASS if cond else FAIL}] {name}" + (f"  -- {detail}" if detail else ""))


async def main() -> int:
    init_db()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        # 随机手机号：dev 库持久化，固定号会被上一次运行注册（1004），必须幂等
        suffix = random.randint(1000, 9999)
        phone = f"138{suffix:08d}"
        phone2 = f"139{suffix:08d}"

        async def send_code(p: str) -> str:
            r = await c.post("/api/auth/send-code", json={"phone": p})
            return r.json()["data"].get("dev_code", "")

        # ---------------- 1. 注册并拿到第一个令牌 ----------------
        code = await send_code(phone)
        r = await c.post(
            "/api/auth/register",
            json={"phone": phone, "code": code, "password": "kick-pass"},
        )
        d = r.json()["data"]
        token1 = d["token"]
        check("注册成功并下发令牌", bool(token1))
        check("默认昵称非 null", bool(d.get("nickname")), f"nickname={d.get('nickname')}")

        # 第一个令牌可正常访问
        r = await c.get("/api/auth/me", headers={"Authorization": f"Bearer {token1}"})
        check("令牌1 可访问 /me", r.status_code == 200)

        # ---------------- 2. 同一账号在另一台设备登录（用密码，避开短信限频） ----------------
        r = await c.post(
            "/api/auth/login", json={"phone": phone, "password": "kick-pass"}
        )
        body = r.json()
        token2 = (body.get("data") or {}).get("token", "")
        check("二次登录（密码）成功", bool(token2), body.get("message", ""))

        # ---------------- 3. 旧令牌应被踢 ----------------
        r = await c.get("/api/auth/me", headers={"Authorization": f"Bearer {token1}"})
        kicked = r.status_code == 401 and r.headers.get("X-Kicked") == "1"
        check(
            "令牌1 已被踢下线（401 + X-Kicked）",
            kicked,
            f"status={r.status_code} x-kicked={r.headers.get('X-Kicked')} detail={r.json().get('detail')}",
        )

        # ---------------- 4. 新令牌正常 ----------------
        r = await c.get("/api/auth/me", headers={"Authorization": f"Bearer {token2}"})
        check("令牌2 仍可正常访问", r.status_code == 200)

        # ---------------- 5. 邀请闭环 ----------------
        # A 建家庭
        r = await c.post(
            "/api/family/create",
            json={"name": "互踢测试家庭"},
            headers={"Authorization": f"Bearer {token2}"},
        )
        fid = r.json()["data"]["family_id"]
        check("创建家庭成功", bool(fid))

        # B 注册
        code_b = await send_code(phone2)
        r = await c.post(
            "/api/auth/register",
            json={"phone": phone2, "code": code_b, "password": "b-pass"},
        )
        token_b = r.json()["data"]["token"]
        check("B 注册成功", bool(token_b))

        # A 邀请 B（B 未注册时应报错，这里已注册故应成功）
        r = await c.post(
            "/api/family/invite",
            json={"family_id": fid, "invitee_phone": phone2},
            headers={"Authorization": f"Bearer {token2}"},
        )
        invited = r.json().get("code") == 0
        check("邀请已注册用户成功", invited, r.json().get("message", ""))

        # B 能列举到邀请（这是之前缺失的关键环节）
        r = await c.get(
            "/api/family/invitations", headers={"Authorization": f"Bearer {token_b}"}
        )
        invitations = r.json().get("data", {}).get("invitations", [])
        check("B 可列举收到的邀请", len(invitations) > 0, f"共 {len(invitations)} 条")

        if invitations:
            inv_id = invitations[0]["invitation_id"]
            r = await c.post(
                "/api/family/accept",
                json={"invitation_id": inv_id},
                headers={"Authorization": f"Bearer {token_b}"},
            )
            check("B 接受邀请成功", r.json().get("code") == 0, r.json().get("message", ""))

            r = await c.get(
                "/api/family/members", headers={"Authorization": f"Bearer {token2}"}
            )
            members = r.json().get("data", {}).get("members", [])
            check("家庭成员增至 2 人", len(members) == 2, f"实际 {len(members)} 人")

    print()
    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    print(f"互踢与邀请闭环测试结果: {passed}/{total} 通过")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
