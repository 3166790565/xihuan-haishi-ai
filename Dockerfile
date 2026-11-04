# syntax=docker/dockerfile:1
# 单容器：FastAPI 同时托管前端静态页 + 发信 API + 后台 WebUI。
# 不需要 nginx（静态文件由 FastAPI StaticFiles 提供），少一层容器与配置面。
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    DATA_DIR=/data

WORKDIR /app

# 先只拷依赖清单，让 pip 层在代码变动时命中缓存。
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 再拷代码与前端资源（web/ 是前端唯一真源）。
COPY app/ ./app/
COPY web/ ./web/

# 配置与限流计数落挂载卷，容器重建不丢、重启不清零。
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000

# Debian slim 没有 curl 也没有 wget；用 python 自己探活，否则 HEALTHCHECK 永远 unhealthy。
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os,urllib.request,sys; \
url='http://127.0.0.1:%s/healthz'%os.environ.get('PORT','8000'); \
sys.exit(0 if urllib.request.urlopen(url,timeout=3).status==200 else 1)"

# 用 sh -c 以便展开 ${PORT}（PaaS 常注入 $PORT；本地不设则默认 8000）。
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]