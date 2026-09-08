import pytest

from caregiver import db, demo, interventions, journey


@pytest.mark.parametrize("text,expected", [
    ("Transfusion of 2 units packed red blood cells", "rbc"),
    ("PRBC transfusion", "rbc"),
    ("Blood transfusion", "rbc"),
    ("Platelet transfusion, apheresis (1 unit)", "platelet"),
    ("filgrastim 480 MCG/0.8ML injection", "gcsf"),
    ("pegfilgrastim 6 MG/0.6ML injection", "gcsf"),
    ("Neulasta 6 mg SC", "gcsf"),
    ("epoetin alfa 10000 units", "esa"),
    ("ferumoxytol 510 mg IV", "iron"),
    ("immune globulin 10% IV", "ivig"),
    ("romiplostim 250 mcg", "tpo"),
    # things that must not be mistaken for an intervention
    ("Type and screen", None),
    ("lenalidomide 25 MG capsule", None),
    ("dexamethasone 20 MG tablet", None),
    ("Hemoglobin", None),
])
def test_classifier(text, expected):
    got = interventions.classify(text)
    assert (got.key if got else None) == expected


def test_markers_land_on_the_right_charts(cfg, conn):
    demo.load(cfg, conn)
    ev = interventions.detect(conn)
    hgb = interventions.markers_for(ev, "hemoglobin")
    wbc = interventions.markers_for(ev, "wbc")
    anc = interventions.markers_for(ev, "anc")
    plt = interventions.markers_for(ev, "platelets")

    assert {m["key"] for m in hgb} == {"rbc"}          # transfusions, not growth factor
    assert {m["key"] for m in wbc} == {"gcsf"}         # growth factor, not transfusions
    assert {m["key"] for m in anc} == {"gcsf"}         # every white-count chart
    assert {m["key"] for m in plt} == {"platelet"}
    assert len(hgb) == 6 and len(plt) == 2
    assert all("d" in m and "color" in m for m in hgb)


def test_transfusion_tally(cfg, conn):
    demo.load(cfg, conn)
    t = interventions.tally(interventions.detect(conn))
    assert t["transfusion_days"] == 8                  # 6 RBC + 2 platelet days
    assert t["transfusion_first"] < t["transfusion_last"]
    by = {p["key"]: p["count"] for p in t["by_type"]}
    assert by["rbc"] == 6 and by["platelet"] == 2 and by["gcsf"] == 7
    # growth factor is supportive care but is not a transfusion
    assert "gcsf" not in {p["key"] for p in t["transfusions_by_type"]}


def test_same_type_twice_in_one_day_counts_once(cfg, conn):
    demo.load(cfg, conn)
    before = interventions.tally(interventions.detect(conn))["transfusion_days"]
    day = interventions.detect(conn)[0]["date"]
    db.upsert(conn, "procedure", {"id": "dup-tx", "performed": day, "status": "completed",
                                  "display": "Transfusion of 1 unit packed red blood cells", "source": "demo"})
    conn.commit()
    assert interventions.tally(interventions.detect(conn))["transfusion_days"] == before


def test_panel_series_carries_markers(cfg, conn):
    from caregiver.web import queries as Q
    demo.load(cfg, conn)
    series = Q.panel_series(conn)
    hgb = next(s for s in series["crab"] if s["key"] == "hemoglobin")
    assert hgb["markers"] and hgb["markers"][0]["key"] == "rbc"
    m_protein = next(s for s in series["disease"] if s["key"] == "m_protein")
    assert m_protein["markers"] == []                  # nothing to overlay on a disease marker


def test_journey_reconstructs_the_course_in_order(cfg, conn):
    demo.load(cfg, conn)
    j = journey.build(conn)
    order = [s["key"] for s in j["found"]]
    got = {s["key"]: s for s in j["found"]}

    for key in ("presentation", "finding", "surgery", "workup", "biopsy", "diagnosis",
                "radiation", "treatment", "rehab", "response"):
        assert key in got, f"{key} not detected"
    # the disease itself is the diagnosis, not the complication that presented
    assert "Multiple myeloma" in got["diagnosis"]["title"]
    assert "cord compression" in got["finding"]["title"].lower()
    # and it is dated after the work-up that found it, not before
    assert got["diagnosis"]["date"] > got["workup"]["date"]
    assert order.index("presentation") < order.index("surgery") < order.index("treatment")
    assert not j["out_of_order"]


def test_journey_ignores_complications_that_merely_name_the_disease(cfg, conn):
    demo.load(cfg, conn)
    j = journey.build(conn)
    dx = next(s for s in j["found"] if s["key"] == "diagnosis")
    assert "kidney" not in dx["title"].lower() and "anemia" not in dx["title"].lower()


def test_caregiver_milestone_overrides_detection(cfg, conn):
    from caregiver.web import queries as Q
    demo.load(cfg, conn)
    Q.add_note(conn, journey.MILESTONE_KIND, "Told she might never walk again", "",
               owner="presentation", event_date="2026-02-19")
    j = journey.build(conn)
    pres = next(s for s in j["found"] if s["key"] == "presentation")
    assert pres["title"] == "Told she might never walk again" and pres["origin"] == "caregiver"


def test_journey_reports_impossible_ordering(cfg, conn):
    """Treatment dated before the diagnosis means something was mis-detected; say so."""
    demo.load(cfg, conn)
    conn.execute("UPDATE condition SET onset='2026-12-01', recorded='2026-12-01' "
                 "WHERE display LIKE '%Multiple myeloma%'")
    conn.commit()
    assert any("before" in w for w in journey.build(conn)["out_of_order"])


def test_intervention_routes(cfg, conn):
    from fastapi.testclient import TestClient
    from caregiver.web.app import create_app
    demo.load(cfg, conn)
    c = TestClient(create_app(cfg))
    r = c.get("/interventions")
    assert r.status_code == 200 and "transfusion day" in r.text
    assert "Red blood cell transfusion" in c.get("/labs").text
    assert c.get("/labs/test/Hemoglobin").status_code == 200
    assert "Course of illness" in c.get("/").text
