# Survolocking · 骚扰电话拦截系统

> 端侧优先、云端辅助的骚扰电话拦截方案。拦截决策在端侧 200ms 内完成，云端只做离线分析、规则下发、家庭聚合；原始电话号码**永不上云**。

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)
[![Server: Python 3.12+](https://img.shields.io/badge/Python-3.12+-blue.svg)](./server)
[![Android: API 24+](https://img.shields.io/badge/Android-API%2024+-3DDC84.svg)](./android)
[![iOS: 16+](https://img.shields.io/badge/iOS-16+-000000.svg)](./ios)
[![Code style: Conventional Commits](https://img.shields.io/badge/commit-Conventional-orange.svg)](./CONTRIBUTING.md)
[![CI: GitHub Actions](https://img.shields.io/badge/CI-GitHub%20Actions-2088FF.svg)](./.github/workflows)

[功能特性](#特性) · [架构](#架构) · [快速开始](#快速开始) · [部署](#部署) · [文档](#文档) · [贡献](#贡献) · [License](#license)

---

## 特性

- 🛡️ **四层决策引擎**：本地硬规则 → 起零风险缓存 → 本地微行为 → 兜底放行；任何层异常自动降级到放行。
- 🔒 **零明文上云**：服务端只存 `HMAC-SHA256` 哈希，盐由服务端下发给端侧，无中心化密钥泄露。
- 👨‍👩‍👧 **家庭共享规则**：聚合家庭成员的拒接行为，按"×N 次触发"自动生成家庭共享规则增量。
- 📲 **多端接入**：Android（Kotlin / BottomNavigation）、iOS（CallKit Extension）、Web（脚手架）。
- 🧱 **完整迁移链**：MySQL 按月分区 + 三个迁移脚本 001/002/003，可重复执行、幂等。
- 🚀 **38 项冒烟测试 + 哈希契约测试 + 会话互踢测试** 全部跑通。
- 🤖 **CI**：GitHub Actions 自动构建 APK 与冒烟测试。

---

## 一、架构（精简）

```
                        ┌───────────────────┐
                        │  Android / iOS    │
                        │  端侧决策 < 200ms  │
                        └────────┬──────────┘
                                 │ 哈希 / token
                                 ▼
   ┌──────────────────────────────────────────────────┐
   │           Nginx + FastAPI（uvicorn, 1 worker）   │
   │   /auth /family /logs /rules /notify /admin       │
   └─────────────┬─────────────────┬───────────────────┘
                 │                 │
        ┌────────▼─────┐    ┌──────▼─────┐
        │  MySQL 8     │    │  Redis 7    │
        │  (按月分区)  │    │  (可选)     │
        └─────────────┘    └────────────┘

   APScheduler 每日凌晨 ─► 家庭聚合 / 规则增量 / DeepSeek 离线分析
```

完整数据流、关键表、单端互踢时序图见 [`docs/architecture.md`](./docs/architecture.md)。

---

## 二、需要你提供的凭据

复制 `server/.env.example` 为 `server/.env` 并填表：

| # | 项 | `.env` 变量 | 必需 | 缺失时的行为 |
|---|---|---|---|---|
| 1 | **MySQL** | `MYSQL_HOST` `MYSQL_PORT` `MYSQL_USER` `MYSQL_PASSWORD` `MYSQL_DATABASE` | ✅ | dev 环境自动降级 SQLite |
| 2 | **DeepSeek** | `DEEPSEEK_API_KEY` `DEEPSEEK_BASE_URL` `DEEPSEEK_MODEL` | ✅ | 跳过离线分析 |
| 3 | **起零数据** | `QILING_TOKEN` `QILING_BASE_URL` | ✅ | 第2层降级到第3层 |
| 4 | 短信验证码 | `SMS_PROVIDER` + 阿里云 / 腾讯云 | ⬜ | `dev` 模式：日志回显 |
| 5 | 消息推送 | `PUSH_PROVIDER` + FCM/APNs 凭据 | ⬜ | `dev` 模式：落库轮询 |
| 6 | Redis | `REDIS_URL` | ⬜ | 改用进程内内存缓存 |
| 7 | 运维保护 | `ADMIN_TOKEN` | ⬜ | dev 环境允许空令牌 |

**另外两项必须自行生成（**不可留空，上线后不可更改**）：**

```bash
openssl rand -hex 32   # JWT_SECRET
openssl rand -hex 32   # PHONE_HASH_SALT
```

> ⚠️ `PHONE_HASH_SALT` 一旦上线**绝不可更改**，否则所有历史日志与已下发规则全部失配。
> 验证所有外部服务：`curl -H "X-Admin-Token: $ADMIN_TOKEN" $BASE/api/admin/selftest` 应全部 PASS。

---

## 三、端云哈希契约（三端必须一致）

```
phoneHash = HMAC-SHA256(activeSalt, normalize(phone))
normalize = 仅保留数字与前导 +（去掉空格、横线、括号、点号、字母等）
```

| 端 | 位置 |
|---|---|
| 服务端 | `server/app/core/security.py → hash_phone()` |
| Android | `android/.../engine/PhoneUtils.kt → PhoneUtils.activeSalt` |
| iOS | `ios/Survolocking/Engine/PhoneUtils.swift → PhoneUtils.salt` |

> 验证脚本：`python server/scripts/test_hash_contract.py` — 7/7 必过。

---

## 四、项目结构

```
survolocking/
├── server/                    云端服务（FastAPI + SQLAlchemy）
│   ├── app/
│   │   ├── api/               auth / family / logs / rules / notify / qiling / admin
│   │   ├── core/              security(哈希·JWT) / cache(Redis·内存) / deps(鉴权)
│   │   ├── services/          sms / push / qiling / deepseek / rule
│   │   ├── models.py          数据模型（MySQL 分区表）
│   │   ├── database.py        连接管理 + 按月分区维护
│   │   └── scheduler.py       每日凌晨离线分析
│   └── scripts/               smoke_test.py / test_hash_contract.py / test_session_kick.py
├── android/                   Android 客户端（Kotlin / BottomNavigation）
│   └── app/src/main/kotlin/com/survolocking/
│       ├── engine/            DecisionEngine(四层) / CallStateMonitorService
│       ├── data/              RuleDatabase(SQLite) / Constants
│       ├── net/               ApiClient（含 KickedException 与全局 onKicked 回调）
│       ├── worker/            RuleSyncWorker / LogUploadWorker
│       └── ui/                MainActivity / 各 Fragment / MarkActivity
├── ios/                       iOS 客户端骨架（Swift）
│   ├── Survolocking/          App / Engine / Data / UI
│   └── SurvolockingCallDirectory/   CallDirectory + Live Caller ID Lookup 扩展
├── deploy/                    Dockerfile / compose / Nginx / systemd / SQL 迁移
├── docs/                      architecture.md / api.md / deploy.md
├── scripts/                   check_android.py（Android 静态一致性校验）
├── .github/workflows/         android-ci.yml / server-ci.yml
├── CHANGELOG.md
├── CONTRIBUTING.md
├── SECURITY.md
└── LICENSE                     MIT
```

---

## 五、快速开始

### 5.1 云端

```bash
cd server
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt     # Windows
# Linux/macOS:  python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env          # 填入凭据（至少填 PHONE_HASH_SALT / JWT_SECRET）
python scripts/smoke_test.py  # 38 项端到端冒烟测试
uvicorn app.main:app --reload # 启动，文档在 /docs（Swagger UI）
```

### 5.2 Android

```bash
cd android
echo "apiBase=https://survoid.top/sl" >> gradle.properties    # 配置服务端基址
./gradlew assembleDebug                                       # → app/build/outputs/apk/debug/app-debug.apk
```

### 5.3 iOS

> 需 macOS + Xcode（仓库仅提供骨架）。详见 `docs/architecture.md`。

---

## 六、部署

### 6.1 本地 docker compose

```bash
cd deploy
docker compose up -d
docker compose logs -f app
```

### 6.2 线上

```bash
cd deploy
python run_deploy.py
```

> 默认会执行 001/002/003 三次迁移、构建镜像、健康检查全绿后启动容器。

### 6.3 关键约束

- **uvicorn 必须为 1 worker**：APScheduler 在进程内，多 worker 会导致每日分析重复执行。
- **PHONE_HASH_SALT 永不可轮换**。
- **main 分支受保护**，只接受 PR；常规流程见 [CONTRIBUTING.md](./CONTRIBUTING.md)。

---

## 七、API 速查

完整接口清单（业务码 / 错误码 / 鉴权 / 演示 cURL）见 [`docs/api.md`](./docs/api.md)。

需要了解的主要入口：

| 入口 | 路径 |
|---|---|
| 验证码登录 | `POST /api/auth/verify-code/check` |
| 密码登录 | `POST /api/auth/login` |
| 我的家庭成员 | `GET  /api/family/members` |
| 我收到的邀请 | `GET  /api/family/invitations` |
| 接受邀请 | `POST /api/family/accept` |
| 自检 | `GET  /api/admin/selftest` |

---

## 八、文档

| 文档 | 内容 |
|---|---|
| [`docs/architecture.md`](./docs/architecture.md) | 数据流、关键表、单端互踢时序 |
| [`docs/api.md`](./docs/api.md) | REST 接口清单 + cURL 示例 |
| [`docs/deploy.md`](./docs/deploy.md) | 本地 / 线上部署全过程 |
| [CHANGELOG.md](./CHANGELOG.md) | 历史变更（Keep a Changelog） |
| [CONTRIBUTING.md](./CONTRIBUTING.md) | 分支策略、Conventional Commits |
| [SECURITY.md](./SECURITY.md) | 漏洞报告流程 |

---

## 九、贡献

接受 PR，分支策略：

- `main` — 受保护，仅 PR 合并
- `feature/<scope>` / `fix/<scope>` / `chore/<scope>` — 通用
- `hotfix/<scope>` — 紧急修复

提交信息遵循 [Conventional Commits 1.0](https://www.conventionalcommits.org/zh-hans/)。
示例：

```
feat(auth): 加入 session_id 实现单设备互踢
fix(family): 修复受邀方无法看到邀请列表
chore(deps): 升级 fastapi 0.115
```

完整规范见 [CONTRIBUTING.md](./CONTRIBUTING.md)。

---

## 十、License

[MIT](./LICENSE) © 2026 牧歌 &lt;g2315562507@163.com&gt;

---

> ⚠️ **提醒**：本仓库**绝不包含**任何 `.env`、私钥、服务器密码。  
> `.gitignore` 已默认屏蔽 `**/.env`、`*.pem`、`*.key`、`deploy/survolocking_deploy.tar.gz` 等。  
> 提交前请务必 `git status` 确认改动列表里没有这些文件。
