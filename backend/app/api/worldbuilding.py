from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db

router = APIRouter()


@router.get(
    "/books/{book_id}/worldbuilding", response_model=list[schemas.WorldbuildingOut]
)
def list_worldbuilding(book_id: int, db: Session = Depends(get_db)):
    return db.scalars(
        select(models.Worldbuilding).where(models.Worldbuilding.book_id == book_id)
    ).all()


@router.post("/worldbuilding", response_model=schemas.WorldbuildingOut)
def create_worldbuilding(
    payload: schemas.WorldbuildingCreate, db: Session = Depends(get_db)
):
    if not db.get(models.Book, payload.book_id):
        raise HTTPException(404, "书籍不存在")
    item = models.Worldbuilding(**payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.patch("/worldbuilding/{item_id}", response_model=schemas.WorldbuildingOut)
def update_worldbuilding(
    item_id: int, payload: schemas.WorldbuildingUpdate, db: Session = Depends(get_db)
):
    item = db.get(models.Worldbuilding, item_id)
    if not item:
        raise HTTPException(404, "设定不存在")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(item, k, v)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/worldbuilding/{item_id}")
def delete_worldbuilding(item_id: int, db: Session = Depends(get_db)):
    item = db.get(models.Worldbuilding, item_id)
    if not item:
        raise HTTPException(404, "设定不存在")
    db.delete(item)
    db.commit()
    return {"ok": True}
