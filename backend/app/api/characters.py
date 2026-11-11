from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..memory import facts

router = APIRouter()


def _view(
    character: models.Character, derived: dict[str, dict[str, str]]
) -> schemas.CharacterOut:
    result = schemas.CharacterOut.model_validate(character)
    result.derived_state = derived.get(character.id, {})
    return result


@router.get("/books/{book_id}/characters", response_model=list[schemas.CharacterOut])
def list_characters(book_id: str, db: Session = Depends(get_db)):
    characters = db.scalars(
        select(models.Character)
        .where(models.Character.book_id == book_id)
        .order_by(models.Character.order_index)
    ).all()
    derived = facts.replay(db, book_id).characters
    return [_view(character, derived) for character in characters]


@router.post("/characters", response_model=schemas.CharacterOut)
def create_character(payload: schemas.CharacterCreate, db: Session = Depends(get_db)):
    if not db.get(models.Book, payload.book_id):
        raise HTTPException(404, "书籍不存在")
    character = models.Character(**payload.model_dump())
    db.add(character)
    db.commit()
    db.refresh(character)
    return _view(character, {})


@router.patch("/characters/{character_id}", response_model=schemas.CharacterOut)
def update_character(
    character_id: str, payload: schemas.CharacterUpdate, db: Session = Depends(get_db)
):
    character = db.get(models.Character, character_id)
    if not character:
        raise HTTPException(404, "人物不存在")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(character, k, v)
    db.commit()
    db.refresh(character)
    return _view(character, facts.replay(db, character.book_id).characters)


@router.delete("/characters/{character_id}")
def delete_character(character_id: str, db: Session = Depends(get_db)):
    character = db.get(models.Character, character_id)
    if not character:
        raise HTTPException(404, "人物不存在")
    db.delete(character)
    db.commit()
    return {"ok": True}
