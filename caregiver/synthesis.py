"""Cross-cutting synthesis over a recent window.

Everything here is arithmetic over what the record already contains: which tracked values moved and
by how much, which medications changed, which reports landed, and where those things fail to line up
with each other. It describes the record. It does not interpret it, and it is not advice.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from . import db, myeloma

DEFAULT_WEEKS = 12
SIGNIFICANT_PCT = 10.0          # below this a move is noise for display purposes
STALE_ABNORMAL_DAYS = 21        # an abnormal result with no visit since is worth surfacing
ORPHAN_REPORT_DAYS = 14         # an imaging/path report with no visit after it


def _d(s: str | None) -> str:
    return (s or "")[:10]


def _parse(s: str | None) -> date | None:
    try:
        return date.fromisoformat(_d(s))
    except (ValueError, TypeError):
        return None


def _pct(new: float, old: float) -> float | None:
    return None if not old else (new - old) / abs(old) * 100.0


# ---------------------------------------------------------------- pieces
def trajectories(conn, start: str, end: str) -> list[dict]:
    """For each tracked value: where it started the window, where it is now, and which way that is."""
    out = []
    for item in myeloma.PANEL:
        pts = db.rows(conn, "SELECT effective, value_num, value_unit, ref_low, ref_high FROM observation "
                            "WHERE panel_key=? AND value_num IS NOT NULL AND substr(effective,1,10) BETWEEN ? AND ? "
                            "ORDER BY effective", (item.key, start, end))
        if len(pts) < 2:
            continue
        first, last = pts[0], pts[-1]
        pct = _pct(last["value_num"], first["value_num"])
        if pct is None:
            continue
        values = [p["value_num"] for p in pts]
        nadir, peak = min(values), max(values)
        rise_from_nadir = _pct(last["value_num"], nadir) if nadir else None
        out.append({
            "key": item.key, "label": item.label, "group": item.group, "unit": last["value_unit"] or item.unit_hint,
            "n": len(pts), "first": first["value_num"], "first_date": _d(first["effective"]),
            "last": last["value_num"], "last_date": _d(last["effective"]), "pct": pct,
            "meaning": myeloma.direction_meaning(item.key, pct), "nadir": nadir, "peak": peak,
            "rise_from_nadir": rise_from_nadir,
            "abnormal": ("high" if last["ref_high"] is not None and last["value_num"] > last["ref_high"]
                         else "low" if last["ref_low"] is not None and last["value_num"] < last["ref_low"] else ""),
            "crab": myeloma.crab_flag(item.key, last["value_num"]),
            "points": [{"d": _d(p["effective"]), "v": p["value_num"]} for p in pts],
        })
    out.sort(key=lambda t: (t["meaning"] != "worse", -abs(t["pct"])))
    return out


def _med_key(name: str | None) -> str:
    n = (name or "").lower()
    return n.split(" ")[0].split("-")[0] if n else "unknown"


def treatment_changes(conn, start: str, end: str) -> dict[str, list[dict]]:
    """Medications started, stopped, or re-dosed inside the window."""
    rows = db.rows(conn, "SELECT * FROM medication ORDER BY COALESCE(authored, start)")
    by_drug: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_drug[_med_key(r["medication"])].append(r)

    started, stopped, redosed = [], [], []
    for key, hist in by_drug.items():
        in_win = [h for h in hist if start <= _d(h.get("authored") or h.get("start")) <= end]
        if not in_win:
            continue
        before = [h for h in hist if _d(h.get("authored") or h.get("start")) < start]
        latest = in_win[-1]
        entry = {"key": key, "name": latest["medication"], "date": _d(latest.get("authored") or latest.get("start")),
                 "dosage": latest.get("dosage"), "requester": latest.get("requester"), "reason": latest.get("reason"),
                 "status": latest.get("status")}
        if (latest.get("status") or "") in ("stopped", "completed", "cancelled", "entered-in-error"):
            stopped.append(entry)
        elif not before:
            started.append(entry)
        else:
            prev = before[-1]
            if (prev.get("dosage") or "") != (latest.get("dosage") or "") or prev.get("medication") != latest.get("medication"):
                redosed.append({**entry, "from": prev.get("medication"), "from_dosage": prev.get("dosage")})
    for lst in (started, stopped, redosed):
        lst.sort(key=lambda e: e["date"], reverse=True)
    return {"started": started, "stopped": stopped, "redosed": redosed}


def window_reports(conn, start: str, end: str) -> list[dict]:
    return db.rows(conn, "SELECT id, effective, kind, display, conclusion, performer FROM diagnostic_report "
                         "WHERE kind IN ('imaging','pathology') AND substr(effective,1,10) BETWEEN ? AND ? "
                         "ORDER BY effective DESC", (start, end))


def window_visits(conn, start: str, end: str) -> dict[str, Any]:
    encs = db.rows(conn, "SELECT id, start, class, type, reason, location, practitioners FROM encounter "
                         "WHERE substr(start,1,10) BETWEEN ? AND ? ORDER BY start DESC", (start, end))
    who: dict[str, int] = defaultdict(int)
    for e in encs:
        try:
            for p in json.loads(e["practitioners"] or "[]"):
                if p.get("name"):
                    who[p["name"]] += 1
        except json.JSONDecodeError:
            pass
    return {"count": len(encs), "encounters": encs[:20],
            "clinicians": sorted(who.items(), key=lambda kv: -kv[1])}


def problem_changes(conn, start: str, end: str) -> dict[str, list[dict]]:
    new = db.rows(conn, "SELECT * FROM condition WHERE substr(COALESCE(onset, recorded),1,10) BETWEEN ? AND ? "
                        "ORDER BY COALESCE(onset, recorded) DESC", (start, end))
    resolved = db.rows(conn, "SELECT * FROM condition WHERE abatement IS NOT NULL "
                             "AND substr(abatement,1,10) BETWEEN ? AND ? ORDER BY abatement DESC", (start, end))
    seen = set()
    new = [c for c in new if not (c["display"] or "").lower() in seen and not seen.add((c["display"] or "").lower())]
    return {"new": new, "resolved": resolved}


# ---------------------------------------------------------------- the cross-relating part
def signals(conn, start: str, end: str, traj: list[dict], meds: dict, reports: list[dict],
            visits: dict) -> list[dict]:
    """Findings that only appear when two parts of the record are read against each other.

    Each signal states what was observed and where to look. Severity orders the display:
    'watch' is the strongest, then 'note', then 'info'.
    """
    out: list[dict] = []
    today = date.today()

    # 1. Disease markers against the IMWG direction-of-travel thresholds.
    disease = [t for t in traj if t["group"] == "disease"]
    falling = [t for t in disease if t["pct"] <= -myeloma.RESPONSE_DROP * 100]
    rising = [t for t in disease if t["rise_from_nadir"] is not None
              and t["rise_from_nadir"] >= myeloma.PROGRESSION_RISE * 100 and t["pct"] > 0]
    if falling:
        out.append({"severity": "info", "title": "Disease markers are falling",
                    "detail": ", ".join(f"{t['label']} {t['first']:g} → {t['last']:g} {t['unit']} ({t['pct']:+.0f}%)"
                                        for t in falling[:4]),
                    "why": "A fall of 25% or more is the IMWG partial-response threshold.", "link": "/labs"})
    if rising:
        out.append({"severity": "watch", "title": "A disease marker has risen from its low point",
                    "detail": "; ".join(f"{t['label']} nadir {t['nadir']:g} → {t['last']:g} {t['unit']} "
                                        f"(+{t['rise_from_nadir']:.0f}% from nadir, {t['last_date']})" for t in rising),
                    "why": "A rise of 25% or more above the nadir is the IMWG progression threshold. "
                           "Confirm against the treating team's own reading before acting.", "link": "/labs"})

    # 2. CRAB criteria currently met.
    crab = [t for t in traj if t["crab"]]
    if crab:
        out.append({"severity": "watch", "title": "CRAB criteria met at the latest draw",
                    "detail": "; ".join(f"{t['label']} {t['last']:g} {t['unit']} on {t['last_date']}" for t in crab),
                    "why": "Calcium >11, creatinine >2, eGFR <40, or haemoglobin <10.", "link": "/labs"})

    # 3. Values that moved the wrong way by a wide margin.
    worse = [t for t in traj if t["meaning"] == "worse" and abs(t["pct"]) >= 20 and t["group"] != "disease"]
    if worse:
        out.append({"severity": "note", "title": "Moved in the unfavourable direction this window",
                    "detail": "; ".join(f"{t['label']} {t['first']:g} → {t['last']:g} {t['unit']} ({t['pct']:+.0f}%)"
                                        for t in worse[:5]), "link": "/labs"})

    # 4. A drug started, then a tracked value moved sharply after it.
    for m in meds["started"] + meds["redosed"]:
        md = _parse(m["date"])
        if not md:
            continue
        hits = []
        for t in traj:
            after = [p for p in t["points"] if (_parse(p["d"]) or today) > md]
            before = [p for p in t["points"] if (_parse(p["d"]) or today) <= md]
            if not after or not before:
                continue
            pct = _pct(after[-1]["v"], before[-1]["v"])
            if pct is not None and abs(pct) >= 25 and myeloma.direction_meaning(t["key"], pct) == "worse":
                hits.append(f"{t['label']} {before[-1]['v']:g} → {after[-1]['v']:g} {t['unit']} ({pct:+.0f}%)")
        if hits:
            out.append({"severity": "note", "title": f"Values moved after {m['name']} started ({m['date']})",
                        "detail": "; ".join(hits[:4]),
                        "why": "Timing only. This is not evidence the drug caused the change, but it is the "
                               "kind of pairing worth raising.", "link": "/medications"})

    # 5. An abnormal latest result with no visit since.
    latest_visit = max((_parse(e["start"]) for e in visits["encounters"] if _parse(e["start"])), default=None)
    stale = []
    for t in traj:
        ld = _parse(t["last_date"])
        if not t["abnormal"] or not ld:
            continue
        if (today - ld).days >= STALE_ABNORMAL_DAYS and (latest_visit is None or latest_visit < ld):
            stale.append(f"{t['label']} {t['last']:g} {t['unit']} ({t['abnormal']}) on {t['last_date']}")
    if stale:
        out.append({"severity": "note", "title": "Abnormal results with no recorded visit since",
                    "detail": "; ".join(stale[:5]),
                    "why": "Either the follow-up happened outside this record, or nobody has reviewed it.",
                    "link": "/labs"})

    # 6. A report with no encounter after it.
    orphans = []
    for r in reports:
        rd = _parse(r["effective"])
        if not rd or (today - rd).days < ORPHAN_REPORT_DAYS:
            continue
        after = db.one(conn, "SELECT id FROM encounter WHERE substr(start,1,10) > ? LIMIT 1", (_d(r["effective"]),))
        if not after:
            orphans.append(f"{_d(r['effective'])} {r['display']}")
    if orphans:
        out.append({"severity": "note", "title": "Imaging or pathology with no visit recorded afterwards",
                    "detail": "; ".join(orphans[:4]),
                    "why": "Results the record does not show being discussed with anyone.", "link": "/reports"})

    # 7. Active drugs whose prescriber has not appeared in the window.
    seen_names = {n.lower() for n, _ in visits["clinicians"]}
    orphan_rx: dict[str, list[str]] = defaultdict(list)
    for m in db.rows(conn, "SELECT medication, requester, MAX(COALESCE(authored,start)) last FROM medication "
                           "WHERE requester IS NOT NULL AND (status IS NULL OR status IN ('active','on-hold')) "
                           "GROUP BY lower(medication)"):
        req = (m["requester"] or "").lower()
        if req and not any(req in s or s in req for s in seen_names):
            orphan_rx[m["requester"]].append(m["medication"])
    if orphan_rx:
        out.append({"severity": "note", "title": "Active prescriptions from clinicians not seen this window",
                    "detail": "; ".join(f"{who}: {', '.join(d[:3])}" for who, d in list(orphan_rx.items())[:4]),
                    "why": "The classic handoff gap. Someone should own each of these.", "link": "/medications"})

    # 8. Open caregiver items that are overdue.
    overdue = db.rows(conn, "SELECT title, due, kind FROM note WHERE status='open' AND due IS NOT NULL AND due < ? "
                            "ORDER BY due", (today.isoformat(),))
    if overdue:
        out.append({"severity": "watch", "title": f"{len(overdue)} caregiver item(s) past their date",
                    "detail": "; ".join(f"{o['title']} (due {o['due']})" for o in overdue[:4]), "link": "/notes"})

    rank = {"watch": 0, "note": 1, "info": 2}
    out.sort(key=lambda s: rank.get(s["severity"], 3))
    return out


# ---------------------------------------------------------------- assembly
def build(conn: sqlite3.Connection, weeks: int = DEFAULT_WEEKS) -> dict[str, Any]:
    end_d = date.today()
    start_d = end_d - timedelta(weeks=weeks)
    # If the record ends well before today (a stale import), anchor on the newest data instead.
    newest = db.one(conn, "SELECT MAX(substr(effective,1,10)) d FROM observation")
    newest_d = _parse(newest["d"]) if newest else None
    anchored = False
    if newest_d and (end_d - newest_d).days > 14:
        end_d, start_d, anchored = newest_d, newest_d - timedelta(weeks=weeks), True
    start, end = start_d.isoformat(), end_d.isoformat()

    traj = trajectories(conn, start, end)
    meds = treatment_changes(conn, start, end)
    reports = window_reports(conn, start, end)
    visits = window_visits(conn, start, end)
    problems = problem_changes(conn, start, end)
    return {
        "weeks": weeks, "start": start, "end": end, "anchored_to_data": anchored,
        "trajectories": traj, "meds": meds, "reports": reports, "visits": visits, "problems": problems,
        "signals": signals(conn, start, end, traj, meds, reports, visits),
        "improving": [t for t in traj if t["meaning"] == "better"],
        "worsening": [t for t in traj if t["meaning"] == "worse"],
        "counts": {
            "labs": db.one(conn, "SELECT COUNT(*) n FROM observation WHERE substr(effective,1,10) BETWEEN ? AND ?", (start, end))["n"],
            "notes": db.one(conn, "SELECT COUNT(*) n FROM document WHERE substr(date,1,10) BETWEEN ? AND ?", (start, end))["n"],
            "reports": len(reports), "visits": visits["count"],
            "med_changes": len(meds["started"]) + len(meds["stopped"]) + len(meds["redosed"]),
        },
    }


def markdown(conn, weeks: int = DEFAULT_WEEKS, label: str = "Patient") -> str:
    s = build(conn, weeks)
    L = [f"# {label} — {weeks}-week summary", f"_{s['start']} to {s['end']}_", ""]
    if s["anchored_to_data"]:
        L += ["> Window anchored to the newest data in the record, not to today.", ""]
    c = s["counts"]
    L += [f"{c['visits']} visits · {c['labs']} results · {c['reports']} imaging/pathology reports · "
          f"{c['notes']} notes · {c['med_changes']} medication changes", ""]
    if s["signals"]:
        L += ["## What stands out", ""]
        for sig in s["signals"]:
            L.append(f"- **[{sig['severity']}] {sig['title']}** — {sig['detail']}")
            if sig.get("why"):
                L.append(f"  _{sig['why']}_")
        L.append("")
    if s["trajectories"]:
        L += ["## Direction of travel", "", "| Value | Start | Latest | Change | |", "|---|---|---|---|---|"]
        for t in s["trajectories"][:18]:
            L.append(f"| {t['label']} | {t['first']:g} ({t['first_date']}) | {t['last']:g} {t['unit']} "
                     f"({t['last_date']}) | {t['pct']:+.0f}% | {t['meaning']} |")
        L.append("")
    for key, heading in (("started", "Started"), ("redosed", "Dose changed"), ("stopped", "Stopped")):
        if s["meds"][key]:
            L += [f"### {heading}"] + [f"- **{m['name']}** {m.get('dosage') or ''} · {m['date']}"
                                       + (f" · {m['requester']}" if m.get("requester") else "") for m in s["meds"][key]] + [""]
    if s["reports"]:
        L += ["## Imaging & pathology", ""]
        L += [f"- **{_d(r['effective'])} {r['display']}** — {(r['conclusion'] or '')[:400]}" for r in s["reports"]]
        L.append("")
    if s["problems"]["new"]:
        L += ["## New problems recorded", ""] + [f"- {c['display']} ({_d(c.get('onset') or c.get('recorded'))})"
                                                 for c in s["problems"]["new"]] + [""]
    if s["visits"]["clinicians"]:
        L += ["## Who was seen", ""] + [f"- {n} — {k} visit(s)" for n, k in s["visits"]["clinicians"]] + [""]
    return "\n".join(L) + "\n"
