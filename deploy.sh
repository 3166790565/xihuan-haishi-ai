#!/usr/bin/env bash
#
# 一键部署「喜欢还是爱」。
#
# 在服务器上把源码放到任意目录（例如 /opt/lovequiz），然后：
#     ./deploy.sh
# 脚本会问你要后台口令和对外端口，写进 .env，构建镜像并启动容器。
#
# 非交互用法（便于自动化 / 重跑）：
#     ./deploy.sh --password '强口令' --port 8000 --yes
#
# 重复执行是安全的：已有 .env 会作为默认值，容器会被就地重建，
# /data 卷里的配置与限流计数不受影响。
#
set -euo pipefail

cd "$(dirname "$0")"

ENV_FILE=".env"
DEFAULT_PORT=8000
MIN_PW_LEN=8

# ── 输出 ─────────────────────────────────────────────────────────────────────
if [ -t 1 ]; then
  C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_ERR=$'\033[31m'; C_DIM=$'\033[2m'; C_END=$'\033[0m'
else
  C_OK=""; C_WARN=""; C_ERR=""; C_DIM=""; C_END=""
fi
info() { printf '%s==>%s %s\n' "$C_DIM" "$C_END" "$*"; }
ok()   { printf '%s ok %s %s\n' "$C_OK" "$C_END" "$*"; }
warn() { printf '%s警告%s %s\n' "$C_WARN" "$C_END" "$*" >&2; }
die()  { printf '%s错误%s %s\n' "$C_ERR" "$C_END" "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
用法: ./deploy.sh [选项]

  --password <口令>   后台登录口令（不给则交互询问，至少 8 位）
  --port <端口>       宿主机对外端口（默认 8000）
  -y, --yes           非交互模式，缺失项用默认值/自动生成
  --no-build          跳过镜像构建，直接启动已有镜像
  -h, --help          显示本帮助

说明:
  · 配置写入当前目录的 .env，容器内监听端口固定为 8000，宿主机端口由 --port 决定。
  · ADMIN_PASSWORD 只是「初始口令」：/data 里已有口令时不会被覆盖。
    要真正改口令，需删除数据卷后重新部署（会丢失 SMTP 配置与限流计数）。
EOF
}

# ── 读取已有 .env 的单个键（不 source，避免值里的特殊字符被执行）─────────────
read_env() {
  [ -f "$ENV_FILE" ] || return 0
  sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1 | sed -e "s/^'//" -e "s/'\$//"
}

# ── 参数 ─────────────────────────────────────────────────────────────────────
ADMIN_PASSWORD=""
HOST_PORT=""
ASSUME_YES=0
NO_BUILD=0

while [ $# -gt 0 ]; do
  case "$1" in
    --password)
      [ $# -ge 2 ] || die "--password 需要一个值"
      ADMIN_PASSWORD="$2"; shift 2 ;;
    --port)
      [ $# -ge 2 ] || die "--port 需要一个值"
      HOST_PORT="$2"; shift 2 ;;
    -y|--yes) ASSUME_YES=1; shift ;;
    --no-build) NO_BUILD=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "未知参数：$1（用 --help 查看用法）" ;;
  esac
done

# ── 前置检查 ─────────────────────────────────────────────────────────────────
info "检查 Docker 环境"
command -v docker >/dev/null 2>&1 || die "找不到 docker，请先安装 Docker。"
docker info >/dev/null 2>&1 || die "Docker 守护进程没在跑（试试 systemctl start docker）。"

if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  die "找不到 Docker Compose（需要 compose v2 插件或 docker-compose 命令）。"
fi
ok "Docker $(docker --version | sed 's/^Docker version //; s/,.*//')"

[ -f docker-compose.yml ] || die "当前目录没有 docker-compose.yml，请把源码放全再运行。"
[ -f Dockerfile ] || die "当前目录没有 Dockerfile，请把源码放全再运行。"

# ── 收集配置 ─────────────────────────────────────────────────────────────────
OLD_PASSWORD="$(read_env ADMIN_PASSWORD)"
OLD_SECRET="$(read_env SECRET_KEY)"
OLD_PORT="$(read_env HOST_PORT)"

if [ -z "$HOST_PORT" ]; then
  HOST_PORT="${OLD_PORT:-$DEFAULT_PORT}"
  if [ "$ASSUME_YES" -eq 0 ]; then
    printf '对外端口 [%s]: ' "$HOST_PORT"
    read -r input_port || true
    [ -n "${input_port:-}" ] && HOST_PORT="$input_port"
  fi
fi
case "$HOST_PORT" in
  ''|*[!0-9]*) die "端口必须是数字：$HOST_PORT" ;;
esac
[ "$HOST_PORT" -ge 1 ] && [ "$HOST_PORT" -le 65535 ] || die "端口超出范围：$HOST_PORT"

