"""Run Alembic migrations programmatically (used at startup and by `python -m app migrate`)."""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig

from .config import BACKEND_DIR


def alembic_config(db_path: Path | str) -> AlembicConfig:
    cfg = AlembicConfig(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{Path(db_path).as_posix()}")
    cfg.attributes["configure_logger"] = False
    return cfg


def upgrade(db_path: Path | str) -> None:
    command.upgrade(alembic_config(db_path), "head")
