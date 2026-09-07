"""Import FHIR JSON from files or folders.

Works for: Apple Health 'Export Health Data' (clinical-records/*.json), MyChart bulk FHIR downloads,
Epic 'Download my record' JSON, or anything containing Bundles / single resources.
"""
from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

from ..config import Config
from ..fhir import normalize as N
from ..fhir.sync import store_resources


def _iter_json_blobs(path: Path):
    if path.is_dir():
        for f in sorted(path.rglob("*.json")):
            yield f, f.read_text(errors="replace")
    elif path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if name.lower().endswith(".json"):
                    yield Path(name), z.read(name).decode("utf-8", "replace")
    else:
        yield path, path.read_text(errors="replace")


def import_path(cfg: Config, conn: sqlite3.Connection, path: Path, keep_raw: bool = True) -> dict[str, int]:
    counts: dict[str, int] = {}
    batch: list[dict] = []
    for _, text in _iter_json_blobs(path):
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            continue
        for res in N.iter_resources(obj):
            if res.get("resourceType") in N.HANDLERS and res.get("id"):
                batch.append(res)
                counts[res["resourceType"]] = counts.get(res["resourceType"], 0) + 1
    store_resources(conn, batch, f"import:{path.name}", cfg.raw_dir if keep_raw else None)
    return counts
