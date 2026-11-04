# -*- coding: utf-8 -*-
"""应用入口 —— 单进程 FastAPI，三件事一个容器里全做：
  1. 托管前端静态页（web/）
  2. 发信 API（POST /api/mail/send）—— 选填，测试者把自己的结果发到自己邮箱
  3. 后台 WebUI（/admin）—— 配置 SMTP、查看限流状态、发测试邮件

设计取舍：
  - 不用 nginx。前端只是几张静态页 + 几个 .js，FastAPI 的 StaticFiles 够用，少一层容器。
  - 不用数据库。配置与限流计数都落 /data 挂载卷的 JSON 文件（app/store.py 已做原子写 + 锁）。
  - 所有请求体大小受 app 限制；发信前重算判定（不信任客户端提交的结论文本，防伪造邮件内容）。

关键安全边界（不是写在 README 就算数，代码里强制）：
  - /api/mail/send 是「任意访客可让服务器向任意邮箱发信」—— 限流必须先过、答案必须完整、
    邮箱格式必须合法，否则 4xx。邮件正文模板固定、不含可点击外部 URL（防当钓鱼通道）。
  - /admin 所有写操作要带有效会话 token。ADMIN_PASSWORD 未设置时整体禁用，不默认放行。
  - get_runtime_config() 含明文密码，永远不能进响应体；对外只给 store.public_view()。
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import content, mailer, scoring, store

log = logging.getLogger("lovequiz")

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
DATA_DIR = ROOT / "data"

# 请求体上限：发信端点不需要更多，限制大小本身就是一层防护（防滥用大 body 耗资源）。
_MAX_BODY = 8 * 1024

# 邮箱格式只做轻量校验，不做「探测该邮箱是否存在」这种动作（那本身是信息泄露）。
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,253}\.[A-Za-z]{2,}$")
# 代号也限制长度与控制字符，防邮件头注入（mailer.py 已做转义，这里是入口层第二道）。
_ALIAS_RE = re.compile(r"^[^\s<>@]{1,20}$")

app = FastAPI(title="喜欢还是爱", docs_url=None, redoc_url=None, openapi_url=None)

# 模块级缓存：题目/文案只在进程启动时读一次。改文案靠重启或单独的清缓存端点，
# 不在每次请求读盘（会拖慢响应，也不利于限流计数稳定）。
_QUESTIONS: dict[str, Any] = {}
_TEXTS: dict[str, Any] = {}


@app.on_event("startup")
def _load() -> None:
    """启动时读数据层；解析失败要让进程直接起不来，而不是上线一个渲染错乱的应用。"""
    global _QUESTIONS, _TEXTS
    _QUESTIONS = content.load_questions()
    _TEXTS = content.load_texts()
    log.info("loaded %d questions, %d types", len(_QUESTIONS.get("questions", [])),
             len(_TEXTS.get("types", {})))
    # store 在 SECRET_KEY/ADMIN_PASSWORD 缺失时会 print 警告，这里不重复。


# ── 前端静态页 ────────────────────────────────────────────────────────────────
# StaticFiles 挂在 /，但 index.html 与 data/ 要显式控制缓存头（见下）。
# 顺序：具体路由（/api、/admin、/healthz）必须先于 StaticFiles 兜底注册，否则被静态吃掉。


@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"ok": True, "admin": store.admin_status()["enabled"]})


@app.get("/")
def index_root() -> FileResponse:
    # 首页不长缓存，文案改完即时生效（零构建无内容 hash，这是唯一手段）。
    return FileResponse(WEB_DIR / "index.html", media_type="text/html; charset=utf-8",
                        headers={"Cache-Control": "no-store"})


# data/*.js 设短缓存 + must-revalidate；非程序员改文案后可即时回滚。
@app.get("/data/{name}")
def data_file(name: str) -> FileResponse:
    if not re.fullmatch(r"[A-Za-z0-9_\-]+\.js", name):
        raise HTTPException(404)
    p = WEB_DIR / "data" / name
    if not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, media_type="application/javascript; charset=utf-8",
                        headers={"Cache-Control": "public, max-age=300, must-revalidate"})


# StaticFiles 挂在 /static，前缀不与具体路由冲突，留在此处。
# 注意：SPA 兜底路由 `/{path:path}` 必须放在文件最末尾、所有 /api/* 与 /admin 之后，
# 否则 FastAPI 按声明顺序匹配会把 API 和后台路由全部吃掉（这是本项目踩过的坑）。
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


# ── 发信 API ──────────────────────────────────────────────────────────────────
def _client_ip(request: Request) -> str:
    # 只信第一层 X-Forwarded-For 的最左 IP，且仅在它像 IP 时采用；
    # 直连场景用 request.client.host。不信任多层 XFF 链（可伪造）。
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        first = xff.split(",")[0].strip()
        if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}|[0-9a-fA-F:]+", first):
            return first
    return request.client.host if request.client else "0.0.0.0"


def _err(code: str, reason: str, status: int) -> JSONResponse:
    return JSONResponse({"ok": False, "code": code, "reason": reason}, status_code=status)


@app.post("/api/mail/send")
async def mail_send(request: Request) -> JSONResponse:
    raw = await request.body()
    if len(raw) > _MAX_BODY:
        return _err("too_large", "请求体过大", 413)
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return _err("bad_json", "请求不是合法 JSON", 400)
    if not isinstance(payload, dict):
        return _err("bad_json", "请求体不是对象", 400)

    answers = payload.get("answers")
    alias = payload.get("alias")
    to_addr = payload.get("to")

    if not isinstance(answers, dict) or not isinstance(to_addr, str) or alias is None:
        return _err("incomplete", "缺少字段", 400)
    if not _EMAIL_RE.match(to_addr):
        # 邮箱格式校验在后端做第二道（前端已做一次）；message 与前端 errors.badEmail 同义。
        return _err("bad_email", "邮箱格式看起来不对", 400)
    if not (alias == "" or _ALIAS_RE.match(str(alias))):
        return _err("bad_alias", "代号过长或含非法字符", 400)

    # 重算判定：不信任客户端的结论文本。
    try:
        result = scoring.evaluate(answers, _QUESTIONS)
    except scoring.AnswerError as exc:
        return _err("incomplete", str(exc), 400)

    cfg = store.get_runtime_config()
    if not store.mail_ready(cfg):
        return _err("mail_disabled", "服务器还没配置发信邮箱", 503)

    ip = _client_ip(request)
    ok, reason = store.allow_send(ip, to_addr, cfg)
    if not ok:
        return _err("rate_limited", reason, 429)

    # 生成正文（模板固定，mailer 内已转义 alias）。
    subject, html, plain = mailer.build_result_html(result, _QUESTIONS, _TEXTS, str(alias))
    r = mailer.send_result_mail(cfg, to_addr, (subject, html, plain))
    if r.get("ok"):
        return JSONResponse({"ok": True})
    # 发信失败但额度已占（store 的设计：放行即计数，防用失败刷探测）。
    return _err("send_failed", r.get("reason") or "发送失败", 502)


# ── 后台 WebUI ────────────────────────────────────────────────────────────────
# 后台是独立的单页 HTML，登录后通过 token 调 /api/admin/* 写配置。
# ADMIN_PASSWORD 未设置时，整个 /admin 与 /api/admin/* 都返回 503 + 明确提示，
# 不默认放行、不猜默认口令。

def _require_admin(authorization: str | None) -> None:
    if not store.admin_enabled():
        raise HTTPException(503, detail="后台未启用（ADMIN_PASSWORD 未设置）")
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    elif authorization and authorization.lower().startswith("token "):
        token = authorization[6:].strip()
    if not token or not store.check_token(token):
        raise HTTPException(401, detail="未登录或会话已过期")


@app.get("/admin")
def admin_page() -> HTMLResponse:
    return HTMLResponse(content=(WEB_DIR / "admin.html").read_text(encoding="utf-8"),
                        headers={"Cache-Control": "no-store"})


@app.post("/api/admin/login")
async def admin_login(request: Request) -> JSONResponse:
    if not store.admin_enabled():
        return JSONResponse({"ok": False, "code": "disabled",
                             "reason": "后台未启用（ADMIN_PASSWORD 未设置）"}, 503)
    payload = await request.body()
    if len(payload) > 256:
        return JSONResponse({"ok": False, "code": "bad_request", "reason": "请求过大"}, 400)
    try:
        data = json.loads(payload)
    except ValueError:
        return JSONResponse({"ok": False, "code": "bad_request", "reason": "非 JSON"}, 400)
    if not store.verify_admin(data.get("password")):
        return JSONResponse({"ok": False, "code": "bad_credentials", "reason": "口令错误"}, 401)
    token = store.make_token()
    return JSONResponse({"ok": True, "token": token})


@app.get("/api/admin/status")
def admin_status(authorization: str | None = Header(None)) -> JSONResponse:
    # 状态本身是公开的（是否启用），配置细节要登录。
    base = store.admin_status()
    if not base["enabled"]:
        return JSONResponse(base)
    _require_admin(authorization)
    cfg = store.get_runtime_config()
    base["config"] = store.public_view(cfg)
    base["mail_ready"] = store.mail_ready(cfg)
    base["rate"] = store.rate_snapshot()
    return JSONResponse(base)


@app.post("/api/admin/config")
async def admin_config_save(request: Request, authorization: str | None = Header(None)) -> JSONResponse:
    _require_admin(authorization)
    payload = await request.body()
    if len(payload) > 2048:
        return JSONResponse({"ok": False, "code": "too_large", "reason": "配置过大"}, 400)
    try:
        patch = json.loads(payload)
    except ValueError:
        return JSONResponse({"ok": False, "code": "bad_request", "reason": "非 JSON"}, 400)
    if not isinstance(patch, dict):
        return JSONResponse({"ok": False, "code": "bad_request", "reason": "不是对象"}, 400)
    try:
        view = store.save_settings(patch)
    except (ValueError, TypeError) as exc:
        return JSONResponse({"ok": False, "code": "bad_field", "reason": str(exc)}, 400)
    return JSONResponse({"ok": True, "config": view})


@app.post("/api/admin/test-mail")
async def admin_test_mail(request: Request, authorization: str | None = Header(None)) -> JSONResponse:
    """用当前配置发一封测试邮件到管理员自己填的地址，把 SMTP 错误分类成人话。"""
    _require_admin(authorization)
    payload = await request.body()
    try:
        data = json.loads(payload)
    except ValueError:
        return JSONResponse({"ok": False, "reason": "非 JSON"}, 400)
    to_addr = data.get("to") if isinstance(data, dict) else None
    if not isinstance(to_addr, str) or not _EMAIL_RE.match(to_addr):
        return JSONResponse({"ok": False, "reason": "收件邮箱格式不对"}, 400)
    cfg = store.get_runtime_config()
    if not store.mail_ready(cfg):
        return JSONResponse({"ok": False, "reason": "发信配置不完整"}, 400)
    subject, html, plain = mailer.build_test_mail(to_addr)
    r = mailer.send_result_mail(cfg, to_addr, (subject, html, plain))
    return JSONResponse(r)


@app.post("/api/admin/rate/reset")
def admin_rate_reset(authorization: str | None = Header(None)) -> JSONResponse:
    _require_admin(authorization)
    store.reset_rate_state()
    return JSONResponse({"ok": True})


# ── SPA 兜底（必须放在所有具体路由之后，否则会吞掉 /api/* 和 /admin）─────────────
@app.get("/{path:path}")
def spa_fallback(path: str) -> FileResponse:
    """单页应用：除已注册的 /api、/admin、/healthz、/data 等路由外，回落到 index.html
    或命中的静态文件。因为 FastAPI 按声明顺序匹配，这条必须最后声明。"""
    candidates = [WEB_DIR / path, WEB_DIR / (path + ".js"), WEB_DIR / (path + ".css")]
    for c in candidates:
        if c.is_file() and WEB_DIR in c.resolve().parents:
            return FileResponse(c, headers={"Cache-Control": "public, max-age=3600"})
    return FileResponse(WEB_DIR / "index.html", media_type="text/html; charset=utf-8",
                        headers={"Cache-Control": "no-store"})
