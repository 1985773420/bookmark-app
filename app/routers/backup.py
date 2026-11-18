"""备份、导出与恢复。"""

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from ..db import reset_engine
from ..deps import get_db
from ..services import auth
from ..services import backup as bk

router = APIRouter(prefix="/api", tags=["backup"])


@router.get("/backup")
def list_backups() -> list[dict]:
    return bk.list_backups()


@router.post("/backup", status_code=201)
def create_backup(payload: dict | None = None, db: Session = Depends(get_db)) -> dict:
    note = (payload or {}).get("note", "")
    return bk.create_backup(db, note=note)


@router.get("/backup/{name}/download")
def download_backup(name: str):
    path = bk.backup_path(name)
    if path is None:
        raise HTTPException(404, "备份不存在")
    return FileResponse(path, media_type="application/zip", filename=name)


@router.delete("/backup/{name}")
def delete_backup(name: str) -> dict:
    path = bk.backup_path(name)
    if path is None:
        raise HTTPException(404, "备份不存在")
    path.unlink()
    return {"deleted": name}


@router.post("/backup/{name}/restore")
def restore_backup(name: str) -> dict:
    """把某个备份包还原成当前数据库。恢复前会自动另存当前库。"""
    path = bk.backup_path(name)
    if path is None:
        raise HTTPException(404, "备份不存在")
    try:
        result = bk.restore_from_zip(path)
    except (ValueError, OSError) as exc:
        raise HTTPException(400, f"恢复失败：{exc}") from exc
    # 文件已经换掉了，连接池必须重建，否则进程还在读写旧库
    reset_engine()
    # 会话签名密钥也在库里，恢复后得重新读，不然旧 cookie 和库对不上
    auth.forget_secret()
    return result


@router.post("/backup/upload")
async def upload_backup(file: UploadFile = File(...)) -> dict:
    """导入外部 zip 备份到备份目录，之后可在列表里恢复。"""
    if not (file.filename or "").endswith(".zip"):
        raise HTTPException(400, "只接受 zip 备份包")
    data = await file.read()
    if len(data) > 512 * 1024 * 1024:
        raise HTTPException(413, "备份包过大（上限 512MB）")
    bk.config.ensure_dirs()
    target = bk.config.BACKUP_DIR / f"bookmark-upload-{file.filename}"
    target.write_bytes(data)
    return {"saved": target.name, "size": len(data)}


@router.get("/export/json")
def export_json(db: Session = Depends(get_db)) -> Response:
    return Response(
        content=bk.json_bytes(db),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="bookmarks.json"'},
    )


@router.get("/export/html")
def export_html(db: Session = Depends(get_db)) -> Response:
    return Response(
        content=bk.export_netscape_html(db).encode("utf-8"),
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="bookmarks.html"'},
    )


@router.post("/import/json")
async def import_json(file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    import json as _json

    raw = await file.read()
    try:
        payload = _json.loads(raw.decode("utf-8"))
    except (_json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(400, f"JSON 解析失败：{exc}") from exc
    if not isinstance(payload, dict) or "bookmarks" not in payload:
        raise HTTPException(400, "不是本站导出的 JSON 格式")
    return bk.import_json(db, payload)


@router.get("/backup/preview/{name}")
def preview_backup(name: str) -> dict:
    path = bk.backup_path(name)
    if path is None:
        raise HTTPException(404, "备份不存在")
    return {"name": name, "manifest": bk.read_manifest(path), "includes": bk._peek_includes(path)}
