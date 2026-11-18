"""书签 CRUD、排序、批量操作、元数据刷新。"""

import asyncio
import time
from collections import OrderedDict
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import config
from ..db import SessionLocal
from ..deps import get_db
from ..models import Bookmark, Tag
from ..schemas import BookmarkIn, BookmarkPatch, BookmarkReorder, BulkAction
from ..services import domains as dom
from ..services import fetcher
from ..services.importer import get_or_create_tags

router = APIRouter(prefix="/api", tags=["bookmarks"])

_SORTS = {
    "default": (Bookmark.sort_order, Bookmark.id),
    "created_desc": (Bookmark.created_at.desc(), Bookmark.id.desc()),
    "created_asc": (Bookmark.created_at, Bookmark.id),
    "title": (func.lower(Bookmark.title), Bookmark.id),
    "domain": (Bookmark.domain, Bookmark.id),
    "status": (Bookmark.status_code.is_(None), Bookmark.status_code),
}

# 撤销删除：删掉的那一行先在这儿躺几分钟，前端 toast 上的「撤销」能原样插回去
# （连 id 和 sort_order 都不变，位置跟没删过一样）。只活在进程内存里，
# 重启就没了——撤销窗口本来也只有几秒，不值得为它加一张表。
_UNDO: "OrderedDict[int, dict]" = OrderedDict()
_UNDO_TTL = 600.0
_UNDO_MAX = 40
_UNDO_FIELDS = (
    "id", "url", "url_lan", "domain", "title", "description", "favicon_path", "category_id",
    "sort_order", "status_code", "last_checked_at", "is_private", "fetch_error",
    "created_at", "updated_at",
)


def _remember(b: Bookmark) -> None:
    snap = {f: getattr(b, f) for f in _UNDO_FIELDS}
    snap["tags"] = [t.name for t in b.tags]
    snap["_at"] = time.time()
    _UNDO.pop(b.id, None)
    _UNDO[b.id] = snap
    while len(_UNDO) > _UNDO_MAX:
        _UNDO.popitem(last=False)



def _serialize(b: Bookmark) -> dict:
    return {
        "id": b.id,
        "url": b.url,
        "url_lan": b.url_lan,
        "domain": b.domain,
        "title": b.title,
        "description": b.description,
        "favicon_path": b.favicon_path,
        "category_id": b.category_id,
        "sort_order": b.sort_order,
        "status_code": b.status_code,
        "last_checked_at": b.last_checked_at,
        "is_private": b.is_private,
        "fetch_error": b.fetch_error,
        "created_at": b.created_at,
        "updated_at": b.updated_at,
        "tags": [{"id": t.id, "name": t.name} for t in b.tags],
    }


@router.get("/bookmarks")
def list_bookmarks(
    db: Session = Depends(get_db),
    category_id: str | None = Query(None, description="数字为分类 id，'none' 表示未分类，'all' 表示全部"),
    include_children: bool = False,
    tag: str | None = None,
    q: str | None = None,
    sort: str = "default",
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
) -> dict:
    stmt = select(Bookmark)

    if category_id and category_id != "all":
        if category_id == "none":
            stmt = stmt.where(Bookmark.category_id.is_(None))
        else:
            try:
                cid = int(category_id)
            except ValueError:
                raise HTTPException(400, "category_id 需要是数字、'none' 或 'all'") from None
            if include_children:
                from ..services import taxonomy

                stmt = stmt.where(Bookmark.category_id.in_(taxonomy.descendants(db, cid) or [cid]))
            else:
                stmt = stmt.where(Bookmark.category_id == cid)

    if tag:
        stmt = stmt.join(Bookmark.tags).where(Tag.name == tag)

    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Bookmark.title.ilike(like),
                Bookmark.url.ilike(like),
                Bookmark.domain.ilike(like),
                Bookmark.description.ilike(like),
                Bookmark.url_lan.ilike(like),
            )
        )

    stmt = stmt.order_by(*_SORTS.get(sort, _SORTS["default"]))
    total = db.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    ).scalar_one()
    rows = db.execute(stmt.limit(limit).offset(offset)).scalars().all()
    return {"total": total, "items": [_serialize(b) for b in rows]}


@router.get("/bookmarks/{bm_id}")
def get_bookmark(bm_id: int, db: Session = Depends(get_db)) -> dict:
    b = db.get(Bookmark, bm_id)
    if b is None:
        raise HTTPException(404, "书签不存在")
    return _serialize(b)


