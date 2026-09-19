"""Work out what a downloaded file actually is, and import it.

MyChart hands you a file whose name tells you nothing: `MyChart.zip`, `export.zip`, `ccda.xml`,
`Documents.zip`. This sniffs the content rather than trusting the extension, so one entry point
handles a C-CDA download, an Apple Health export, a raw FHIR bundle, or a folder of any of them.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

from . import db
from .config import Config

CCDA_MARKER = b"urn:hl7-org:v3"
MAX_SNIFF = 8192


def _looks_ccda(blob: bytes) -> bool:
    return CCDA_MARKER in blob[:MAX_SNIFF]


def _looks_fhir(blob: bytes) -> bool:
    head = blob[:MAX_SNIFF]
    return b'"resourceType"' in head


def detect_kind(path: Path) -> str:
    """Return 'ccda', 'fhir', 'mixed', 'empty' or 'unknown'."""
    path = Path(path)
    if path.is_dir():
        kinds = {detect_kind(f) for f in path.rglob("*") if f.is_file()}
        kinds.discard("unknown")
        kinds.discard("empty")
        if not kinds:
            return "empty"
        return kinds.pop() if len(kinds) == 1 else "mixed"

    if not path.exists():
        return "unknown"

    suffix = path.suffix.lower()
    if suffix == ".zip":
        try:
            with zipfile.ZipFile(path) as z:
                names = [n for n in z.namelist() if not n.endswith("/")]
                found = set()
                for n in names:
                    low = n.lower()
                    if not low.endswith((".xml", ".json")):
                        continue
                    try:
                        head = z.open(n).read(MAX_SNIFF)
                    except Exception:
                        continue
                    if low.endswith(".xml") and _looks_ccda(head):
                        found.add("ccda")
                    elif low.endswith(".json") and _looks_fhir(head):
                        found.add("fhir")
                    if len(found) > 1:
                        return "mixed"
                return found.pop() if found else "empty"
        except (zipfile.BadZipFile, OSError):
            # a truncated or still-downloading zip, or one that moved out from under us
            return "unknown"

    if suffix in (".xml", ".json", ".txt", ""):
        try:
            head = path.open("rb").read(MAX_SNIFF)
        except OSError:
            return "unknown"
        if _looks_ccda(head):
            return "ccda"
        if _looks_fhir(head):
            return "fhir"
        return "empty" if not head.strip() else "unknown"
    return "unknown"


def file_digest(path: Path) -> str:
    """Content hash, so the same download under a new name is recognised as already imported."""
    h = hashlib.sha256()
    if Path(path).is_dir():
        for f in sorted(Path(path).rglob("*")):
            if f.is_file():
                h.update(f.name.encode())
                h.update(f.read_bytes())
    else:
        h.update(Path(path).read_bytes())
    return h.hexdigest()[:32]


def import_any(cfg: Config, conn: sqlite3.Connection, path: Path) -> dict:
    """Import a file or folder of any supported kind. Safe to call twice: rows upsert."""
    from .importers import ccda, fhir_bundle

    path = Path(path)
    kind = detect_kind(path)
    result: dict = {"path": str(path), "kind": kind, "counts": {}}
    if kind == "ccda":
        result["counts"] = ccda.import_path(cfg, conn, path)
    elif kind == "fhir":
        result["counts"] = fhir_bundle.import_path(cfg, conn, path)
    elif kind == "mixed":
        merged: dict[str, int] = {}
        for fn in (ccda.import_path, fhir_bundle.import_path):
            for k, v in (fn(cfg, conn, path) or {}).items():
                merged[k] = merged.get(k, 0) + v
        result["counts"] = merged
    result["rows"] = sum(result["counts"].values()) if result["counts"] else 0
    return result


def already_imported(conn: sqlite3.Connection, digest: str) -> bool:
    db.ensure_import_log(conn)
    return db.one(conn, "SELECT 1 ok FROM import_log WHERE digest=?", (digest,)) is not None


def record_import(conn: sqlite3.Connection, path: Path, digest: str, result: dict) -> None:
    db.ensure_import_log(conn)
    conn.execute(
        "INSERT INTO import_log(path, digest, kind, rows, imported_at) VALUES(?,?,?,?,?) "
        "ON CONFLICT(digest) DO UPDATE SET path=excluded.path, imported_at=excluded.imported_at",
        (str(path), digest, result.get("kind"), result.get("rows", 0), db.now_iso()))
    conn.commit()
