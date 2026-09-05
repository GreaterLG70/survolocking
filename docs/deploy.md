# 部署手册

## 一、选型

| 角色 | 选择 | 替代方案 |
|---|---|---|
| API | `uvicorn app.main:app --workers 1` | gunicorn |
| 缓存 | Redis 6+ | 内存缓存（降级） |
| 数据库 | MySQL 8.0+ | SQLite（dev） |
| 反代 | Nginx | Caddy |
| 短信 | 阿里云号码认证（dypnsapi SendSmsVerifyCode） | 腾讯云 / dev 模式 |
| 推送 | 极光 / FCM | dev 模式落库轮询 |

> ⚠️ **uvicorn 必须为 1 worker**：APScheduler 在进程内，多 worker 会重复执行每日任务。

## 二、本地起一套

### 1) Python 服务

```bash
cd server
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
cp .env.example .env
# 至少填：PHONE_HASH_SALT（32 字节 hex）、JWT_SECRET、MYSQL_*（可选，省略则 SQLite）
python scripts/smoke_test.py          # 38 项端到端
uvicorn app.main:app --reload         # 默认 8000
```

### 2) Android 客户端

```bash
cd android
# 设置 API 基址（默认 https://survoid.top/sl）
echo "apiBase=https://survoid.top/sl" >> gradle.properties
./gradlew assembleDebug               # → app/build/outputs/apk/debug/app-debug.apk
```

## 三、生产部署

### A. 一键部署（推荐）

```bash
# 1. 把整包传到服务器
scp deploy/survolocking_deploy.tar.gz root@<server>:/tmp/

# 2. 服务器上解压 + docker compose
ssh root@<server> <<'EOF'
set -e
cd /opt
tar -xzf /tmp/survolocking_deploy.tar.gz survolocking
cd survolocking/deploy
docker compose up -d --build
docker compose logs -f app
EOF
```

### B. 手工部署

1. MySQL：建库 `CREATE DATABASE survolocking DEFAULT CHARSET utf8mb4`；执行 `deploy/migrations/*.sql` 与 `server/migrations/*.sql`。
2. Redis：`docker run -d --name redis -p 6379:6379 redis:7-alpine`
3. API：

   ```bash
   cd /opt/survolocking/server
   python -m venv .venv && .venv/bin/pip install -r requirements.txt
   cp .env.example .env && vi .env
   cp ../deploy/survolocking.service /etc/systemd/system/
   systemctl enable --now survolocking
   ```

4. Nginx：把 `deploy/nginx/survolocking.conf` 软链到 `/etc/nginx/sites-enabled/` 后 `nginx -s reload`。

### C. 域名与证书

```bash
# certbot 自动续签
apt install -y certbot python3-certbot-nginx
certbot --nginx -d survoid.top
```

## 四、迁移链

```
001_init.sql                   # 建 users / family / logs
002_add_account_fields.sql     # password_hash / nickname / avatar
003_add_session_and_nickname.sql  # session_id 列 + 老用户昵称兜底
```

每次部署由 `deploy/run_deploy.py` 串行执行。

## 五、监控与告警

- `GET /health` → 容器探针 200
- `GET /api/admin/selftest` → 周期性跑一次；任一字段 fail 即报警
- 日志 → `docker compose logs -f app` + 直送 ELK/腾讯云 CLS（看你方便）

## 六、回滚策略

```bash
# 回滚到上个 tag
git checkout v0.1.0
cd deploy && docker compose up -d --build
```

数据迁移原则：**只加不减不重命名**。如确需重命名，按「双写 → 切读 → 删旧」三步走，每步间隔 ≥ 1 个发布周期。
