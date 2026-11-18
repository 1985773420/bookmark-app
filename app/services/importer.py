"""导入编排：解析结果 → 分类树 → 并发抓取 → 落库，全程写进度。

用 ImportJob 这张表当进度通道：前端轮询 job 就能画进度条，
失败原因落进 error_log，事后能查是哪几条没进来。
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..db import SessionLocal
from ..models import Bookmark, ImportJob, Tag
from . import fetcher, taxonomy
from .domains import display_title
from .parser import ParsedItem

_MAX_ERROR_LINES = 200


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_or_create_tags(db: Session, names: list[str]) -> list[Tag]:
    out: list[Tag] = []
    for raw in names:
        name = raw.strip()[:64]
        if not name:
            continue
        tag = db.execute(select(Tag).where(Tag.name == name)).scalars().first()
        if tag is None:
            tag = Tag(name=name)
            db.add(tag)
            db.flush()
        out.append(tag)
    return out


async def run_import(
    job_id: int,
    items: list[ParsedItem],
    *,
    category_id: int | None = None,
    use_tree: bool = True,
    tags: list[str] | None = None,
    fetch: bool = True,
    skip_existing: bool = True,
) -> None:
    """后台任务入口。自己开 session，因为不依赖请求上下文。"""
    db = SessionLocal()
    try:
        job = db.get(ImportJob, job_id)
        if job is None:
            return
        job.status = "running"
        job.total = len(items)
        db.commit()

        common_tags = get_or_create_tags(db, tags or [])
        existing: set[str] = set(db.execute(select(Bookmark.url)).scalars().all())
        path_cache: dict[tuple, int | None] = {}
        errors: list[str] = []

        chunk = max(1, config.FETCH_CONCURRENCY)
        cancelled = False

        for start in range(0, len(items), chunk):
            db.refresh(job)
            if job.status == "cancelled":
                cancelled = True
                break

            batch = items[start : start + chunk]
            pages: dict[str, fetcher.PageInfo] = {}

            if fetch:
                todo = [it.url for it in batch if not (skip_existing and it.url in existing)]
                if todo:
                    try:
                        pages = await fetcher.fetch_many(todo)
                    except Exception as exc:  # 网络层整体失败不应中断导入
                        errors.append(f"批量抓取失败：{type(exc).__name__}: {exc}"[:300])

            for it in batch:
                job.processed += 1
                if not it.valid:
                    job.failed += 1
                    if len(errors) < _MAX_ERROR_LINES:
                        errors.append(f"{it.url or '空行'} —— {it.reason}")
                    continue
                if skip_existing and it.url in existing:
                    job.duplicate += 1
                    continue

                page = pages.get(it.url)
                try:
                    _insert_one(
                        db, it, page, category_id, use_tree, common_tags, path_cache
                    )
                except Exception as exc:
                    job.failed += 1
                    if len(errors) < _MAX_ERROR_LINES:
                        errors.append(f"{it.url} —— 写库失败 {type(exc).__name__}: {exc}"[:300])
                    db.rollback()
                    continue

                existing.add(it.url)
                job.success += 1

            db.commit()

        job.finished_at = _now()
        job.status = "cancelled" if cancelled else "done"
        job.error_log = "\n".join(errors) if errors else None
        db.commit()
    except Exception as exc:
        db.rollback()
        try:
            job = db.get(ImportJob, job_id)
            if job is not None:
                job.status = "failed"
                job.error_log = f"{type(exc).__name__}: {exc}"[:2000]
                job.finished_at = _now()
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


def _insert_one(
    db: Session,
    it: ParsedItem,
    page: fetcher.PageInfo | None,
    category_id: int | None,
    use_tree: bool,
    common_tags: list[Tag],
    path_cache: dict[tuple, int | None],
) -> None:
    target_cat = category_id
    if use_tree and it.path:
        key = (category_id, tuple(it.path))
        if key not in path_cache:
            path_cache[key] = taxonomy.get_or_create_path(db, list(it.path), category_id)
        target_cat = path_cache[key]

    title = it.title
    description = it.description
    favicon_path = None
    status_code = None
    fetch_error = None

    if page is not None:
        title = title or page.title
        description = description or page.description
        favicon_path = getattr(page, "favicon_path", None)
        status_code = page.status_code
        fetch_error = page.error

    bm = Bookmark(
        url=it.url,
        domain=it.domain,
        title=(title or display_title(it.url, None))[:512],
        description=description,
        favicon_path=favicon_path,
        category_id=target_cat,
        status_code=status_code,
        last_checked_at=_now() if status_code else None,
        fetch_error=fetch_error[:255] if fetch_error else None,
    )
    merged = {t.name: t for t in common_tags}
    for t in get_or_create_tags(db, it.tags):
        merged.setdefault(t.name, t)
    if merged:
        bm.tags = list(merged.values())
    db.add(bm)