@router.post("/bookmarks", status_code=201)
async def create_bookmark(payload: BookmarkIn, db: Session = Depends(get_db)) -> dict:
    norm = dom.normalize(payload.url)
    if norm is None:
        raise HTTPException(400, "URL 无法识别，请检查是否是合法域名")
    url, domain = norm
    try:
        url_lan = dom.normalize_lan(payload.url_lan)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None

    if db.execute(select(Bookmark).where(Bookmark.url == url)).scalars().first():
        raise HTTPException(409, "这个网址已经收藏过了")

    title = payload.title
    description = payload.description
    favicon_path = None
    status_code = None
    fetch_error = None

    # 抓取是外部 IO，最坏要几秒。这里给一个硬性预算：拿不到就把标题落到域名，
    # 把图标交给后台任务补——用户点「保存」后最多等 FETCH_BUDGET 秒就能拿到响应。
    if payload.fetch:
        try:
            async with fetcher.client_for(url) as client:
                info = await asyncio.wait_for(
                    _grab(client, url, domain), timeout=config.FETCH_BUDGET
                )
        except (asyncio.TimeoutError, TimeoutError):
            info = None
            fetch_error = "抓取超时"
        if info is not None:
            title = title or info.title
            description = description or info.description
            status_code = info.status_code
            fetch_error = info.error or fetch_error
            favicon_path = info.favicon_path

    bm = Bookmark(
        url=url,
        url_lan=url_lan,
        domain=domain,
        title=(title or dom.display_title(url, None))[:512],
        description=description,
        favicon_path=favicon_path,
        category_id=payload.category_id,
        status_code=status_code,
        last_checked_at=datetime.now(timezone.utc) if status_code else None,
        is_private=payload.is_private,
        fetch_error=(fetch_error or "")[:255] or None,
    )
    bm.tags = get_or_create_tags(db, payload.tags)
    db.add(bm)
    db.commit()
    db.refresh(bm)
    bm_id = bm.id

    # 没拿到图标的（超时 / 站点没声明）丢后台补，不挡这次请求。
    if payload.fetch and not favicon_path:
        _schedule_icon_fill(bm_id, url, domain)
    return _serialize(bm)


async def _grab(client, url: str, domain: str) -> fetcher.PageInfo:
    """抓页面 + 图标，图标预算独立于页面预算。"""
    info = await fetcher.fetch_page(client, url)
    if not info.favicon_path and info.icon_candidates:
        try:
            got = await fetcher.resolve_icon(client, domain, info.icon_candidates)
            if got:
                info.favicon_path = got[0]
        except Exception:
            pass
    return info


# 后台补图标的并发上限：新增书签是单人操作，2 个足够，且不跟用户下一次操作抢代理
_ICON_FILL_SEM = asyncio.Semaphore(2)
_BG_TASKS: set[asyncio.Task] = set()


def _schedule_icon_fill(bm_id: int, url: str, domain: str) -> None:
    task = asyncio.create_task(_fill_icon(bm_id, url, domain))
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)


async def _fill_icon(bm_id: int, url: str, domain: str) -> None:
    """后台补图标：成功就写库，前端下次刷新就能看到。"""
    async with _ICON_FILL_SEM:
        try:
            async with fetcher.client_for(url) as client:
                got = await fetcher.resolve_icon(
                    client,
                    domain,
                    [f"{fetcher._root_of(url)}/favicon.ico"]
                    + [tpl.format(domain=domain) for tpl in config.FALLBACK_ICON_URLS],
                )
        except Exception:
            return
    if not got:
        return
    db = SessionLocal()
    try:
        b = db.get(Bookmark, bm_id)
        if b is not None and not b.favicon_path:
            b.favicon_path = got[0]
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


