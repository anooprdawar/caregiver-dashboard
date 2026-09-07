"""Index a folder of imaging: DICOM (from the radiology CD/portal) and PDFs of reports.

The FHIR API gives you the radiologist's *report* but never the pixels. Request the images from
the radiology department, drop the folder into data/imaging/, and this links them by date.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from .. import db
from ..config import Config


def _dicom_meta(path: Path) -> dict | None:
    try:
        import pydicom
        ds = pydicom.dcmread(str(path), stop_before_pixels=True, force=True)
    except Exception:
        return None
    if not getattr(ds, "SOPClassUID", None) and not getattr(ds, "Modality", None):
        return None
    sd = str(getattr(ds, "StudyDate", "") or "")
    study_date = f"{sd[:4]}-{sd[4:6]}-{sd[6:8]}" if len(sd) == 8 else None
    return {
        "kind": "dicom", "modality": str(getattr(ds, "Modality", "") or "") or None, "study_date": study_date,
        "study_desc": str(getattr(ds, "StudyDescription", "") or "") or None,
        "series_desc": str(getattr(ds, "SeriesDescription", "") or "") or None,
        "body_part": str(getattr(ds, "BodyPartExamined", "") or "") or None,
        "study_uid": str(getattr(ds, "StudyInstanceUID", "") or "") or None,
        "series_uid": str(getattr(ds, "SeriesInstanceUID", "") or "") or None,
        "institution": str(getattr(ds, "InstitutionName", "") or "") or None,
    }


def index_folder(cfg: Config, conn: sqlite3.Connection, folder: Path | None = None) -> dict[str, int]:
    folder = folder or cfg.imaging_dir
    counts = {"dicom_series": 0, "documents": 0, "skipped": 0}
    seen_series: set[str] = set()
    with db.tx(conn):
        for f in sorted(folder.rglob("*")):
            if not f.is_file() or f.name.startswith("."):
                continue
            rel = str(f.relative_to(folder))
            ext = f.suffix.lower()
            if ext in (".pdf", ".jpg", ".jpeg", ".png", ".txt", ".rtf", ".docx"):
                row = {"path": rel, "kind": "document", "study_desc": f.stem.replace("_", " "), "size": f.stat().st_size,
                       "indexed_at": db.now_iso(), "study_date": _date_from_name(f.stem)}
                db.upsert(conn, "imaging_file", row)
                counts["documents"] += 1
                continue
            meta = _dicom_meta(f)
            if not meta:
                counts["skipped"] += 1
                continue
            # one row per series (thousands of slices otherwise); path points at the series folder
            key = meta.get("series_uid") or rel
            if key in seen_series:
                continue
            seen_series.add(key)
            db.upsert(conn, "imaging_file", {**meta, "path": str(f.parent.relative_to(folder)) or ".",
                                             "size": sum(p.stat().st_size for p in f.parent.iterdir() if p.is_file()),
                                             "indexed_at": db.now_iso()})
            counts["dicom_series"] += 1
        _link_reports(conn)
    return counts


def _date_from_name(stem: str) -> str | None:
    import re
    m = re.search(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})", stem)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def _link_reports(conn: sqlite3.Connection) -> None:
    """Link each study to the imaging DiagnosticReport on the same date (±1 day)."""
    files = db.rows(conn, "SELECT path, study_date, modality FROM imaging_file WHERE study_date IS NOT NULL")
    reports = db.rows(conn, "SELECT id, effective, display FROM diagnostic_report WHERE kind='imaging' AND effective IS NOT NULL")
    for f in files:
        try:
            fd = datetime.fromisoformat(f["study_date"][:10])
        except ValueError:
            continue
        best = None
        for r in reports:
            try:
                rd = datetime.fromisoformat(r["effective"][:10])
            except ValueError:
                continue
            delta = abs((rd - fd).days)
            if delta <= 1:
                score = delta - (1 if f["modality"] and f["modality"].lower() in (r["display"] or "").lower() else 0)
                if best is None or score < best[0]:
                    best = (score, r["id"])
        if best:
            conn.execute("UPDATE imaging_file SET report_id=? WHERE path=?", (best[1], f["path"]))
