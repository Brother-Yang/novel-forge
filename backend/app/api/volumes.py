from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..memory import sync as memory_sync

router = APIRouter()


@router.post("/volumes", response_model=schemas.VolumeOut)
def create_volume(payload: schemas.VolumeCreate, db: Session = Depends(get_db)):
    if not db.get(models.Book, payload.book_id):
        raise HTTPException(404, "书籍不存在")
    volume = models.Volume(**payload.model_dump())
    db.add(volume)
    db.commit()
    db.refresh(volume)
    return volume


@router.patch("/volumes/{volume_id}", response_model=schemas.VolumeOut)
def update_volume(
    volume_id: str, payload: schemas.VolumeUpdate, db: Session = Depends(get_db)
):
    volume = db.get(models.Volume, volume_id)
    if not volume:
        raise HTTPException(404, "卷不存在")
    old_title = volume.title
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(volume, k, v)
    if volume.title != old_title:
        memory_sync.enqueue(db, volume.book_id, memory_sync.VOLUME, volume.id)
    db.commit()
    db.refresh(volume)
    return volume


@router.delete("/volumes/{volume_id}")
def delete_volume(volume_id: str, db: Session = Depends(get_db)):
    volume = db.get(models.Volume, volume_id)
    if not volume:
        raise HTTPException(404, "卷不存在")
    book_id = volume.book_id
    db.delete(volume)
    memory_sync.enqueue(db, book_id, memory_sync.VOLUME, volume_id, removed=True)
    db.commit()
    return {"ok": True}
