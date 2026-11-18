"""分类树操作：树构建、路径创建、安全删除。"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Bookmark, Category


def build_tree(db: Session, with_counts: bool = True, public_only: bool = False) -> list[dict]:
    """返回嵌套的分类树，每个节点带 bookmark_count（不含子孙）。

    public_only=True 时计数只算公开书签 —— 游客看到的分类树不能泄露私有书签的数量。
    """
    rows = db.execute(
        select(Category).order_by(Category.sort_order, Category.id)
    ).scalars().all()

    counts: dict[int | None, int] = {}
    if with_counts:
        count_stmt = select(Bookmark.category_id, func.count(Bookmark.id)).group_by(
            Bookmark.category_id)
        if public_only:
            count_stmt = count_stmt.where(Bookmark.is_private.is_(False))
        counts = {cid: n for cid, n in db.execute(count_stmt).all()}

    nodes: dict[int, dict] = {}
    for c in rows:
        nodes[c.id] = {
            "id": c.id,
            "name": c.name,
            "parent_id": c.parent_id,
            "icon": c.icon,
            "sort_order": c.sort_order,
            "bookmark_count": counts.get(c.id, 0),
            "children": [],
        }

    roots: list[dict] = []
    for c in rows:
        node = nodes[c.id]
        parent = nodes.get(c.parent_id) if c.parent_id else None
        if parent is None:
            roots.append(node)
        else:
            parent["children"].append(node)
    return roots


def flatten(tree: list[dict], depth: int = 0) -> list[tuple[dict, int]]:
    """把树压平成 [(节点, 深度)]，供下拉框与侧边栏渲染。"""
    out: list[tuple[dict, int]] = []
    for node in tree:
        out.append((node, depth))
        out.extend(flatten(node["children"], depth + 1))
    return out


def descendants(db: Session, category_id: int) -> list[int]:
    """收集某节点及其全部子孙的 id。"""
    tree = build_tree(db, with_counts=False)
    found: list[int] = []

    def walk(nodes: list[dict]) -> bool:
        hit = False
        for n in nodes:
            if n["id"] == category_id:
                hit = True
                _collect(n, found)
            elif walk(n["children"]):
                hit = True
        return hit

    def _collect(node: dict, acc: list[int]) -> None:
        acc.append(node["id"])
        for ch in node["children"]:
            _collect(ch, acc)

    walk(tree)
    return found


def get_or_create_path(db: Session, names: list[str], root_parent: int | None = None) -> int | None:
    """按目录路径逐级查找/创建分类，返回最深一级的 id。

    用于浏览器书签 HTML 导入：文件里的文件夹层级直接长成分类树。
    """
    parent = root_parent
    for raw in names:
        name = raw.strip()[:128]
        if not name:
            continue
        stmt = select(Category).where(Category.name == name)
        stmt = stmt.where(Category.parent_id.is_(None)) if parent is None else stmt.where(Category.parent_id == parent)
        cat = db.execute(stmt).scalars().first()
        if cat is None:
            cat = Category(name=name, parent_id=parent)
            db.add(cat)
            db.flush()
        parent = cat.id
    return parent


def would_cycle(db: Session, category_id: int, new_parent_id: int | None) -> bool:
    """把 category_id 挂到 new_parent_id 下是否形成环。"""
    if new_parent_id is None:
        return False
    if new_parent_id == category_id:
        return True
    return new_parent_id in descendants(db, category_id)


def delete_category(db: Session, cat: Category, mode: str = "move") -> dict:
    """删除分类。

    mode=move  子分类与书签上提到父级（默认，不丢数据）
    mode=purge 连同整个子树一起删掉，书签变未分类
    """
    stats = {"children_moved": 0, "bookmarks_moved": 0, "deleted": 0}
    if mode == "purge":
        ids = descendants(db, cat.id) or [cat.id]
        stats["deleted"] = len(ids)
        db.execute(
            Bookmark.__table__.update()
            .where(Bookmark.category_id.in_(ids))
            .values(category_id=cat.parent_id)
        )
        stats["bookmarks_moved"] = 1  # 具体数量由调用方回读
        db.delete(cat)  # 级联删除子孙
        db.flush()
        return stats

    # move：只删这一层
    db.execute(
        Category.__table__.update()
        .where(Category.parent_id == cat.id)
        .values(parent_id=cat.parent_id)
    )
    db.execute(
        Bookmark.__table__.update()
        .where(Bookmark.category_id == cat.id)
        .values(category_id=cat.parent_id)
    )
    db.delete(cat)
    db.flush()
    return stats
