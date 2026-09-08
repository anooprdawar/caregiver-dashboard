"""Read models for the dashboard. Pure SQL -> dicts; templates stay dumb."""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from .. import db, myeloma

ACTIVE_MED_STATUS = ("active", "on-hold", "completed", None)  # Epic marks long-running as 'active'


def _d(s: str | None) -> str:
    return (s or "")[:10]


def _load(rows: list[dict], *json_cols: str) -> list[dict]:
    for r in rows:
        for c in json_cols:
            if isinstance(r.get(c), str):
                try:
                    r[c] = json.loads(r[c])
                except json.JSONDecodeError:
                    pass
        r.pop("raw", None)
    return rows


# ------------------------------------------------------------------ overview
def patient(conn) -> dict | None:
    p = db.one(conn, "SELECT * FROM patient ORDER BY (source LIKE 'epic%') DESC LIMIT 1")
    if p:
        p.pop("raw", None)
        if p.get("birth_date"):
            try:
                b = date.fromisoformat(p["birth_date"][:10])
                t = date.today()
                p["age"] = t.year - b.year - ((t.month, t.day) < (b.month, b.day))
            except ValueError:
                pass
    return p


def conditions(conn, active_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM condition"
    if active_only:
        sql += " WHERE (clinical_status IS NULL OR clinical_status IN ('active','recurrence','relapse')) AND abatement IS NULL"
    sql += " ORDER BY COALESCE(onset, recorded) DESC"
    rows = _load(db.rows(conn, sql))
    # de-dup same display (Epic lists encounter diagnoses repeatedly)
    seen, out = set(), []
    for r in rows:
        k = (r["display"] or r["code"] or "").lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def care_team(conn) -> list[dict]:
    pracs = {r["id"]: {**r, "encounters": 0, "last_seen": None, "first_seen": None}
             for r in _load(db.rows(conn, "SELECT * FROM practitioner"))}
    for e in db.rows(conn, "SELECT id, start, practitioners FROM encounter"):
        try:
            parts = json.loads(e["practitioners"] or "[]")
        except json.JSONDecodeError:
            parts = []
        for p in parts:
            pid = p.get("id") or f"name:{p.get('name')}"
            if pid not in pracs:
                pracs[pid] = {"id": pid, "name": p.get("name"), "specialty": None, "role": p.get("type"),
                              "encounters": 0, "last_seen": None, "first_seen": None}
            pr = pracs[pid]
            pr["encounters"] += 1
            d = _d(e["start"])
            if d:
                pr["last_seen"] = max(pr["last_seen"] or "", d)
                pr["first_seen"] = min(pr["first_seen"] or "9999", d)
    # prescriptions written
    for m in db.rows(conn, "SELECT requester, COUNT(*) n FROM medication WHERE requester IS NOT NULL GROUP BY requester"):
        for pr in pracs.values():
            if pr.get("name") and pr["name"].lower() in (m["requester"] or "").lower():
                pr["prescriptions"] = m["n"]
    out = [p for p in pracs.values() if p.get("name")]
    out.sort(key=lambda p: (-(p["encounters"]), p["last_seen"] or ""), reverse=False)
    out.sort(key=lambda p: p["last_seen"] or "", reverse=True)
    return out


def _med_group_key(name: str | None) -> str:
    n = (name or "").lower()
    # 'dexamethasone 4 MG tablet' and 'dexamethasone 20 MG tablet' are the same drug for the caregiver
    return n.split(" ")[0].split("-")[0] if n else "unknown"


def medications(conn) -> dict[str, list[dict]]:
    rows = _load(db.rows(conn, "SELECT * FROM medication ORDER BY COALESCE(authored, start) DESC"))
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[_med_group_key(r["medication"])].append(r)
    active, stopped = [], []
    for key, hist in groups.items():
        latest = hist[0]
        is_active = (latest["status"] or "active") in ("active", "on-hold", "intended", "unknown") and not (
            latest.get("end") and latest["end"] < date.today().isoformat())
        item = {"key": key, "name": latest["medication"], "latest": latest, "history": hist,
                "changes": len(hist), "first": _d(hist[-1].get("authored") or hist[-1].get("start")),
                "last": _d(latest.get("authored") or latest.get("start"))}
        (active if is_active else stopped).append(item)
    active.sort(key=lambda x: x["name"] or "")
    stopped.sort(key=lambda x: x["last"], reverse=True)
    return {"active": active, "stopped": stopped}


def panel_latest(conn, groups: tuple[str, ...] = ("disease", "crab", "counts")) -> list[dict]:
    out = []
    for item in myeloma.PANEL:
        if item.group not in groups:
            continue
        pts = db.rows(conn, "SELECT effective, value_num, value_unit, ref_low, ref_high, ref_text, interpretation "
                            "FROM observation WHERE panel_key=? AND value_num IS NOT NULL ORDER BY effective DESC LIMIT 2",
                      (item.key,))
        if not pts:
            continue
        cur, prev = pts[0], (pts[1] if len(pts) > 1 else None)
        delta = None
        if prev and prev["value_num"]:
            delta = (cur["value_num"] - prev["value_num"]) / abs(prev["value_num"]) * 100 if prev["value_num"] else None
        flag = "crab" if myeloma.crab_flag(item.key, cur["value_num"]) else (
            "high" if cur["ref_high"] is not None and cur["value_num"] > cur["ref_high"] else
            "low" if cur["ref_low"] is not None and cur["value_num"] < cur["ref_low"] else "")
        out.append({"key": item.key, "label": item.label, "group": item.group, "note": item.note,
                    "value": cur["value_num"], "unit": cur["value_unit"] or item.unit_hint, "date": _d(cur["effective"]),
                    "prev": prev["value_num"] if prev else None, "prev_date": _d(prev["effective"]) if prev else None,
                    "delta_pct": delta, "ref": cur["ref_text"], "flag": flag})
    return out


def upcoming(conn, days: int = 60) -> list[dict]:
    today = date.today().isoformat()
    end = (date.today() + timedelta(days=days)).isoformat()
    return _load(db.rows(conn, "SELECT * FROM appointment WHERE start >= ? AND start <= ? AND (status IS NULL OR status NOT IN "
                               "('cancelled','noshow','entered-in-error')) ORDER BY start", (today, end)), "participants")


def recent_reports(conn, n: int = 6) -> list[dict]:
    return _load(db.rows(conn, "SELECT id, effective, kind, display, conclusion, performer FROM diagnostic_report "
                               "WHERE kind IN ('imaging','pathology') ORDER BY effective DESC LIMIT ?", (n,)))


def overview(conn) -> dict[str, Any]:
    return {
        "patient": patient(conn), "counts": db.counts(conn), "last_sync": db.get_meta(conn, "last_sync"),
        "conditions": conditions(conn, active_only=True)[:12], "team": care_team(conn)[:10],
        "meds": medications(conn)["active"], "panel": panel_latest(conn), "upcoming": upcoming(conn),
        "reports": recent_reports(conn), "open_notes": notes(conn, status="open")[:10],
        "sync_log": db.rows(conn, "SELECT * FROM sync_log ORDER BY id DESC LIMIT 20"),
    }


# ------------------------------------------------------------------ timeline
def timeline(conn, kinds: set[str] | None = None, q: str | None = None, since: str | None = None) -> list[dict]:
    items: list[dict] = []

    def add(kind, date_, title, subtitle, href, who=None, item_id=None):
        if not date_:
            return
        if since and date_[:10] < since:
            return
        items.append({"date": date_, "day": _d(date_), "kind": kind, "title": title or "", "subtitle": subtitle or "",
                      "href": href, "who": who, "id": item_id})

    if not kinds or "encounter" in kinds:
        for e in db.rows(conn, "SELECT id,start,class,type,reason,location,practitioners FROM encounter"):
            who = ", ".join(p.get("name") or "" for p in json.loads(e["practitioners"] or "[]") if p.get("name"))
            add("encounter", e["start"], f"{e['class'] or 'Visit'}: {e['type'] or ''}".strip(": "), e["reason"] or e["location"],
                f"/encounters/{e['id']}", who, e["id"])
    if not kinds or "report" in kinds:
        for r in db.rows(conn, "SELECT id,effective,kind,display,conclusion,performer FROM diagnostic_report WHERE kind<>'lab'"):
            add(r["kind"], r["effective"], r["display"], (r["conclusion"] or "")[:200], f"/reports/{r['id']}", r["performer"], r["id"])
    if not kinds or "lab" in kinds:
        for r in db.rows(conn, "SELECT substr(effective,1,10) d, COUNT(*) n, GROUP_CONCAT(DISTINCT panel_key) keys "
                               "FROM observation WHERE category LIKE '%aborator%' OR panel_key IS NOT NULL GROUP BY d"):
            keys = [myeloma.BY_KEY[k].label for k in (r["keys"] or "").split(",") if k in myeloma.BY_KEY]
            add("lab", r["d"], f"Labs drawn ({r['n']} results)", ", ".join(keys[:6]), f"/labs?date={r['d']}")
    if not kinds or "document" in kinds:
        for d in db.rows(conn, "SELECT id,date,type,title,author FROM document"):
            add("document", d["date"], d["title"] or d["type"], d["type"], f"/documents/{d['id']}", d["author"], d["id"])
    if not kinds or "medication" in kinds:
        for m in db.rows(conn, "SELECT id,authored,start,status,medication,dosage,requester FROM medication"):
            add("medication", m["authored"] or m["start"], f"Rx {m['status'] or ''}: {m['medication']}", m["dosage"],
                f"/medications#{_med_group_key(m['medication'])}", m["requester"], m["id"])
    if not kinds or "procedure" in kinds:
        for p in db.rows(conn, "SELECT id,performed,display,performer,encounter_id FROM procedure"):
            add("procedure", p["performed"], p["display"], None, f"/encounters/{p['encounter_id']}" if p["encounter_id"] else "/timeline", p["performer"], p["id"])
    if not kinds or "condition" in kinds:
        for c in db.rows(conn, "SELECT id,onset,recorded,display,clinical_status FROM condition"):
            add("condition", c["onset"] or c["recorded"], f"Dx: {c['display']}", c["clinical_status"], "/timeline?kinds=condition", None, c["id"])
    if not kinds or "appointment" in kinds:
        for a in db.rows(conn, "SELECT id,start,status,type,description,location FROM appointment"):
            add("appointment", a["start"], f"Appt ({a['status']}): {a['type'] or a['description'] or ''}", a["location"], "/timeline?kinds=appointment", None, a["id"])
    if not kinds or "note" in kinds:
        for n in db.rows(conn, "SELECT id,created,event_date,kind,title,body,status FROM note"):
            add("note", n["event_date"] or n["created"], f"{n['kind'].title()}: {n['title']}", (n["body"] or "")[:200], f"/notes#{n['id']}", None, n["id"])
    if q:
        ql = q.lower()
        items = [i for i in items if ql in (i["title"] + " " + i["subtitle"] + " " + (i["who"] or "")).lower()]
    items.sort(key=lambda i: i["date"], reverse=True)
    return items


# ------------------------------------------------------------------ labs
def panel_series(conn) -> dict[str, list[dict]]:
    """{group: [{key,label,unit,note,points:[{d,v,lo,hi}],latest}]}"""
    groups: dict[str, list[dict]] = defaultdict(list)
    for item in myeloma.PANEL:
        pts = db.rows(conn, "SELECT effective, value_num, value_unit, ref_low, ref_high FROM observation "
                            "WHERE panel_key=? AND value_num IS NOT NULL ORDER BY effective", (item.key,))
        if not pts:
            continue
        # collapse same-day duplicates (keep last)
        byday: dict[str, dict] = {}
        for p in pts:
            byday[_d(p["effective"])] = p
        points = [{"d": d, "v": p["value_num"], "lo": p["ref_low"], "hi": p["ref_high"]} for d, p in sorted(byday.items())]
        last = pts[-1]
        groups[item.group].append({"key": item.key, "label": item.label, "unit": last["value_unit"] or item.unit_hint,
                                   "note": item.note, "points": points, "latest": last["value_num"],
                                   "latest_date": _d(last["effective"]), "lo": last["ref_low"], "hi": last["ref_high"],
                                   "crab": myeloma.crab_flag(item.key, last["value_num"])})
    return {g: groups[g] for g in myeloma.GROUP_LABELS if g in groups}


def labs_on(conn, day: str) -> list[dict]:
    return _load(db.rows(conn, "SELECT * FROM observation WHERE substr(effective,1,10)=? ORDER BY category, display", (day,)))


def all_tests(conn, q: str | None = None) -> list[dict]:
    sql = ("SELECT display, code, panel_key, COUNT(*) n, MAX(effective) last, MIN(effective) first FROM observation "
           "WHERE display IS NOT NULL")
    params: list = []
    if q:
        sql += " AND display LIKE ?"
        params.append(f"%{q}%")
    sql += " GROUP BY lower(display) ORDER BY last DESC"
    rows = db.rows(conn, sql, params)
    for r in rows:
        latest = db.one(conn, "SELECT value_num, value_str, value_unit, ref_text, interpretation FROM observation "
                              "WHERE lower(display)=lower(?) ORDER BY effective DESC LIMIT 1", (r["display"],))
        r.update(latest or {})
        r["last"], r["first"] = _d(r["last"]), _d(r["first"])
    return rows


def test_series(conn, display: str) -> list[dict]:
    return _load(db.rows(conn, "SELECT * FROM observation WHERE lower(display)=lower(?) ORDER BY effective", (display,)))


# ------------------------------------------------------------------ detail pages
def reports(conn, kind: str | None = None) -> list[dict]:
    sql = "SELECT id,effective,kind,category,display,status,conclusion,performer,encounter_id FROM diagnostic_report"
    params: list = []
    if kind:
        sql += " WHERE kind=?"
        params.append(kind)
    sql += " ORDER BY effective DESC"
    return db.rows(conn, sql, params)


def report(conn, rid: str) -> dict | None:
    r = db.one(conn, "SELECT * FROM diagnostic_report WHERE id=?", (rid,))
    if not r:
        return None
    _load([r], "result_ids")
    ids = r.get("result_ids") or []
    r["results"] = _load(db.rows(conn, f"SELECT * FROM observation WHERE id IN ({','.join('?' * len(ids))}) OR report_id=? ORDER BY display",
                                 [*ids, rid])) if ids else _load(db.rows(conn, "SELECT * FROM observation WHERE report_id=?", (rid,)))
    r["files"] = db.rows(conn, "SELECT * FROM imaging_file WHERE report_id=?", (rid,))
    return r


def documents(conn) -> list[dict]:
    return db.rows(conn, "SELECT id,date,type,category,title,author,status,encounter_id, length(content_text) len "
                         "FROM document ORDER BY date DESC")


def document(conn, did: str) -> dict | None:
    r = db.one(conn, "SELECT * FROM document WHERE id=?", (did,))
    return _load([r])[0] if r else None


def encounters(conn) -> list[dict]:
    return _load(db.rows(conn, "SELECT * FROM encounter ORDER BY start DESC"), "practitioners")


def encounter(conn, eid: str) -> dict | None:
    e = db.one(conn, "SELECT * FROM encounter WHERE id=?", (eid,))
    if not e:
        return None
    _load([e], "practitioners")
    e["observations"] = _load(db.rows(conn, "SELECT * FROM observation WHERE encounter_id=? ORDER BY effective, display", (eid,)))
    e["reports"] = db.rows(conn, "SELECT id,effective,kind,display,conclusion FROM diagnostic_report WHERE encounter_id=? ORDER BY effective", (eid,))
    e["documents"] = db.rows(conn, "SELECT id,date,type,title,author FROM document WHERE encounter_id=? ORDER BY date", (eid,))
    e["medications"] = _load(db.rows(conn, "SELECT * FROM medication WHERE encounter_id=? ORDER BY authored", (eid,)))
    e["procedures"] = _load(db.rows(conn, "SELECT * FROM procedure WHERE encounter_id=? ORDER BY performed", (eid,)))
    e["conditions"] = _load(db.rows(conn, "SELECT * FROM condition WHERE encounter_id=?", (eid,)))
    e["notes"] = db.rows(conn, "SELECT * FROM note WHERE related_type='encounter' AND related_id=?", (eid,))
    return e


def imaging(conn) -> list[dict]:
    files = db.rows(conn, "SELECT f.*, r.display report_display FROM imaging_file f LEFT JOIN diagnostic_report r ON r.id=f.report_id "
                          "ORDER BY f.study_date DESC")
    reps = reports(conn, "imaging")
    return {"files": files, "reports": reps}


# ------------------------------------------------------------------ caregiver notes
NOTE_KINDS = ("question", "issue", "decision", "handoff", "symptom", "todo")


def notes(conn, status: str | None = None, kind: str | None = None) -> list[dict]:
    sql, params = "SELECT * FROM note WHERE 1=1", []
    if status:
        sql += " AND status=?"
        params.append(status)
    if kind:
        sql += " AND kind=?"
        params.append(kind)
    sql += " ORDER BY status='closed', COALESCE(due,'9999'), id DESC"
    return db.rows(conn, sql, params)


def add_note(conn, kind: str, title: str, body: str = "", due: str | None = None, owner: str | None = None,
             related_type: str | None = None, related_id: str | None = None, event_date: str | None = None,
             source: str = "caregiver") -> int:
    now = db.now_iso()
    cur = conn.execute(
        "INSERT INTO note(created,updated,kind,title,body,status,due,owner,related_type,related_id,event_date,source) "
        "VALUES(?,?,?,?,?,'open',?,?,?,?,?,?)",
        (now, now, kind if kind in NOTE_KINDS else "question", title, body, due or None, owner or None,
         related_type or None, related_id or None, event_date or now[:10], source))
    conn.commit()
    return cur.lastrowid


def set_note_status(conn, nid: int, status: str) -> None:
    conn.execute("UPDATE note SET status=?, updated=? WHERE id=?", (status, db.now_iso(), nid))
    conn.commit()


def delete_note(conn, nid: int) -> None:
    conn.execute("DELETE FROM note WHERE id=?", (nid,))
    conn.commit()
