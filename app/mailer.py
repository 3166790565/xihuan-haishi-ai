# -*- coding: utf-8 -*-
"""SMTP 发信 + 结果邮件正文渲染 —— 全 stdlib，不引入任何新依赖。

职责：
  - send_result_mail(cfg, to_addr, subject_body_html)：按 cfg 里的 smtp_security 建连接并发送
  - build_result_html(result, data_questions, texts, alias)：用判定结果 + 文案生成正文

关键约束：
  1. 失败必须翻译成人话（reason 是给用户看的中文，附一句可操作建议），
     不能只把异常字符串原样 return —— 后台要照着这句话判断该改哪个字段。
  2. 不写入任何真实邮箱账密；本文件不发任何「自检式」真实网络请求，
     连接只在 send_result_mail() 被调用时发生。
  3. 正文模板固定，不含任何可点击的外部 URL（不写 <a href>）：
     这是一个匿名可用的代发通道，把正文做成带链接的模板就等于把它变成钓鱼载体。
  4. alias 与邮箱都是用户输入，进 HTML 前一律 html.escape，进纯文本前压成单行。
  5. 不 import fastapi，纯逻辑，便于直接单测。
"""
from __future__ import annotations

import email.utils
import html
import smtplib
import socket
import ssl
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parseaddr
from typing import Any

DEFAULT_TIMEOUT_SEC = 20.0

# 邮件里展示八种形态的顺序与前端清单一致（不含 sprout，它是九题制才出现的中间态）。
# 第二个元素只是「文案层缺这个 key 时」的兜底名（斯滕伯格理论的标准译名），
# 正常渲染一律取 texts["types"][key]["name"]，不拿它顶替真文案。
EIGHT_TYPES: tuple[tuple[str, str], ...] = (
    ("nonlove", "无爱"),
    ("liking", "喜欢"),
    ("infatuation", "迷恋之爱"),
    ("empty", "空洞之爱"),
    ("romantic", "浪漫之爱"),
    ("companionate", "伴侣之爱"),
    ("fatuous", "冲动之爱"),
    ("consummate", "完满之爱"),
)

DIM_LABELS: dict[str, str] = {"intimacy": "亲密", "passion": "激情", "commitment": "承诺"}
DIM_ORDER: tuple[str, str, str] = ("intimacy", "passion", "commitment")

MAIL_DISABLED = "服务器还没配置发信邮箱。"
BAD_RECIPIENT = "收件邮箱格式看起来不对。"


# --------------------------------------------------------------------------- #
# 错误分类：把异常映射成「后台能看懂的一句中文 + 下一步怎么办」
# --------------------------------------------------------------------------- #

def _auth_hint(host: str) -> str:
    h = (host or "").lower()
    if any(x in h for x in ("qq", "exmail", "foxmail")):
        return "QQ 邮箱请在设置里开启 SMTP 服务并使用「授权码」，不是 QQ 登录密码。"
    if any(x in h for x in ("163", "126", "yeah")):
        return "163/126 邮箱需要先用网页版开启 SMTP 并生成「客户端授权密码」，不是登录密码。"
    if "gmail" in h:
        return "Gmail 需要生成「应用专用密码」，并且通常要先开启两步验证。"
    if any(x in h for x in ("smtp.1", "sina", "aliyun", "qiye")):
        return "请使用邮箱服务商提供的 SMTP 授权码 / 应用专用密码。"
    return "请确认用户名与密码/授权码是否成对，多数服务商要求用授权码而不是网页登录密码。"


