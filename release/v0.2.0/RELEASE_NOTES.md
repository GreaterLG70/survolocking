# Survolocking v0.2.0

> 第二次正式发版。修复了 v0.1.0 暴露的一批 Android 端交互/状态问题，新增家庭邀请闭环、保活三板斧、品牌图标，并把仓库治理规范化（MIT、Codecov、Coding Flow、CI、文档站）。

## ⚠️ 升级提醒

- 老的安卓端**清缓存后再装新包**，避免旧的图形验证码登录状态回滚到 1004。
- 服务端这次不含数据库迁移，默认能向后兼容。如要开启"互踢"功能，请执行 `deploy/migrations/003_add_session_and_nickname.sql`。

## 📦 发布资产

| 文件 | 大小 | SHA256 |
| --- | ---: | --- |
| `survolocking-v0.2.0-debug.apk` | 8.48 MB | `cf1022b74806b3976830af26f1beea3bc28752f99c05d8fa812566e8f2ead967` |
| `survolocking-0.2.0-source.tar.gz` | 0.76 MB | `27914881cd69095fcde0aaaf368834c46c52c2f2f70818b9e3e0be6d794e011c` |

## ✨ 修复（Fixes）

- **登录重做** — 密码登录 Tab 移除图形验证码；验证码登录 Tab 移除"可选密码"框，根治 1004 *该手机号已注册* 死局。
- **双端互踢（QQ 式）** — 服务端为每个 token 打 `session_id`，新登录覆盖旧会话；旧设备收到 `401 + X-Kicked`，客户端弹 *账号被迫离线* 并强制回登录页。
- **个人中心改版** — 小黑盒风格卡片；昵称为 `null` 时服务端兜底为 `用户+手机尾号`，本地兜底"守护者"。
- **家庭邀请闭环** — 新增 `GET /api/family/invitations` 列出收到的邀请，受邀方现在能看见并接受。
- **家庭页卡片化** — 收到邀请、家庭信息、成员列表、创建/接受/拒绝/退出分组摆放。
- **黑白名单可点开** — 点击卡片后弹 `AlertDialog` 看明细。

## 🆕 新增（Features）

- **家庭页 7 秒轮询** — 打开窗口时每 7 秒刷新一次，离开页面自动停止。
- **后台保活三板斧** — `START_STICKY` + 任务移除后 `AlarmManager` 2 秒自拉起 + `MainActivity.onResume` 兜底重启。
- **状态栏常驻图标** — 5 档密度 (`mdpi`/`hdpi`/`xhdpi`/`xxhdpi`/`xxxhdpi`) + adaptive；通知全程带品牌色 `#0F766E`。
- **品牌 launcher 图标** — adaptive 矢量 (`#0F766E` 背景) + 圆形蒙版。
- **服务端迁移 003** — 新增 `session_id` 列、修补老用户昵称。
- **仓库规范化** — MIT、Conventional Commits、Coding Flow、CI（`android-ci.yml` / `server-ci.yml`）、文档站、Issue/PR 模板、CODEOWNERS。

## 🔐 API 不兼容

| 接口 | 行为 |
| --- | --- |
| `POST /api/auth/login` | 响应 JWT 中含 `sid`，重复登录会触发旧设备 `X-Kicked`。 |
| `GET /api/family/invitations` | 新接口，返回当前用户收到的邀请。 |
| `PATCH /api/users/me` | 昵称为空时服务端兜底，不会出现"修改失败"。 |

## 👥 贡献者

- 牧歌 <g2315562507@163.com>

## 🔗 链接

- 文档站：[`docs/architecture.md`](docs/architecture.md) / [`docs/api.md`](docs/api.md) / [`docs/deploy.md`](docs/deploy.md)
- 健康检查：<https://survoid.top/sl/health>（liveness/readiness 探针，仅返回 JSON 状态，非 Web 体验入口。实际使用请扫码安装上面的 APK。）
- 完整变更日志：[`CHANGELOG.md`](CHANGELOG.md)

---

按 MIT 协议发布 · Made with ❤️ by 牧歌
