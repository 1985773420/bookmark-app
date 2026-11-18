"""备份与导出。

备份包是一个 zip，同时塞三种东西，各管一种恢复场景：
  bookmark.db        —— 原样恢复（用 sqlite 在线备份 API 取一致快照，避开 WAL 撕裂）
  bookmarks.json     —— 跨程序迁移、脚本处理
  bookmarks.html     —— Netscape 格式，能直接导进任何浏览器或别的书签程序
  favicons/          —— 图标原文件，避免恢复后重新出网抓一遍
"""

import io
import json
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import config
from ..models import Bookmark, Category
from . import domains, taxonomy

MANIFEST = "manifest.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def snapshot_db(dest: Path) -> None:
    """用 sqlite 在线备份 API 取一致快照（WAL 模式下直接拷文件可能拿到撕裂的库）。"""
    src = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(dest)
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def export_json(db: Session) -> dict:
    cats = db.execute(select(Category).order_by(Category.id)).scalars().all()
    bms = db.execute(
        select(Bookmark).options(selectinload(Bookmark.tags)).order_by(Bookmark.id)
    ).scalars().all()
    return {
        "format": "bookmark-app/v1",
        "exported_at": _now().isoformat(),
        "categories": [
            {"id": c.id, "name": c.name, "parent_id": c.parent_id, "icon": c.icon,
             "sort_order": c.sort_order}
            for c in cats
        ],
        "bookmarks": [
            {
                "url": b.url,
                "url_lan": b.url_lan,
                "domain": b.domain,
                "title": b.title,
                "description": b.description,
                "category_id": b.category_id,
                "tags": [t.name for t in b.tags],
                "favicon_path": b.favicon_path,
                "is_private": b.is_private,
                "created_at": b.created_at.isoformat() if b.created_at else None,
            }
            for b in bms
        ],
    }


def export_netscape_html(db: Session) -> str:
    """Netscape 书签格式，浏览器可直接导入。目录层级按分类树还原。"""
    from html import escape

    tree = taxonomy.build_tree(db, with_counts=False)
    by_id = {b.id: b for b in db.execute(select(Bookmark)).scalars().all()}
    bookmarks = db.execute(
        select(Bookmark).options(selectinload(Bookmark.tags)).order_by(Bookmark.sort_order, Bookmark.id)
    ).scalars().all()
    grouped: dict[int | None, list[Bookmark]] = {}
    for b in bookmarks:
        grouped.setdefault(b.category_id, []).append(b)

    out: list[str] = [
        "<!DOCTYPE NETSCAPE-Bookmark-file-1>",
        "<!-- 由书签库导出 -->",
        '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">',
        "<TITLE>Bookmarks</TITLE>",
        "<H1>Bookmarks</H1>",
        "<DL><p>",
    ]

    def emit(node: dict, indent: str) -> None:
        out.append(f'{indent}<DT><H3>{escape(node["name"])}</H3>')
        out.append(f"{indent}<DL><p>")
        for b in grouped.get(node["id"], []):
            out.append(f'{indent}    <DT><A HREF="{escape(b.url, quote=True)}">{escape(b.title or b.url)}</A>')
            if b.url_lan:
                # 内网地址用 TAGS 属性带着走，浏览器本身不认，但再导回本站时能认出来
                out[-1] = out[-1].replace("</A>", f' TAGS="内网:{escape(b.url_lan, quote=True)}"></A>')
            if b.description:
                out.append(f'{indent}    <DD>{escape(b.description)}')
        for child in node["children"]:
            emit(child, indent + "    ")
        out.append(f"{indent}</DL><p>")

    for root in tree:
        emit(root, "    ")

    for b in grouped.get(None, []):
        lan_attr = f' TAGS="内网:{escape(b.url_lan, quote=True)}"' if b.url_lan else ""
        out.append(f'    <DT><A HREF="{escape(b.url, quote=True)}"{lan_attr}>{escape(b.title or b.url)}</A>')
    out.append("</DL><p>")
    return "\n".join(out)