@router.patch("/bookmarks/{bm_id}")
async def update_bookmark(bm_id: int, payload: BookmarkPatch, db: Session = Depends(get_db)) -> dict:
    b = db.get(Bookmark, bm_id)
    if b is None:
        raise HTTPException(404, "书签不存在")
    data = payload.model_dump(exclude_unset=True)

    # 改外网地址：重新归一化、查重、重算 domain，并且换地址后标题/图标基本失效，
    # 除了调用方显式给了标题，否则重新抓一遍。
    refetch = False
    if "url" in data:
        raw_url = data.pop("url")
        norm = dom.normalize(raw_url or "")
        if norm is None:
            raise HTTPException(400, "URL 无法识别，请检查是否是合法域名")
        new_url, new_domain = norm
        if new_url == b.url:
            pass
        elif db.execute(
            select(Bookmark).where(Bookmark.url == new_url, Bookmark.id != b.id)
        ).scalars().first():
            raise HTTPException(409, "这个网址已经收藏过了")
        else:
            if b.url_lan and dom.domain_of(new_url) == dom.domain_of(b.url):
                # 同一个站点只换了协议/端口，内网地址大概率不用跟着变
                pass
            b.url = new_url
            b.domain = new_domain
            refetch = "title" not in data

    if "url_lan" in data:
        try:
            data["url_lan"] = dom.normalize_lan(data["url_lan"])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
    if "tags" in data and data["tags"] is not None:
        b.tags = get_or_create_tags(db, data.pop("tags"))
    for key, val in data.items():
        setattr(b, key, val)
    if refetch:
        try:
            async with fetcher.client_for(b.url) as client:
                info = await asyncio.wait_for(
                    _grab(client, b.url, b.domain), timeout=config.FETCH_BUDGET
                )
        except (asyncio.TimeoutError, TimeoutError):
            info = None
            b.fetch_error = "抓取超时"
        except Exception as exc:  # 抓取失败不该让整个保存失败
            info = None
            b.fetch_error = str(exc)[:255]
        if info is not None:
            if info.title:
                b.title = info.title[:512]
            if info.description:
                b.description = info.description
            if info.error:
                b.fetch_error = info.error[:255]
            if info.favicon_path:
                b.favicon_path = info.favicon_path
    db.commit()
    db.refresh(b)
    return _serialize(b)


@router.delete("/bookmarks/{bm_id}")
def delete_bookmark(bm_id: int, db: Session = Depends(get_db)) -> dict:
    b = db.get(Bookmark, bm_id)
    if b is None:
        raise HTTPException(404, "书签不存在")
    _remember(b)
    db.delete(b)
    db.commit()
    return {"deleted": bm_id, "undo": True}


@router.post("/bookmarks/{bm_id}/restore")
def restore_bookmark(bm_id: int, db: Session = Depends(get_db)) -> dict:
    """把刚删掉的书签放回去：id / 分类 / 排序 / 图标全都沿用删除前的值。"""
    snap = _UNDO.pop(bm_id, None)
    if snap is None or time.time() - snap["_at"] > _UNDO_TTL:
        raise HTTPException(410, "撤销窗口已过，删掉的书签找不回来了")
    if db.get(Bookmark, bm_id) is not None:
        raise HTTPException(409, "这个 id 已经被占用了")
    tag_names = snap.pop("tags")
    snap.pop("_at", None)
    b = Bookmark(**snap)
    b.tags = get_or_create_tags(db, tag_names)
    db.add(b)
    db.commit()
    db.refresh(b)
    return _serialize(b)


@router.put("/bookmarks/reorder")
def reorder(payload: BookmarkReorder, db: Session = Depends(get_db)) -> dict:
    """拖拽后一次性提交顺序。

    只动 sort_order，不碰分类：拖拽目前只用于卡片排序，而"全部书签"页拿不到
    具体分类，早期版本会顺手把 category_id 写成 None，等于把整页书签清空归类。
    改分类走 /bulk 的 move 或右键菜单。
    """
    rows = db.execute(
        select(Bookmark).where(Bookmark.id.in_(payload.ordered_ids))
    ).scalars().all()
    by_id = {r.id: r for r in rows}
    for i, bid in enumerate(payload.ordered_ids):
        b = by_id.get(bid)
        if b is None:
            continue
        b.sort_order = i
    db.commit()
    return {"updated": len(by_id)}


@router.post("/bookmarks/bulk")
def bulk(payload: BulkAction, db: Session = Depends(get_db)) -> dict:
    rows = db.execute(select(Bookmark).where(Bookmark.id.in_(payload.ids))).scalars().all()
    if not rows:
        return {"affected": 0, "action": payload.action}

    if payload.action == "delete":
        for b in rows:
            _remember(b)
            db.delete(b)
        affected = len(rows)
    elif payload.action == "move":
        for b in rows:
            b.category_id = payload.category_id
        affected = len(rows)
    elif payload.action in ("private", "public"):
        # 公开 / 私有批量切换：游客只看得到非私有的那些
        want = payload.action == "private"
        for b in rows:
            b.is_private = want
        affected = len(rows)
    elif payload.action == "lan_clear":
        # 只清内网地址，书签本身和 url 不动
        for b in rows:
            b.url_lan = None
        affected = len(rows)
    elif payload.action in ("tag_add", "tag_remove"):
        tags = get_or_create_tags(db, payload.tags)
        for b in rows:
            current = {t.name: t for t in b.tags}
            if payload.action == "tag_add":
                for t in tags:
                    current.setdefault(t.name, t)
            else:
                for t in tags:
                    current.pop(t.name, None)
            b.tags = list(current.values())
        affected = len(rows)
    else:
        raise HTTPException(400, f"不支持的操作 {payload.action}")

    db.commit()
    return {"affected": affected, "action": payload.action}


