"""图标落盘与校验。

第三方图标服务经常返回伪装成图片的 HTML 错误页，也有站点把 .ico 当 text/html 发。
所以校验以魔术字节为准，Content-Type 只作参考；过小的文件同样丢弃（多半是 1x1 占位图）。
"""

import hashlib
from pathlib import Path

from .. import config

_MAGIC: list[tuple[bytes, str]] = [
    (b"\x00\x00\x01\x00", "ico"),
    (b"\x00\x00\x02\x00", "cur"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
]

_MIN_BYTES = 64


def sniff(data: bytes, content_type: str = "") -> str | None:
    """判断二进制内容是不是图片，返回扩展名。"""
    if len(data) < _MIN_BYTES:
        return None
    for magic, ext in _MAGIC:
        if data.startswith(magic):
            return ext

    head = data[:1024].lstrip()
    if head.startswith(b"<?xml") or head.startswith(b"<svg") or b"<svg" in head[:512]:
        return "svg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"

    # 魔术字节没命中时退回 Content-Type，但仍要排除明显的 HTML
    lowered = data[:512].lower()
    if b"<html" in lowered or b"<!doctype" in lowered or b"<head" in lowered:
        return None
    ct = (content_type or "").split(";")[0].strip().lower()
    return config.ICON_CONTENT_TYPES.get(ct)


def save(data: bytes, ext: str) -> str:
    """按内容哈希落盘，天然去重。返回文件名（不含目录）。"""
    digest = hashlib.sha256(data).hexdigest()[:16]
    name = f"{digest}.{ext}"
    path = config.FAVICON_DIR / name
    if not path.exists():
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
    return name


def path_of(name: str | None) -> Path | None:
    """把数据库里的文件名还原成绝对路径，同时挡住 ../ 之类的越权读取。"""
    if not name:
        return None
    if "/" in name or "\\" in name or name.startswith("."):
        return None
    p = config.FAVICON_DIR / name
    return p if p.is_file() else None
