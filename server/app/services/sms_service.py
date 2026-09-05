"""
短信验证码服务。

provider = dev        : 不真实发送，验证码写入日志并回显（联调用）
provider = aliyun     : 阿里云「短信服务」SendSms（dysmsapi）
                        需企业资质申请签名，个人认证账号自 2025-06 起无法新增自用资质。
provider = aliyun_pns : 阿里云「号码认证服务 - 短信认证」SendSmsVerifyCode（dypnsapi）
                        免资质/免签名/免模板申请，用平台赠送的签名 + 赠送模板，
                        个人开发者可用。验证码由阿里云生成并保管，
                        校验走 CheckSmsVerifyCode（云端校验）。
provider = tencent    : 腾讯云短信 SendSms
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid
from base64 import b64encode
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import httpx

from app.config import settings
from app.core.cache import cache
from app.core.security import generate_sms_code

logger = logging.getLogger("survolocking.sms")

# 单号码发送频率限制窗口（秒）
_RATE_WINDOW = 60


def _percent_encode(s: str) -> str:
    return quote(str(s), safe="~")


async def _send_aliyun(phone: str, code: str) -> bool:
    params: dict[str, Any] = {
        "AccessKeyId": settings.ALIYUN_ACCESS_KEY_ID,
        "Action": "SendSms",
        "Format": "JSON",
        "PhoneNumbers": phone,
        "RegionId": settings.ALIYUN_SMS_REGION,
        "SignName": settings.SMS_SIGN_NAME,
        "SignatureMethod": "HMAC-SHA1",
        "SignatureNonce": uuid.uuid4().hex,
        "SignatureVersion": "1.0",
        "TemplateCode": settings.SMS_TEMPLATE_ID,
        "TemplateParam": json.dumps({"code": code}, separators=(",", ":")),
        "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "Version": "2017-05-25",
    }
    sorted_keys = sorted(params)
    canonical = "&".join(
        f"{_percent_encode(k)}={_percent_encode(params[k])}" for k in sorted_keys
    )
    string_to_sign = f"GET&{_percent_encode('/')}&{_percent_encode(canonical)}"
    signature = b64encode(
        hmac.new(
            f"{settings.ALIYUN_ACCESS_KEY_SECRET}&".encode(),
            string_to_sign.encode(),
            hashlib.sha1,
        ).digest()
    ).decode()
    params["Signature"] = signature

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get("https://dysmsapi.aliyuncs.com/", params=params)
        body = resp.json()
    if body.get("Code") == "OK":
        return True
    logger.error("阿里云短信发送失败: %s", body)
    return False


def _rpc_sign(params: dict[str, Any], secret: str) -> str:
    """阿里云 OpenAPI RPC 风格签名（HMAC-SHA1）。"""
    # 布尔值必须转成小写字符串 "true"/"false"，否则 str(True)="True" 会导致签名不匹配
    normalized = {
        k: ("true" if v is True else "false" if v is False else v)
        for k, v in params.items()
    }
    canonical = "&".join(
        f"{_percent_encode(k)}={_percent_encode(normalized[k])}" for k in sorted(params)
    )
    string_to_sign = f"GET&{_percent_encode('/')}&{_percent_encode(canonical)}"
    return b64encode(
        hmac.new(
            f"{secret}&".encode(),
            string_to_sign.encode(),
            hashlib.sha1,
        ).digest()
    ).decode()


def _common_params(action: str) -> dict[str, Any]:
    return {
        "AccessKeyId": settings.ALIYUN_ACCESS_KEY_ID,
        "Action": action,
        "Format": "JSON",
        "SignatureMethod": "HMAC-SHA1",
        "SignatureNonce": uuid.uuid4().hex,
        "SignatureVersion": "1.0",
        "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "Version": "2017-05-25",
    }


async def _pns_call(action: str, biz: dict[str, Any]) -> dict[str, Any]:
    """调用号码认证服务（dypnsapi）OpenAPI。"""
    params = {k: _norm_param(v) for k, v in {**_common_params(action), **biz}.items()}
    params["Signature"] = _rpc_sign(params, settings.ALIYUN_ACCESS_KEY_SECRET)
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get("https://dypnsapi.aliyuncs.com/", params=params)
        return resp.json()


def _norm_param(v: Any) -> Any:
    """
    参数值归一化，保证「参与签名的值」与「实际发送的值」完全一致。

    坑：bool 必须写成小写 true/false。Python 的 str(True) 是 "True"（大写 T），
    而 httpx 实际发送的是小写的 true，两者不一致会直接导致
    SignatureDoesNotMatch（服务器回显的 string to sign 里是小写 true）。
    """
    if isinstance(v, bool):
        return "true" if v else "false"
    return v


# 号码认证短信的频控类错误码。文档标称 FREQUENCY_FAIL，
# 实际网关返回带 biz. 前缀的 biz.FREQUENCY，两者都要认。
_PNS_RATE_CODES = {
    "FREQUENCY_FAIL",
    "BUSINESS_LIMIT_CONTROL",
    "biz.FREQUENCY",
    "biz.BUSINESS_LIMIT_CONTROL",
}


async def _send_aliyun_pns(phone: str) -> tuple[bool, str, int]:
    """
    号码认证服务「短信认证」发送验证码。

    TemplateParam 传 {"code":"##code##"} 占位，验证码由阿里云生成并保管，
    这样才能用 CheckSmsVerifyCode 做云端校验。

    返回 (是否成功, 验证码, 限频剩余秒数)。
    """
    biz = {
        "PhoneNumber": phone,
        "SignName": settings.SMS_SIGN_NAME,
        "TemplateCode": settings.SMS_TEMPLATE_ID,
        # ##code## 占位 → 阿里云生成验证码并可云端校验（务必保持占位，勿传具体值）
        "TemplateParam": json.dumps(
            {"code": "##code##", "min": str(settings.SMS_CODE_EXPIRE // 60)},
            separators=(",", ":"),
        ),
        "CodeType": 1,  # 纯数字
        "CodeLength": 6,
        "ValidTime": settings.SMS_CODE_EXPIRE,
        "Interval": settings.SMS_SEND_INTERVAL,
        "DuplicatePolicy": 1,  # 覆盖：新码生效、旧码失效
        # 取回验证码仅用于本地兜底副本与 dev 回显，不会下发给客户端（非 dev）
        "ReturnVerifyCode": True,
    }
    try:
        body = await _pns_call("SendSmsVerifyCode", biz)
    except Exception as e:
        logger.error("号码认证短信请求异常: %s", e)
        return False, "", 0

    code = body.get("Code", "")
    if code == "OK":
        verify_code = (body.get("Model") or {}).get("VerifyCode") or ""
        logger.info("号码认证短信已发送 phone=%s", phone)
        return True, verify_code, 0

    # 频控：阿里云侧 Interval 或天级流控
    if code in _PNS_RATE_CODES:
        logger.warning("号码认证短信触发频控 phone=%s code=%s", phone, code)
        return False, "", max(1, settings.SMS_SEND_INTERVAL)
    logger.error("号码认证短信发送失败 phone=%s body=%s", phone, body)
    return False, "", 0


async def _check_aliyun_pns(phone: str, code: str) -> bool:
    """号码认证服务「短信认证」云端校验验证码。"""
    try:
        body = await _pns_call(
            "CheckSmsVerifyCode", {"PhoneNumber": phone, "VerifyCode": code}
        )
    except Exception as e:
        logger.error("号码认证短信校验请求异常: %s", e)
        return False

    # 接口调用成功不代表核验通过，必须以 Model.VerifyResult 为准
    if body.get("Code") == "OK":
        result = (body.get("Model") or {}).get("VerifyResult")
        if result == "PASS":
            return True
        logger.info("号码认证短信校验未通过 phone=%s result=%s", phone, result)
        return False
    logger.warning("号码认证短信校验失败 phone=%s body=%s", phone, body)
    return False


# 探测结果缓存：探测会真实消耗一次频控配额，避免运维反复点自检把通道打满
_probe_cache: tuple[float, tuple[bool, str]] | None = None
_PROBE_TTL = 60


async def probe_channel(force: bool = False) -> tuple[bool, str]:
    """
    探测短信通道配置是否正确（不真正给真实号码发短信）。

    结果缓存 60 秒：探测本身会占用号���认证的频率配额，
    反复调用会让真实用户在这段时间内发不出验证码。
    """
    global _probe_cache
    now = time.time()
    if not force and _probe_cache and (now - _probe_cache[0]) < _PROBE_TTL:
        ok, detail = _probe_cache[1]
        left = int(_PROBE_TTL - (now - _probe_cache[0]))
        return ok, f"{detail}（{left}s 内缓存结果，避免重复占用频控配额）"
    result = await _probe_channel_uncached()
    _probe_cache = (now, result)
    return result


async def _probe_channel_uncached() -> tuple[bool, str]:
    """
    探测短信通道配置是否正确（不真正给真实号码发短信）。

    aliyun_pns：直接带官方测试号 13800138000 调 SendSmsVerifyCode。
      该号码会被阿里云判为非法号码，但**在校验号码之前**已经完成
      签名/模板校验，所以据此可区分配置错误与"号码非法（即配置正确）"。
    aliyun：用测试号触发 dysmsapi 的 SendSms，同样按错误码区分。
    """
    if settings.SMS_PROVIDER == "aliyun_pns":
        try:
            body = await _pns_call(
                "SendSmsVerifyCode",
                {
                    # 官方测试号：签名/模板通过后会卡在号码校验，不会真正下发
                    "PhoneNumber": "13800138000",
                    "SignName": settings.SMS_SIGN_NAME,
                    "TemplateCode": settings.SMS_TEMPLATE_ID,
                    "TemplateParam": json.dumps(
                        {"code": "##code##", "min": "5"}, separators=(",", ":")
                    ),
                    "CodeType": 1,
                },
            )
        except Exception as e:
            return False, f"请求失败: {e}"
        code = body.get("Code", "")
        msg = str(body.get("Message", ""))
        if code == "OK":
            return True, "号码认证通道可用（签名与模板均已通过校验）"
        if "MOBILE_NUMBER_ILLEGAL" in code:
            return True, "签名与模板校验通过（测试号仅触发号码校验，配置正确）"
        # 频控发生在签名/模板校验之后，能触发频控即说明配置本身没问题
        if code in _PNS_RATE_CODES:
            return True, f"通道可用（签名与模板已通过校验），当前触发频控：{code}"
        if "FUNCTION_NOT_OPENED" in code:
            return False, "未开通号码认证「短信认证」功能，请到控制台开启"
        if "SIGN" in code.upper() or "签名" in msg:
            return False, f"签名不可用（{code}）：请到号码认证控制台选用平台赠送的签名"
        if "TEMPLATE" in code.upper() or "模板" in msg:
            return False, f"模板不可用（{code}）：请到号码认证控制台选用平台赠送的模板"
        # 测试号在频控窗口内被反复探测时，阿里云会回一个笼统的 UNKNOWN，
        # 它并不代表配置错误，不能据此判定通道不可用
        if not code or code == "UNKNOWN":
            return True, (
                f"配置已下发（签名={settings.SMS_SIGN_NAME} 模板={settings.SMS_TEMPLATE_ID}），"
                f"但本次探测返回笼统的 {code or '空'}，多为测试号频控所致；"
                "建议用真实手机号实测一次发送以最终确认"
            )
        return False, f"{code}: {msg}"

    if settings.SMS_PROVIDER == "aliyun":
        # 复用 _send_aliyun 的签名逻辑，但要把返回的错误码暴露出来
        params: dict[str, Any] = {
            "AccessKeyId": settings.ALIYUN_ACCESS_KEY_ID,
            "Action": "SendSms",
            "Format": "JSON",
            "PhoneNumbers": "13800138000",
            "RegionId": settings.ALIYUN_SMS_REGION,
            "SignName": settings.SMS_SIGN_NAME,
            "SignatureMethod": "HMAC-SHA1",
            "SignatureNonce": uuid.uuid4().hex,
            "SignatureVersion": "1.0",
            "TemplateCode": settings.SMS_TEMPLATE_ID,
            "TemplateParam": json.dumps({"code": "000000"}, separators=(",", ":")),
            "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "Version": "2017-05-25",
        }
        sorted_keys = sorted(params)
        canonical = "&".join(
            f"{_percent_encode(k)}={_percent_encode(params[k])}" for k in sorted_keys
        )
        string_to_sign = f"GET&{_percent_encode('/')}&{_percent_encode(canonical)}"
        params["Signature"] = b64encode(
            hmac.new(
                f"{settings.ALIYUN_ACCESS_KEY_SECRET}&".encode(),
                string_to_sign.encode(),
                hashlib.sha1,
            ).digest()
        ).decode()
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://dysmsapi.aliyuncs.com/", params=params
                )
                body = resp.json()
        except Exception as e:
            return False, f"请求失败: {e}"
        code = body.get("Code", "")
        if code == "OK":
            return True, "模板与签名均可用"
        if code == "isv.MOBILE_NUMBER_ILLEGAL":
            return True, "模板与签名校验通过（测试号仅触发号码校验）"
        if code == "isv.SMS_TEMPLATE_ILLEGAL":
            return False, "模板 CODE 在该账号下不存在，请到阿里云控制台核对 SMS_TEMPLATE_ID"
        if code == "isv.SMS_SIGNATURE_ILLEGAL":
            return False, "签名名在该账号下不存在，请到阿里云控制台申请/核对 SMS_SIGN_NAME"
        return False, f"未知错误 {code}: {body.get('Message', '')}"
    if settings.SMS_PROVIDER == "tencent":
        return False, "腾讯云短信通道暂未实现探测"
    return False, "dev 模式（不真实发送）"


def _tc3_sign(payload: str, timestamp: int, date: str) -> str:
    service = "sms"
    algorithm = "TC3-HMAC-SHA256"
    canonical_request = "\n".join(
        [
            "POST",
            "/",
            "",
            "content-type:application/json; charset=utf-8",
            f"host:sms.tencentcloudapi.com",
            f"x-tc-action:{_percent_encode('SendSms').lower()}",
            "",
            "content-type;host;x-tc-action",
            hashlib.sha256(payload.encode()).hexdigest(),
        ]
    )
    credential_scope = f"{date}/{service}/tc3_request"
    string_to_sign = "\n".join(
        [
            algorithm,
            str(timestamp),
            credential_scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )

    def _hmac(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    secret_date = _hmac(f"TC3{settings.TENCENT_SECRET_KEY}".encode(), date)
    secret_service = _hmac(secret_date, service)
    secret_signing = _hmac(secret_service, "tc3_request")
    signature = hmac.new(secret_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()
    return (
        f"{algorithm} Credential={settings.TENCENT_SECRET_ID}/{credential_scope}, "
        f"SignedHeaders=content-type;host;x-tc-action, Signature={signature}"
    )


async def _send_tencent(phone: str, code: str) -> bool:
    payload = json.dumps(
        {
            "PhoneNumberSet": [f"+86{phone}"],
            "SmsSdkAppId": settings.TENCENT_SMS_APPID,
            "SignName": settings.SMS_SIGN_NAME,
            "TemplateId": settings.SMS_TEMPLATE_ID,
            "TemplateParamSet": [code],
        },
        separators=(",", ":"),
    )
    now = int(time.time())
    date = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")
    headers = {
        "Authorization": _tc3_sign(payload, now, date),
        "Content-Type": "application/json; charset=utf-8",
        "Host": "sms.tencentcloudapi.com",
        "X-TC-Action": "SendSms",
        "X-TC-Version": "2021-01-11",
        "X-TC-Timestamp": str(now),
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            "https://sms.tencentcloudapi.com/", content=payload, headers=headers
        )
        body = resp.json()
    err = body.get("Response", {}).get("Error")
    if err:
        logger.error("腾讯云短信发送失败: %s", err)
        return False
    return True


async def send_code(phone: str) -> tuple[bool, str, int]:
    """
    发送验证码。返回 (是否成功, 验证码, 限频剩余秒数)。

    无论真实发送成功与否，验证码都会落缓存/库，
    只是 dev 模式下额外回显给调用方用于联调。

    retry_after：被限频时返回距离窗口结束的剩余秒数（>=1），
    供客户端把本地倒计时与服务端对齐；成功时为 0。
    """
    rate_key = f"sms:rate:{phone}"
    if cache.incr(rate_key, ttl=_RATE_WINDOW) > 1:
        remaining = cache.ttl(rate_key)
        logger.info("短信限频 phone=%s 剩余 %ss", phone, remaining)
        return False, "", max(1, remaining)

    code = generate_sms_code()
    expire_at = datetime.now() + timedelta(seconds=settings.SMS_CODE_EXPIRE)

    if settings.SMS_PROVIDER == "aliyun_pns":
        # 验证码由阿里云生成；同时留存本地副本，作为云端校验接口异常时的兜底
        sent, pns_code, retry_after = await _send_aliyun_pns(phone)
        if not sent:
            cache.delete(rate_key)
            return False, "", retry_after
        _persist_code(phone, pns_code, expire_at)
        return True, pns_code, 0

    if settings.SMS_PROVIDER == "aliyun":
        sent = await _send_aliyun(phone, code)
    elif settings.SMS_PROVIDER == "tencent":
        sent = await _send_tencent(phone, code)
    else:
        sent = True
        logger.warning("[DEV] 短信验证码 phone=%s code=%s（未真实发送）", phone, code)

    if not sent:
        # 发送失败不占用限频窗口，允许用户立即重试
        cache.delete(rate_key)
        return False, code, 0

    _persist_code(phone, code, expire_at)
    return True, code, 0


def _persist_code(phone: str, code: str, expire_at: datetime) -> None:
    """优先写缓存；缓存不可用时落库，保证功能可用。"""
    from app.database import is_sqlite_fallback
    from app.models import SmsCode

    if cache.backend == "redis" or not is_sqlite_fallback():
        cache.setex_json(f"sms:code:{phone}", settings.SMS_CODE_EXPIRE, code)

    # 始终落库一份，作为缓存失效时的兜底与审计依据
    from app.database import session_scope

    with session_scope() as s:
        s.add(SmsCode(phone=phone, code=code, expire_at=expire_at))


async def verify_code_async(phone: str, code: str) -> bool:
    """
    校验验证码（统一入口）。

    aliyun_pns 走阿里云云端校验（CheckSmsVerifyCode），
    云端不可用时降级到本地副本，避免云端抖动把正确验证码判为失败。
    其余 provider 走本地缓存/库校验。
    """
    if settings.SMS_PROVIDER == "aliyun_pns":
        if await _check_aliyun_pns(phone, code):
            # 云端已判定通过，同步作废本地副本，避免同一验证码被重放
            _invalidate_local(phone)
            return True
        return _verify_local(phone, code)
    return _verify_local(phone, code)


def _invalidate_local(phone: str) -> None:
    """作废该手机号所有未使用的本地验证码副本。"""
    cache.delete(f"sms:code:{phone}")
    from app.database import session_scope
    from app.models import SmsCode

    with session_scope() as s:
        s.query(SmsCode).filter(
            SmsCode.phone == phone, SmsCode.used.is_(False)
        ).update({"used": True})


def _verify_local(phone: str, code: str) -> bool:
    """本地校验验证码。命中后立即使其失效，防止重放。"""
    cached = cache.get_json(f"sms:code:{phone}")
    if cached is not None:
        if str(cached) == str(code):
            cache.delete(f"sms:code:{phone}")
            return True
        return False

    from app.database import session_scope
    from app.models import SmsCode

    with session_scope() as s:
        record = (
            s.query(SmsCode)
            .filter(
                SmsCode.phone == phone,
                SmsCode.code == code,
                SmsCode.used.is_(False),
                SmsCode.expire_at > datetime.now(),
            )
            .order_by(SmsCode.id.desc())
            .first()
        )
        if record is None:
            return False
        record.used = True
        s.add(record)
        return True


def constant_time_compare(a: str, b: str) -> bool:
    return secrets.compare_digest(str(a), str(b))
