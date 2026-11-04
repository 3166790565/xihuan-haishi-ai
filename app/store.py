# -*- coding: utf-8 -*-
"""配置存储 + 管理会话 + 发信限流 —— 安全边界在这个文件里，不在路由层。

职责：
  - <项目根>/data/settings.json 的读写（目录可用环境变量 DATA_DIR 覆盖，自动创建）
  - 管理口令只保存 pbkdf2_hmac 摘要；SMTP 授权密码只留在本机配置文件里
  - 签名会话 token（hmac + SECRET_KEY + 过期时间）
  - 按 IP / 按收件邮箱 / 全局每日总量三个维度的持久化限流

关键约束（改动前务必读）：
  1. 本模块不 import fastapi、不发任何网络请求，纯逻辑，便于直接单测。
  2. public_view() 是唯一可以交给前端/接口的读出口：smtp_password 只可能是
     "****"（已设置）或 ""（未设置），绝不可能出现明文。内部取完整配置一律用
     get_runtime_config()，它只给服务端自用（例如传给 mailer），禁止回传前端。
  3. 所有写盘都是「先写 .tmp 再 os.replace」的原子写，并且全程持锁。
  4. 限流状态落盘在 rate_state.json，重启不清零；日期一变旧计数自动过期。
  5. allow_send() 通过检查即占用额度（含 min_interval 那一项），所以「发送失败」
     也一样计数：否则攻击者可以让发送一直失败来白刷探测上限。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

SETTINGS_NAME = "settings.json"
RATE_NAME = "rate_state.json"

# pbkdf2 参数：轮数写死下限，避免被配置改小；摘要串里自带轮数，校验时按存的来。
PBKDF2_ROUNDS = 200_000
PBKDF2_ALGO = "sha256"
SALT_BYTES = 16

# 会话 token 默认有效期（秒）。
TOKEN_TTL_SEC = 8 * 3600

# 全局最小发信间隔（秒）：与按 IP 的 mail_min_interval_sec 叠加。
# 用来挡「很多个 IP 各发一封」把 SMTP 配额瞬间打光的打法。
# 做成可用环境变量覆盖，是为了单测能在同一秒内连续调用；生产默认 2 秒。
GLOBAL_MIN_SEND_INTERVAL_DEFAULT = 2.0


def global_min_send_interval() -> float:
    raw = os.environ.get("MAIL_GLOBAL_MIN_INTERVAL_SEC")
    if raw is None or not raw.strip():
        return GLOBAL_MIN_SEND_INTERVAL_DEFAULT
    try:
        value = float(raw.strip())
    except ValueError:
        return GLOBAL_MIN_SEND_INTERVAL_DEFAULT
    return value if value >= 0 else 0.0

# 收件邮箱维度的当日上限。配置字段表里没有给它独立项，所以内置一个常量。
TO_DAILY_LIMIT_DEFAULT = 5

_LOCK_SETTINGS = threading.RLock()
_LOCK_RATE = threading.RLock()

_SECRET_LOCK = threading.Lock()
_SECRET_KEY_CACHE: str | None = None
_SECRET_WARNING_SHOWN = False

_SECURITY_VALUES = ("starttls", "ssl", "none")

DEFAULTS: dict[str, Any] = {
    "smtp_host": "",
    "smtp_port": 465,
    "smtp_security": "ssl",
    "smtp_user": "",
    "smtp_password": "",
    "from_addr": "",
    "from_name": "喜欢还是爱",
    "mail_daily_limit": 200,
    "mail_ip_daily_limit": 10,
    "mail_min_interval_sec": 15,
    "admin_pw_hash": "",
}

# 这些键属于「可对外出读」的配置（密码另算，见 public_view）。
PUB_KEYS: tuple[str, ...] = (
    "smtp_host",
    "smtp_port",
    "smtp_security",
    "smtp_user",
    "from_addr",
    "from_name",
    "mail_daily_limit",
    "mail_ip_daily_limit",
    "mail_min_interval_sec",
)

_INT_KEYS: tuple[str, ...] = (
    "smtp_port",
    "mail_daily_limit",
    "mail_ip_daily_limit",
    "mail_min_interval_sec",
)

# 「这次没改密码」的几种写法：不传 / 空串 / 表单回填的掩码占位，一律保留磁盘原值。
_PASSWORD_KEEP_SENTINELS = ("", "****", None)


class _Missing:
    """区分「patch 里没有这个键」与「键在但值是 None」。"""
    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover
        return "<missing>"


_MISSING = _Missing()


# --------------------------------------------------------------------------- #
# 路径与原子写
# --------------------------------------------------------------------------- #

def data_dir() -> Path:
    """配置与限流状态的落盘目录；DATA_DIR 环境变量优先，默认 <项目根>/data。"""
    raw = os.environ.get("DATA_DIR")
    path = Path(raw).expanduser().resolve() if raw and raw.strip() else (ROOT / "data")
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_path() -> Path:
    return data_dir() / SETTINGS_NAME


def rate_path() -> Path:
    return data_dir() / RATE_NAME


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """先写同目录 .tmp、fsync、再 os.replace —— 任何时刻读到的都是完整文件。

    临时名带随机后缀：万一有两个进程同时写，也不会互相把对方的 .tmp 改成半截。
    """
    tmp = path.with_name(f"{path.name}.{secrets.token_hex(6)}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    """容错读盘：文件不存在 / 半截 JSON 都返回 None，不让后台因为一个坏文件起不来。"""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            obj = json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):      # ValueError 覆盖 JSONDecodeError
        return None
    return obj if isinstance(obj, dict) else None


# --------------------------------------------------------------------------- #
# settings：内部读写
# --------------------------------------------------------------------------- #

def _to_int(value: Any, fallback: int) -> int:
    try:
        if isinstance(value, bool):
            raise ValueError
        return int(str(value).strip())
    except (TypeError, ValueError):
        return fallback


def _to_float(value: Any, fallback: float) -> float:
    try:
        if isinstance(value, bool):
            raise ValueError
        return float(str(value).strip())
    except (TypeError, ValueError):
        return fallback


def _coerce(cfg: dict[str, Any]) -> dict[str, Any]:
    """补齐缺省、做类型收敛。前端传字符串也要能活，非法值一律回落默认。"""
    merged = dict(DEFAULTS)
    for key in DEFAULTS:
        if key in cfg:
            merged[key] = cfg[key]
    merged["smtp_security"] = (
        merged["smtp_security"]
        if merged["smtp_security"] in _SECURITY_VALUES
        else DEFAULTS["smtp_security"]
    )
    for key in ("smtp_host", "smtp_user", "from_addr", "from_name", "smtp_password", "admin_pw_hash"):
        merged[key] = str(merged.get(key) or "")
    merged["smtp_host"] = merged["smtp_host"].strip()
    merged["smtp_user"] = merged["smtp_user"].strip()
    merged["from_addr"] = merged["from_addr"].strip()
    merged["from_name"] = merged["from_name"].strip() or DEFAULTS["from_name"]

    port = _to_int(merged["smtp_port"], DEFAULTS["smtp_port"])
    merged["smtp_port"] = port if 0 < port <= 65535 else DEFAULTS["smtp_port"]
    # 0 和负数都当「没填」，回落到默认：一个误填不该把限流整个关掉
    merged["mail_daily_limit"] = max(1, _to_int(merged["mail_daily_limit"], DEFAULTS["mail_daily_limit"]))
    merged["mail_ip_daily_limit"] = max(1, _to_int(merged["mail_ip_daily_limit"], DEFAULTS["mail_ip_daily_limit"]))
    merged["mail_min_interval_sec"] = max(0, _to_int(merged["mail_min_interval_sec"], DEFAULTS["mail_min_interval_sec"]))
    return merged


def _hash_password(password: str) -> str:
    """pbkdf2_hmac(sha256, 随机 salt, 固定轮数)，存 hex 摘要。"""
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(PBKDF2_ALGO, password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return f"pbkdf2-{PBKDF2_ALGO}${PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def _seed_admin_from_env(cfg: dict[str, Any]) -> bool:
    """ADMIN_PASSWORD 只提供「初始」口令：已存过 hash 就不再覆盖，
    否则每次启动都会把后台口令静默改回环境变量里那个旧值（也可能是别人改过的）。"""
    if cfg.get("admin_pw_hash"):
        return False
    initial = os.environ.get("ADMIN_PASSWORD")
    if not initial or not initial.strip():
        return False
    cfg["admin_pw_hash"] = _hash_password(initial.strip())
    return True


def _read_settings() -> dict[str, Any]:
    """内部完整配置（含明文密码）。只允许服务端自己调用，禁止回传前端。"""
    with _LOCK_SETTINGS:
        cfg = _coerce(_read_json(settings_path()) or {})
        if _seed_admin_from_env(cfg):
            _atomic_write_json(settings_path(), cfg)
        return cfg


def get_runtime_config() -> dict[str, Any]:
    """服务端自用的完整配置，含 smtp_password 明文。绝对不要把它放进任何响应体。"""
    return _read_settings()


def mask_user(user: str) -> str:
    """ab****@qq.com —— 保留前 2 个字符与域名：够核对「是哪个号」，不够拼出账号。"""
    text = str(user or "").strip()
    if not text:
        return ""
    local, sep, domain = text.partition("@")
    shown = local[:1] if len(local) <= 2 else local[:2]
    return f"{shown}****{('@' + domain) if sep else ''}"


def public_view(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """唯一可对外出读的配置视图。密码不出明文，账号只出掩码。"""
    merged = cfg if cfg is not None else _read_settings()
    out: dict[str, Any] = {key: merged.get(key, DEFAULTS[key]) for key in PUB_KEYS}
    # smtp_user 按字段表原样出读（后台要能核对填的是哪个号），同时额外给掩码版本，
    # 供只想展示「配了哪个账号」的场景使用。密码则永远只有 "****" / "" 两种取值。
    out["smtp_user_masked"] = mask_user(str(merged.get("smtp_user") or ""))
    out["smtp_user_set"] = bool(merged.get("smtp_user"))
    out["smtp_password"] = "****" if merged.get("smtp_password") else ""
    out["smtp_password_set"] = bool(merged.get("smtp_password"))
    out["admin_pw_configured"] = bool(merged.get("admin_pw_hash"))
    out["mail_to_daily_limit"] = TO_DAILY_LIMIT_DEFAULT
    out["mail_ready"] = mail_ready(merged)
    return out


def mail_ready(cfg: dict[str, Any] | None = None) -> bool:
    """发信配置是否完整可用。不完整时路由层应直接返回「服务器还没配置发信邮箱」。

    判定条件必须与 mailer.send_result_mail 的实际守卫保持一致，否则会出现
    「配置被说成不完整、但 mailer 其实能发」的自相矛盾：
      - 必须有 host 与 from_addr；
      - 密码只在配了 smtp_user 时才必需 —— mailer 是 `if cfg.get("smtp_user"): smtp.login(...)`，
        无认证的内部中继（只填 host/from_addr）必须被认作可用。
    之前这里多要求了 smtp_password，导致无认证 SMTP 永远被判 mail_disabled。
    """
    merged = cfg if cfg is not None else _read_settings()
    if not merged.get("smtp_host") or not merged.get("from_addr"):
        return False
    if merged.get("smtp_user") and not merged.get("smtp_password"):
        return False
    return True


def save_settings(patch: dict[str, Any]) -> dict[str, Any]:
    """合并写入配置，返回 public_view()。

    密码字段的规则是这里最容易出事的地方，固定为三条：
      - patch 里没有 smtp_password，或值是空串 / 掩码占位 "****"  -> 保留磁盘上的原值。
        （后台表单回填时提交的就是 "****"，不能把它当成新密码存进去）
      - 显式传 false 或字符串 "__clear__"                          -> 主动清空密码
      - 其它字符串                                                  -> 作为新密码写入
    """
    if not isinstance(patch, dict):
        raise TypeError("patch 必须是 dict")
    with _LOCK_SETTINGS:
        current = _read_settings()
        merged = dict(current)
        for key in PUB_KEYS:
            if key in patch:
                merged[key] = patch[key]
        if "from_name" in patch:
            merged["from_name"] = patch["from_name"]

        raw_pw = patch.get("smtp_password", _MISSING)
        if raw_pw is _MISSING or raw_pw in _PASSWORD_KEEP_SENTINELS:
            merged["smtp_password"] = current["smtp_password"]
        elif raw_pw is False or (isinstance(raw_pw, str) and raw_pw.strip() == "__clear__"):
            merged["smtp_password"] = ""
        else:
            merged["smtp_password"] = str(raw_pw)

        merged = _coerce(merged)
        merged["admin_pw_hash"] = current["admin_pw_hash"]   # 口令只走 set_admin_password()
        _atomic_write_json(settings_path(), merged)
        return public_view(merged)


# --------------------------------------------------------------------------- #
# 管理口令与会话
# --------------------------------------------------------------------------- #

def admin_enabled(cfg: dict[str, Any] | None = None) -> bool:
    """后台是否可用。

    ADMIN_PASSWORD 未设置且磁盘上没有 hash 时返回 False —— 此时后台整体禁用。
    这里刻意不做「默认口令」「首次启动自动生成并打印口令」这类兜底：
    任何隐式放行都会让忘记配置的部署直接变成公开后台。
    """
    merged = cfg if cfg is not None else _read_settings()
    return bool(merged.get("admin_pw_hash"))


def admin_status() -> dict[str, Any]:
    """给路由层一个明确状态，不要用「密码错误」掩盖「后台根本没开」。"""
    merged = _read_settings()
    return {
        "enabled": admin_enabled(merged),
        "configured": bool(merged.get("admin_pw_hash")),
        "note": ("后台可用" if merged.get("admin_pw_hash")
                 else "未设置管理口令（ADMIN_PASSWORD 未提供且没有已存哈希），后台已整体禁用"),
    }


def verify_admin(password: Any, cfg: dict[str, Any] | None = None) -> bool:
    """定长比较 pbkdf2 摘要。后台未启用时一律 False（不放行）。"""
    merged = cfg if cfg is not None else _read_settings()
    stored = merged.get("admin_pw_hash") or ""
    if not stored or not isinstance(password, str) or not password:
        return False
    parts = stored.split("$")
    if len(parts) != 4 or not parts[0].startswith("pbkdf2-"):
        return False
    try:
        algo = parts[0].split("-", 1)[1]
        rounds = int(parts[1])
        salt = bytes.fromhex(parts[2])
        expect = bytes.fromhex(parts[3])
    except (ValueError, TypeError):
        return False
    if rounds < 100_000:            # 摘要串被人改小轮数也不接受
        return False
    got = hashlib.pbkdf2_hmac(algo, password.encode("utf-8"), salt, rounds)
    return hmac.compare_digest(got, expect)


def set_admin_password(new_password: str) -> dict[str, Any]:
    """改口令（需要路由层先验过旧口令）。返回 public_view。"""
    if not isinstance(new_password, str) or len(new_password) < 8:
        raise ValueError("管理口令至少 8 个字符")
    with _LOCK_SETTINGS:
        merged = _read_settings()
        merged["admin_pw_hash"] = _hash_password(new_password)
        _atomic_write_json(settings_path(), merged)
        return public_view(merged)


def _secret_key() -> bytes:
    """SECRET_KEY 环境变量优先；没设置就本次进程随机生成并打印一行警告。

    随机生成是为了「能跑」而不是「安全」：进程重启后旧 token 全部失效，
    已登录的管理会话会被踢出。警告必须让人看见，否则这像个静默的坑。
    """
    global _SECRET_KEY_CACHE, _SECRET_WARNING_SHOWN
    with _SECRET_LOCK:
        env = os.environ.get("SECRET_KEY", "").strip()
        if env:
            return env.encode("utf-8")
        if _SECRET_KEY_CACHE is None:
            _SECRET_KEY_CACHE = secrets.token_hex(32)
        if not _SECRET_WARNING_SHOWN:
            _SECRET_WARNING_SHOWN = True
            print("[store][警告] 未设置环境变量 SECRET_KEY，已临时随机生成会话签名密钥；"
                  "进程重启后所有已登录的管理会话都会失效。生产环境请显式设置 SECRET_KEY。")
        return _SECRET_KEY_CACHE.encode("utf-8")


def _sign(payload: str) -> str:
    return hmac.new(_secret_key(), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def make_token(ttl_sec: int = TOKEN_TTL_SEC) -> str:
    """签名会话 token：base64url(payload).hex(hmac)。payload 里带过期时间戳。"""
    import base64

    exp = int(time.time()) + max(60, _to_int(ttl_sec, TOKEN_TTL_SEC))
    payload = json.dumps({"exp": exp, "n": secrets.token_hex(8)}, separators=(",", ":"))
    blob = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{blob}.{_sign(blob)}"


def check_token(token: Any) -> bool:
    """验签 + 验过期。任何异常形状都返回 False，不抛错。"""
    import base64

    if not isinstance(token, str) or "." not in token:
        return False
    blob, _, sig = token.rpartition(".")
    if not blob or not sig:
        return False
    if not hmac.compare_digest(sig, _sign(blob)):
        return False
    try:
        padded = blob + "=" * (-len(blob) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        exp = int(data["exp"])
    except (ValueError, TypeError, KeyError, UnicodeDecodeError):
        return False
    return exp > int(time.time())


# --------------------------------------------------------------------------- #
# 限流
# --------------------------------------------------------------------------- #

def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _normalize_ip(ip: Any) -> str:
    text = str(ip or "").strip()
    return text or "unknown"


def _addr_key(addr: Any) -> str:
    """收件邮箱归一化后再计数，否则 a+b@gmail.com 和 ab@gmail.com 会绕过邮箱维度。
    只折叠 Gmail/Googlemail 的点号与 + 别名，并且保留域名大小写不敏感；
    不做全局去点（很多自建域的点号是有意义的）。"""
    text = str(addr or "").strip().lower()
    local, sep, domain = text.partition("@")
    if not sep:
        return text
    if domain in ("gmail.com", "googlemail.com"):
        local = local.split("+", 1)[0].replace(".", "")
    else:
        local = local.split("+", 1)[0]
    return f"{local}@{domain}"


def _load_state() -> dict[str, Any]:
    """读限流状态；日期一变就丢弃当日计数重来。

    过期时刻意保留 last_send_ts / ip_last 这两个「瞬时」量：它们是间隔判断而不是当日
    配额，否则攻击者只要等到零点整点就能把最小间隔一起抹平，连续抢发。
    """
    raw = _read_json(rate_path()) or {}
    if not isinstance(raw, dict):
        raw = {}
    day = _today()
    same_day = raw.get("date") == day
    ip_last_raw = raw.get("ip_last") if isinstance(raw.get("ip_last"), dict) else {}
    return {
        "date": day,
        "ip": raw.get("ip") if same_day and isinstance(raw.get("ip"), dict) else {},
        "to": raw.get("to") if same_day and isinstance(raw.get("to"), dict) else {},
        "total": _to_int(raw.get("total"), 0) if same_day else 0,
        "last_send_ts": _to_float(raw.get("last_send_ts"), 0.0),
        # 每个 IP 各自的上一次发信时刻（跨日保留）
        "ip_last": {str(k): _to_float(v, 0.0) for k, v in ip_last_raw.items()},
    }


def allow_send(ip: Any, to_addr: Any, cfg: dict[str, Any] | None = None) -> tuple[bool, str]:
    """三维限流：按 IP 当日 / 按收件邮箱当日 / 全局当日总量，外加两级最小间隔。

    返回 (是否放行, 人话原因)。放行即占用额度并落盘 —— 不需要「发送成功后再记一笔」，
    发信失败也一样消耗额度，这是刻意的（见模块头第 5 条）。
    """
    merged = cfg if cfg is not None else _read_settings()
    ip_key = _normalize_ip(ip)
    to_key = _addr_key(to_addr)
    ip_limit = int(merged.get("mail_ip_daily_limit", DEFAULTS["mail_ip_daily_limit"]))
    total_limit = int(merged.get("mail_daily_limit", DEFAULTS["mail_daily_limit"]))
    min_interval = _to_float(merged.get("mail_min_interval_sec"), 0.0)
    now = time.time()

    with _LOCK_RATE:
        state = _load_state()

        if state["total"] >= total_limit:
            return False, f"今日全站发信已达上限（{total_limit} 封），请明天再试或调高 mail_daily_limit。"

        ip_count = _to_int(state["ip"].get(ip_key), 0)
        if ip_count >= ip_limit:
            return False, f"该来源 IP 今日发送次数已达上限（{ip_limit} 次）。"

        if to_key:
            to_count = _to_int(state["to"].get(to_key), 0)
            if to_count >= TO_DAILY_LIMIT_DEFAULT:
                return False, f"该收件邮箱今日已达接收上限（{TO_DAILY_LIMIT_DEFAULT} 封）。"

        # 按 IP 的最小间隔：读的是这个 IP 自己的上一次时刻，不是全站时刻
        last_ip_ts = _to_float(state["ip_last"].get(ip_key), 0.0)
        if min_interval > 0 and last_ip_ts and now - last_ip_ts < min_interval:
            wait = min_interval - (now - last_ip_ts)
            return False, f"同一来源发送太快，请 {max(1, int(wait))} 秒后再试。"

        # 全站最小间隔：挡住「一批 IP 各发一封」把配额瞬间打光
        g_interval = global_min_send_interval()
        if g_interval > 0 and state["last_send_ts"] and now - state["last_send_ts"] < g_interval:
            wait = g_interval - (now - state["last_send_ts"])
            return False, f"服务器整体发信过于频繁，请 {max(1, int(wait))} 秒后再试。"

        state["ip"][ip_key] = ip_count + 1
        if to_key:
            state["to"][to_key] = _to_int(state["to"].get(to_key), 0) + 1
        state["total"] += 1
        state["last_send_ts"] = now
        state["ip_last"][ip_key] = now
        _atomic_write_json(rate_path(), state)
        return True, ""


def rate_snapshot(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """当前计数概览，供后台展示（只出计数，不出 IP/邮箱列表，避免留下可枚举的收件人清单）。"""
    merged = cfg if cfg is not None else _read_settings()
    with _LOCK_RATE:
        state = _load_state()
    return {
        "date": state["date"],
        "total_used": state["total"],
        "total_limit": int(merged.get("mail_daily_limit", DEFAULTS["mail_daily_limit"])),
        "ip_limit": int(merged.get("mail_ip_daily_limit", DEFAULTS["mail_ip_daily_limit"])),
        "to_limit": TO_DAILY_LIMIT_DEFAULT,
        "tracked_ip_count": len(state["ip"]),
        "tracked_to_count": len(state["to"]),
    }


def reset_rate_state() -> None:
    """清空当日计数与间隔时间戳（测试与后台应急用）。"""
    with _LOCK_RATE:
        _atomic_write_json(rate_path(), {"date": _today(), "ip": {}, "to": {}, "total": 0,
                                         "last_send_ts": 0, "ip_last": {}})
