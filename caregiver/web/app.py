"""Local dashboard. Binds to 127.0.0.1 only; there is no auth because there is no network exposure."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import brief, db, myeloma, synthesis, textfmt
from ..config import Config, load
from . import queries as Q

HERE = Path(__file__).parent


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or load()
    cfg.ensure_dirs()
    app = FastAPI(title="Caregiver Dashboard")
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    tpl = Jinja2Templates(directory=HERE / "templates")
    tpl.env.filters["day"] = lambda s: (s or "")[:10]
    tpl.env.filters["dt"] = lambda s: (s or "")[:16].replace("T", " ")
    tpl.env.filters["num"] = lambda v: "" if v is None else (f"{v:g}" if abs(v) >= 1 else f"{v:.2f}")
    tpl.env.filters["nl2br"] = lambda s: (s or "").replace("\n", "<br>")
    tpl.env.filters["clinical"] = textfmt.render
    tpl.env.filters["gist"] = textfmt.summarize
    tpl.env.globals.update(label=cfg.patient_label, GROUP_LABELS=myeloma.GROUP_LABELS, NOTE_KINDS=Q.NOTE_KINDS)

    def conn() -> sqlite3.Connection:
        return db.connect(cfg.db_path)

    def render(request: Request, name: str, **ctx):
        c = conn()
        try:
            ctx.setdefault("open_count", len(Q.notes(c, status="open")))
            ctx.setdefault("last_sync", db.get_meta(c, "last_sync"))
            ctx.setdefault("is_demo", db.get_meta(c, "demo") == "1")
        finally:
            c.close()
        return tpl.TemplateResponse(request, name, ctx)

    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request):
        c = conn()
        try:
            return render(request, "overview.html", page="overview", syn=synthesis.build(c), **Q.overview(c))
        finally:
            c.close()

    @app.get("/summary", response_class=HTMLResponse)
    def summary(request: Request, weeks: int = synthesis.DEFAULT_WEEKS):
        c = conn()
        try:
            weeks = max(1, min(weeks, 260))
            return render(request, "summary.html", page="summary", s=synthesis.build(c, weeks), weeks=weeks)
        finally:
            c.close()

    @app.get("/summary.md", response_class=PlainTextResponse)
    def summary_md(weeks: int = synthesis.DEFAULT_WEEKS):
        c = conn()
        try:
            return synthesis.markdown(c, max(1, min(weeks, 260)), cfg.patient_label)
        finally:
            c.close()

    @app.get("/timeline", response_class=HTMLResponse)
    def timeline(request: Request, kinds: str = "", q: str = "", since: str = ""):
        c = conn()
        try:
            ks = set(k for k in kinds.split(",") if k) or None
            items = Q.timeline(c, ks, q or None, since or None)
            return render(request, "timeline.html", page="timeline", items=items, kinds=ks or set(), q=q, since=since,
                          all_kinds=["encounter", "imaging", "pathology", "other", "lab", "document", "medication",
                                     "procedure", "condition", "appointment", "note"])
        finally:
            c.close()

    @app.get("/labs", response_class=HTMLResponse)
    def labs(request: Request, date: str = ""):
        c = conn()
        try:
            series = Q.panel_series(c)
            day = Q.labs_on(c, date) if date else []
            days = db.rows(c, "SELECT substr(effective,1,10) d, COUNT(*) n FROM observation WHERE category LIKE '%aborator%' GROUP BY d ORDER BY d DESC")
            return render(request, "labs.html", page="labs", series=series, series_json=json.dumps(series), date=date,
                          day_results=day, days=days)
        finally:
            c.close()

    @app.get("/labs/all", response_class=HTMLResponse)
    def labs_all(request: Request, q: str = ""):
        c = conn()
        try:
            return render(request, "labs_all.html", page="labs", tests=Q.all_tests(c, q or None), q=q)
        finally:
            c.close()

    @app.get("/labs/test/{display}", response_class=HTMLResponse)
    def lab_test(request: Request, display: str):
        c = conn()
        try:
            rows = Q.test_series(c, display)
            pts = [{"d": (r["effective"] or "")[:10], "v": r["value_num"], "lo": r["ref_low"], "hi": r["ref_high"]} for r in rows if r["value_num"] is not None]
            return render(request, "lab_test.html", page="labs", display=display, rows=rows, points_json=json.dumps(pts))
        finally:
            c.close()

    @app.get("/reports", response_class=HTMLResponse)
    def reports(request: Request, kind: str = ""):
        c = conn()
        try:
            return render(request, "reports.html", page="reports", reports=Q.reports(c, kind or None), kind=kind)
        finally:
            c.close()

    @app.get("/reports/{rid}", response_class=HTMLResponse)
    def report(request: Request, rid: str):
        c = conn()
        try:
            r = Q.report(c, rid)
            if not r:
                return PlainTextResponse("not found", 404)
            return render(request, "report.html", page="reports", r=r, notes=db.rows(c, "SELECT * FROM note WHERE related_type='report' AND related_id=?", (rid,)))
        finally:
            c.close()

    @app.get("/documents", response_class=HTMLResponse)
    def documents(request: Request):
        c = conn()
        try:
            return render(request, "documents.html", page="documents", docs=Q.documents(c))
        finally:
            c.close()

    @app.get("/documents/{did}", response_class=HTMLResponse)
    def document(request: Request, did: str):
        c = conn()
        try:
            d = Q.document(c, did)
            if not d:
                return PlainTextResponse("not found", 404)
            return render(request, "document.html", page="documents", d=d)
        finally:
            c.close()

    @app.get("/medications", response_class=HTMLResponse)
    def medications(request: Request):
        c = conn()
        try:
            return render(request, "medications.html", page="medications", **Q.medications(c),
                          allergies=db.rows(c, "SELECT * FROM allergy"))
        finally:
            c.close()

    @app.get("/encounters", response_class=HTMLResponse)
    def encounters(request: Request):
        c = conn()
        try:
            return render(request, "encounters.html", page="encounters", encs=Q.encounters(c))
        finally:
            c.close()

    @app.get("/encounters/{eid}", response_class=HTMLResponse)
    def encounter(request: Request, eid: str):
        c = conn()
        try:
            e = Q.encounter(c, eid)
            if not e:
                return PlainTextResponse("not found", 404)
            return render(request, "encounter.html", page="encounters", e=e)
        finally:
            c.close()

    @app.get("/team", response_class=HTMLResponse)
    def team(request: Request):
        c = conn()
        try:
            return render(request, "team.html", page="team", team=Q.care_team(c),
                          handoffs=Q.notes(c, kind="handoff"))
        finally:
            c.close()

    @app.get("/imaging", response_class=HTMLResponse)
    def imaging(request: Request):
        c = conn()
        try:
            return render(request, "imaging.html", page="imaging", imaging_dir=str(cfg.imaging_dir), **Q.imaging(c))
        finally:
            c.close()

    @app.get("/notes", response_class=HTMLResponse)
    def notes(request: Request, status: str = "", kind: str = ""):
        c = conn()
        try:
            return render(request, "notes.html", page="notes", notes=Q.notes(c, status or None, kind or None),
                          status=status, kind=kind)
        finally:
            c.close()

    @app.post("/notes")
    def add_note(kind: str = Form("question"), title: str = Form(...), body: str = Form(""), due: str = Form(""),
                 owner: str = Form(""), related_type: str = Form(""), related_id: str = Form(""), event_date: str = Form(""),
                 back: str = Form("/notes")):
        c = conn()
        try:
            Q.add_note(c, kind, title.strip(), body.strip(), due, owner, related_type, related_id, event_date)
        finally:
            c.close()
        return RedirectResponse(back or "/notes", status_code=303)

    @app.post("/notes/{nid}/status")
    def note_status(nid: int, status: str = Form(...), back: str = Form("/notes")):
        c = conn()
        try:
            Q.set_note_status(c, nid, status)
        finally:
            c.close()
        return RedirectResponse(back, status_code=303)

    @app.post("/notes/{nid}/delete")
    def note_delete(nid: int, back: str = Form("/notes")):
        c = conn()
        try:
            Q.delete_note(c, nid)
        finally:
            c.close()
        return RedirectResponse(back, status_code=303)

    @app.get("/brief", response_class=HTMLResponse)
    def brief_html(request: Request):
        c = conn()
        try:
            return render(request, "brief.html", page="brief", b=brief.build(c, cfg.patient_label))
        finally:
            c.close()

    @app.get("/brief.md", response_class=PlainTextResponse)
    def brief_md():
        c = conn()
        try:
            return brief.markdown(c, cfg.patient_label)
        finally:
            c.close()

    @app.get("/search", response_class=HTMLResponse)
    def search(request: Request, q: str = ""):
        c = conn()
        try:
            ql = f"%{q}%"
            hits = {
                "documents": db.rows(c, "SELECT id, date, title, author FROM document WHERE content_text LIKE ? OR title LIKE ? ORDER BY date DESC LIMIT 50", (ql, ql)),
                "reports": db.rows(c, "SELECT id, effective, display, kind FROM diagnostic_report WHERE text LIKE ? OR conclusion LIKE ? OR display LIKE ? ORDER BY effective DESC LIMIT 50", (ql, ql, ql)),
                "medications": db.rows(c, "SELECT id, authored, medication, dosage, status FROM medication WHERE medication LIKE ? OR dosage LIKE ? OR reason LIKE ? LIMIT 50", (ql, ql, ql)),
                "observations": db.rows(c, "SELECT display, COUNT(*) n FROM observation WHERE display LIKE ? GROUP BY display LIMIT 50", (ql,)),
                "notes": db.rows(c, "SELECT id, kind, title, status FROM note WHERE title LIKE ? OR body LIKE ? LIMIT 50", (ql, ql)),
            } if q else {}
            return render(request, "search.html", page="search", q=q, hits=hits)
        finally:
            c.close()

    return app


app = None  # created lazily by `caregiver serve`
