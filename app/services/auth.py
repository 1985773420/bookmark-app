"""登录鉴权：scrypt 口令哈希 + HMAC 签名的会话 cookie。

只维护一个账号，凭据落在 Setting 表里 —— 这样跟着 bookmark.db 一起进备份包，
换机器或恢复备份后登录状态仍然有效，不需要额外同步密钥文件。
"""

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Setting

COOKIE_NAME = "bm_session"
SESSION_DAYS = 30

_KEY_USER = "auth.username"
_KEY_HASH = "auth.password_hash"
_KEY_SALT = "auth.salt"
_KEY_SECRET = "auth.secret"
_KEY_GEN = "auth.gen"

SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1

# 签名密钥缓存：每个请求都要验签，没必要每次都回表
_secret_cache: str | None = None


# ---------- Setting 读写 ----------


def _get(db: Session, key: str) -> str | None:
    row = db.get(Setting, key)
    return row.value if row else None


def _set(db: Session, key: str, value: str) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value


# ---------- 口令哈希 ----------


def _hash(password: str, salt: str) -> str:
    dk = hashlib.scrypt(
        password.encode("utf-8"), salt=salt.encode("utf-8"),
        n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32,
    )
    return base64.b64encode(dk).decode("ascii")


def verify_password(db: Session, password: str) -> bool:
    """口令比对。哈希与盐都对不上就算失败，用 compare_digest 防时序侧信道。"""
    salt = _get(db, _KEY_SALT)
    stored = _get(db, _KEY_HASH)
    if not salt or not stored:
        return False
    return hmac.compare_digest(_hash(password, salt), stored)


def username(db: Session) -> str:
    return _get(db, _KEY_USER) or "admin"


def set_password(db: Session, password: str) -> None:
    salt = secrets.token_hex(16)
    _set(db, _KEY_SALT, salt)
    _set(db, _KEY_HASH, _hash(password, salt))
    # 换密码同时换会话代号：token 是无状态的，不作废代号的话，
    # 改密前漏出去的 cookie 还能一直用到过期（最长 30 天）。
    _set(db, _KEY_GEN, secrets.token_hex(8))
    db.commit()


def set_username(db: Session, name: str) -> None:
    _set(db, _KEY_USER, name.strip() or "admin")
    db.commit()


def _secret(db: Session) -> str:
    global _secret_cache
    if _secret_cache is None:
        val = _get(db, _KEY_SECRET)
        if not val:
            val = secrets.token_hex(32)
            _set(db, _KEY_SECRET, val)
            db.commit()
        _secret_cache = val
    return _secret_cache


def forget_secret() -> None:
    """数据库被整体换掉（恢复备份）后必须调用，否则还拿旧库的密钥验签。"""
    global _secret_cache
    _secret_cache = None


def _gen(db: Session) -> str:
    """会话代号。改密码时会换一个，旧 token 一并作废。老库没这个键就补一个。"""
    val = _get(db, _KEY_GEN)
    if not val:
        val = secrets.token_hex(8)
        _set(db, _KEY_GEN, val)
        db.commit()
    return val


# ---------- 会话 cookie ----------


def make_token(db: Session, user: str) -> str:
    exp = int((datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).timestamp())
    payload = f"{user}|{exp}|{_gen(db)}"
    sig = hmac.new(_secret(db).encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return base64.urlsafe_b64encode(f"{payload}|{sig}".encode()).decode().rstrip("=")


def read_token(db: Session, token: str | None) -> str | None:
    """验签并检查过期，任何异常都当作未登录。"""
    if not token:
        return None
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
        user, exp_s, gen, sig = raw.rsplit("|", 3)
        expect = hmac.new(
            _secret(db).encode(), f"{user}|{exp_s}|{gen}".encode(), hashlib.sha256
        ).hexdigest()[:32]
        if not hmac.compare_digest(sig, expect):
            return None
        # 代号对不上说明密码改过了，这枚 token 是改密前发的
        if not hmac.compare_digest(gen, _gen(db)):
            return None
        if int(exp_s) < datetime.now(timezone.utc).timestamp():
            return None
        return user
    except (ValueError, UnicodeDecodeError, TypeError):
        return None


def cookie_secure(request: Request) -> bool:
    """只有走 https 时才加 Secure，否则 http 访问会拿不到 cookie 登不进去。"""
    return request.url.scheme == "https"


def ensure_default_credentials(db: Session, default_user: str = "admin") -> str | None:
    """首次启动时建默认账号，返回明文口令（已存在则返回 None）。"""
    if _get(db, _KEY_HASH):
        return None
    password = secrets.token_urlsafe(12)
    set_username(db, default_user)
    set_password(db, password)
    return password
