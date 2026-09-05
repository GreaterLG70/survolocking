"""本地自测：账号密码登录 + 头像建库全链路（仅连本地 SQLite，隔离线上）。"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8011"


def call(method: str, path: str, body=None, token=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


def show(tag, status, resp):
    print(f"[{tag}] HTTP {status} | code={resp.get('code')} msg={resp.get('message')}")
    if "data" in resp:
        d = resp["data"]
        print("    data:", json.dumps(d, ensure_ascii=False)[:300])


print("=== health ===")
show("health", *call("GET", "/health"))

# 1) 发送验证码（dev 模式回显）
s, send = call("POST", "/api/auth/send-code", {"phone": "13800000000"})
show("send-code", s, send)
code = send.get("data", {}).get("dev_code")

# 2) 注册（带密码 + 昵称）
s, reg = call("POST", "/api/auth/register",
              {"phone": "13800000000", "code": code, "password": "abc123", "nickname": "测试用户"})
show("register", s, reg)
token = reg.get("data", {}).get("token")

# 3) 密码登录
s, login = call("POST", "/api/auth/login", {"phone": "13800000000", "password": "abc123"})
show("login(pwd)", s, login)

# 4) 错误密码应失败
s, bad = call("POST", "/api/auth/login", {"phone": "13800000000", "password": "wrong11"})
show("login(wrong)", s, bad)

# 5) me（应含 nickname/avatar）
s, me = call("GET", "/api/auth/me", token=token)
show("me", s, me)

# 6) 更新资料（昵称 + 头像）
s, prof = call("PUT", "/api/auth/profile",
               {"nickname": "新昵称", "avatar": "https://survoid.top/avatar.png"}, token=token)
show("profile", s, prof)

# 7) me 验证 avatar 已更新
s, me2 = call("GET", "/api/auth/me", token=token)
show("me2", s, me2)

# 8) 验证码登录兼容（新号）
s, send2 = call("POST", "/api/auth/send-code", {"phone": "13900000000"})
code2 = send2.get("data", {}).get("dev_code")
s, reg2 = call("POST", "/api/auth/register",
               {"phone": "13900000000", "code": code2, "password": "pwd123", "nickname": "用户B"})
show("register2(is_new)", s, reg2)
s, login2 = call("POST", "/api/auth/login", {"phone": "13900000000", "code": code2})
show("login(code)", s, login2)

print("=== 自测结束 ===")
