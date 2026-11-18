"""模板环境与通用依赖。"""

import hashlib
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from . import config
from .db import SessionLocal

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


def get_db():
    """请求级会话。渲染型路由也用它，所以不做成 Depends 生成器之外的形态。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def render(request: Request, name: str, status_code: int = 200, **ctx):
    # 调用方常顺手把 request 塞进 ctx，这里统一以入参为准，避免重复传参
    ctx.pop("request", None)
    ctx.setdefault("app_name", "书签库")
    # 当前登录账号由鉴权中间件挂在 request.state 上
    ctx.setdefault("user", getattr(request.state, "user", None))
    # 游客视图：页面只读，按钮和私有内容都不渲染
    ctx.setdefault("anonymous", ctx["user"] is None)
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def icon_url(favicon_path: str | None) -> str | None:
    return f"/icons/{favicon_path}" if favicon_path else None


def hue(seed: str | None) -> str:
    """没有图标时按域名派生一个稳定的底色，生成首字母色块。"""
    if not seed:
        return "hsl(215 15% 55%)"
    h = int(hashlib.md5(seed.encode("utf-8")).hexdigest()[:8], 16) % 360
    return f"hsl({h} 52% 52%)"


def display_title(b) -> str:
    """卡片上展示的标题。

    自动抓来的标题经常不能直接上墙：空标题、就是网址本身、抓重定向页面得到的
    "Just a moment..."、或者拼了一把关键词的 "X · Y · X · Y"。这里只修正展示，
    不动数据库里的原值（管理页仍能编辑原始标题）。
    """
    title = (getattr(b, "title", None) or "").strip()
    domain = (getattr(b, "domain", None) or "").strip()
    nice = domain[4:] if domain.startswith("www.") else domain
    url = (getattr(b, "url", None) or "").strip().rstrip("/").lower()
    if not title:
        return nice
    low = title.lower().rstrip("/")
    if "://" in low or low.startswith("www.") or low in {url, f"http://{url[8:]}", f"https://{url[8:]}"}:
        return nice
    if "just a moment" in low or "attention required" in low or "请稍候" in title:
        return nice
    if len(title) >= 60 and " · " in title:
        return title.split(" · ")[0].strip()
    return title


# 静态资源的版本号：取 CSS/JS 里最新的 mtime（容器启动时算一次，重建镜像就变）。
# 模板里写成 /static/app.css?v={{ asset_v }}，改了样式 URL 就跟着变，
# 浏览器缓存自然失效——不需要用户手动强制刷新。
#
# 起因：Starlette 的 StaticFiles 只发 ETag/Last-Modified、不发 Cache-Control，
# 浏览器按「(现在 - 最后修改时间) × 10%」自己猜一个新鲜期。这个库的 mtime 是
# 几天前的，于是被猜成了"几天内不用问服务器"，改完 CSS 重建容器，刷新页面看到的
# 还是旧样式（实测 --stud 是空值、box-shadow 仍是老规则）。
def static_version() -> str:
    stamps = [0.0]
    for name in ("app.css", "fluid.css", "app.js", "studio.css", "studio.js", "sw.js"):
        try:
            stamps.append((STATIC_DIR / name).stat().st_mtime)
        except OSError:
            pass
    return str(int(max(stamps)))


templates.env.globals["asset_v"] = static_version()
templates.env.globals["icon_url"] = icon_url
templates.env.globals["display_title"] = display_title
templates.env.filters["hue"] = hue
