# 部署到云

本应用是**单个容器、无数据库、无状态**（配置与限流计数在挂载卷里）。任何支持容器的平台都能跑。

## 1. 准备两个密钥

```bash
# 后台口令（自己定）
export ADMIN_PASSWORD='换成你的强口令'

# 会话签名密钥（随机生成一次，之后固定使用；不固定则每次重启后后台要重新登录）
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

`SECRET_KEY` 泄露不等于口令泄露，但能让别人伪造后台会话，别提交进仓库。

## 2. 在云端构建并推送镜像

本机 Docker daemon 未启动、WSL 无发行版，所以镜像构建放在云端做。任选其一：

### 方案 A：平台直接构建（最省事）
把仓库推到 GitHub / GitLab，用平台的「从 Dockerfile 构建」功能（Railway / Render / Fly.io / 阿里云 ACR 构建机 / 腾讯云 Coding 等），构建上下文由平台拉取。

### 方案 B：云端 CI 构建后推镜像仓库
在 GitHub Actions 或云厂商 CI 里：

```yaml
# 示例：GitHub Actions 片段
- run: docker build --platform linux/amd64 -t $REGISTRY/lovequiz:$GITHUB_SHA .
- run: docker push $REGISTRY/lovequiz:$GITHUB_SHA
```

显式加 `--platform linux/amd64`，避免在 ARM 本机 build 出云端跑不了的镜像。

## 3. 部署时必配

| 项 | 值 |
|---|---|
| 端口 | 容器内 `8000`（若平台注入 `$PORT`，应用会自动跟随） |
| 环境变量 | `ADMIN_PASSWORD`、`SECRET_KEY` |
| 持久卷 | 挂到 `/data`（**必须**，否则重启后 SMTP 配置和限流计数全丢） |
| 健康检查 | `GET /healthz` |

云厂商示例：

```bash
# Fly.io
fly launch --dockerfile Dockerfile
fly volumes create lovequiz_data --size 1
fly secrets set ADMIN_PASSWORD='...' SECRET_KEY='...'
fly deploy

# Railway / Render：在面板里设环境变量 + Volume 挂载 /data 即可
```

## 4. 上云后的检查清单

```bash
curl -s https://你的域名/healthz                      # {"ok":true,...}
curl -s -o /dev/null -w "%{http_code}\n" https://你的域名/          # 200
curl -s https://你的域名/data/questions.js | head -c 80            # 应返回 JS
```

然后在浏览器里：

1. 走完 9 题，确认结果页三类图表都正常。
2. `/admin` 登录 → 配 SMTP → 发测试邮件 → 真的收到。
3. 用手机再走一遍（移动端是主场景）。

## 5. 网络与 HTTPS

- HTTPS 由平台 / CDN 终止，应用只监听 http。前端一律用相对路径，不硬编码协议。
- 反向代理后面要传 `X-Forwarded-For`，限流按它取真实客户端 IP；应用只信任第一跳。
- 若挂了 CDN：`/` 与 `/admin` 设 `no-store`（应用已带该头），但**边缘节点也可能缓存**，改版后手动刷新一次。`/data/*.js` 是 5 分钟短缓存 + 改文案时递增 `?v=`。

## 6. 成本与规模

无数据库、无需 GPU（转写是本地一次性工具，不进镜像）。容器内存占用很小，最便宜的 512MB 实例够用。发信量受每日上限约束（默认全站 200 封/天，可在 `app/store.py` 的 `DEFAULTS` 里调）。

## 7. 回滚

镜像是不可变产物，回滚就是部署上一个 tag：

```bash
docker pull $REGISTRY/lovequiz:<上一个版本>
```

配置在 `/data/settings.json`，回滚应用不会动它；若配置改坏了，删除该文件即回到未配置状态（后台会显示 mail_ready=false，不会崩）。
