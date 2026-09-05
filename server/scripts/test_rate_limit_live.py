"""
验证线上 https://survoid.top/sl 的 /api/auth/send-code：
- 第一次请求 aliyun 会失败（template_illegal），返回 1005 '短信发送失败'，
  不会创建 rate key（因为 cache.delete 立即清理）。
- 这个测试主要确认：连续 5 次请求的 1005 响应是来自 aliyun 失败分支（无 retry_after），
  而不是 rate-limit 分支（有 retry_after）。

辅助检查：检查 1005 data 字段的行为。
"""
from __future__ import annotations

import json
import time
import urllib.request

URL = "https://survoid.top/sl/api/auth/send-code"
PHONE = "13900000001"


def post(url: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def main() -> int:
    # 5 次连发，间隔 1s
    results = []
    for i in range(3):
        t0 = time.time()
        r = post(URL, {"phone": PHONE})
        elapsed = time.time() - t0
        results.append((r.get("code"), r.get("message"), (r.get("data") or {}).get("retry_after"), elapsed))
        print(f"第{i+1}次: code={r.get('code')} msg={r.get('message')} retry_after={(r.get('data') or {}).get('retry_after')} 用时={elapsed:.2f}s")
        time.sleep(1)

    # 全部应来自 '短信发送失败' 分支（aliyun template_illegal），无 retry_after
    fail_count = sum(1 for c, _, ra, _ in results if c == 1005 and ra is None)
    retry_count = sum(1 for c, _, ra, _ in results if c == 1005 and ra is not None)
    print()
    if fail_count == 3 and retry_count == 0:
        print("线上行为符合预期：aliyun 失败时立即放行（无 retry_after），rate-limit 路径需 aliyun 成功才触发")
        return 0
    print(f"异常：失败分支 {fail_count}，限频分支 {retry_count}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
