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


def _apple_export_zip(tmp_path):
    """Build a zip shaped like iPhone 'Export All Health Data': apple_health_export/clinical-records/*.json"""
    import base64
    import zipfile

    def b64(s):
        return base64.b64encode(s.encode()).decode()

    records = {
        "Observation-A1.json": {
            "resourceType": "Observation", "id": "A1", "status": "final",
            "category": [{"coding": [{"code": "laboratory"}]}],
            "code": {"coding": [{"system": "http://loinc.org", "code": "36916-5"}], "text": "Kappa Free Light Chain"},
            "subject": {"reference": "Patient/UCSF-1"}, "effectiveDateTime": "2026-08-14T07:32:00Z",
            "valueQuantity": {"value": 41.2, "unit": "mg/L"},
            "referenceRange": [{"low": {"value": 3.3}, "high": {"value": 19.4}}],
            "interpretation": [{"coding": [{"code": "H", "display": "High"}]}]},
        "DiagnosticReport-R9.json": {
            "resourceType": "DiagnosticReport", "id": "R9", "status": "final",
            "category": [{"coding": [{"code": "RAD", "display": "Radiology"}]}],
            "code": {"text": "MR THORACIC SPINE W WO CONTRAST"}, "subject": {"reference": "Patient/UCSF-1"},
            "effectiveDateTime": "2026-08-02T14:10:00Z",
            "presentedForm": [{"contentType": "text/html", "data": b64("<p>IMPRESSION: No residual cord compression.</p>")}]},
        "DocumentReference-N3.json": {
            "resourceType": "DocumentReference", "id": "N3", "status": "current", "type": {"text": "Progress Notes"},
            "subject": {"reference": "Patient/UCSF-1"}, "date": "2026-08-14T18:00:00Z",
            "author": [{"display": "Samuel Chen, MD"}], "description": "Heme/Onc progress note",
            "content": [{"attachment": {"contentType": "text/plain", "data": b64("M-protein down to 0.2.")}}]},
        "MedicationRequest-M2.json": {
            "resourceType": "MedicationRequest", "id": "M2", "status": "active", "intent": "order",
            "medicationCodeableConcept": {"coding": [{"system": "http://www.nlm.nih.gov/research/umls/rxnorm",
                                                      "code": "643035", "display": "lenalidomide 15 MG capsule"}]},
            "subject": {"reference": "Patient/UCSF-1"}, "authoredOn": "2026-06-04",
            "requester": {"display": "Samuel Chen, MD"},
            "dosageInstruction": [{"text": "15 mg PO daily days 1-21 of 28"}]},
    }
    z = tmp_path / "export.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for name, res in records.items():
            zf.writestr(f"apple_health_export/clinical-records/{name}", json.dumps(res))
        zf.writestr("apple_health_export/export.xml", "<HealthData>step counts, not FHIR</HealthData>")
    return z


def test_apple_health_export_zip(cfg, conn, tmp_path):
    """The iPhone Health export is the supported sync path; guard its exact shape."""
    counts = fhir_bundle.import_path(cfg, conn, _apple_export_zip(tmp_path))
    assert counts == {"Observation": 1, "DiagnosticReport": 1, "DocumentReference": 1, "MedicationRequest": 1}

    obs = db.one(conn, "SELECT * FROM observation")
    assert obs["panel_key"] == "kappa_flc" and obs["value_num"] == 41.2 and obs["interpretation"] == "High"

    rep = db.one(conn, "SELECT * FROM diagnostic_report")
    assert rep["kind"] == "imaging" and "No residual cord compression" in rep["text"]

    doc = db.one(conn, "SELECT * FROM document")
    assert doc["content_text"] == "M-protein down to 0.2." and doc["author"] == "Samuel Chen, MD"

    med = db.one(conn, "SELECT * FROM medication")
    assert med["medication"] == "lenalidomide 15 MG capsule" and med["requester"] == "Samuel Chen, MD"

    # re-importing the same export must not duplicate
    fhir_bundle.import_path(cfg, conn, _apple_export_zip(tmp_path))
    assert db.counts(conn)["observation"] == 1
