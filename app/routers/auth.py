"""登录、退出、改密码。"""

import time

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..deps import get_db, render
from ..services import auth

router = APIRouter(tags=["auth"])

# 登录失败限速：同一来源 5 分钟内错 8 次就先拒一会儿
_FAIL_WINDOW = 300
_FAIL_LIMIT = 8
_fails: dict[str, list[float]] = {}


def _client_key(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "?")


def _too_many(key: str) -> bool:
    now = time.time()
    hits = [t for t in _fails.get(key, []) if now - t < _FAIL_WINDOW]
    _fails[key] = hits
    return len(hits) >= _FAIL_LIMIT


def _note_fail(key: str) -> None:
    _fails.setdefault(key, []).append(time.time())


def _safe_next(raw: str | None) -> str:
    """只接受站内相对路径，挡掉 //evil.com 这种协议相对跳转。"""
    if not raw or not raw.startswith("/") or raw.startswith("//"):
        return "/"
    return raw


@router.get("/login")
def login_page(request: Request, next: str = "/", db: Session = Depends(get_db)):
    # 已登录就不用再看登录页了
    if auth.read_token(db, request.cookies.get(auth.COOKIE_NAME)):
        return RedirectResponse(_safe_next(next), status_code=303)
    return render(request, "login.html", next=_safe_next(next),
                  username=auth.username(db), error=None)


@router.post("/login")
def do_login(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    next: str = Form("/"),
    db: Session = Depends(get_db),
):
    target = _safe_next(next)
    key = _client_key(request)

    def fail(message: str, status: int = 401):
        return render(request, "login.html", status_code=status,
                      next=target, username=username, error=message)

    if _too_many(key):
        return fail("尝试次数过多，请等几分钟再试", status=429)

    if not username.strip() or not password:
        return fail("用户名和密码都要填")

    ok_user = username.strip() == auth.username(db)
    ok_pass = auth.verify_password(db, password)
    # 用户名和口令分别校验、两个都要算完，避免把"用户名是否存在"探出来
    if not (ok_user and ok_pass):
        _note_fail(key)
        return fail("用户名或密码错误")

    _fails.pop(key, None)
    resp = RedirectResponse(target, status_code=303)
    resp.set_cookie(
        auth.COOKIE_NAME,
        auth.make_token(db, username.strip()),
        max_age=auth.SESSION_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=auth.cookie_secure(request),
        path="/",
    )
    return resp


@router.post("/logout")
def logout() -> RedirectResponse:
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(auth.COOKIE_NAME, path="/")
    return resp


@router.post("/api/auth/password")
def change_password(payload: dict, db: Session = Depends(get_db)) -> JSONResponse:
    """改口令。必须带旧口令；改完当前 cookie 立即作废，要用新口令重登。"""
    old = (payload or {}).get("old_password") or ""
    new = (payload or {}).get("new_password") or ""
    if not auth.verify_password(db, old):
        raise HTTPException(400, "原密码不对")
    if len(new) < 8:
        raise HTTPException(400, "新密码至少 8 位")

    auth.set_password(db, new)
    resp = JSONResponse({"ok": True, "relogin": True})
    resp.delete_cookie(auth.COOKIE_NAME, path="/")
    return resp