def create_backup(db: Session, note: str = "") -> dict:
    config.ensure_dirs()
    name = f"bookmark-{_stamp()}.zip"
    dest = config.BACKUP_DIR / name
    tmp_db = config.BACKUP_DIR / f".snapshot-{_stamp()}.db"

    snapshot_db(tmp_db)
    try:
        payload_json = json.dumps(export_json(db), ensure_ascii=False, indent=2)
        payload_html = export_netscape_html(db)
        manifest = {
            "format": "bookmark-app/v1",
            "created_at": _now().isoformat(),
            "note": note,
            "includes": ["bookmark.db", "bookmarks.json", "bookmarks.html", "favicons/"],
        }
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            zf.write(tmp_db, "bookmark.db")
            zf.writestr("bookmarks.json", payload_json)
            zf.writestr("bookmarks.html", payload_html)
            zf.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
            for icon in sorted(config.FAVICON_DIR.glob("*")):
                if icon.is_file() and not icon.name.endswith(".tmp"):
                    zf.write(icon, f"favicons/{icon.name}")
    finally:
        tmp_db.unlink(missing_ok=True)

    _prune()
    st = dest.stat()
    return {
        "name": name,
        "size": st.st_size,
        "created_at": datetime.fromtimestamp(st.st_mtime, timezone.utc),
        "includes": manifest["includes"],
    }


def _prune(keep: int | None = None) -> int:
    """按保留份数清理旧备份。"""
    keep = keep or config.BACKUP_KEEP
    files = sorted(
        (p for p in config.BACKUP_DIR.glob("bookmark-*.zip") if p.is_file()),
        key=lambda p: p.name,
        reverse=True,
    )
    removed = 0
    for p in files[keep:]:
        p.unlink(missing_ok=True)
        removed += 1
    return removed


def list_backups() -> list[dict]:
    config.ensure_dirs()
    out: list[dict] = []
    for p in sorted(config.BACKUP_DIR.glob("bookmark-*.zip"), reverse=True):
        st = p.stat()
        out.append({
            "name": p.name,
            "size": st.st_size,
            "created_at": datetime.fromtimestamp(st.st_mtime, timezone.utc),
            "includes": _peek_includes(p),
        })
    return out


def _peek_includes(path: Path) -> list[str]:
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
        top: list[str] = []
        for n in names:
            key = "favicons/" if n.startswith("favicons/") else n
            if key not in top:
                top.append(key)
        return top
    except (zipfile.BadZipFile, OSError):
        return []


def backup_path(name: str) -> Path | None:
    """校验备份文件名，挡住目录穿越。"""
    if "/" in name or "\\" in name or not name.startswith("bookmark-") or not name.endswith(".zip"):
        return None
    p = config.BACKUP_DIR / name
    return p if p.is_file() else None


def restore_from_zip(path: Path) -> dict:
    """从备份包恢复。恢复前先把当前库另存一份，防止恢复错了没法回头。"""
    config.ensure_dirs()
    safety = config.BACKUP_DIR / f"pre-restore-{_stamp()}.db"
    if config.DB_PATH.exists():
        snapshot_db(safety)

    icons_restored = 0
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        if "bookmark.db" not in names:
            raise ValueError("备份包里没有 bookmark.db")
        data = zf.read("bookmark.db")

        # 先写临时文件再原子替换，避免中途失败留下半个库
        tmp = config.DB_PATH.with_suffix(".db.restoring")
        tmp.write_bytes(data)
        # 旧的 WAL/SHM 必须一起清掉，否则会和新库对不上
        for suffix in ("-wal", "-shm"):
            side = Path(str(config.DB_PATH) + suffix)
            side.unlink(missing_ok=True)
        tmp.replace(config.DB_PATH)

        for n in names:
            if n.startswith("favicons/") and not n.endswith("/"):
                target = config.FAVICON_DIR / Path(n).name
                target.write_bytes(zf.read(n))
                icons_restored += 1

    return {"db_bytes": len(data), "icons_restored": icons_restored, "safety_copy": safety.name}


