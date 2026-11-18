"""导入：解析预览、启动导入任务、轮询进度。"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import get_db
from ..models import Bookmark, ImportJob
from ..schemas import ImportRunIn
from ..services import parser
from ..services.importer import run_import

router = APIRouter(prefix="/api/import", tags=["import"])


@router.post("/preview")
def preview(payload: dict, db: Session = Depends(get_db)) -> dict:
    text = payload.get("text") or ""
    filename = payload.get("filename") or ""
    if not text.strip():
        raise HTTPException(400, "没有可解析的内容")

    fmt, items = parser.parse_any(text, filename)
    existing = set(db.execute(select(Bookmark.url)).scalars().all())

    seen: set[str] = set()
    out: list[dict] = []
    duplicates = 0
    valid = 0
    categories: list[str] = []

    for it in items:
        if not it.valid:
            out.append({"url": it.url, "domain": "", "title": it.title,
                        "path": it.path, "valid": False, "reason": it.reason})
            continue
        valid += 1
        if it.url in existing or it.url in seen:
            duplicates += 1
        seen.add(it.url)
        path = " / ".join(it.path)
        if path and path not in categories:
            categories.append(path)
        out.append({
            "url": it.url, "domain": it.domain, "title": it.title, "path": it.path,
            "tags": it.tags, "valid": True, "reason": None,
            "duplicate": it.url in existing,
        })

    return {
        "format": fmt,
        "total": len(items),
        "valid": valid,
        "invalid": len(items) - valid,
        "duplicates": duplicates,
        "categories": categories,
        "items": out[:500],
        "truncated": len(out) > 500,
    }


@router.post("/run", status_code=202)
async def run(payload: ImportRunIn, background: BackgroundTasks, db: Session = Depends(get_db)) -> dict:
    if not payload.text.strip():
        raise HTTPException(400, "没有可导入的内容")

    fmt, items = parser.parse_any(payload.text, payload.filename)
    if not items:
        raise HTTPException(400, "没有解析出任何条目")

    job = ImportJob(
        source_name=(payload.filename or f"{fmt} 粘贴内容")[:255],
        total=len(items),
        status="pending",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    background.add_task(
        run_import,
        job.id,
        items,
        category_id=payload.category_id,
        use_tree=payload.use_tree,
        tags=payload.tags,
        fetch=payload.fetch,
        skip_existing=payload.skip_existing,
    )
    return {"job_id": job.id, "total": len(items), "format": fmt}


@router.get("/jobs")
def list_jobs(limit: int = Query(20, ge=1, le=200), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(
        select(ImportJob).order_by(ImportJob.id.desc()).limit(limit)
    ).scalars().all()
    return [_job_out(j) for j in rows]


@router.get("/jobs/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db)) -> dict:
    job = db.get(ImportJob, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    return _job_out(job)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int, db: Session = Depends(get_db)) -> dict:
    job = db.get(ImportJob, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    if job.status in ("done", "failed", "cancelled"):
        return {"job_id": job_id, "status": job.status}
    job.status = "cancelled"
    db.commit()
    return {"job_id": job_id, "status": "cancelled"}


def _job_out(job: ImportJob) -> dict:
    return {
        "id": job.id,
        "source_name": job.source_name,
        "total": job.total,
        "processed": job.processed,
        "success": job.success,
        "duplicate": job.duplicate,
        "failed": job.failed,
        "status": job.status,
        "error_log": job.error_log,
        "created_at": job.created_at,
        "finished_at": job.finished_at,
    }
