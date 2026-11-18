"""应用入口。

启动时建表、拉起每日自动备份的守护协程；关闭时干净收尾。
路由分成两块：/api/* 走 JSON，页面走服务端渲染的 Jinja2 模板。
"""

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .db import SessionLocal, init_db
from .deps import STATIC_DIR
from .routers import auth as auth_router
from .routers import backup, bookmarks, categories, icons, imports, pages
from .services import auth
from .services import backup as bk

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("bookmark")

_STOP = object()


async def auto_backup_loop() -> None:
    """每天 BACKUP_HOUR 点打一次备份。容器里没有 cron，所以放在应用内。"""
    if not config.BACKUP_ENABLED:
        log.info("自动备份已关闭（BM_BACKUP_ENABLED=0）")
        return
    while True:
        now = datetime.now()
        nxt = now.replace(hour=config.BACKUP_HOUR, minute=0, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        delay = (nxt - now).total_seconds()
        log.info("下次自动备份：%s（%.0f 秒后）", nxt.strftime("%Y-%m-%d %H:%M"), delay)
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        db = SessionLocal()
        try:
            info = bk.create_backup(db, note="自动备份")
            log.info("自动备份完成：%s（%d 字节）", info["name"], info["size"])
        except Exception:
            log.exception("自动备份失败")
        finally:
            db.close()
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_dirs()
    init_db()
    log.info("数据目录 %s", config.DATA_DIR)
    db = SessionLocal()
    try:
        # 首次启动自动建账号，口令只在这一次打印出来；之后只认库里的哈希
        pwd = auth.ensure_default_credentials(db)
        if pwd:
            log.warning("=" * 58)
            log.warning("首次启动，已创建默认账号")
            log.warning("  用户名：%s", auth.username(db))
            log.warning("  密码：%s", pwd)
            log.warning("请登录后尽快用侧栏底部的「改密」换掉密码")
            log.warning("=" * 58)
    finally:
        db.close()
    task = asyncio.create_task(auto_backup_loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="书签库", version="1.0.0", lifespan=lifespan)

# 完全免登录放行的路径：登录页、健康检查、PWA 三件套，以及静态资源（登录页也要用 css/svg）
_PUBLIC_PATHS = {
    "/login",
    "/healthz",
    "/manifest.webmanifest",
    "/sw.js",
    "/apple-touch-icon.png",
}
_PUBLIC_PREFIXES = ("/static/",)

# 游客也能看的路径：首页只展示公开书签，图标同理。其余页面照旧要登录。
_GUEST_PATHS = {"/"}
_GUEST_PREFIXES = ("/icons/",)


def _no_store(response):
    """HTML 响应一律不许缓存。

    页面是按当前账号渲染的，浏览器要是靠启发式规则把它缓存下来，退出登录或者
    换个账号还能从缓存里翻出上一份内容。
    """
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.middleware("http")
async def require_login(request: Request, call_next):
    """全站登录门禁。

    /api/* 未登录返回 401 JSON（前端 fetch 好处理），页面则 303 跳登录页并带上
    next，登录后原路返回。放行路径只有登录页、健康检查和静态资源。

    游客可读的页面（首页和图标）不拦，但 user 必须是 None —— 页面据此只渲染公开书签。
    """
    path = request.url.path
    if path in _PUBLIC_PATHS or path.startswith(_PUBLIC_PREFIXES):
        request.state.user = None
        return _no_store(await call_next(request))

    token = request.cookies.get(auth.COOKIE_NAME)
    user = None
    if token:
        db = SessionLocal()
        try:
            user = auth.read_token(db, token)
        finally:
            db.close()
    if user is None:
        if path in _GUEST_PATHS or path.startswith(_GUEST_PREFIXES):
            request.state.user = None
            return _no_store(await call_next(request))
        if path.startswith("/api/"):
            return JSONResponse({"detail": "未登录或登录已过期"}, status_code=401)
        target = path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)

    # 模板里要显示当前账号，挂到 request 上由 render() 取用
    request.state.user = user
    return _no_store(await call_next(request))


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(auth_router.router)
app.include_router(pages.router)
app.include_router(categories.router)
app.include_router(bookmarks.router)
app.include_router(icons.router)
app.include_router(imports.router)
app.include_router(backup.router)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("未处理异常 %s %s", request.method, request.url.path)
    return JSONResponse({"detail": f"服务器内部错误：{type(exc).__name__}"}, status_code=500)
