import pytest
from fastapi.testclient import TestClient

from caregiver import db, demo
from caregiver.web import queries as Q
from caregiver.web.app import create_app


@pytest.fixture
def client(cfg, conn):
    demo.load(cfg, conn)
    return TestClient(create_app(cfg))


ROUTES = ["/", "/timeline", "/timeline?kinds=medication&q=dex", "/labs", "/labs/all", "/labs/test/Hemoglobin", "/reports",
          "/reports/dr-mri", "/documents", "/documents/doc-dc", "/medications", "/encounters", "/encounters/e-inpt",
          "/team", "/imaging", "/notes", "/brief", "/brief.md", "/search?q=lisinopril"]


@pytest.mark.parametrize("path", ROUTES)
def test_routes_render(client, path):
    r = client.get(path)
    assert r.status_code == 200, r.text[:300]
    assert len(r.text) > 300


def test_note_lifecycle(client, conn):
    r = client.post("/notes", data={"kind": "issue", "title": "Check sulfa", "body": "x"}, follow_redirects=False)
    assert r.status_code == 303
    n = db.one(conn, "SELECT * FROM note WHERE title='Check sulfa'")
    assert n["status"] == "open"
    client.post(f"/notes/{n['id']}/status", data={"status": "closed"})
    assert db.one(conn, "SELECT status FROM note WHERE id=?", (n["id"],))["status"] == "closed"
    client.post(f"/notes/{n['id']}/delete", data={})
    assert db.one(conn, "SELECT id FROM note WHERE id=?", (n["id"],)) is None


def test_medication_grouping_shows_latest_dose(client, conn):
    meds = Q.medications(conn)
    len_ = next(m for m in meds["active"] if m["key"] == "lenalidomide")
    assert len_["changes"] == 2 and "15 MG" in len_["name"]
    assert any(m["key"] == "oxycodone" for m in meds["stopped"])


def test_panel_latest_flags_crab(client, conn):
    panel = {p["key"]: p for p in Q.panel_latest(conn)}
    assert panel["m_protein"]["delta_pct"] < 0  # responding
    assert panel["hemoglobin"]["flag"] in ("", "low")


def test_brief_markdown(client):
    md = client.get("/brief.md").text
    for h in ("## Active problems", "## Key labs", "## Current medications", "## Open questions"):
        assert h in md
    assert "Bactrim" in md