def read_manifest(path: Path) -> dict:
    try:
        with zipfile.ZipFile(path) as zf:
            if MANIFEST in zf.namelist():
                return json.loads(zf.read(MANIFEST).decode("utf-8"))
    except (zipfile.BadZipFile, OSError, json.JSONDecodeError):
        pass
    return {}


def import_json(db: Session, payload: dict) -> dict:
    """把 export_json 的产物灌进库（用于跨程序迁移）。

    可以往非空库里导：分类按（父节点, 名称）复用已有的，书签按 URL 去重，
    所以重复导入不会长出一片重复的分类树。
    """
    id_map: dict[int, int] = {}
    created_cats = 0
    reused_cats = 0
    created_bms = 0
    skipped = 0

    # (parent_id, name) -> 已有分类 id，避免重复导入时凭空多出一棵树
    known: dict[tuple[int | None, str], int] = {
        (row.parent_id, row.name): row.id
        for row in db.execute(select(Category)).scalars().all()
    }

    def _land(c: dict, parent_id: int | None) -> None:
        nonlocal created_cats, reused_cats
        name = c.get("name") or "未命名"
        hit = known.get((parent_id, name))
        if hit is not None:
            id_map[c["id"]] = hit
            reused_cats += 1
            return
        cat = Category(
            name=name,
            parent_id=parent_id,
            icon=c.get("icon"),
            sort_order=int(c.get("sort_order") or 0),
        )
        db.add(cat)
        db.flush()
        id_map[c["id"]] = cat.id
        known[(parent_id, name)] = cat.id
        created_cats += 1

    # 父节点可能排在子节点后面，按层级多轮遍历直到全部落位
    pending = list(payload.get("categories") or [])
    while pending:
        progressed = False
        rest: list[dict] = []
        for c in pending:
            pid = c.get("parent_id")
            if pid is not None and pid not in id_map:
                rest.append(c)
                continue
            _land(c, id_map.get(pid) if pid is not None else None)
            progressed = True
        if not progressed:
            # 剩下的都是环，强制挂到顶层，别死循环
            for c in rest:
                _land(c, None)
            break
        pending = rest

    existing = set(db.execute(select(Bookmark.url)).scalars().all())
    for b in payload.get("bookmarks") or []:
        url = b.get("url")
        if not url or url in existing:
            skipped += 1
            continue
        from ..models import Bookmark as BM

        try:
            url_lan = domains.normalize_lan(b.get("url_lan"))
        except ValueError:
            url_lan = None      # 备份里的内网地址坏了不该拖垮整次导入
        bm = BM(
            url=url,
            url_lan=url_lan,
            domain=b.get("domain") or "",
            title=b.get("title"),
            description=b.get("description"),
            category_id=id_map.get(b.get("category_id")),
            is_private=bool(b.get("is_private")),
        )
        bm.tags = _tags_for(db, b.get("tags") or [])
        db.add(bm)
        existing.add(url)
        created_bms += 1

    db.commit()
    return {
        "categories": created_cats,
        "categories_reused": reused_cats,
        "bookmarks": created_bms,
        "skipped": skipped,
    }


def _tags_for(db: Session, names: list[str]):
    from ..models import Tag

    out = []
    for raw in names:
        name = (raw or "").strip()[:64]
        if not name:
            continue
        tag = db.execute(select(Tag).where(Tag.name == name)).scalars().first()
        if tag is None:
            tag = Tag(name=name)
            db.add(tag)
            db.flush()
        out.append(tag)
    return out


def json_bytes(db: Session) -> bytes:
    return json.dumps(export_json(db), ensure_ascii=False, indent=2).encode("utf-8")


def as_stream(db: Session) -> io.BytesIO:
    return io.BytesIO(json_bytes(db))
