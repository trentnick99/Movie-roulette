from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from .. import security as sec
from ..db import get_db
from ..models import Category, Movie, MovieCategory
from ..services import categories as cats
from .common import fail, iso

router = APIRouter(prefix="/api/categories", tags=["categories"], dependencies=[Depends(sec.require_host)])

RULE_KEYS = {"logic", "genres", "decades", "collections", "people", "studios", "keywords", "min_rating",
             "max_runtime", "unplayed"}


def _clean_rules(rules: dict[str, Any] | None) -> dict[str, Any]:
    rules = dict(rules or {})
    unknown = set(rules) - RULE_KEYS
    if unknown:
        raise ValueError(f"unknown rule(s): {', '.join(sorted(unknown))}")
    if rules.get("logic", "all") not in ("all", "any"):
        raise ValueError("logic must be 'all' or 'any'")
    for k in ("genres", "decades", "collections", "people", "studios"):
        if k in rules:
            rules[k] = [int(x) for x in rules[k] or []]
    if "keywords" in rules:
        rules["keywords"] = [str(x)[:60] for x in rules["keywords"] or []][:50]
    return {k: v for k, v in rules.items() if v not in (None, [], "")}


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(None, max_length=1000)
    rules: dict[str, Any] = Field(default_factory=dict)
    movie_ids: list[int] = Field(default_factory=list, max_length=5000)

    @field_validator("rules")
    @classmethod
    def _rules(cls, v):
        return _clean_rules(v)


class CategoryPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=1000)
    rules: dict[str, Any] | None = None
    hidden: bool | None = None

    @field_validator("rules")
    @classmethod
    def _rules(cls, v):
        return None if v is None else _clean_rules(v)


class Reorder(BaseModel):
    ids: list[int] = Field(max_length=5000)


class Membership(BaseModel):
    movie_ids: list[int] = Field(min_length=1, max_length=5000)
    mode: str = Field(pattern="^(include|exclude|clear)$")


def view(db: Session, c: Category, count: int | None = None) -> dict:
    return {"id": c.id, "kind": c.kind, "name": c.name, "slug": c.slug, "ref_id": c.ref_id,
            "rules": c.rules or {}, "description": c.description, "hidden": c.hidden, "sort_order": c.sort_order,
            "count": count if count is not None else len(cats.category_movie_ids(db, c)),
            "created_at": iso(c.created_at)}


@router.get("")
def list_categories(db: Session = Depends(get_db), include_empty: bool = False):
    out = []
    for c in cats.ordered_categories(db):
        n = len(cats.category_movie_ids(db, c))
        if n or include_empty or c.kind == "custom":
            out.append(view(db, c, n))
    return out


@router.post("")
def create(body: CategoryIn, db: Session = Depends(get_db)):
    name = body.name.strip()
    if db.query(Category).filter(Category.kind == "custom", Category.name.ilike(name)).first():
        fail(409, "exists", "A custom category with that name already exists.")
    c = Category(kind="custom", name=name, description=body.description, rules=body.rules,
                 slug=cats._unique_slug(db, cats.slugify(name)),
                 sort_order=0)
    db.add(c)
    db.flush()
    for mid in set(body.movie_ids):
        if db.get(Movie, mid):
            db.add(MovieCategory(category_id=c.id, movie_id=mid, mode="include"))
    db.commit()
    return view(db, c)


@router.patch("/{cat_id}")
def edit(cat_id: int, body: CategoryPatch, db: Session = Depends(get_db)):
    c = db.get(Category, cat_id)
    if c is None:
        fail(404, "not_found", "Category not found.")
    if body.name is not None:
        c.name = body.name.strip()
    if body.description is not None:
        c.description = body.description
    if body.hidden is not None:
        c.hidden = body.hidden
    if body.rules is not None:
        if c.kind != "custom":
            fail(422, "not_custom", "Only custom categories have rules.")
        c.rules = body.rules
    db.commit()
    return view(db, c)


@router.delete("/{cat_id}")
def delete(cat_id: int, db: Session = Depends(get_db)):
    c = db.get(Category, cat_id)
    if c is None:
        fail(404, "not_found", "Category not found.")
    if c.kind != "custom":
        fail(422, "not_custom", "Automatic categories cannot be deleted; hide them instead.")
    db.delete(c)
    db.commit()
    return {"ok": True}


@router.post("/reorder")
def reorder(body: Reorder, db: Session = Depends(get_db)):
    """The listed categories come first, in the given order; the rest keep their relative order after them."""
    current = cats.ordered_categories(db)
    by_id = {c.id: c for c in current}
    listed = [by_id[i] for i in dict.fromkeys(body.ids) if i in by_id]
    rest = [c for c in current if c not in listed]
    for i, c in enumerate(listed + rest):
        c.sort_order = i + 1
    db.commit()
    return {"ok": True}


@router.post("/{cat_id}/movies")
def membership(cat_id: int, body: Membership, db: Session = Depends(get_db)):
    """include: add to a custom category; exclude: keep a movie out of any category; clear: remove the rule."""
    c = db.get(Category, cat_id)
    if c is None:
        fail(404, "not_found", "Category not found.")
    if body.mode == "include" and c.kind != "custom":
        fail(422, "not_custom", "Movies join automatic categories through their TMDB data.")
    for mid in set(body.movie_ids):
        if db.get(Movie, mid) is None:
            continue
        row = db.get(MovieCategory, (c.id, mid))
        if body.mode == "clear":
            if row is not None:
                db.delete(row)
        elif row is None:
            db.add(MovieCategory(category_id=c.id, movie_id=mid, mode=body.mode))
        else:
            row.mode = body.mode
    db.commit()
    return view(db, c)
