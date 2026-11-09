from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import security as sec
from ..db import get_db
from ..services import stats

router = APIRouter(prefix="/api", tags=["stats"], dependencies=[Depends(sec.require_host)])


@router.get("/stats")
def overview(db: Session = Depends(get_db)):
    return stats.overview(db)
