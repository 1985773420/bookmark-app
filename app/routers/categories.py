"""分类树 CRUD。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import get_db
from ..models import Category
from ..schemas import CategoryIn, CategoryPatch
from ..services import taxonomy

router = APIRouter(prefix="/api/categories", tags=["categories"])


def _dup_name(db: Session, name: str, parent_id: int | None, exclude: int | None = None) -> bool:
    stmt = select(Category).where(Category.name == name)
    stmt = stmt.where(Category.parent_id.is_(None) if parent_id is None else Category.parent_id == parent_id)
    if exclude is not None:
        stmt = stmt.where(Category.id != exclude)
    return db.execute(stmt).scalars().first() is not None


@router.get("")
def list_categories(db: Session = Depends(get_db)) -> list[dict]:
    return taxonomy.build_tree(db)


@router.post("", status_code=201)
def create_category(payload: CategoryIn, db: Session = Depends(get_db)) -> dict:
    name = payload.name.strip()
    if _dup_name(db, name, payload.parent_id):
        raise HTTPException(409, f"同级下已存在分类「{name}」")
    if payload.parent_id is not None and db.get(Category, payload.parent_id) is None:
        raise HTTPException(404, "父分类不存在")

    cat = Category(
        name=name, parent_id=payload.parent_id, icon=payload.icon, sort_order=payload.sort_order
    )
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return {"id": cat.id, "name": cat.name, "parent_id": cat.parent_id,
            "icon": cat.icon, "sort_order": cat.sort_order, "bookmark_count": 0, "children": []}


@router.patch("/{cat_id}")
def update_category(cat_id: int, payload: CategoryPatch, db: Session = Depends(get_db)) -> dict:
    cat = db.get(Category, cat_id)
    if cat is None:
        raise HTTPException(404, "分类不存在")

    data = payload.model_dump(exclude_unset=True)
    new_parent = data.get("parent_id", cat.parent_id)
    if "parent_id" in data and taxonomy.would_cycle(db, cat_id, new_parent):
        raise HTTPException(400, "不能把分类移动到它自己的子分类下")
    if new_parent is not None and db.get(Category, new_parent) is None:
        raise HTTPException(404, "父分类不存在")

    new_name = (data.get("name") or cat.name).strip()
    if _dup_name(db, new_name, new_parent, exclude=cat_id):
        raise HTTPException(409, f"同级下已存在分类「{new_name}」")

    for key in ("name", "parent_id", "icon", "sort_order"):
        if key in data:
            setattr(cat, key, data[key])
    cat.name = new_name
    db.commit()
    db.refresh(cat)
    return {"id": cat.id, "name": cat.name, "parent_id": cat.parent_id,
            "icon": cat.icon, "sort_order": cat.sort_order}


@router.delete("/{cat_id}")
def delete_category(
    cat_id: int,
    mode: str = Query("move", pattern="^(move|purge)$"),
    db: Session = Depends(get_db),
) -> dict:
    cat = db.get(Category, cat_id)
    if cat is None:
        raise HTTPException(404, "分类不存在")
    ids = taxonomy.descendants(db, cat_id) or [cat_id]
    taxonomy.delete_category(db, cat, mode=mode)
    db.commit()
    return {"deleted_ids": ids, "mode": mode}


@router.put("/reorder")
def reorder_categories(payload: dict, db: Session = Depends(get_db)) -> dict:
    """payload: {"items": [{"id": 1, "parent_id": null, "sort_order": 0}, ...]}"""
    items = payload.get("items") or []
    for i, item in enumerate(items):
        cat = db.get(Category, int(item["id"]))
        if cat is None:
            continue
        cat.sort_order = int(item.get("sort_order", i))
        if "parent_id" in item:
            pid = item["parent_id"]
            pid = None if pid in (None, "", "null") else int(pid)
            if taxonomy.would_cycle(db, cat.id, pid):
                raise HTTPException(400, "移动会形成循环引用")
            cat.parent_id = pid
    db.commit()
    return {"updated": len(items)}
