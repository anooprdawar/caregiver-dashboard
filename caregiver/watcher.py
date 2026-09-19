"""Watch a folder and import anything clinical that lands in it.

This is the zero-fragility half of keeping the record current: whatever you download, from whatever
portal, by whatever route, drop it in the folder and it appears in the dashboard. It never logs in,
never touches a password, and has nothing to break when a portal redesigns its pages.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Callable, Iterable

from . import db
from .config import Config
from .ingest import already_imported, detect_kind, file_digest, import_any, record_import

CANDIDATE_SUFFIXES = (".zip", ".xml", ".json")
SETTLE_SECONDS = 2.0          # a file must stop growing before we touch it
MAX_AGE_DAYS_DEFAULT = 30     # ignore downloads older than this on the first pass


def candidates(folder: Path, max_age_days: int | None = MAX_AGE_DAYS_DEFAULT) -> list[Path]:
    out = []
    cutoff = time.time() - (max_age_days * 86400) if max_age_days else None
    for f in sorted(Path(folder).glob("*")):
        if not f.is_file() or f.name.startswith((".", "~")):
            continue
        if f.suffix.lower() not in CANDIDATE_SUFFIXES:
            continue
        if f.suffix.lower() == ".crdownload" or f.name.endswith(".part"):
            continue
        if cutoff and f.stat().st_mtime < cutoff:
            continue
        out.append(f)
    return out


def _settled(path: Path) -> bool:
    """True once the file has stopped changing, so a half-written download is never parsed."""
    try:
        a = path.stat()
        time.sleep(SETTLE_SECONDS)
        b = path.stat()
    except OSError:
        return False
    return a.st_size == b.st_size and a.st_size > 0


def ingest_new(cfg: Config, conn: sqlite3.Connection, folder: Path,
               max_age_days: int | None = MAX_AGE_DAYS_DEFAULT,
               on_event: Callable[[str, dict], None] | None = None) -> list[dict]:
    """Import every unseen clinical file in `folder`. Returns one result per file imported."""
    results = []
    for f in candidates(folder, max_age_days):
        kind = detect_kind(f)
        if kind in ("unknown", "empty"):
            continue
        if not _settled(f):
            continue
        digest = file_digest(f)
        if already_imported(conn, digest):
            continue
        if on_event:
            on_event("importing", {"path": f, "kind": kind})
        result = import_any(cfg, conn, f)
        record_import(conn, f, digest, result)
        results.append(result)
        if on_event:
            on_event("imported", result)
    return results


def watch(cfg: Config, folder: Path, interval: float = 5.0, once: bool = False,
          max_age_days: int | None = MAX_AGE_DAYS_DEFAULT,
          on_event: Callable[[str, dict], None] | None = None) -> list[dict]:
    """Poll `folder` forever (or one pass with once=True), importing whatever appears."""
    seen_any: list[dict] = []
    while True:
        conn = db.connect(cfg.db_path)
        try:
            seen_any += ingest_new(cfg, conn, folder, max_age_days, on_event)
        finally:
            conn.close()
        if once:
            return seen_any
        time.sleep(interval)
