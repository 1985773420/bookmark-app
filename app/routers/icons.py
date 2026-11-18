"""图标文件服务。"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import get_db
from ..models import Bookmark
from ..services import icons

router = APIRouter(tags=["icons"])

_EXT_CT = {
    "ico": "image/x-icon",
    "png": "image/png",
    "jpg": "image/jpeg",
    "gif": "image/gif",
    "svg": "image/svg+xml",
    "webp": "image/webp",
    "cur": "image/x-icon",
}


@router.get("/icons/{name}")
def serve_icon(request: Request, name: str, db: Session = Depends(get_db)) -> Response:
    # 图标地址也能被游客直接访问：私有书签的图标在未登录时按"不存在"处理
    if getattr(request.state, "user", None) is None:
        flags = db.execute(
            select(Bookmark.is_private).where(Bookmark.favicon_path == name)
        ).scalars().all()
        if flags and all(flags):
            raise HTTPException(404, "图标不存在")
    path = icons.path_of(name)
    if path is None:
        raise HTTPException(404, "图标不存在")
    ext = path.suffix.lstrip(".").lower()
    return Response(
        content=path.read_bytes(),
        media_type=_EXT_CT.get(ext, "application/octet-stream"),
        headers={"Cache-Control": "public, max-age=604800, immutable"},
    )
