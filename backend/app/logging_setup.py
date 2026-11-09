"""Structured logging: readable console output plus a rotating JSON-lines file in data/logs.
Secrets (TMDB keys, tokens, passwords) are redacted from every record."""
from __future__ import annotations

import json
import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_SECRET = re.compile(r"(?i)(api_key=|bearer\s+|password[\"']?\s*[:=]\s*[\"']?|launch_token=|token=)([^\s&\"',]+)")


def redact(text: str) -> str:
    return _SECRET.sub(lambda m: m.group(1) + "[redacted]", text)


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        clean = redact(msg)
        if clean != msg:
            record.msg, record.args = clean, ()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {"time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"), "level": record.levelname,
                "logger": record.name, "message": redact(record.getMessage())}
        if record.exc_info:
            data["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(data, ensure_ascii=False)


def setup(log_dir: Path, level: str = "INFO") -> None:
    root = logging.getLogger()
    if getattr(root, "_mr_configured", False):
        return
    root.setLevel(level.upper())
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))
    console.addFilter(RedactFilter())
    root.addHandler(console)
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(log_dir / "movie_roulette.jsonl", maxBytes=5_000_000, backupCount=3,
                                 encoding="utf-8")
        fh.setFormatter(JsonFormatter())
        fh.addFilter(RedactFilter())
        root.addHandler(fh)
    except OSError:
        pass
    for noisy in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root._mr_configured = True  # type: ignore[attr-defined]
