"""数据模型：分类树 + 标签 双轨组织。

- Category 自关联，支持任意层级分类目录树，书签归属唯一分类
- Tag 扁平多对多，承担"待读/常用/内部"这类横切维度
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


bookmark_tag = Table(
    "bookmark_tag",
    Base.metadata,
    Column("bookmark_id", ForeignKey("bookmark.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tag.id", ondelete="CASCADE"), primary_key=True),
)


class Category(Base):
    """分类树节点。parent_id 为空表示顶层分类。"""

    __tablename__ = "category"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("category.id", ondelete="CASCADE"), nullable=True, index=True
    )
    icon: Mapped[str | None] = mapped_column(String(64))
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    children: Mapped[list["Category"]] = relationship(
        back_populates="parent",
        cascade="all, delete-orphan",
        order_by="Category.sort_order, Category.id",
    )
    parent: Mapped["Category | None"] = relationship(
        back_populates="children", remote_side="Category.id"
    )
    bookmarks: Mapped[list["Bookmark"]] = relationship(back_populates="category")

    __table_args__ = (UniqueConstraint("parent_id", "name", name="uq_category_parent_name"),)


class Bookmark(Base):
    __tablename__ = "bookmark"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False, unique=True, index=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # 内网地址：同一套服务在内网用 host:port 直连。url 始终是外网地址，
    # 卡片上默认用它；浏览器探测到能直达内网时改跳这里（见 app.js 的 LAN 探测）。
    url_lan: Mapped[str | None] = mapped_column(String(2048))
    title: Mapped[str | None] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text)
    favicon_path: Mapped[str | None] = mapped_column(String(255))

    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("category.id", ondelete="SET NULL"), nullable=True, index=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    status_code: Mapped[int | None] = mapped_column(Integer)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    is_private: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # 抓取失败原因，用于排查为什么图标/标题是空的
    fetch_error: Mapped[str | None] = mapped_column(String(255))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    category: Mapped["Category | None"] = relationship(back_populates="bookmarks")
    tags: Mapped[list["Tag"]] = relationship(
        secondary=bookmark_tag, back_populates="bookmarks", lazy="selectin"
    )


class Tag(Base):
    __tablename__ = "tag"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    bookmarks: Mapped[list["Bookmark"]] = relationship(
        secondary=bookmark_tag, back_populates="tags"
    )


class ImportJob(Base):
    """导入任务，用于进度条与失败回溯。"""

    __tablename__ = "import_job"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_name: Mapped[str] = mapped_column(String(255), default="")
    total: Mapped[int] = mapped_column(Integer, default=0)
    processed: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[int] = mapped_column(Integer, default=0)
    duplicate: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    # pending / running / done / failed / cancelled
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    error_log: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class Setting(Base):
    __tablename__ = "setting"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
