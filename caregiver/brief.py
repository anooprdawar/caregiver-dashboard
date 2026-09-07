"""One-page appointment brief. The artifact the caregiver hands to the next doctor."""
from __future__ import annotations

from datetime import date

from . import db
from .web import queries as Q


def _fmt(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:g}" if abs(v) >= 1 else f"{v:.2f}"


def build(conn, label: str = "Patient") -> dict:
    ov = Q.overview(conn)
    meds = Q.medications(conn)
    return {
        "generated": date.today().isoformat(), "label": label, "patient": ov["patient"], "conditions": ov["conditions"],
        "team": [t for t in ov["team"] if t.get("encounters")][:8], "active_meds": meds["active"],
        "recent_stopped": meds["stopped"][:6], "panel": ov["panel"], "reports": ov["reports"][:5],
        "upcoming": ov["upcoming"], "open_notes": Q.notes(conn, status="open"),
        "allergies": db.rows(conn, "SELECT substance, reaction, severity FROM allergy WHERE status IS NULL OR status='active'"),
        "last_sync": ov["last_sync"],
    }


def markdown(conn, label: str = "Patient") -> str:
    b = build(conn, label)
    p = b["patient"] or {}
    L = [f"# {label} — care brief ({b['generated']})", ""]
    if p:
        L.append(f"**{p.get('name') or label}** · DOB {p.get('birth_date') or '?'}" + (f" · age {p['age']}" if p.get("age") else "") + (f" · MRN {p['mrn']}" if p.get("mrn") else ""))
    if b["last_sync"]:
        L.append(f"_Data synced from MyChart {b['last_sync'][:16].replace('T', ' ')} UTC_")
    L += ["", "## Active problems"]
    L += [f"- {c['display']}" + (f" (since {c['onset'][:10]})" if c.get("onset") else "") for c in b["conditions"]] or ["- none recorded"]
    if b["allergies"]:
        L += ["", "## Allergies"] + [f"- **{a['substance']}** — {a['reaction'] or ''} {('(' + a['severity'] + ')') if a['severity'] else ''}".rstrip() for a in b["allergies"]]
    L += ["", "## Key labs (latest vs previous)", "", "| Test | Latest | Date | Previous | Change | Ref |", "|---|---|---|---|---|---|"]
    for x in b["panel"]:
        if x["group"] not in ("disease", "crab", "counts"):
            continue
        chg = f"{x['delta_pct']:+.0f}%" if x["delta_pct"] is not None else ""
        flag = " ⚠" if x["flag"] else ""
        L.append(f"| {x['label']} | {_fmt(x['value'])} {x['unit'] or ''}{flag} | {x['date']} | {_fmt(x['prev'])} | {chg} | {x['ref'] or ''} |")
    L += ["", "## Current medications"]
    for m in b["active_meds"]:
        lt = m["latest"]
        L.append(f"- **{m['name']}** — {lt.get('dosage') or ''}" + (f" · by {lt['requester']}" if lt.get("requester") else "") + (f" · {m['changes']} changes" if m["changes"] > 1 else ""))
    if b["recent_stopped"]:
        L += ["", "### Recently stopped"] + [f"- {m['name']} (last {m['last']})" for m in b["recent_stopped"]]
    L += ["", "## Recent imaging / pathology"]
    for r in b["reports"]:
        L.append(f"- **{(r['effective'] or '')[:10]} {r['display']}** — {(r['conclusion'] or '')[:300]}")
    L += ["", "## Care team"]
    L += [f"- {t['name']}" + (f" ({t['specialty']})" if t.get("specialty") else "") + f" · {t['encounters']} visits · last {t['last_seen']}" for t in b["team"]]
    L += ["", "## Upcoming"]
    L += [f"- {a['start'][:16].replace('T', ' ')} — {a['type'] or a['description'] or ''} @ {a.get('location') or ''}" for a in b["upcoming"]] or ["- none scheduled"]
    L += ["", "## Open questions / issues for the team"]
    for n in b["open_notes"]:
        L.append(f"- [{n['kind']}] **{n['title']}**" + (f" (due {n['due']})" if n.get("due") else ""))
        if n.get("body"):
            L.append(f"  {n['body']}")
    return "\n".join(L) + "\n"
