"""
1005 限频 + retry_after 专项校验。

模拟同一号码连续触发 send-code，断言：
  1. 第一次返回 code=0
  2. 第二次返回 code=1005
  3. 1005 响应中 data.retry_after 在 1..60 之间
  4. 第三次仍 1005，但 retry_after 已下降（验证 TTL 没被重置）
  5. 60s 后窗口自然结束（不实际等 60s，而是用更短窗口重测）

运行： APP_ENV=dev SMS_PROVIDER=dev python scripts/test_rate_limit.py
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from app.config import settings
from app.core import cache as cache_module
from app.main import app

PASS, FAIL = "PASS", "FAIL"
_results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    _results.append((name, cond, detail))
    print(f"[{PASS if cond else FAIL}] {name}" + (f"  -- {detail}" if detail else ""))


async def main() -> int:
    from app.database import init_db
    init_db()

    # 缩短 rate window 便于测试：直接修改模块常量
    from app.services import sms_service

    original_window = sms_service._RATE_WINDOW
    sms_service._RATE_WINDOW = 5
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://rl.test"
        ) as client:
            phone = "13900000001"

            # 清掉之前可能留下的限频键
            cache_module.cache.delete(f"sms:rate:{phone}")

            r1 = (await client.post(
                "/api/auth/send-code", json={"phone": phone}
            )).json()
            check("第一次 200", r1.get("code") == 0, str(r1))

            r2 = (await client.post(
                "/api/auth/send-code", json={"phone": phone}
            )).json()
            check("第二次 1005", r2.get("code") == 1005, str(r2))
            ra2 = (r2.get("data") or {}).get("retry_after", 0)
            check("retry_after 在 1..5 之间", 1 <= ra2 <= 5, f"retry_after={ra2}")

            # 立即第三次：TTL 应当已经减少（验证不会被重置）
            time.sleep(1.2)
            r3 = (await client.post(
                "/api/auth/send-code", json={"phone": phone}
            )).json()
            check("第三次仍 1005", r3.get("code") == 1005, str(r3))
            ra3 = (r3.get("data") or {}).get("retry_after", 0)
            check(
                f"retry_after 下降（{ra2} → {ra3}）",
                ra3 < ra2,
                f"验证 TTL 未被 incr 重置",
            )

            # 等窗口结束，再发应可成功
            time.sleep(ra3 + 1.5)
            r4 = (await client.post(
                "/api/auth/send-code", json={"phone": phone}
            )).json()
            check("窗口结束后 200", r4.get("code") == 0, str(r4))
    finally:
        sms_service._RATE_WINDOW = original_window

    print()
    if all(ok for _, ok, _ in _results):
        print(f"限频测试通过：{len(_results)}/{len(_results)}")
        return 0
    print(f"限频测试失败：{sum(1 for _, ok, _ in _results if not ok)}/{len(_results)}")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
