import base64
from caregiver import myeloma
from caregiver.fhir import normalize as N


def test_observation_quantity_and_panel():
    res = {"resourceType": "Observation", "id": "o1", "status": "final",
           "category": [{"coding": [{"code": "laboratory"}]}],
           "code": {"coding": [{"system": "http://loinc.org", "code": "36916-5", "display": "Kappa FLC"}]},
           "subject": {"reference": "Patient/p"}, "effectiveDateTime": "2026-01-02T08:00:00Z",
           "valueQuantity": {"value": 1850, "unit": "mg/L"},
           "referenceRange": [{"low": {"value": 3.3}, "high": {"value": 19.4}}],
           "interpretation": [{"coding": [{"code": "H", "display": "High"}]}]}
    rows = N.normalize(res, "t")
    assert len(rows) == 1
    table, row = rows[0]
    assert table == "observation"
    assert row["value_num"] == 1850 and row["panel_key"] == "kappa_flc"
    assert row["ref_low"] == 3.3 and row["ref_text"] == "3.3–19.4"
    assert row["category"] == "laboratory"  # falls back to code when no display


def test_observation_components_split():
    res = {"resourceType": "Observation", "id": "bp", "code": {"text": "BP"}, "subject": {"reference": "Patient/p"},
           "effectiveDateTime": "2026-01-01", "component": [
               {"code": {"coding": [{"system": "http://loinc.org", "code": "8480-6"}]}, "valueQuantity": {"value": 140, "unit": "mmHg"}},
               {"code": {"coding": [{"system": "http://loinc.org", "code": "8462-4"}]}, "valueQuantity": {"value": 90, "unit": "mmHg"}}]}
    rows = N.normalize(res, "t")
    assert [r["panel_key"] for _, r in rows] == ["bp_systolic", "bp_diastolic"]
    assert rows[0][1]["id"] == "bp-c0"


def test_value_string_numeric_parsing():
    res = {"resourceType": "Observation", "id": "o", "code": {"text": "Creatinine"}, "valueString": "1.8 mg/dL", "subject": {"reference": "Patient/p"}}
    _, row = N.normalize(res, "t")[0]
    assert row["value_num"] == 1.8 and row["panel_key"] == "creatinine"
    res["valueString"] = "<0.1"
    _, row = N.normalize(res, "t")[0]
    assert row["value_num"] is None and row["value_str"] == "<0.1"


def test_diagnostic_report_kind_and_text():
    text = "IMPRESSION: cord compression"
    res = {"resourceType": "DiagnosticReport", "id": "r", "status": "final", "subject": {"reference": "Patient/p"},
           "category": [{"coding": [{"code": "RAD", "display": "Radiology"}]}], "code": {"text": "MRI thoracic spine"},
           "effectiveDateTime": "2026-01-01", "presentedForm": [{"contentType": "text/html", "data": base64.b64encode(b"<p>IMPRESSION:</p><p>cord compression</p>").decode()}]}
    _, row = N.normalize(res, "t")[0]
    assert row["kind"] == "imaging" and "cord compression" in row["text"]


def test_document_reference_binary_inlined():
    res = {"resourceType": "DocumentReference", "id": "d", "status": "current", "subject": {"reference": "Patient/p"}, "date": "2026-01-01",
           "type": {"text": "Progress Notes"}, "content": [{"attachment": {"contentType": "text/html", "url": "Binary/abc"}}],
           "context": {"encounter": [{"reference": "Encounter/e1"}]}}
    _, row = N.normalize(res, "t", binaries={"abc": b"<b>hello</b> world"})[0]
    assert row["content_text"] == "hello world" and row["encounter_id"] == "e1"


def test_medication_request_and_dstu2_order():
    res = {"resourceType": "MedicationOrder", "id": "m", "status": "active", "dateWritten": "2026-01-01",
           "medicationCodeableConcept": {"coding": [{"system": "http://www.nlm.nih.gov/research/umls/rxnorm", "code": "643037", "display": "lenalidomide 25 MG"}]},
           "patient": {"reference": "Patient/p"}, "dosageInstruction": [{"text": "daily x21"}], "prescriber": {"display": "Dr. C"}}
    _, row = N.normalize(res, "t")[0]
    assert row["medication"] == "lenalidomide 25 MG" and row["rxnorm"] == "643037" and row["requester"] == "Dr. C" and row["authored"] == "2026-01-01"


def test_classify_by_name_patterns():
    assert myeloma.classify(None, "Free Kappa Light Chains, Serum") == "kappa_flc"
    assert myeloma.classify(None, "Hemoglobin A1c") is None
    assert myeloma.classify(None, "Hemoglobin") == "hemoglobin"
    assert myeloma.classify("2160-0", "Whatever") == "creatinine"
    assert myeloma.crab_flag("calcium", 11.5) and not myeloma.crab_flag("calcium", 9.0)


def test_iter_resources_bundle():
    b = {"resourceType": "Bundle", "entry": [{"resource": {"resourceType": "Patient", "id": "1"}}, {"resource": {"resourceType": "Bundle", "entry": [{"resource": {"resourceType": "Condition", "id": "2"}}]}}]}
    assert [r["id"] for r in N.iter_resources(b)] == ["1", "2"]
