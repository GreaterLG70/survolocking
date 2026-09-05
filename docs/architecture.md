# 架构与数据流

> 该文档的目的是让新加入的开发者 **5 分钟内** 理解 Survolocking 的全貌。

## 总体原则

- **端侧优先** — 拦截决策必须在端侧 200ms 内完成，云端只做离线。
- **零明文上云** — 哈希契约：
  `phoneHash = HMAC-SHA256(activeSalt, normalize(phone))`
- **宁可漏接不可误拦** — 四层决策，任一层异常走"放行"。
- **隐私沙盒** — 端侧缓存通话日志 + 哈希规则；家庭通过哈希聚合，不传明文。

## 四层决策引擎（端侧）

| 层 | 名称 | 预算 | 网络 | 命中动作 |
|---|---|---|---|---|
| 1 | 本地硬规则 | 0-50 ms | ❌ | 通讯录 → 黑白名单 → 异常号段 → 云规则段 |
| 2 | 起零风险缓存 | 0-5 ms | ❌（决策时） | 缓存命中 → 放行/拦截；未命中 → 后台预热 |
| 3 | 本地微行为 | 0-30 ms | ❌ | 短响铃×3 → 强制放行；否则累计标记 |
| 4 | 兜底放行 | 0 ms | ❌ | 响铃；通话后引导标记 |

## 服务端模块（FastAPI）

```
┌─────────────────────────────────────────────────────────────┐
│                       Nginx (TLS / 反代)                    │
└────────────┬────────────────────────────────┬───────────────┘
             │                                │
     ┌───────▼───────┐                ┌───────▼───────┐
     │   API（uvicorn,1 worker）        │  Redis 缓存 │
     │  /auth /family /logs            │  (可降级)    │
     │  /rules /notify /qiling         └───────────────┘
     │  /admin                         ┌───────────────┐
     └────┬────────────────────────────┤  MySQL（按月 │
          │                            │   分区表）    │
   ┌──────▼───────────────────────┐    └───────────────┘
   │ APScheduler 每日凌晨         │
   │ • 家庭日志聚合 → 规则增量     │
   │ • DeepSeek 离线分析（可选）   │
   └───────────────────────────────┘
```

### 关键表

| 表 | 用途 |
|---|---|
| `users` | 手机号 + 密码哈希 + 昵称 + `session_id`（单端互踢） |
| `family_invitations` | `invitee_user_id` + `status`（0=待处理, 1=已接受, 2=已拒绝） |
| `call_logs`（按月分区）| 哈希化的拨出/拨入日志 |
| `family_member_rules` | 家庭聚合规则增量 |
| `system_rules` | `qiling` 下发的全网号段 |

### 单端互踢

```
[设备 A 登录]                [设备 B 登录]
  ↓                              ↓
  server.db.users.session_id = sidA
  tokenA.jwt.sid = sidA         server.db.users.session_id = sidB
                                tokenB.jwt.sid = sidB
[设备 A 的请求]
  tokenA → get_current_user()
    看到 sidA != users.session_id = sidB
    → HTTP 401, X-Kicked: 1
    → 客户端弹"账号被迫离线"并清登录态
```

## 三端哈希契约

参见根 README 的「端云哈希契约」。一图流：

```
                  activeSalt（从登录响应获取）
                           │
            ┌──────────────┼──────────────┐
            ▼              ▼              ▼
   服务端 hash_phone()  Android       iOS
   (security.py)       PhoneUtils.kt PhoneUtils.swift
            │              │              │
            └──── 必须 byte-identical ───┘
```

`server/scripts/test_hash_contract.py` 在每次改动后强制校验。

## 部署拓扑

```
                   Internet
                      │
                ┌─────▼─────┐
                │  Nginx    │  443 / TLS, ACME
                └─────┬─────┘
                      │ /sl/*
                ┌─────▼─────┐
                │  survolocking-app  (1 worker)
                │  FastAPI + APScheduler
                └─────┬─────┘
                ┌─────▼─────┐    ┌───────────┐
                │  MySQL 8  │    │  Redis    │
                └───────────┘    └───────────┘
```

## App 端架构（Android）

```
MainActivity (BottomNavigation)
├─ DataFragment           拦截规则 / 黑白名单
├─ MarkFragment            通话后标记
├─ ProfileFragment         账号信息 / 设置
└─ FamilyFragment          家庭组 / 邀请（7 秒热刷新）

CallStateMonitorService    前台服务 + START_STICKY + 自拉起
RuleDatabase (SQLite)      本地规则 + 通话日志
ApiClient (OkHttp)         集中异常 → KickedException
DecisionEngine             四层拦截
RuleSyncWorker             周期同步云规则
LogUploadWorker            周期上传日志
```
