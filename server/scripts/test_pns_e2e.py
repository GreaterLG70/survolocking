"""号码认证服务（aliyun_pns）短信认证端到端验证。

完整走一遍真实链路：
  1) SendSmsVerifyCode  → 真实手机号收到短信
  2) 手工输入收到的验证码
  3) CheckSmsVerifyCode → 云端校验是否 PASS

用法（在服务器上 / 本地隔离实例均可）：
    APP_ENV=dev python scripts/test_pns_e2e.py 13800138000

注意：发送会计费，且受 60 秒频控限制（同一号码两次发送需间隔 60s）。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.services import sms_service


async def main(phone: str) -> int:
    print("=" * 60)
    print("号码认证服务 - 短信认证 端到端验证")
    print("=" * 60)
    print(f"provider     : {settings.SMS_PROVIDER}")
    print(f"签名 SignName: {settings.SMS_SIGN_NAME}")
    print(f"模板 Template: {settings.SMS_TEMPLATE_ID}")
    print(f"目标手机号   : {phone}")
    print("-" * 60)

    if settings.SMS_PROVIDER != "aliyun_pns":
        print(f"[跳过] 当前 provider={settings.SMS_PROVIDER}，本脚本只验证 aliyun_pns")
        return 1

    # ---------------- 1. 发送验证码 ----------------
    print("[1/3] 调用 SendSmsVerifyCode 发送验证码 ...")
    sent, code, retry_after = await sms_service._send_aliyun_pns(phone)
    if not sent:
        if retry_after:
            print(f"[失败] 触发频控，请 {retry_after} 秒后重试")
        else:
            print("[失败] 发送失败，请查看上方日志中的响应体")
        return 1
    print(f"[成功] 短信已发送，云端生成的验证码 = {code}（仅本次调试可见）")

    # ---------------- 2. 等待用户确认收到 ----------------
    print("-" * 60)
    print("[2/3] 请检查手机是否收到短信")
    try:
        got = input("请输入手机上收到的验证码（直接回车则用云端返回值继续）: ").strip()
    except EOFError:
        got = ""
    if not got:
        got = code
        print(f"       使用云端返回值继续: {got}")

    # ---------------- 3. 云端校验 ----------------
    print("-" * 60)
    print("[3/3] 调用 CheckSmsVerifyCode 云端校验 ...")
    passed = await sms_service._check_aliyun_pns(phone, got)
    if passed:
        print("[成功] 云端校验通过（VerifyResult=PASS）")
    else:
        print("[失败] 云端校验未通过")

    # ---------------- 附加：错误验证码应被拒绝 ----------------
    print("-" * 60)
    wrong = "000000" if got != "000000" else "111111"
    rejected = await sms_service._check_aliyun_pns(phone, wrong)
    print(f"[附加] 用错误验证码 {wrong} 校验 → {'被接受（异常！）' if rejected else '被正确拒绝'}")

    print("=" * 60)
    if passed and not rejected:
        print("端到端验证结果：通过 ✅  短信认证链路完全打通")
        return 0
    print("端到端验证结果：未通过 ❌")
    return 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python scripts/test_pns_e2e.py <手机号>")
        sys.exit(1)
    sys.exit(asyncio.run(main(sys.argv[1])))
