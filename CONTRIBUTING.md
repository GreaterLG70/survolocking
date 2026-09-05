# 贡献指南

感谢你愿意为 Survolocking 贡献一份力。请在打开 PR 之前花几分钟读完这份文档。

## 1. 行为准则

- 我们对人友善，对代码严格。
- 评论聚焦在 issue 本身，不针对个人。
- 任何侮辱、歧视或骚扰行为都将被从仓库移除。

## 2. 提 issue / PR 之前

- **搜一下**：确认问题或改动没有被反复讨论过。
- **最小复现**：崩溃/异常请附带日志、机型、Android 版本。
- **PR 只解决一个事情**：避免巨型 PR。
- **不要把 `.env`、SSH 私钥、服务器密码、APK 文件**贴上来——`.gitignore` 会拦截，但 PR 描述里手贱粘也会泄漏。

## 3. 分支策略

```
main                  受保护主分支，仅通过 PR 合并
│
├── feature/<scope>   新功能，例如 feature/auth、feature/family-ui
├── fix/<scope>       修 bug，例如 fix/login-1004、fix/kicked-logout
├── chore/<scope>     杂项（脚本、文档、重构），例如 chore/salt-sync
├── docs/<scope>       纯文档
└── hotfix/<scope>    紧急修复，独立打 tag
```

- 永远 **从最新的 `main` 拉分支**：`git fetch origin && git checkout main && git pull && git checkout -b feature/...`。
- 推之前先 `git fetch origin main && git rebase origin/main`，避免冗余合并提交。
- `git push --force-with-lease` 是允许的，不要用 `--force`（除非你清楚地知道后果）。

## 4. 提交信息规范（Conventional Commits）

每条 commit message 形如：

```
<type>(<scope>): <subject>
// 空一行
<body 详细说明动机与权衡>
// 空一行
BREAKING CHANGE: <reason>

Refs #123
```

**type 一览**

| type | 含义 |
|---|---|
| `feat` | 新增功能 |
| `fix` | 修 bug |
| `perf` | 性能优化 |
| `refactor` | 重构（既不修 bug 也不加功能） |
| `docs` | 仅文档 |
| `style` | 排版 / 注释，无逻辑改动 |
| `test` | 仅测试 |
| `chore` | 构建脚本、CI、依赖等 |
| `revert` | 撤销某条 commit |

**scope 示例**：`auth` / `family` / `notifications` / `android-ui` / `server-api` / `ci` 等，没有固定值。

**subject 要求**：祈使句、50 字内、首字母不大写、句末不加句号。

**body 要求**：写"为什么"而不写"做了什么"，贴上问题背景、权衡、链接。可换行。

**BREAKING CHANGE**：改完会在 commit 末尾加一行 `BREAKING CHANGE: <原因>`。

**示例**

```
feat(auth): 加入 session_id 实现单设备互踢

参考 QQ 的体验，第二台设备登录后第一台要立即被踢出。
在 users 表新增 session_id 列，登录刷新，JWT 携带，
鉴权中间件比对不一致返回 401 + X-Kicked 头。

BREAKING CHANGE: 旧用户首次登录后会强制重新登录。
Refs #42
```

## 5. 检入前自测

- 服务器改动 → 至少跑过 `python server/scripts/smoke_test.py`
- Android 改动 → 至少跑过 `./gradlew.bat assembleDebug`
- 哈希/盐改动 → 必跑 `python server/scripts/test_hash_contract.py`
- 不再跑通就 **不要** 提 PR。

## 6. 合并规则

- PR title 必须符合上面的 Conventional Commits（自动 squash 时会被采纳为合并提交）。
- 至少 1 个 reviewer 通过后才能合并（单人项目可设置自我合并）。
- "squash and merge" 是默认选项，保留完整 commit 历史用 "rebase and merge"。

## 7. 发版与 tag

- 合并到 main 后用 `scripts/release.sh`（待补）打 tag：
  - 升级 `__init__.py` 与 `android/app/build.gradle.kts` 版本号 → `CHANGELOG.md` 整理版本段 → push tag → GitHub Release。
- 紧急热修复 → `hotfix/` 分支 → 合并 main → 单打 patch tag。
