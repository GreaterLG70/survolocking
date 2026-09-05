# 更新日志

本项目所有值得关注的变更都会记录在此文件。
格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
并遵循 [语义化版本](https://semver.org/lang/zh-CN/) 规范。

## [未发布]

### 新增
- 仓库规范化：MIT License、Conventional Commits、Coding Flow、CI、文档站。

## [0.2.0] - 2026-09-05

### 修复
- 登录页重做：密码登录 Tab 移除图形验证码，验证码登录 Tab 移除可选密码框（根治 1004 "该手机号已注册"死局）。
- 双端登录互踢（参考 QQ）：`session_id` 字段 + JWT sid 装载 + `X-Kicked` 响应头，客户端弹"账号被迫离线"并强制回登录页。
- 个人中心改版：小黑盒风格卡片，昵称 null 服务端兜底为"用户+手机尾号"。
- 家庭邀请闭环：新增 `GET /api/family/invitations` 列出收到的邀请，受邀方现在能看到并接受。
- 家庭页卡片化：收到邀请卡片 + 家庭信息卡 + 成员列表 + 创建/接受/拒绝/退出操作。
- 黑白名单可点击查看明细。
- 后台保活：`START_STICKY` + 任务移除后 AlarmManager 自拉起 + onResume 兜底重启。
- 服务端迁移 003：新增 `session_id` 列、修补老用户昵称。

### 新增
- 家庭页 7 秒轮询热刷新。
- 状态栏常驻图标（5 档密度 + 自适应 adaptive 图标）。
- 品牌 launcher 图标（自适应 + 圆形蒙版）。

## [0.1.0] - 2026-09-02

### 新增
- FastAPI 云端骨架：注册/登录、家庭、日志、规则、推送、起零、DeepSeek、管理员。
- MySQL 按月分区 + Redis 缓存（可降级到内存）。
- Android 客户端骨架：4 层拦截引擎（本地硬规则、起零缓存、微行为、兜底放行）。
- iOS 客户端骨架：Call Directory Extension + Live Caller ID Lookup。
- Docker + Nginx + systemd 部署模板。
- 38 项冒烟测试 + 哈希契约测试 + 鉴权流测试。

[未发布]: https://github.com/GreaterLG70/survolocking/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/GreaterLG70/survolocking/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/GreaterLG70/survolocking/releases/tag/v0.1.0