@router.post("/bookmarks/refresh")
async def refresh(payload: dict, db: Session = Depends(get_db)) -> dict:
    """重新抓标题/描述/图标，可选顺带探测死活。"""
    ids = [int(i) for i in (payload.get("ids") or [])]
    only_missing = bool(payload.get("only_missing_icons"))
    check_status = bool(payload.get("check_status", True))

    stmt = select(Bookmark)
    if ids:
        stmt = stmt.where(Bookmark.id.in_(ids))
    if only_missing:
        stmt = stmt.where(Bookmark.favicon_path.is_(None))
    rows = db.execute(stmt).scalars().all()
    if not rows:
        return {"checked": 0, "updated": 0, "icons_found": 0}

    updated = icons_found = 0
    # 原来是一条一条串行抓：25 条 × 每条最坏 20s = 「刷新元数据」能转好几分钟。
    # 现在按 FETCH_CONCURRENCY 并发，并给每条一个硬预算，整批耗时约等于最慢的那一条。
    sem = asyncio.Semaphore(config.FETCH_CONCURRENCY)

    async def one(client: httpx.AsyncClient, lan_client: httpx.AsyncClient, b: Bookmark) -> None:
        nonlocal updated, icons_found
        async with sem:
            try:
                info = await asyncio.wait_for(
                    _grab(lan_client if fetcher.is_internal(b.url) else client, b.url, b.domain),
                    timeout=config.FETCH_BUDGET,
                )
            except (asyncio.TimeoutError, TimeoutError):
                b.fetch_error = "抓取超时"
                return
            except Exception as exc:
                b.fetch_error = str(exc)[:255]
                return
        if info.title and (not b.title or b.title == b.domain):
            b.title = info.title[:512]
            updated += 1
        if info.description and not b.description:
            b.description = info.description
        if check_status:
            b.status_code = info.status_code
            b.last_checked_at = datetime.now(timezone.utc)
        b.fetch_error = (info.error or "")[:255] or None
        if info.favicon_path:
            b.favicon_path = info.favicon_path
            icons_found += 1

    async with fetcher.make_client() as client, fetcher.make_client(proxy=None) as lan_client:
        await asyncio.gather(*(one(client, lan_client, b) for b in rows), return_exceptions=True)
    db.commit()
    return {"checked": len(rows), "updated": updated, "icons_found": icons_found}


@router.get("/tags")
def list_tags(db: Session = Depends(get_db)) -> list[dict]:
    """标签及各自的书签数。

    用内连接：批量去标签会在 tag 表里留下没有书签的孤儿行，外连接会让它们
    以「0」的形式一直挂在侧栏里。
    """
    rows = db.execute(
        select(Tag.name, func.count(Bookmark.id))
        .join(Tag.bookmarks)
        .group_by(Tag.name)
        .order_by(func.count(Bookmark.id).desc(), Tag.name)
    ).all()
    return [{"name": n, "count": c} for n, c in rows]


@router.delete("/tags/{name}")
def delete_tag(name: str, db: Session = Depends(get_db)) -> dict:
    tag = db.execute(select(Tag).where(Tag.name == name)).scalars().first()
    if tag is None:
        raise HTTPException(404, "标签不存在")
    db.delete(tag)
    db.commit()
    return {"deleted": name}


@router.get("/stats")
def stats(db: Session = Depends(get_db)) -> dict:
    from ..models import Category

    return {
        "bookmarks": db.execute(select(func.count(Bookmark.id))).scalar_one(),
        "categories": db.execute(select(func.count(Category.id))).scalar_one(),
        # 只数真正挂在书签上的标签，孤儿行不算（同 /tags 的口径）
        "tags": db.execute(
            select(func.count(func.distinct(Tag.id))).select_from(Tag).join(Tag.bookmarks)
        ).scalar_one(),
        "without_icon": db.execute(
            select(func.count(Bookmark.id)).where(Bookmark.favicon_path.is_(None))
        ).scalar_one(),
        "dead": db.execute(
            select(func.count(Bookmark.id)).where(
                Bookmark.status_code.is_not(None), Bookmark.status_code >= 400
            )
        ).scalar_one(),
    }
