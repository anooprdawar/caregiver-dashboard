import json
import time
import zipfile
from pathlib import Path

import pytest

from caregiver import db, ingest, watcher

from test_importers import FIX


def _ccda_bytes():
    return (FIX / "ucsf_ccda.xml").read_bytes()


def _fhir_obj():
    return {"resourceType": "Observation", "id": "w1", "status": "final",
            "code": {"coding": [{"system": "http://loinc.org", "code": "718-7"}], "text": "Hemoglobin"},
            "subject": {"reference": "Patient/p"}, "effectiveDateTime": "2026-09-01",
            "valueQuantity": {"value": 9.1, "unit": "g/dL"}}


@pytest.mark.parametrize("name,write,expected", [
    ("a.xml", lambda p: p.write_bytes(_ccda_bytes()), "ccda"),
    ("b.json", lambda p: p.write_text(json.dumps(_fhir_obj())), "fhir"),
    ("junk.txt", lambda p: p.write_text("hello"), "unknown"),
    ("empty.xml", lambda p: p.write_text(""), "empty"),
])
def test_detect_kind_by_content(tmp_path, name, write, expected):
    p = tmp_path / name
    write(p)
    assert ingest.detect_kind(p) == expected


def test_detect_kind_inside_zips(tmp_path):
    """MyChart and Apple both hand you a .zip; only the content distinguishes them."""
    mychart = tmp_path / "MyChart.zip"
    with zipfile.ZipFile(mychart, "w") as z:
        z.writestr("Documents/summary.xml", _ccda_bytes())
    apple = tmp_path / "export.zip"
    with zipfile.ZipFile(apple, "w") as z:
        z.writestr("apple_health_export/clinical-records/o.json", json.dumps(_fhir_obj()))
        z.writestr("apple_health_export/export.xml", "<HealthData>steps</HealthData>")
    both = tmp_path / "both.zip"
    with zipfile.ZipFile(both, "w") as z:
        z.writestr("a.xml", _ccda_bytes())
        z.writestr("b.json", json.dumps(_fhir_obj()))

    assert ingest.detect_kind(mychart) == "ccda"
    assert ingest.detect_kind(apple) == "fhir"
    assert ingest.detect_kind(both) == "mixed"
    assert ingest.detect_kind(tmp_path / "nope.zip") == "unknown"


def test_import_any_routes_to_the_right_importer(cfg, conn, tmp_path):
    z = tmp_path / "MyChart.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("summary.xml", _ccda_bytes())
    r = ingest.import_any(cfg, conn, z)
    assert r["kind"] == "ccda" and r["rows"] > 0
    assert db.counts(conn)["diagnostic_report"] == 3          # the imaging/pathology fix still applies

    j = tmp_path / "o.json"
    j.write_text(json.dumps(_fhir_obj()))
    r2 = ingest.import_any(cfg, conn, j)
    assert r2["kind"] == "fhir"
    assert db.one(conn, "SELECT panel_key FROM observation WHERE id='w1'")["panel_key"] == "hemoglobin"


def test_watch_imports_new_files_once_each(cfg, conn, tmp_path, monkeypatch):
    monkeypatch.setattr(watcher, "SETTLE_SECONDS", 0.01)
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "MyChart.xml").write_bytes(_ccda_bytes())
    (downloads / "notes.txt").write_text("shopping list")

    first = watcher.watch(cfg, downloads, once=True)
    assert len(first) == 1 and first[0]["kind"] == "ccda"
    assert db.counts(conn)["condition"] == 3

    # a second pass must not re-import the same download
    assert watcher.watch(cfg, downloads, once=True) == []

    # a genuinely new file is picked up
    (downloads / "later.json").write_text(json.dumps(_fhir_obj()))
    third = watcher.watch(cfg, downloads, once=True)
    assert len(third) == 1 and third[0]["kind"] == "fhir"


def test_watch_ignores_partial_downloads_and_old_files(cfg, conn, tmp_path, monkeypatch):
    monkeypatch.setattr(watcher, "SETTLE_SECONDS", 0.01)
    d = tmp_path / "dl"
    d.mkdir()
    (d / "half.crdownload").write_bytes(_ccda_bytes())
    (d / "part.zip.part").write_bytes(b"x")
    assert watcher.candidates(d) == []

    old = d / "ancient.xml"
    old.write_bytes(_ccda_bytes())
    import os
    long_ago = time.time() - 90 * 86400
    os.utime(old, (long_ago, long_ago))
    assert watcher.candidates(d, max_age_days=30) == []
    assert watcher.candidates(d, max_age_days=None) == [old]


def test_identical_file_under_a_new_name_is_not_reimported(cfg, conn, tmp_path, monkeypatch):
    monkeypatch.setattr(watcher, "SETTLE_SECONDS", 0.01)
    d = tmp_path / "dl"
    d.mkdir()
    (d / "MyChart.xml").write_bytes(_ccda_bytes())
    assert len(watcher.watch(cfg, d, once=True)) == 1
    (d / "MyChart (1).xml").write_bytes(_ccda_bytes())      # the classic second download
    assert watcher.watch(cfg, d, once=True) == []


def test_import_log_survives_a_database_made_before_it_existed(cfg, tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    sqlite3.connect(str(path)).close()
    conn = db.connect(path)
    assert not ingest.already_imported(conn, "abc")
    ingest.record_import(conn, Path("x.xml"), "abc", {"kind": "ccda", "rows": 3})
    assert ingest.already_imported(conn, "abc")
    conn.close()
