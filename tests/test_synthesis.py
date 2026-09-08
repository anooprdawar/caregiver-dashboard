from caregiver import db, demo, synthesis
from caregiver.importers import ccda

from test_importers import FIX


def test_ccda_emits_imaging_and_pathology_reports(cfg, conn):
    ccda.import_path(cfg, conn, FIX / "ucsf_ccda.xml")
    reps = {r["display"]: r for r in db.rows(conn, "SELECT * FROM diagnostic_report")}
    assert len(reps) == 3
    mri = reps["MR THORACIC SPINE W WO CONTRAST"]
    assert mri["kind"] == "imaging" and mri["effective"] == "2026-08-02"
    assert "No residual cord compression" in mri["conclusion"]
    path = reps["Surgical Pathology - bone marrow biopsy"]
    assert path["kind"] == "pathology" and "t(11;14)" in path["text"]
    # a narrative-only imaging section still becomes a report
    assert reps["Diagnostic Imaging"]["kind"] == "imaging"
    # the real lab is still a lab, not a report
    assert db.one(conn, "SELECT display, value_num FROM observation")["value_num"] == 12.5


def test_ccda_problem_status_and_no_phantom_conditions(cfg, conn):
    ccda.import_path(cfg, conn, FIX / "ucsf_ccda.xml")
    conds = {c["display"]: c for c in db.rows(conn, "SELECT * FROM condition")}
    assert set(conds) == {"Multiple myeloma, IgG kappa", "Spinal cord compression", "Essential hypertension"}
    # the nested Problem Status observations must not become diagnoses of their own
    assert "Active" not in conds and "Resolved" not in conds
    assert conds["Multiple myeloma, IgG kappa"]["clinical_status"] == "active"
    assert conds["Spinal cord compression"]["clinical_status"] == "resolved"
    # no explicit status observation: infer active from the absence of an abatement date
    assert conds["Essential hypertension"]["clinical_status"] == "active"


def test_active_problems_reach_the_overview(cfg, conn):
    from caregiver.web import queries as Q
    ccda.import_path(cfg, conn, FIX / "ucsf_ccda.xml")
    active = [c["display"] for c in Q.conditions(conn, active_only=True)]
    assert "Multiple myeloma, IgG kappa" in active and "Essential hypertension" in active
    assert "Spinal cord compression" not in active


def test_synthesis_detects_response_and_handoff_gaps(cfg, conn):
    demo.load(cfg, conn)
    s = synthesis.build(conn, weeks=12)
    titles = " | ".join(x["title"] for x in s["signals"])
    assert "Disease markers are falling" in titles
    assert "not seen this window" in titles          # orphaned prescriptions
    assert s["counts"]["labs"] > 0
    improving = {t["key"] for t in s["improving"]}
    assert "m_protein" in improving and "kappa_flc" in improving


def test_synthesis_flags_progression_from_nadir(cfg, conn):
    """A disease marker rebounding 25%+ off its nadir must raise a 'watch', not stay silent."""
    demo.load(cfg, conn)
    pid = db.get_meta(conn, "patient_id") or "demo-patient"
    from datetime import date, timedelta
    for i, (days_ago, val) in enumerate([(30, 0.20), (14, 0.35), (2, 0.60)]):
        d = (date.today() - timedelta(days=days_ago)).isoformat()
        db.upsert(conn, "observation", {"id": f"rise-{i}", "patient_id": pid, "effective": d,
                                        "code": "33358-3", "display": "M-spike", "value_num": val,
                                        "value_unit": "g/dL", "panel_key": "m_protein", "source": "demo"})
    conn.commit()
    s = synthesis.build(conn, weeks=12)
    watch = [x for x in s["signals"] if x["severity"] == "watch"]
    assert any("risen from its low point" in x["title"] for x in watch), [x["title"] for x in s["signals"]]


def test_summary_routes(cfg, conn):
    from fastapi.testclient import TestClient
    from caregiver.web.app import create_app
    demo.load(cfg, conn)
    c = TestClient(create_app(cfg))
    for path in ["/summary", "/summary?weeks=4", "/summary?weeks=52", "/summary.md", "/"]:
        r = c.get(path)
        assert r.status_code == 200, path
    assert "12-week summary" in c.get("/summary.md").text or "week summary" in c.get("/summary.md").text
    # out-of-range weeks are clamped, not crashed on
    assert c.get("/summary?weeks=99999").status_code == 200
    assert c.get("/summary?weeks=0").status_code == 200


def test_reload_reparses_stored_ccda(cfg, conn):
    """After a parser upgrade the user must not need the original download again."""
    from caregiver.fhir.sync import reload_all
    ccda.import_path(cfg, conn, FIX / "ucsf_ccda.xml")
    conn.execute("DELETE FROM diagnostic_report")
    conn.execute("INSERT INTO condition (id, display, clinical_status, source) VALUES "
                 "('stale', 'Phantom from old parser', 'completed', 'ccda:ucsf_ccda.xml')")
    conn.commit()
    r = reload_all(cfg, conn)
    assert r["ccda_files"] == 1 and r["ccda_rows"] > 0
    assert db.counts(conn)["diagnostic_report"] == 3
    assert db.one(conn, "SELECT id FROM condition WHERE id='stale'") is None
