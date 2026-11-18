"""页面路由：服务端渲染，无构建链。"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..deps import STATIC_DIR, get_db, render
from ..models import Bookmark, Category, ImportJob, Tag
from ..services import backup as bk
from ..services import taxonomy

router = APIRouter(tags=["pages"])


def _sidebar(db: Session, public_only: bool = False) -> dict:
    tree = taxonomy.build_tree(db, public_only=public_only)
    stmt = select(func.count(Bookmark.id))
    if public_only:
        stmt = stmt.where(Bookmark.is_private.is_(False))
    total = db.execute(stmt).scalar_one()
    uncat_stmt = select(func.count(Bookmark.id)).where(Bookmark.category_id.is_(None))
    if public_only:
        uncat_stmt = uncat_stmt.where(Bookmark.is_private.is_(False))
    uncategorized = db.execute(uncat_stmt).scalar_one()
    tag_stmt = (
        select(Tag.name, func.count(Bookmark.id))
        # 内连接：批量去标签留下的孤儿行不该再挂在侧栏里显示 0
        .join(Tag.bookmarks)
    )
    if public_only:
        tag_stmt = tag_stmt.where(Bookmark.is_private.is_(False))
    tags = db.execute(
        tag_stmt.group_by(Tag.name)
        .order_by(func.count(Bookmark.id).desc(), Tag.name)
        .limit(40)
    ).all()
    return {
        "tree": tree,
        "flat": taxonomy.flatten(tree),
        "uncategorized": uncategorized,
        "total": total,
        "tags": [{"name": n, "count": c} for n, c in tags],
    }


def _is_anonymous(request: Request) -> bool:
    return getattr(request.state, "user", None) is None


def _base_ctx(db: Session, active: str, public_only: bool = False) -> dict:
    ctx = _sidebar(db, public_only=public_only)
    ctx["active"] = active
    return ctx


@router.get("/")
def index(request: Request, category_id: str = "all", tag: str = "", q: str = "",
          db: Session = Depends(get_db)):
    anon = _is_anonymous(request)
    stmt = select(Bookmark)
    if anon:
        # 游客只看得见公开书签
        stmt = stmt.where(Bookmark.is_private.is_(False))
    if category_id and category_id != "all":
        if category_id == "none":
            stmt = stmt.where(Bookmark.category_id.is_(None))
        else:
            try:
                cid = int(category_id)
                stmt = stmt.where(Bookmark.category_id == cid)
            except ValueError:
                category_id = "all"
    if tag:
        stmt = stmt.join(Bookmark.tags).where(Tag.name == tag)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(Bookmark.title.ilike(like) | Bookmark.url.ilike(like) |
                          Bookmark.domain.ilike(like) | Bookmark.description.ilike(like))
    stmt = stmt.order_by(Bookmark.sort_order, Bookmark.id)
    rows = db.execute(stmt).scalars().all()

    cat_name = "全部书签"
    if category_id == "none":
        cat_name = "未分类"
    elif category_id != "all":
        cat = db.get(Category, int(category_id))
        cat_name = cat.name if cat else "全部书签"

    ctx = _base_ctx(db, "nav", public_only=anon)
    ctx.update({"bookmarks": rows, "category_id": category_id,
                "tag": tag, "q": q, "cat_name": cat_name, "anonymous": anon})
    return render(request, "index.html", **ctx)


@router.get("/manage")
def manage(request: Request, category_id: str = "all", tag: str = "", q: str = "",
           db: Session = Depends(get_db)):
    flat = taxonomy.flatten(taxonomy.build_tree(db))
    stmt = select(Bookmark)
    if category_id and category_id != "all":
        if category_id == "none":
            stmt = stmt.where(Bookmark.category_id.is_(None))
        else:
            try:
                stmt = stmt.where(Bookmark.category_id == int(category_id))
            except ValueError:
                category_id = "all"
    if tag:
        stmt = stmt.join(Bookmark.tags).where(Tag.name == tag)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(Bookmark.title.ilike(like) | Bookmark.url.ilike(like) |
                          Bookmark.domain.ilike(like) | Bookmark.description.ilike(like))
    stmt = stmt.order_by(Bookmark.category_id.is_(None), Bookmark.sort_order, Bookmark.id)
    rows = db.execute(stmt).scalars().all()

    ctx = _base_ctx(db, "manage")
    ctx.update({"bookmarks": rows, "flat_categories": flat,
                "category_id": category_id, "tag": tag, "q": q})
    return render(request, "manage.html", **ctx)


@router.get("/import")
def import_page(request: Request, db: Session = Depends(get_db)):
    jobs = db.execute(select(ImportJob).order_by(ImportJob.id.desc()).limit(10)).scalars().all()
    ctx = _base_ctx(db, "import")
    ctx.update({"jobs": jobs})
    return render(request, "import.html", **ctx)


@router.get("/backup")
def backup_page(request: Request, db: Session = Depends(get_db)):
    ctx = _base_ctx(db, "backup")
    ctx.update({"backups": bk.list_backups()})
    return render(request, "backup.html", **ctx)


@router.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "app": "bookmark-app"}


# ---------- PWA ----------
# manifest 和 SW 都要能被"未登录"的浏览器取到：manifest 与图标的抓取不带 cookie，
# SW 脚本的请求又必须直接 200（不能吃 303 跳转），所以这三个路径在门禁里放行。


@router.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    return FileResponse(
        STATIC_DIR / "manifest.webmanifest",
        media_type="application/manifest+json",
    )


@router.get("/sw.js", include_in_schema=False)
def service_worker():
    return FileResponse(
        STATIC_DIR / "sw.js",
        media_type="text/javascript",
        # SW 放在 /static 之外，用这个头把控制范围放到整站
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


@router.get("/apple-touch-icon.png", include_in_schema=False)
def apple_touch_icon():
    """iOS 默认会来这儿找主屏图标。"""
    return FileResponse(STATIC_DIR / "icon-180.png", media_type="image/png")
