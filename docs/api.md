# API 速查

> 服务地址：生产 `https://survoid.top/sl`，本地 `http://127.0.0.1:8000`

所有接口约定 `Content-Type: application/json`，所有业务响应：

```json
{ "code": 0, "message": "ok", "data": { ... } }
```

业务码：

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1001 | 手机号格式错 |
| 1002 | 密码错 |
| 1003 | 验证码错 / 过期 / 频率过快 |
| 1004 | 该手机号已注册（当前走 /login 隐式注册，已不再抛） |
| 1005 | 短信发送失败 |
| 1006 | 账号被踢（响应头 `X-Kicked: 1`，401） |
| 1101 | 鉴权过期/无效（401） |
| 1201 | 资源不存在 |
| 1300 | 服务降级中 |
| 1500 | 服务端异常 |

## Auth

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| POST | `/api/auth/register` | — | 老接口，新策略下不再使用 |
| POST | `/api/auth/login` | — | 手机号 + 密码（**密码登录 Tab**用此） |
| POST | `/api/auth/verify-code/send` | — | 发送短信验证码（dev 模式打印到日志） |
| POST | `/api/auth/verify-code/check` | — | 校验验证码；新号隐式注册，老号直登（**验证码登录 Tab**） |
| POST | `/api/auth/logout` | ✅ | 主动下线 |
| GET  | `/api/auth/me` | ✅ | 个人信息，含昵称、手机号尾4、activeSalt |

## Family

| 方法 | 路径 | 说明 |
|---|---|---|
| GET  | `/api/family/members` | 我的家庭成员 |
| GET  | `/api/family/invitations` | **我收到的待处理邀请** |
| POST | `/api/family/invite` | 邀请其他用户 |
| POST | `/api/family/accept` | 接受邀请（`invitation_id`） |
| POST | `/api/family/reject` | 拒绝邀请 |
| POST | `/api/family/leave` | 退出家庭 |

## Rules

| 方法 | 路径 | 说明 |
|---|---|---|
| GET  | `/api/rules/list` | 获取当前生效的家庭规则 + 全网号段 |
| POST | `/api/rules/blacklist` | 上报黑名单条目（手机号 → 哈希） |

## Logs

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/logs/upload` | 批量上传通话哈希日志 |
| GET  | `/api/logs/export` | 导出我账号下所有日志（合规需求） |

## Notify

| 方法 | 路径 | 说明 |
|---|---|---|
| GET  | `/api/notify/poll` | 推送降级回查（每 30s） |
| POST | `/api/notify/read/{id}` | 标记已读 |

## Admin

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| GET  | `/api/admin/selftest` | `X-Admin-Token` | 全服务自检 |
| GET  | `/health` | — | 容器健康 |

## 互动示例

```bash
# 1. 发送验证码（dev 模式写日志）
curl -s -X POST $BASE/api/auth/verify-code/send \
  -H "Content-Type: application/json" \
  -d '{"phone":"13800139000","captcha":"abcd","dev_code":"0000"}'

# 2. 校验验证码（dev 模式 dev_code 即为正确）
curl -s -X POST $BASE/api/auth/verify-code/check \
  -H "Content-Type: application/json" \
  -d '{"phone":"13800139000","code":"0000","scene":"login"}'

# 3. 拿到 token
TOKEN=$(curl -s ... | jq -r .data.token)

# 4. 拉个人信息
curl -s $BASE/api/auth/me -H "Authorization: Bearer $TOKEN"

# 5. 邀请 13159986312 加入家庭
curl -s -X POST $BASE/api/family/invite \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"phone":"13159986312"}'
```