def classify_smtp_error(exc: BaseException, cfg: dict[str, Any] | None = None) -> str:
    """把底层异常翻译成人话。顺序很重要：先具体后笼统。"""
    cfg = cfg or {}
    host = str(cfg.get("smtp_host") or "")
    port = cfg.get("smtp_port")
    sec = str(cfg.get("smtp_security") or "")

    # 认证失败（454 / 535）
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        detail = str(getattr(exc, "smtp_error", "") or exc)
        return f"SMTP 认证失败：用户名或密码（授权码）不被接受。{_auth_hint(host)}（服务器原话：{detail[:120]}）"

    # 收件人被拒（550 / 551 / 553 用户不存在）
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        resp = getattr(exc, "recipients", None) or {}
        code = ""
        for _addr, value in resp.items():
            try:
                code = str(value[0])
            except (TypeError, IndexError, AttributeError):
                pass
            break
        # 不回显收件地址：这段 reason 会进后台界面，拼上原始输入等于多一个 XSS 面。
        return ("收件地址被服务器拒绝：该邮箱可能不存在、已停用，或被判定为不可投递"
                + (f"（错误码 {code}）" if code else "")
                + "。请换一个常用邮箱再试；如果任何人都收不到，"
                  "说明发信账号被服务商限制了对外投递，需要去服务商后台解限。")

    # 发件人被拒（553 常是 MAIL FROM 与登录账号不一致）
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return ("发件地址被拒绝：多数服务商要求「发件地址」必须与登录的 SMTP 账号一致，"
                "不接受替别的域名代发。请把 from_addr 改成 smtp_user 对应的那个地址。"
                f"（服务器原话：{str(getattr(exc, 'smtp_error', '') or exc)[:120]}）")

    # 数据/协议层错误：命令不被支持、响应无法解析
    if isinstance(exc, smtplib.SMTPDataError):
        return ("SMTP 协议响应异常：端口上跑的可能不是 SMTP 服务，或者 SSL 用法与端口不匹配"
                f"（当前 smtp_security={sec or '未设置'}）。465 端口通常选 ssl，587 端口通常选 starttls，"
                "25 端口在无加密的内网才选 none。")

    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return ("连接被服务器提前断开：常见原因是端口与加密方式不匹配"
                f"（当前 smtp_security={sec or '未设置'}，端口 {port}）。"
                "465 请选 ssl；587 请选 starttls；直连 25 才选 none。"
                "其次是本机网络或服务商封锁了该端口。")

    if isinstance(exc, smtplib.SMTPResponseException):
        return f"服务器返回错误 {getattr(exc, 'smtp_code', '')}：{str(getattr(exc, 'smtp_error', '') or exc)[:160]}"

    if isinstance(exc, smtplib.SMTPException):
        return f"SMTP 流程未完成：{str(exc)[:160]}（可先检查端口与加密方式是否匹配）"

    # TLS 握手层：证书、协议不匹配
    if isinstance(exc, ssl.SSLError):
        return ("TLS 握手失败：多半是加密方式选错了。"
                f"465 端口应选 ssl，587 端口应选 starttls（当前 smtp_security={sec or '未设置'}）。"
                "少数内网证书不受信任也会导致这一步失败。")

    # 超时
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return (f"连接或发送超时（超过 {cfg.get('_timeout', DEFAULT_TIMEOUT_SEC)} 秒）。"
                "常见于本机所在网络封了 25/465/587 出站，或服务商限速。"
                "可以换一个端口（587 + starttls 通常最不容易被封）再试。")

    # DNS
    if isinstance(exc, socket.gaierror):
        return f"域名解析失败：smtp_host「{host}」解析不出地址，请检查拼写（正确形式如 smtp.qq.com）。"

    # 连接被拒 / 网络不可达
    if isinstance(exc, ConnectionRefusedError):
        return (f"连接被拒绝：{host}:{port} 上没有接受连接的 SMTP 服务。"
                "请确认端口号（ ssl 常见 465，starttls 常见 587）与 smtp_host 是否填反或填错。")

    if isinstance(exc, (ConnectionAbortedError, ConnectionResetError, OSError)):
        return (f"无法连到 SMTP 服务器（{host or '未填写'}:{port}）："
                "可能是端口不通、防火墙拦截、或服务商不允许该出站端口。"
                "换一个端口（587 + starttls）或换网络再试。")

    return f"未知错误：{type(exc).__name__}: {str(exc)[:160]}"


# --------------------------------------------------------------------------- #
# 发送
# --------------------------------------------------------------------------- #

