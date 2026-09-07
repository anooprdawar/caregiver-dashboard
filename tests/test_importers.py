import json
from pathlib import Path

from caregiver import db
from caregiver.importers import ccda, fhir_bundle

FIX = Path(__file__).parent / "fixtures"


def test_ccda_import(cfg, conn):
    counts = ccda.import_path(cfg, conn, FIX / "sample_ccda.xml")
    assert counts["observation"] == 2 and counts["medication"] == 1 and counts["condition"] == 1
    obs = {r["display"]: r for r in db.rows(conn, "SELECT * FROM observation")}
    assert obs["M-spike"]["panel_key"] == "m_protein" and obs["M-spike"]["interpretation"] == "H"
    assert obs["Calcium"]["ref_low"] == 8.6 and obs["Calcium"]["value_num"] == 11.4
    med = db.one(conn, "SELECT * FROM medication")
    assert med["medication"].startswith("lenalidomide") and med["start"] == "2026-02-16"
    doc = db.one(conn, "SELECT * FROM document")
    assert "cord compression" in doc["content_text"] and doc["author"] == "Samuel Chen"
    assert db.one(conn, "SELECT * FROM patient")["mrn"] == "MRN123"
    # idempotent
    ccda.import_path(cfg, conn, FIX / "sample_ccda.xml")
    assert db.counts(conn)["observation"] == 2


def test_fhir_folder_import(cfg, conn, tmp_path):
    d = tmp_path / "clinical-records"
    d.mkdir()
    (d / "a.json").write_text(json.dumps({"resourceType": "Observation", "id": "x", "code": {"text": "Platelets"},
                                          "subject": {"reference": "Patient/p"}, "effectiveDateTime": "2026-01-01",
                                          "valueQuantity": {"value": 120, "unit": "K/uL"}}))
    (d / "junk.json").write_text("not json")
    counts = fhir_bundle.import_path(cfg, conn, d)
    assert counts == {"Observation": 1}
    assert (cfg.raw_dir / "Observation" / "x.json").exists()
    assert db.one(conn, "SELECT panel_key FROM observation")["panel_key"] == "platelets"
