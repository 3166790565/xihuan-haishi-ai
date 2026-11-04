# 喜欢还是爱

一套 9 题的是/否自测，判断你对**某一个具体的人**是「喜欢」还是「爱」。
理论依据：罗伯特·斯滕伯格的爱情三角理论（亲密 / 激情 / 承诺）。
解读口径取自一段 9 分 26 秒的中文口播，已转写校对后写入 `web/data/interpretations.js`。

## 快速开始（本地）

不需要 Docker，直接跑：

```bash
cd "E:/PythonProject/斯坦伯格喜欢or爱"
PYTHONUTF8=1 python -m uvicorn app.main:app --port 8000
```

打开 http://127.0.0.1:8000/ 做题，http://127.0.0.1:8000/admin 进后台。

后端依赖：`fastapi` `uvicorn`（其余为传递依赖）。邮件走 Python 标准库 `smtplib`。

## 环境变量

| 变量 | 作用 | 不设置时 |
|---|---|---|
| `ADMIN_PASSWORD` | 后台首次登录口令 | **后台整体禁用**，`/admin` 与 `/api/admin/*` 返回 503 |
| `SECRET_KEY` | 会话签名密钥 | 每次启动随机生成 → 重启后需重新登录后台 |
| `DATA_DIR` | 配置与限流计数落盘目录 | `项目根/data` |
| `PORT` | 容器内监听端口 | `8000` |
| `MAIL_GLOBAL_MIN_INTERVAL_SEC` | 全站发信最小间隔（秒） | `2` |

## 后台配置发信邮箱

1. 设置 `ADMIN_PASSWORD` 后启动，访问 `/admin` 登录。
2. 填 SMTP 服务器、端口、加密方式、账号、密码（授权码）、发件地址。
3. 点「发一封测试邮件」，填你自己的邮箱，收到即通。

**常见邮箱注意事项**

- **QQ 邮箱 / 163 邮箱**：密码栏要填**授权码**，不是登录密码。在邮箱设置里开启 SMTP 服务后生成。
- **端口与加密方式要匹配**：465 一般配 `ssl`，587 一般配 `starttls`。填错会得到「连接被提前断开」的提示。
- **无认证的内部中继**：只填服务器与发件地址、账号密码留空即可（代码不强制要求密码）。

配置保存在 `DATA_DIR/settings.json`，密码加密存储；后台接口读取时只返回 `****`，不会下发明文。

## 修改文案（非程序员）

只需要改 `web/data/interpretations.js` 里的中文句子。规则：

1. 只改引号里的中文，**不要动字段名、不要动 JSON 结构**（这个文件的赋值号右边必须是合法 JSON：字段名带引号、对象里不写注释、末尾不写多余逗号）。
2. `body` 是字符串数组，一段一个元素，增删段落就是增删元素。
3. 留空（`""` 或 `[]`）表示该区块不渲染 —— 不会显示占位符。
4. **改完务必把 `web/index.html` 里 `interpretations.js?v=N` 的数字 +1**，否则线上可能仍是旧缓存。

改题目改 `web/data/questions.js`。三道母题（`role: "anchor"`）有契约校验：如果改动了文字，必须同时在 `normalizedFrom` 里写明理由，否则页面加载时会报契约错误。

## 改完怎么验

```bash
# 判定逻辑：设计断言 + 机械字段 + 全部 512 种答案三方对撞
PYTHONUTF8=1 python tools/test_scoring.py

# 数据契约自检（9 题 / 每维 3 题 / 母题逐字 / 类型齐全）
node -e '...'   # 或直接打开页面，校验失败会在页面上明示
```

## 一键部署（推荐）

把源码放到服务器上任意目录（例如 `/opt/lovequiz`），然后：

```bash
cd /opt/lovequiz
./deploy.sh
```

脚本会问两件事：**对外端口**（默认 8000）和**后台登录口令**（至少 8 位，输入不回显）。
其余它自己做：生成 `SECRET_KEY`、写 `.env`（权限 600）、检查端口占用、构建镜像、启动容器、轮询 `/healthz`。

非交互模式（便于重跑和自动化）：

```bash
./deploy.sh --password '你的口令' --port 8000 --yes
```

| 选项 | 说明 |
|---|---|
| `--password <口令>` | 后台登录口令；不给则交互询问 |
| `--port <端口>` | 宿主机对外端口，默认 8000 |
| `-y, --yes` | 非交互，缺项用默认值或自动生成 |
| `--no-build` | 跳过构建，用已有镜像启动 |
| `-h, --help` | 帮助 |

重复执行是安全的：已有 `.env` 会作为默认值并沿用 `SECRET_KEY`，容器就地重建，`/data` 卷里的 SMTP 配置与限流计数不受影响。

> **注意**：`ADMIN_PASSWORD` 只是**初始口令**。`/data` 里已经存过口令时，新口令不会覆盖旧的，后台仍用旧口令登录。
> 真要换口令，得 `docker compose down -v` 清空数据卷——SMTP 配置和限流计数会一起没。

### 手动方式

```bash
echo "ADMIN_PASSWORD='你的口令'" > .env
echo "SECRET_KEY=$(openssl rand -hex 32)" >> .env
docker compose up -d --build
```

或完全不用 compose：

```bash
docker build -t lovequiz .
docker run -d -p 8000:8000 -v lovequiz-data:/data \
  -e ADMIN_PASSWORD='你的口令' -e SECRET_KEY='一串随机字符' lovequiz
```

健康检查：`GET /healthz` → `{"ok":true,...}`。

## 目录

```
app/         后端：main.py 路由 / scoring.py 判定 / store.py 配置与限流 / mailer.py 发信 / content.py 读数据
web/         前端：index.html + styles.css + app.js + scoring.js + validate.js + data/*.js + admin.html
tools/       test_scoring.py 判定回归；e2e_browser.py 真实浏览器走查
tests/       golden-vectors.json 判定黄金向量
```

## 边界与声明

- 本测试衡量的是**你此刻的自我报告**，不是心理诊断，也不判断 Ta 对你是什么感情。
- 答卷不写入浏览器存储、不上传统计。只有你主动填邮箱点发送，答案才会经服务器转成一封邮件发给你自己；**服务器不留存答案**。
- 发信端点是公开的，已内置按 IP / 按收件邮箱 / 全站三维限流与每日总量上限。建议用专用小号做发件账号，不要用主邮箱。