def _addr_ok(addr: str) -> bool:
    """很轻的格式校验：真正的把关在服务商那边，这里只挡住明显的手滑。"""
    text = str(addr or "").strip()
    if not text or text != parseaddr(text)[1] or text.count("@") != 1:
        return False
    local, _, domain = text.partition("@")
    return bool(local) and "." in domain and " " not in text


def send_result_mail(
    cfg: dict[str, Any],
    to_addr: str,
    subject_body_html: Any,
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> dict[str, Any]:
    """发一封结果邮件。成功 {"ok": True}；失败 {"ok": False, "reason": 人话}。

    subject_body_html 可以是 build_result_html() 返回的 (subject, html, plain)，
    也可以是同内容的 dict {"subject","html","plain"}。
    """
    if isinstance(subject_body_html, dict):
        subject = str(subject_body_html.get("subject") or "")
        html_body = str(subject_body_html.get("html") or "")
        plain_body = str(subject_body_html.get("plain") or "")
    else:
        try:
            subject, html_body, plain_body = (str(x) for x in subject_body_html)
        except (TypeError, ValueError):
            return {"ok": False, "reason": "邮件内容参数不对：需要 (subject, html, plain) 三元组或同结构 dict。"}

    if not isinstance(cfg, dict):
        return {"ok": False, "reason": MAIL_DISABLED}
    # 门槛必须与 store.mail_ready() 完全一致，否则会出现「状态显示就绪、发信却说未配置」。
    # 密码只在配了 smtp_user 时才必需 —— 下面正是 `if cfg.get("smtp_user"): smtp.login(...)`，
    # 无认证的内部中继（只填 host/from_addr）是合法配置。
    if not (cfg.get("smtp_host") and cfg.get("from_addr")):
        return {"ok": False, "reason": MAIL_DISABLED}
    if cfg.get("smtp_user") and not cfg.get("smtp_password"):
        return {"ok": False, "reason": MAIL_DISABLED}
    if not _addr_ok(to_addr):
        return {"ok": False, "reason": BAD_RECIPIENT}
    if not _addr_ok(str(cfg.get("from_addr"))):
        return {"ok": False, "reason": "发件地址不合法：from_addr 必须是完整的邮箱地址（含 @ 与域名）。"}

    to_addr = str(to_addr).strip()
    from_addr = str(cfg["from_addr"]).strip()
    from_name = str(cfg.get("from_name") or "").strip()
    security = str(cfg.get("smtp_security") or "ssl")
    port = int(cfg.get("smtp_port") or (465 if security == "ssl" else 587))
    eff_timeout = float(timeout) if timeout else DEFAULT_TIMEOUT_SEC
    view = dict(cfg)
    view["_timeout"] = eff_timeout

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr_safe(from_addr, from_name)
    msg["To"] = to_addr
    msg["Date"] = email.utils.formatdate(localtime=False)
    msg["Message-ID"] = email.utils.make_msgid(domain=from_addr.rsplit("@", 1)[-1] or None)
    # text 先挂、html 后挂：multipart/alternative 里后面的优先级更高。
    msg.attach(MIMEText(plain_body or "请改用支持 HTML 的邮件客户端查看。", "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    smtp: smtplib.SMTP | None = None
    try:
        if security == "ssl":
            context = ssl.create_default_context()
            smtp = smtplib.SMTP_SSL(cfg["smtp_host"], port, timeout=eff_timeout, context=context)
        else:
            smtp = smtplib.SMTP(cfg["smtp_host"], port, timeout=eff_timeout)
        if security == "starttls":
            # has_extn 只在 EHLO 之后才有意义，所以这里必须显式 EHLO 一次；
            # smtp.ehlo() 失败说明对端连协议都不正常，直接按错误分类返回。
            try:
                smtp.ehlo()
            except smtplib.SMTPException as exc:              # noqa: BLE001
                return {"ok": False, "reason": classify_smtp_error(exc, view)}
            if not smtp.has_extn("starttls"):
                return {"ok": False, "reason":
                        f"{cfg['smtp_host']}:{port} 不支持 STARTTLS。若该端口其实是隐式 SSL（常见于 465），"
                        "请把 smtp_security 改成 ssl；若确实是 587，请确认这条链路没被中间设备拦截。"}
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
        if cfg.get("smtp_user"):
            smtp.login(str(cfg["smtp_user"]), str(cfg["smtp_password"]))
        smtp.sendmail(from_addr, [to_addr], msg.as_string())
        return {"ok": True}
    except Exception as exc:                      # noqa: BLE001 —— 这里就是要兜住全部外因
        return {"ok": False, "reason": classify_smtp_error(exc, view)}
    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except Exception:                     # noqa: BLE001
                pass


def formataddr_safe(addr: str, name: str = "") -> str:
    """带显示名的 From 头。中文显示名要编码成 RFC 2047，否则部分服务商直接拒。"""
    if not name:
        return addr
    return f"{Header(name, 'utf-8').encode()} <{addr}>"


# --------------------------------------------------------------------------- #
# 正文渲染
# --------------------------------------------------------------------------- #

def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _one_line(value: Any, limit: int = 40) -> str:
    """压成一行、限长、去控制字符，但**保留原字符**。

    这是给 HTML 分支用的：进了 HTML 再由 _esc() 转义，用户写真尖括号（比如代号写成
    「<3」）也还能看得出原样。CR/LF 一定被吃掉，所以拼进邮件主题不会注入头部。
    """
    text = " ".join(str(value or "").split())
    text = "".join(ch for ch in text if ch == " " or ord(ch) >= 0x20)
    return text[:limit]


def _plain_alias(value: Any, limit: int = 40) -> str:
    """纯文本分支与邮件主题用的代号：在 _one_line 之上再剥掉尖括号。

    纯文本没法「转义后仍然可读」——&lt; 会原样显示成 &lt;。而代号是自由输入，
    留着裸标签等于给邮件一个「像钓鱼」的外观，所以这里直接删掉这两个字符，代价近乎零。
    """
    text = _one_line(value, limit)
    return text.replace("<", "").replace(">", "")


def _type_name(type_key: str, texts: dict[str, Any]) -> str:
    types = texts.get("types") or {}
    entry = types.get(type_key) or {}
    if isinstance(entry, dict) and entry.get("name"):
        return str(entry["name"])
    for key, label in EIGHT_TYPES:
        if key == type_key:
            return label
    return type_key or "未知形态"


def _headline(result: dict[str, Any], texts: dict[str, Any]) -> str:
    """结论句优先取「这一种形态」自己的 headline，缺了才回落到 verdict 桶的 headline。"""
    types = texts.get("types") or {}
    entry = types.get(result.get("typeKey")) or {}
    if isinstance(entry, dict) and str(entry.get("headline") or "").strip():
        return str(entry["headline"]).strip()
    verdicts = texts.get("verdicts") or {}
    bucket = verdicts.get(result.get("verdict")) or {}
    if isinstance(bucket, dict) and str(bucket.get("headline") or "").strip():
        return str(bucket["headline"]).strip()
    return ""


def _disclaimer(texts: dict[str, Any]) -> str:
    notice = texts.get("notice") or {}
    result = texts.get("result") or {}
    for source in (notice, result):
        for key in ("disclaimer", "footer"):
            text = str(source.get(key) or "").strip()
            if text:
                return text
    return "这不是诊断，也不给你的感情定性。"


def build_result_html(
    result: dict[str, Any],
    data_questions: dict[str, Any],
    texts: dict[str, Any],
    alias: str,
) -> tuple[str, str, str]:
    """生成 (subject, html, plain)。模板固定、无外链、所有用户输入都转义。"""
    result = result if isinstance(result, dict) else {}
    texts = texts if isinstance(texts, dict) else {}
    data_questions = data_questions if isinstance(data_questions, dict) else {}

    # safe_alias 进 HTML（配合 _esc）；plain_alias 进纯文本与邮件主题（尖括号已剥除）
    safe_alias = _one_line(alias, 40) or "Ta"
    plain_alias = _plain_alias(alias, 40) or "Ta"
    type_key = str(result.get("typeKey") or "")
    name = _type_name(type_key, texts)
    head = _headline(result, texts)
    scores = result.get("scores") or {}
    threshold = result.get("threshold")
    if threshold is None:
        rules = data_questions.get("rules") or {}
        threshold = rules.get("presentThreshold", 2)
    per_dim = (data_questions.get("rules") or {}).get("perDim", 3)

    # 维度名以文案层为准，缺了才回落到内置标签；两源都缺就显示英文 key，不猜句子。
    dims_text = texts.get("dims") or {}
    dim_rows: list[tuple[str, int, str]] = []
    for key in DIM_ORDER:
        entry = dims_text.get(key) or {}
        label = str(entry.get("name") or DIM_LABELS.get(key) or key) if isinstance(entry, dict) else DIM_LABELS.get(key, key)
        raw = scores.get(key)
        value = raw if isinstance(raw, int) and not isinstance(raw, bool) else 0
        present = value >= int(threshold or 2)
        dim_rows.append((label, value, "在场" if present else "未达阈值"))

    eight_rows: list[tuple[str, str, bool]] = []
    for key, label in EIGHT_TYPES:
        entry = (texts.get("types") or {}).get(key) or {}
        shown = str(entry.get("name") or label) if isinstance(entry, dict) else label
        eight_rows.append((key, shown, key == type_key))

    borderline = result.get("borderlineDims") or []
    borderline_text = ""
    if borderline:
        labels = []
        for key in borderline:
            entry = dims_text.get(key) or {}
            labels.append(str(entry.get("name") or DIM_LABELS.get(key, key)) if isinstance(entry, dict)
                          else DIM_LABELS.get(key, key))
        res_text = texts.get("result") or {}
        body_tmpl = str(res_text.get("borderlineBody") or "")
        title = str(res_text.get("borderlineTitle") or "").strip()
        joined = "、".join(labels)
        body = body_tmpl.replace("{dims}", joined) if "{dims}" in body_tmpl else joined + " 差一题就会翻边。"
        # 标题拼上引导语，避免正文里孤零零一句「亲密 差一题就会翻边」。
        borderline_text = (title + "：" + body) if title else body

    # 主题走纯文本通道（Header 编码），所以用已经剥掉尖括号的 plain_alias
    subject = f"喜欢还是爱｜对 {plain_alias} 而言：{name}"

    # ------- HTML -------
    li_dim = "\n".join(
        f'      <tr><td>{_esc(label)}</td><td style="text-align:right">{int(value)}/{_esc(per_dim)}</td>'
        f'<td style="text-align:right">{_esc(flag)}</td></tr>'
        for label, value, flag in dim_rows
    )
    li_eight = "\n".join(
        f'      <li>{_esc(shown)}{"　◀ 你在这里" if hit else ""}</li>'
        if not hit else f'      <li style="font-weight:700">{_esc(shown)}　◀ 你在这里</li>'
        for _key, shown, hit in eight_rows
    )
    html_body = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>{_esc(subject)}</title></head>
<body style="margin:0;padding:24px;background:#f6f5f2;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;
  color:#222;line-height:1.7">
  <div style="max-width:560px;margin:0 auto;background:#fff;border:1px solid #e3e0d8;border-radius:10px;padding:28px">
    <div style="font-size:13px;letter-spacing:.08em;color:#8a8577">喜欢还是爱 · 九题自陈结果</div>
    <h1 style="font-size:20px;margin:10px 0 4px">对 {_esc(safe_alias)} 而言</h1>
    <div style="font-size:17px;margin:0 0 18px">{_esc(name)}</div>
    <p style="margin:0 0 22px;font-size:15px">{_esc(head)}</p>
    <h2 style="font-size:14px;margin:0 0 8px;color:#5d5849">三个成分的得分</h2>
    <table role="presentation" style="border-collapse:collapse;width:100%;font-size:14px">
      <tbody>
{li_dim}
      </tbody>
    </table>
    <p style="font-size:12px;color:#8a8577;margin:8px 0 22px">横线上那道竖线是阈值：一个成分要 {_esc(per_dim)} 题里至少 {_esc(threshold)} 题答「是」才算在场。</p>
    <h2 style="font-size:14px;margin:0 0 8px;color:#5d5849">八种形态</h2>
    <ol style="margin:0 0 8px;padding-left:20px;font-size:14px">
{li_eight}
    </ol>
    <p style="margin:0 0 22px">
      <span style="font-weight:700">◀ 你在这里</span> 标出的是本次命中的形态。
    </p>
    <hr style="border:0;border-top:1px solid #eee8db;margin:22px 0">
    <p style="font-size:13px;color:#5d5849;margin:0 0 8px">{_esc(borderline_text)}</p>
    <p style="font-size:13px;color:#5d5849;margin:0 0 8px">本次总分 {_esc(result.get("total", ""))} / 9</p>
    <p style="font-size:13px;color:#5d5849;margin:0 0 18px">版本 {str(_esc(result.get("version", "")))}</p>
    <div style="background:#f3f1ea;border-radius:8px;padding:14px 16px;font-size:13px;color:#4a4639">
      {_esc(_disclaimer(texts))}
    </div>
    <p style="font-size:12px;color:#9a9484;margin:16px 0 0">
      这封邮件由你在「喜欢还是爱」页面上主动请求发送。服务器不留存你的答案，也不包含任何需要登录或点击的链接。
    </p>
  </div>
</body>
</html>
"""

    # ------- plain -------
    plain_lines = [
        "喜欢还是爱 · 九题自陈结果",
        "",
        f"对 {plain_alias} 而言",
        f"形态：{name}",
        head,
        "",
        "三个成分的得分",
    ]
    for label, value, flag in dim_rows:
        plain_lines.append(f"  - {label}：{int(value)}/{per_dim}（{flag}）")
    plain_lines += ["", f"在场判定：每维 {per_dim} 题里至少 {threshold} 题答「是」。", "", "八种形态"]
    for _key, shown, hit in eight_rows:
        plain_lines.append(f"  {'*' if hit else '-'} {shown}{'　◀ 你在这里' if hit else ''}")
    if borderline_text:
        plain_lines += ["", borderline_text]
    plain_lines += ["", f"本次总分 {result.get('total', '')} / 9", "", _disclaimer(texts), "",
                    "这封邮件由你在「喜欢还是爱」页面上主动请求发送。"
                    "服务器不留存你的答案，也不包含任何需要登录或点击的链接。"]
    plain_body = "\n".join(plain_lines) + "\n"
    return subject, html_body, plain_body


def build_test_mail(to_addr: str) -> tuple[str, str, str]:
    """后台「发一封测试邮件给自己」用的简化正文。

    与 build_result_html 的区别：不依赖判定结果与文案数据，正文固定，
    只用来验证 SMTP 配置是否打通。同样无外链、收件地址已转义。
    """
    safe_to = _esc(to_addr)
    subject = "【喜欢还是爱】测试邮件 · SMTP 配置正常"
    html = (
        "<div style='font-family:sans-serif;max-width:560px;margin:auto'>"
        "<h2>SMTP 配置正常</h2>"
        "<p>这是「喜欢还是爱」后台发出的测试邮件。如果你收到了它，说明当前的发信配置可用。</p>"
        f"<p>收件地址：{safe_to}</p>"
        "<p style='color:#888;font-size:12px'>本邮件由管理员主动触发，不含任何答案或链接。</p>"
        "</div>"
    )
    plain = (
        "【喜欢还是爱】测试邮件 · SMTP 配置正常\n\n"
        "这是「喜欢还是爱」后台发出的测试邮件。如果你收到了它，说明当前的发信配置可用。\n"
        f"收件地址：{_one_line(to_addr, 120)}\n\n"
        "本邮件由管理员主动触发，不含任何答案或链接。\n"
    )
    return subject, html, plain
