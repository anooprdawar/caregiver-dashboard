"""Pull everything for the authorized patient into raw JSON + SQLite. Idempotent; run as often as you like."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .. import db
from ..config import Config
from . import normalize as N
from .client import EPIC_SPECS, FhirClient
from .smart import ensure_tokens


def write_raw(raw_dir: Path, res: dict) -> None:
    d = raw_dir / res["resourceType"]
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{res['id']}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))


def store_resources(conn: sqlite3.Connection, resources: list[dict], source: str, raw_dir: Path | None = None,
                    binaries: dict[str, bytes] | None = None) -> int:
    n = 0
    with db.tx(conn):
        for res in resources:
            if raw_dir is not None:
                write_raw(raw_dir, res)
            for table, row in N.normalize(res, source, binaries=binaries):
                db.upsert(conn, table, row)
                n += 1
    return n


def _fetch_binaries(client: FhirClient, docs: list[dict], raw_dir: Path, limit_bytes: int = 5_000_000
                    ) -> dict[str, bytes]:
    """Download note bodies referenced by DocumentReference.content.attachment.url."""
    out: dict[str, bytes] = {}
    bdir = raw_dir / "Binary"
    bdir.mkdir(parents=True, exist_ok=True)
    for d in docs:
        for c in d.get("content") or []:
            url = (c.get("attachment") or {}).get("url") or ""
            if "Binary/" not in url:
                continue
            bid = url.split("/")[-1]
            cached = next(bdir.glob(f"{bid}.*"), None)
            if cached:
                out[bid] = cached.read_bytes()
                continue
            got = client.read_binary(bid)
            if not got:
                continue
            blob, ctype = got
            if len(blob) > limit_bytes:
                continue
            ext = {"text/html": "html", "text/plain": "txt", "application/pdf": "pdf",
                   "text/rtf": "rtf", "application/xml": "xml", "text/xml": "xml"}.get(ctype.split(";")[0], "bin")
            (bdir / f"{bid}.{ext}").write_bytes(blob)
            out[bid] = blob
    return out


def run_sync(cfg: Config, conn: sqlite3.Connection, open_browser: bool = True, only: list[str] | None = None,
             verbose: bool = True) -> dict[str, int]:
    cfg.ensure_dirs()
    tokens = ensure_tokens(cfg.fhir, cfg.token_path, open_browser=open_browser)
    if not tokens.patient:
        raise RuntimeError("Token response had no patient id; the app must request patient-level scopes")
    client = FhirClient(cfg.fhir.base_url, tokens.access_token)
    summary: dict[str, int] = {}
    try:
        for spec in EPIC_SPECS:
            if only and spec.type not in only:
                continue
            started = db.now_iso()
            try:
                resources, errors = client.search_patient(spec, tokens.patient, cfg.fhir.search_overrides.get(spec.type))
                binaries = None
                if spec.type == "DocumentReference" and resources:
                    binaries = _fetch_binaries(client, resources, cfg.raw_dir)
                n = store_resources(conn, resources, f"epic:{cfg.fhir.base_url}", cfg.raw_dir, binaries)
                summary[spec.type] = len(resources)
                status = "ok" if not errors else "partial"
                db.log_sync(conn, "epic", spec.type, len(resources), status, started, "; ".join(errors) or None)
                if verbose:
                    print(f"  {spec.type:<20} {len(resources):>5} resources -> {n} rows" + (f"  ({len(errors)} variant errors)" if errors else ""))
            except Exception as e:  # keep going; one bad resource type should not sink the sync
                db.log_sync(conn, "epic", spec.type, 0, "error", started, str(e))
                if verbose:
                    print(f"  {spec.type:<20} ERROR {e}")
        # Practitioners referenced by encounters/documents: resolve names/specialties when readable
        _resolve_practitioners(client, conn, cfg.raw_dir, verbose)
        db.set_meta(conn, "last_sync", db.now_iso())
        db.set_meta(conn, "patient_id", tokens.patient)
        conn.commit()
    finally:
        client.close()
    return summary


def _resolve_practitioners(client: FhirClient, conn: sqlite3.Connection, raw_dir: Path, verbose: bool) -> None:
    missing = [r["id"] for r in db.rows(conn, "SELECT id FROM practitioner WHERE specialty IS NULL LIMIT 200")]
    got = 0
    for pid in missing:
        res = client.read("Practitioner", pid)
        if res:
            store_resources(conn, [res], "epic", raw_dir)
            got += 1
    if verbose and missing:
        print(f"  Practitioner         resolved {got}/{len(missing)} referenced clinicians")


def reload_raw(cfg: Config, conn: sqlite3.Connection) -> int:
    """Re-normalize everything from data/raw. Use after upgrading the mapping code."""
    total = 0
    binaries: dict[str, bytes] = {}
    bdir = cfg.raw_dir / "Binary"
    if bdir.exists():
        for f in bdir.iterdir():
            binaries[f.stem] = f.read_bytes()
    for d in sorted(cfg.raw_dir.iterdir()):
        if not d.is_dir() or d.name == "Binary":
            continue
        resources = [json.loads(f.read_text()) for f in d.glob("*.json")]
        total += store_resources(conn, resources, "raw", None, binaries)
    return total
