# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目

「喜欢还是爱」：9 题是/否自测，按斯滕伯格爱情三角理论（亲密 / 激情 / 承诺）判定对某个具体的人是「喜欢」还是「爱」。单容器 FastAPI，前端为纯静态文件（无构建、无 npm），无数据库，配置与限流计数落 `DATA_DIR` 下的 JSON。

## 命令

Windows 上跑 Python 一律带 `PYTHONUTF8=1`，否则中文源码/输出可能乱码。

```bash
# 一键部署（Docker，交互问口令与端口，写 .env 后构建启动）
./deploy.sh                       # 或 ./deploy.sh --password '...' --port 8000 --yes

# 本地启动（约 8000 端口）
PYTHONUTF8=1 python -m uvicorn app.main:app --port 8000
# 前台页面 http://127.0.0.1:8000/ ，后台 http://127.0.0.1:8000/admin

# 判定回归（全量：设计断言 + 机械字段 + 512 穷举 + 拒绝用例，需要 node）
PYTHONUTF8=1 python tools/test_scoring.py
# 只要 Python 侧断言，不启 node
PYTHONUTF8=1 python tools/test_scoring.py --quick

# 真实浏览器 E2E（需先自行启动应用与带 --remote-debugging-port 的 Chrome，并 pip install websockets）
python tools/e2e_browser.py --app http://127.0.0.1:8011 --cdp http://127.0.0.1:9333

# 容器
docker compose up -d --build      # docker-compose.yml 强制要求 ADMIN_PASSWORD
docker build -t lovequiz . && docker run -d -p 8000:8000 -v lovequiz-data:/data \
  -e ADMIN_PASSWORD='口令' -e SECRET_KEY='随机串' lovequiz
```

依赖只有 `fastapi` + `uvicorn`（见 `requirements.txt`）；发信用标准库 `smtplib`，测试额外用本机 `node` 作 JS 运行器（不装包）。

## 架构

三层单向依赖，业务文案与判定规则严格分离：

```
web/data/questions.js      ← 题目 + rules + originals（唯一真源）
web/data/interpretations.js← 全部中文文案（唯一真源）
        ↓
web/scoring.js             ← 纯函数判定（浏览器侧）
app/scoring.py             ← 同一规则的独立 Python 实现（发信前重算）
        ↓
web/app.js                 ← 只做渲染/交互，零判定、零业务文案
app/main.py                ← FastAPI 路由 + StaticFiles
app/store.py               ← settings.json / 会话 token / 三维限流
app/mailer.py              ← smtplib 发信 + 邮件正文模板
app/content.py             ← 把 .js 数据文件当 JSON 读
```

数据流：前端算结果只为即时展示；`POST /api/mail/send` 只收 `{answers, alias, to}`，服务端用 `app/scoring.py` **重算**后才生成邮件，不信任客户端结论文本。

两份判定实现（`web/scoring.js` / `app/scoring.py`）必须逐字段一致，`tools/test_scoring.py` 用第三个独立 oracle + 全 512 种答案向量三方对撞。**改判定规则必须同时改两处**，且不要照抄另一份——两处独立推导才能让对撞有意义。

`typeKey` 映射下标是 `I*4 + P*2 + C`（`scoring.py:25`、`scoring.js:21`）：下标 1 是「只有承诺」，下标 4 才是「只有亲密」。`sprout` 是九题制特有的三维各 1 分中间态，不属八分法，`verdict=mixed` 且 `keyQuestionIds` 返回空（否则九题全中，失去意义）。

## 硬约束（改动前必读）

1. **数据文件必须是合法 JSON**。`web/data/*.js` 形如 `window.QUIZ_DATA = { ... };`，`app/content.py` 用正则剥前缀后 `json.loads`。key 必须带引号、对象内不能有注释、不能有尾逗号。注释只能写在赋值语句之前的块注释里。
2. **改文案后递增缓存版本号**：`web/index.html` 里 `interpretations.js?v=1` 的 `?v=` 要 +1，`/data/*.js` 是 `max-age=300` 短缓存。
3. **母题保护**：每题 `role` 只能是 `anchor` / `paraphrase`，每个维度恰好一道 anchor。改 anchor 的 `text` 必须与 `originals` 逐字一致（忽略空白），否则要在 `normalizedFrom` 写清理由；`web/validate.js` 在页面加载时校验，失败会直接显示错误。
4. **路由声明顺序**：`/{path:path}` SPA 兜底必须是 `app/main.py` 最后一个路由，否则吞掉 `/api/*` 与 `/admin`。`/static` 挂载与 `/data/{name}` 也各有缓存头约定，见 `app/main.py:65-97`。
5. **配置出读只有一条路**：`store.public_view()`。`store.get_runtime_config()` 含 SMTP 明文密码，只能服务端自用。密码字段的三种语义（缺省/`"****"` 保留、`""`/`__clear__` 清空、其它为新值）在 `store.save_settings()` 与 `web/admin.html` 各有一份，改一处要同步。
6. **`mail_ready()` 与 `mailer.send_result_mail()` 的守卫必须完全一致**（host + from_addr 必需，密码仅在配了 smtp_user 时必需），否则会出现「状态显示就绪、发信说未配置」。无认证内网中继是合法配置。
7. **限流放行即计数**：`store.allow_send()` 通过检查就落盘占额度，发送失败也算，防止用失败刷探测。改这里不要改成「成功后再记」。
8. **`ADMIN_PASSWORD` 只是初始口令**：已存在 `admin_pw_hash` 时不再覆盖；未设置时 `/admin` 与 `/api/admin/*` 整体 503，不设默认口令。`SECRET_KEY` 未设置则进程启动时随机生成，重启后会话全失效。
9. **邮件正文模板固定、不含任何外部链接**，alias/邮箱一律转义（`_esc` / `_plain_alias`）——这是一个匿名可用的代发通道，不要把它做成钓鱼载体。
10. **前端零持久化**：`web/app.js` 不得使用 localStorage/sessionStorage/cookie/分析脚本/外部资源，答案只存在内存。

## 环境变量

| 变量 | 作用 | 缺省 |
|---|---|---|
| `ADMIN_PASSWORD` | 后台初始口令 | 未设置且无已存 hash → 后台禁用 |
| `SECRET_KEY` | 会话签名密钥 | 每次启动随机生成 |
| `DATA_DIR` | `settings.json` / `rate_state.json` 落盘目录 | `<项目根>/data` |
| `PORT` | 容器内监听端口 | `8000` |
| `MAIL_GLOBAL_MIN_INTERVAL_SEC` | 全站发信最小间隔（秒） | `2` |

## 已知不一致

`web/admin.html:128-129` 读取 `rate.today_total / daily_limit / ip_daily_limit / to_daily_limit`，而 `app/store.py:543` 的 `rate_snapshot()` 返回的是 `total_used / total_limit / ip_limit / to_limit`，限流状态那行目前会显示 `undefined`。修的时候两边对齐即可，不要只改一侧。

## Docker 注意

镜像只 `COPY app/ web/ requirements.txt`（见 `Dockerfile`）；`tools/`、`tests/` 不进镜像（`.dockerignore`）。因此容器内不具备判定回归能力，回归只能在开发机跑。配置与限流计数必须在 `/data` 挂载卷，否则重建即丢。
