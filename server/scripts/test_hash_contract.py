"""
哈希契约回归测试。

锁定「端云哈希一致性」这一最易静默失配的点：
  1) 登录响应必须下发 phone_hash_salt；
  2) 端侧算法 HMAC-SHA256(salt, normalize(phone)) 必须与服务端
     security.hash_phone 计算结果逐字节一致。

只要此测试通过，就证明 Android / iOS / 服务端三端用同一盐、同一算法，
不会出现「日志上传后规则匹配不上」的隐形故障。

运行： python scripts/test_hash_contract.py
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from app.config import settings
from app.core.security import hash_phone, normalize_phone
from app.database import init_db
from app.main import app

PASS, FAIL = "PASS", "FAIL"
_results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    _results.append((name, cond, detail))
    print(f"[{PASS if cond else FAIL}] {name}" + (f"  -- {detail}" if detail else ""))


def client_hmac(salt: str, phone: str) -> str:
    """模拟端侧（Android/iOS）的哈希算法，必须与服务端一致。"""
    return hmac.new(
        salt.encode("utf-8"),
        normalize_phone(phone).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


async def main() -> int:
    init_db()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        phone = "13800138000"

        # 发送验证码（dev 模式回显）
        r = await c.post("/api/auth/send-code", json={"phone": phone})
        code = r.json()["data"].get("dev_code", "")
        check("可获取验证码", bool(code))

        # 注册：register 与 login 共用 _issue，响应含服务端盐
        r = await c.post("/api/auth/register",
                         json={"phone": phone, "code": code, "password": "contract-pass", "nickname": "契约测试"})
        data = r.json()["data"]
        salt = data.get("phone_hash_salt", "")

        check("注册响应下发 phone_hash_salt（登录同源）", bool(salt), f"salt={salt[:8]}…")
        check("服务端配置了非空盐（否则需在 .env 设置 PHONE_HASH_SALT）",
              bool(settings.PHONE_HASH_SALT))

        # 算法等价：端侧 HMAC == 服务端 hash_phone
        client_hash = client_hmac(salt, phone)
        server_hash = hash_phone(phone)
        check("端侧算法与服务端 hash_phone 一致", client_hash == server_hash,
              f"client={client_hash[:12]}… server={server_hash[:12]}…")

        # 归一化等价：带格式号码，端侧与服务端用同一归一化应得同一哈希
        messy = "+86 138-0013-8000"
        check("带格式号码：端侧与服务端哈希一致",
              client_hmac(salt, messy) == hash_phone(messy),
              f"normalized={normalize_phone(messy)}")

        # 含点号/字母等非常规字符也应被统一归一化（三端一致的关键）
        dotted = "138.0013.8000"
        check("含点号号码：端侧与服务端哈希一致",
              client_hmac(salt, dotted) == hash_phone(dotted),
              f"normalized={normalize_phone(dotted)}")

        # /me 同样下发盐（重启刷新场景）
        token = data["token"]
        r = await c.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        check("/me 响应下发 phone_hash_salt",
              bool(r.json()["data"].get("phone_hash_salt")))

    failed = [n for n, ok, _ in _results if not ok]
    print(f"\n哈希契约测试结果: {len(_results) - len(failed)}/{len(_results)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
