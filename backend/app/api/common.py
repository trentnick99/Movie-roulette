from __future__ import annotations

from datetime import UTC
from typing import NoReturn

from fastapi import HTTPException


def fail(status: int, code: str, message: str, **extra) -> NoReturn:
    raise HTTPException(status, detail={"code": code, "message": message, **extra})


def iso(d) -> str | None:
    if d is None:
        return None

    return (d if d.tzinfo else d.replace(tzinfo=UTC)).isoformat()
