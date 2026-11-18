"""Pydantic 模型：API 请求体与响应体。

内部 ORM 模型与实践解耦——导入任务等后台流程会直接操作 ORM，
但对外接口一律走这里定义的 schema。
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------- 分类 ----------


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    parent_id: int | None = None
    icon: str | None = Field(default=None, max_length=64)
    sort_order: int = 0

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("分类名不能为空")
        return v


class CategoryPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    parent_id: int | None = None
    icon: str | None = Field(default=None, max_length=64)
    sort_order: int | None = None


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    parent_id: int | None
    icon: str | None
    sort_order: int
    bookmark_count: int = 0
    children: list["CategoryOut"] = []


# ---------- 标签 ----------


class TagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


# ---------- 书签 ----------


class BookmarkIn(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    title: str | None = Field(default=None, max_length=512)
    description: str | None = None
    category_id: int | None = None
    tags: list[str] = []
    is_private: bool = False
    # 内网地址（可选）：同一条书签在内网直连时用的地址，留空就只走 url
    url_lan: str | None = Field(default=None, max_length=2048)
    # 是否立刻出网抓取标题与图标；导入大批量时可关掉稍后统一抓
    fetch: bool = True

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, v: list[str]) -> list[str]:
        seen: list[str] = []
        for t in v:
            t = t.strip()
            if t and t not in seen:
                seen.append(t)
        return seen


class BookmarkPatch(BaseModel):
    # 外网地址可改：早期这字段只在前端被 readonly 锁住，后端也没留口子，
    # 加内网地址之后想换域名就没辙了。
    url: str | None = Field(default=None, max_length=2048)
    title: str | None = Field(default=None, max_length=512)
    description: str | None = None
    category_id: int | None = None
    tags: list[str] | None = None
    is_private: bool | None = None
    sort_order: int | None = None
    url_lan: str | None = Field(default=None, max_length=2048)


class BookmarkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str
    url_lan: str | None = None
    domain: str
    title: str | None
    description: str | None
    favicon_path: str | None
    category_id: int | None
    sort_order: int
    status_code: int | None
    last_checked_at: datetime | None
    is_private: bool
    fetch_error: str | None
    created_at: datetime
    updated_at: datetime
    tags: list[TagOut] = []


class BookmarkReorder(BaseModel):
    """拖拽排序：一次性提交某个分类下的完整顺序。只改顺序，不改分类。"""

    ordered_ids: list[int]


class BulkAction(BaseModel):
    ids: list[int]
    # move / delete / tag_add / tag_remove / private / public
    action: str
    category_id: int | None = None
    tags: list[str] = []


# ---------- 导入 ----------


class ImportPreviewIn(BaseModel):
    text: str = ""
    filename: str = ""


class ParsedItem(BaseModel):
    url: str
    domain: str
    title: str | None = None
    path: list[str] = []
    valid: bool = True
    reason: str | None = None


class ImportPreviewOut(BaseModel):
    total: int
    valid: int
    invalid: int
    duplicates: int
    items: list[ParsedItem]
    categories: list[str] = []


class ImportRunIn(BaseModel):
    text: str = ""
    filename: str = ""
    category_id: int | None = None
    # 用文件里的目录层级自动建分类（浏览器 HTML 导入）
    use_tree: bool = True
    tags: list[str] = []
    fetch: bool = True
    skip_existing: bool = True


class ImportJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_name: str
    total: int
    processed: int
    success: int
    duplicate: int
    failed: int
    status: str
    error_log: str | None
    created_at: datetime
    finished_at: datetime | None


# ---------- 抓取 / 图标 ----------


class FetchResult(BaseModel):
    url: str
    status_code: int | None = None
    title: str | None = None
    description: str | None = None
    favicon_path: str | None = None
    error: str | None = None


class RefreshIn(BaseModel):
    ids: list[int] = []
    # 只补没有图标的 / 只探测死链
    only_missing_icons: bool = False
    check_status: bool = False


# ---------- 备份 ----------


class BackupOut(BaseModel):
    name: str
    size: int
    created_at: datetime
    includes: list[str] = []


CategoryOut.model_rebuild()
