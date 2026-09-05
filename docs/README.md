# GitHub Pages · 自动部署文档（默认 Document site）

GitHub Pages 会渲染 `docs/` 与根 `README.md`。
要在仓库 `Settings → Pages → Build and deployment → Source` 处选择 `GitHub Actions` 并启用。

启用后，任何合并到 `main` 的 README / docs 改动将自动在：
`https://<owner>.github.io/survolocking/` 同步可见。

Gitee Pages（如果想用）：
- Gitee 的 Pages 服务目前仅对 **企业版**免费用户开放；
- 个人账号请确保 Gitee 项目已设置为开源可见，然后再操作 `服务 → Gitee Pages → 启动`。

文件结构：

```
.
├── README.md              # 项目总览 → 同时作为 Pages 首页
├── SECURITY.md
├── CONTRIBUTING.md
├── CHANGELOG.md
├── docs/
│   ├── architecture.md    # 架构图 / 数据流
│   ├── api.md             # REST 接口
│   └── deploy.md          # 部署手册
└── .github/workflows/
    └── docs-ci.yml        # Pages 构建（可选）
```
