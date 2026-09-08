"""SQLite store. Raw FHIR JSON is kept alongside normalized rows so nothing is ever lost."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS patient (
  id TEXT PRIMARY KEY, name TEXT, birth_date TEXT, sex TEXT, mrn TEXT, source TEXT, raw TEXT);

CREATE TABLE IF NOT EXISTS practitioner (
  id TEXT PRIMARY KEY, name TEXT, specialty TEXT, organization TEXT, phone TEXT, role TEXT, source TEXT, raw TEXT);

CREATE TABLE IF NOT EXISTS encounter (
  id TEXT PRIMARY KEY, patient_id TEXT, start TEXT, "end" TEXT, class TEXT, type TEXT, status TEXT,
  reason TEXT, location TEXT, practitioners TEXT, source TEXT, raw TEXT);

CREATE TABLE IF NOT EXISTS condition (
  id TEXT PRIMARY KEY, patient_id TEXT, encounter_id TEXT, code TEXT, display TEXT, clinical_status TEXT,
  verification_status TEXT, category TEXT, onset TEXT, recorded TEXT, abatement TEXT, source TEXT, raw TEXT);

CREATE TABLE IF NOT EXISTS observation (
  id TEXT PRIMARY KEY, patient_id TEXT, encounter_id TEXT, effective TEXT, issued TEXT, category TEXT,
  code TEXT, code_system TEXT, display TEXT, value_num REAL, value_unit TEXT, value_str TEXT,
  ref_low REAL, ref_high REAL, ref_text TEXT, interpretation TEXT, status TEXT, panel_key TEXT,
  report_id TEXT, source TEXT, raw TEXT);
CREATE INDEX IF NOT EXISTS ix_obs_panel ON observation(panel_key, effective);
CREATE INDEX IF NOT EXISTS ix_obs_eff ON observation(effective);
CREATE INDEX IF NOT EXISTS ix_obs_code ON observation(code);

CREATE TABLE IF NOT EXISTS diagnostic_report (
  id TEXT PRIMARY KEY, patient_id TEXT, encounter_id TEXT, effective TEXT, issued TEXT, category TEXT,
  code TEXT, display TEXT, status TEXT, kind TEXT, conclusion TEXT, text TEXT, performer TEXT,
  result_ids TEXT, source TEXT, raw TEXT);
CREATE INDEX IF NOT EXISTS ix_dr_eff ON diagnostic_report(effective);

CREATE TABLE IF NOT EXISTS document (
  id TEXT PRIMARY KEY, patient_id TEXT, encounter_id TEXT, date TEXT, type TEXT, category TEXT, title TEXT,
  author TEXT, status TEXT, content_text TEXT, content_path TEXT, content_type TEXT, source TEXT, raw TEXT);
CREATE INDEX IF NOT EXISTS ix_doc_date ON document(date);

CREATE TABLE IF NOT EXISTS medication (
  id TEXT PRIMARY KEY, patient_id TEXT, encounter_id TEXT, kind TEXT, authored TEXT, status TEXT, intent TEXT,
  medication TEXT, rxnorm TEXT, dosage TEXT, route TEXT, frequency TEXT, requester TEXT, reason TEXT,
  start TEXT, "end" TEXT, source TEXT, raw TEXT);
CREATE INDEX IF NOT EXISTS ix_med_name ON medication(medication);

CREATE TABLE IF NOT EXISTS procedure (
  id TEXT PRIMARY KEY, patient_id TEXT, encounter_id TEXT, performed TEXT, code TEXT, display TEXT,
  status TEXT, performer TEXT, body_site TEXT, outcome TEXT, source TEXT, raw TEXT);

CREATE TABLE IF NOT EXISTS appointment (
  id TEXT PRIMARY KEY, patient_id TEXT, start TEXT, "end" TEXT, status TEXT, type TEXT, description TEXT,
  participants TEXT, location TEXT, source TEXT, raw TEXT);

CREATE TABLE IF NOT EXISTS allergy (
  id TEXT PRIMARY KEY, patient_id TEXT, substance TEXT, reaction TEXT, severity TEXT, status TEXT,
  recorded TEXT, source TEXT, raw TEXT);

CREATE TABLE IF NOT EXISTS immunization (
  id TEXT PRIMARY KEY, patient_id TEXT, vaccine TEXT, date TEXT, status TEXT, source TEXT, raw TEXT);

CREATE TABLE IF NOT EXISTS care_plan (
  id TEXT PRIMARY KEY, patient_id TEXT, title TEXT, category TEXT, status TEXT, start TEXT, "end" TEXT,
  description TEXT, activities TEXT, source TEXT, raw TEXT);

CREATE TABLE IF NOT EXISTS imaging_file (
  path TEXT PRIMARY KEY, kind TEXT, modality TEXT, study_date TEXT, study_desc TEXT, series_desc TEXT,
  body_part TEXT, study_uid TEXT, series_uid TEXT, institution TEXT, size INTEGER, indexed_at TEXT, report_id TEXT);

-- Caregiver-authored. This is the part MyChart can never give you.
CREATE TABLE IF NOT EXISTS note (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT, updated TEXT, kind TEXT, title TEXT, body TEXT,
  status TEXT DEFAULT 'open', due TEXT, owner TEXT, related_type TEXT, related_id TEXT, event_date TEXT,
  source TEXT DEFAULT 'caregiver');

CREATE TABLE IF NOT EXISTS sync_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, started TEXT, finished TEXT, source TEXT, resource_type TEXT,
  count INTEGER, status TEXT, error TEXT);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

TABLE_COLUMNS: dict[str, list[str]] = {}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(path: Path | str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    _load_columns(conn)
    return conn


# Columns added after the first release. Existing databases are upgraded in place.
_ADDED_COLUMNS = {"note": {"source": "TEXT DEFAULT 'caregiver'"}}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, cols in _ADDED_COLUMNS.items():
        have = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
        for name, decl in cols.items():
            if name not in have:
                conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {decl}')
    conn.commit()


def _load_columns(conn: sqlite3.Connection) -> None:
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        TABLE_COLUMNS[name] = [r[1] for r in conn.execute(f'PRAGMA table_info("{name}")')]


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _prep(table: str, row: dict[str, Any]) -> dict[str, Any]:
    cols = TABLE_COLUMNS[table]
    out = {}
    for c in cols:
        if c not in row:
            continue
        v = row[c]
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        out[c] = v
    return out


def upsert(conn: sqlite3.Connection, table: str, row: dict[str, Any]) -> None:
    """Insert or replace by primary key, preserving the row shape of the table."""
    r = _prep(table, row)
    if not r:
        return
    cols = ", ".join(f'"{c}"' for c in r)
    qs = ", ".join("?" for _ in r)
    pk = "path" if table == "imaging_file" else "id"
    updates = ", ".join(f'"{c}"=excluded."{c}"' for c in r if c != pk)
    sql = f'INSERT INTO "{table}" ({cols}) VALUES ({qs}) ON CONFLICT("{pk}") DO UPDATE SET {updates}'
    conn.execute(sql, list(r.values()))


def upsert_many(conn: sqlite3.Connection, table: str, rows: Iterable[dict[str, Any]]) -> int:
    n = 0
    for row in rows:
        upsert(conn, table, row)
        n += 1
    return n


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, value))


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def log_sync(conn: sqlite3.Connection, source: str, resource_type: str, count: int, status: str,
             started: str, error: str | None = None) -> None:
    conn.execute(
        "INSERT INTO sync_log(started, finished, source, resource_type, count, status, error) VALUES(?,?,?,?,?,?,?)",
        (started, now_iso(), source, resource_type, count, status, error))


def rows(conn: sqlite3.Connection, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def one(conn: sqlite3.Connection, sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
    r = conn.execute(sql, params).fetchone()
    return dict(r) if r else None


# Tables carrying a `source` column, i.e. everything ingested from an outside system.
SOURCED_TABLES = ("patient", "practitioner", "encounter", "condition", "observation", "diagnostic_report",
                  "document", "medication", "procedure", "appointment", "allergy", "immunization", "care_plan")


def source_breakdown(conn: sqlite3.Connection) -> dict[str, int]:
    """How many rows came from each source, e.g. {'demo': 535, 'ccda:UCSF.xml': 1204}."""
    out: dict[str, int] = {}
    for t in (*SOURCED_TABLES, "note"):
        for r in conn.execute(f'SELECT COALESCE(source, \'unknown\') s, COUNT(*) n FROM "{t}" GROUP BY s'):
            out[r[0]] = out.get(r[0], 0) + r[1]
    return out


def delete_source(conn: sqlite3.Connection, source: str, keep_notes: bool = False) -> dict[str, int]:
    """Delete every row from one source. Returns rows removed per table."""
    removed: dict[str, int] = {}
    tables = SOURCED_TABLES if keep_notes else (*SOURCED_TABLES, "note")
    with tx(conn):
        for t in tables:
            cur = conn.execute(f'DELETE FROM "{t}" WHERE source = ?', (source,))
            if cur.rowcount:
                removed[t] = cur.rowcount
        # imaging_file has no source column; drop links to reports that no longer exist
        conn.execute("UPDATE imaging_file SET report_id = NULL WHERE report_id NOT IN (SELECT id FROM diagnostic_report)")
        conn.execute("DELETE FROM sync_log WHERE source = ?", (source,))
        if source == "demo":
            conn.execute("DELETE FROM meta WHERE key = 'demo'")
    return removed


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    out = {}
    for t in ("observation", "diagnostic_report", "document", "medication", "condition", "encounter",
              "appointment", "procedure", "practitioner", "allergy", "imaging_file", "note"):
        out[t] = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
    return out
