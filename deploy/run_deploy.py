"""一次性部署脚本：上传代码 -> 建库迁移 -> 构建镜像 -> 运行容器 -> 健康检查。
凭据通过环境变量传入（不在脚本中硬编码）。仅复用服务器已有 MySQL，不动 nginx/现有容器。
"""
import os, paramiko, sys, time

HOST = os.environ["HOST"]
SSHPW = os.environ["SSHPASS"]
MYSQLPW = os.environ["MYSQLPW"]
LOCAL_ENV = r"F:/WorkBuddyDefault/2026-09-02-04-13-26/survolocking/server/.env"
TARBALL = r"F:/WorkBuddyDefault/2026-09-02-04-13-26/survolocking/deploy/survolocking_deploy.tar.gz"
REMOTE = "/opt/survolocking"

def log(*a):
    print("[deploy]", *a, flush=True)

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
log("SSH 连接", HOST)
ssh.connect(HOST, username="root", password=SSHPW, timeout=20)
sftp = ssh.open_sftp()

# 先确保远端目录存在（sftp.put 不会自动创建父目录），并等待其完成
i, o, e = ssh.exec_command(f"mkdir -p {REMOTE}")
o.read()
log("上传部署包 ...")
sftp.put(TARBALL, f"{REMOTE}/deploy.tar.gz")
ssh.exec_command(f"cd {REMOTE} && tar xzf deploy.tar.gz && rm -f deploy.tar.gz && echo EXTRACTED && ls")

# 生成服务器 .env：复用工作区 .env，仅把 MySQL 主机改为 127.0.0.1（容器 --network host）
log("生成服务器 .env (MYSQL_HOST=127.0.0.1) ...")
with open(LOCAL_ENV, encoding="utf-8") as f:
    env_text = f.read()
env_text = env_text.replace("MYSQL_HOST=8.153.36.132", "MYSQL_HOST=127.0.0.1")
with sftp.open(f"{REMOTE}/server/.env", "wb") as f:
    f.write(env_text.encode("utf-8"))
log("server .env 已写入")

# MySQL：建库 + 迁移
log("创建数据库 + 执行迁移 ...")
cnf = "/tmp/my.cnf"
ssh.exec_command(f"cat > {cnf} <<'EOF'\n[client]\nuser=root\npassword={MYSQLPW}\nEOF\nchmod 600 {cnf}")
sql = (
    f"mysql --defaults-extra-file={cnf} -e 'CREATE DATABASE IF NOT EXISTS survolocking "
    f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;' && "
    f"mysql --defaults-extra-file={cnf} survolocking < {REMOTE}/deploy/migrations/001_init.sql && "
    # 账号体系新字段（密码登录 / 头像）：幂等，重复执行不报错
    f"mysql --defaults-extra-file={cnf} survolocking < {REMOTE}/deploy/migrations/002_add_account_fields.sql && "
    # 单设备登录会话字段 + 老用户昵称修补：幂等
    f"mysql --defaults-extra-file={cnf} survolocking < {REMOTE}/deploy/migrations/003_add_session_and_nickname.sql && "
    f"echo MIGRATION_OK"
)
i, o, e = ssh.exec_command(sql, timeout=90)
out, err = o.read().decode(), e.read().decode()
log("MYSQL OUT:", out.strip(), "| ERR:", err.strip()[:500])
ssh.exec_command(f"rm -f {cnf}")

# Docker 构建
log("构建 Docker 镜像（可能需要几分钟）...")
bc = f"cd {REMOTE} && docker build -f deploy/Dockerfile -t survolocking-app server/ 2>&1 | tail -25"
i, o, e = ssh.exec_command(bc, timeout=600)
log("BUILD OUT:", o.read().decode()[-2500:])
log("BUILD ERR:", e.read().decode()[-800:])

# 运行容器（host 网络：复用本机 MySQL 127.0.0.1:3306，应用暴露 0.0.0.0:8000）
log("启动容器 ...")
run = (
    f"docker rm -f survolocking-app 2>/dev/null; "
    f"docker run -d --network host --name survolocking-app --restart unless-stopped "
    f"-v {REMOTE}/server/.env:/app/.env survolocking-app && sleep 14 && "
    f"echo HEALTH: && curl -s http://127.0.0.1:8000/health; echo"
)
i, o, e = ssh.exec_command(run, timeout=150)
log("RUN/HEALTH:", o.read().decode(), e.read().decode())

log("容器日志（最近 40 行）:")
i, o, e = ssh.exec_command("docker logs --tail 40 survolocking-app 2>&1", timeout=30)
print(o.read().decode())

ssh.close()
log("部署脚本结束")