if [ -z "$ADMIN_PASSWORD" ]; then
  input_pw=""
  input_pw2=""
  if [ "$ASSUME_YES" -eq 1 ]; then
    ADMIN_PASSWORD="$OLD_PASSWORD"
    [ -n "$ADMIN_PASSWORD" ] || die "非交互模式下必须提供 --password。"
  elif [ -n "$OLD_PASSWORD" ]; then
    printf '后台口令（直接回车沿用 .env 里已有的）: '
    read -rs input_pw || true
    printf '\n'
    if [ -n "$input_pw" ]; then
      ADMIN_PASSWORD="$input_pw"
    else
      ADMIN_PASSWORD="$OLD_PASSWORD"
    fi
  else
    printf '设置后台登录口令（至少 %s 位，输入时不回显）: ' "$MIN_PW_LEN"
    read -rs input_pw || true
    printf '\n'
    printf '再输一次: '
    read -rs input_pw2 || true
    printf '\n'
    [ "$input_pw" = "$input_pw2" ] || die "两次输入不一致。"
    ADMIN_PASSWORD="$input_pw"
  fi
fi

[ -n "$ADMIN_PASSWORD" ] || die "后台口令不能为空。"
[ "${#ADMIN_PASSWORD}" -ge "$MIN_PW_LEN" ] || die "口令至少 $MIN_PW_LEN 位（现在是 ${#ADMIN_PASSWORD} 位）。"
# .env 由 docker compose 解析，单引号是值的定界符；含单引号会让整行解析错位。
case "$ADMIN_PASSWORD" in
  *"'"*) die "口令里不要包含单引号（会破坏 .env 解析），换一个。" ;;
esac

if [ -n "$OLD_SECRET" ]; then
  SECRET_KEY="$OLD_SECRET"
  info "沿用已有 SECRET_KEY（重启后后台会话不会失效）"
else
  if command -v openssl >/dev/null 2>&1; then
    SECRET_KEY="$(openssl rand -hex 32)"
  else
    SECRET_KEY="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  fi
  info "已生成新的 SECRET_KEY"
fi

# 端口占用检查：容器启动前挡住，比事后看 docker 报错更好读。
if command -v ss >/dev/null 2>&1; then
  if ss -ltnH 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${HOST_PORT}\$"; then
    die "宿主机端口 $HOST_PORT 已被占用，换一个（用 --port 指定）。"
  fi
fi

# ── 写 .env ──────────────────────────────────────────────────────────────────
umask 077
cat > "$ENV_FILE" <<EOF
# 由 deploy.sh 生成。含后台口令，不要提交进仓库。
ADMIN_PASSWORD='${ADMIN_PASSWORD}'
SECRET_KEY='${SECRET_KEY}'
HOST_PORT='${HOST_PORT}'
EOF
chmod 600 "$ENV_FILE"
ok "配置已写入 $ENV_FILE（权限 600）"

if [ -n "$OLD_PASSWORD" ] && [ "$OLD_PASSWORD" != "$ADMIN_PASSWORD" ]; then
  warn "你改了口令，但 ADMIN_PASSWORD 只是「初始口令」："
  warn "  /data 卷里已有口令时它不会生效，容器仍用旧口令登录后台。"
  warn "  确需更换请先 docker compose down -v（会清空 SMTP 配置与限流计数）。"
fi

# ── 构建并启动 ───────────────────────────────────────────────────────────────
if [ "$NO_BUILD" -eq 1 ]; then
  info "跳过构建，直接启动已有镜像"
  "${COMPOSE[@]}" up -d --no-build
else
  info "构建镜像并启动（首次约 1-3 分钟，取决于网速）"
  "${COMPOSE[@]}" up -d --build
fi

# ── 健康检查 ─────────────────────────────────────────────────────────────────
info "等待服务就绪"
ready=0
for _ in $(seq 1 30); do
  if command -v curl >/dev/null 2>&1; then
    if curl -fsS "http://127.0.0.1:${HOST_PORT}/healthz" >/dev/null 2>&1; then ready=1; break; fi
  elif command -v wget >/dev/null 2>&1; then
    if wget -qO- "http://127.0.0.1:${HOST_PORT}/healthz" >/dev/null 2>&1; then ready=1; break; fi
  else
    # 没有 curl/wget 就用 python（镜像里就跑 python，宿主一般也有）
    if python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:${HOST_PORT}/healthz',timeout=2).status==200 else 1)" >/dev/null 2>&1; then
      ready=1; break
    fi
  fi
  sleep 2
done

echo
if [ "$ready" -eq 1 ]; then
  ok "服务已就绪"
else
  warn "健康检查超时，但容器可能仍在启动。用下面的命令看日志："
  warn "  ${COMPOSE[*]} logs -f lovequiz"
fi

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -n "$IP" ] || IP="<服务器IP>"

cat <<EOF

  前台页面    http://${IP}:${HOST_PORT}/
  后台管理    http://${IP}:${HOST_PORT}/admin
  本机自检    curl -s http://127.0.0.1:${HOST_PORT}/healthz

  后台口令    你在本次部署时设置的那个
  后续操作    ${COMPOSE[*]} logs -f lovequiz      # 看日志
              ${COMPOSE[*]} restart lovequiz      # 重启
              ${COMPOSE[*]} down                  # 停止（保留数据卷）

  首次登录后台后，请在「SMTP 发信配置」里填服务器、账号、授权码，
  再点「发一封测试邮件」验证通过，前台才会出现发信按钮。

EOF
