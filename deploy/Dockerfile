# ---------- 构建阶段 ----------
FROM python:3.12-slim AS builder

WORKDIR /build

COPY requirements.txt .
# 阿里云 PyPI 镜像：服务器在阿里云内网环境，直连 pypi.org 易超时
RUN pip install --no-cache-dir --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/ \
    && pip install --no-cache-dir -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# ---------- 运行阶段 ----------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY . /app

# 非 root 运行
RUN useradd -m -u 10001 survolocking \
    && mkdir -p /app/data && chown -R survolocking:survolocking /app
USER survolocking

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"

# 单 worker 即可：定时任务内嵌在应用进程中，多 worker 会导致任务重复执行
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
