"""The illness course, reconstructed as an ordered set of milestones.

A record stores events in tables; a caregiver remembers a story. This rebuilds the story from the
tables: how the patient presented, what was done about it, when the diagnosis actually landed, and
what treatment followed. Each milestone names the row it came from so it can be checked, and any
phase can be corrected or supplied by hand when the record is silent or wrong.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from . import db

MILESTONE_KIND = "milestone"


@dataclass(frozen=True)
class Phase:
    key: str
    label: str
    hint: str          # what a caregiver would call it, shown when the record has nothing


PHASES: list[Phase] = [
    Phase("presentation", "Presentation", "How this started: the symptoms that brought her in"),
    Phase("surgery", "Surgery", "Any operation done urgently to relieve what was found"),
    Phase("rehab", "Rehab", "Physical or occupational therapy after the operation"),
    Phase("workup", "Work-up", "The bloodwork that pointed at the underlying cause"),
    Phase("finding", "Initial finding", "What the first scan or operation actually showed"),
    Phase("diagnosis", "Diagnosis", "When the underlying disease was finally named"),
    Phase("biopsy", "Biopsy", "Tissue or marrow that confirmed it"),
    Phase("radiation", "Radiation", "Radiotherapy to the affected site"),
    Phase("treatment", "Treatment", "Start of systemic therapy"),
    Phase("response", "Response", "Restaging that measured whether it worked"),
]
PHASE_ORDER = {p.key: i for i, p in enumerate(PHASES)}

SURGERY = re.compile(r"\b(decompress\w*|laminectom\w*|corpectom\w*|fusion|instrumentat\w*|kyphoplast\w*|"
                     r"vertebroplast\w*|resection|craniotom\w*|arthrodesis|discectom\w*)\b", re.I)
REHAB = re.compile(r"\b(physical therapy|occupational therapy|rehab\w*|\bPT\b|\bOT\b|gait training|"
                   r"physiatr\w*|physical medicine)\b", re.I)
BIOPSY = re.compile(r"\b(biopsy|aspirat\w*|marrow|core needle|excisional|fine needle)\b", re.I)
RADIATION = re.compile(r"\b(radiation|radiotherapy|external beam|\bEBRT\b|\bIMRT\b|\bSBRT\b|"
                       r"\bGy\b|radiation oncology|palliative RT)\b", re.I)
# The disease entity itself. A presenting complication (a plasmacytoma pressing on the cord, a
# pathological fracture) is a finding, not the diagnosis, and dating the diagnosis from it puts the
# whole course out of sequence.
DEFINITIVE_DX = re.compile(r"\b(multiple myeloma|myeloma|leukemi\w*|lymphoma|amyloidosis|carcinoma|"
                           r"sarcoma|\bMGUS\b|plasma cell (neoplasm|myeloma|dyscrasia))\b", re.I)
FINDING_DX = re.compile(r"\b(plasmacytoma|cord compression|pathologic\w* fracture|mass|lesion|tumou?r|"
                        r"compression fracture|lytic)\b", re.I)
# "Chronic kidney disease (myeloma kidney)" and "Anemia in neoplastic disease" name the disease but
# are consequences of it. Dating the diagnosis from one of these is wrong.
COMPLICATION_OF = re.compile(r"\b(kidney|renal|nephropath\w*|an\a?emia|neuropath\w*|fracture|compression|"
                             r"hypercalc\w*|hyperviscosity|infection|pain|deposit|due to|secondary to|"
                             r"in neoplastic disease|related to|from)\b", re.I)
CANCER_DX = re.compile(r"\b(myeloma|plasma ?cell|plasmacytoma|amyloidosis|lymphoma|leukemi\w*|"
                       r"carcinoma|sarcoma|neoplasm|malignan\w*|\bMGUS\b|cancer)\b", re.I)
ANTINEOPLASTIC = re.compile(
    r"\b(bortezomib|carfilzomib|ixazomib|lenalidomide|pomalidomide|thalidomide|daratumumab|isatuximab|"
    r"elotuzumab|teclistamab|talquetamab|elranatamab|belantamab|ciltacabtagene|idecabtagene|selinexor|"
    r"venetoclax|panobinostat|melphalan|cyclophosphamide|doxorubicin|bendamustine|cisplatin|etoposide|"
    r"vincristine|rituximab|chemotherap\w*|velcade|revlimid|darzalex|kyprolis|ninlaro|pomalyst|empliciti)\b", re.I)
RESPONSE = re.compile(r"\b(response|restag\w*|interval|follow.?up|post.?treatment|remission)\b", re.I)
WORKUP_KEYS = ("m_protein", "kappa_flc", "flc_ratio", "b2m", "upep", "igg")
RED_FLAG = re.compile(r"\b(weakness|unable to (walk|void|urinate)|retention|numbness|paralysis|cauda equina|"
                      r"cord compression|incontinen\w*|gait|fall|paresis|paraplegi\w*|saddle)\b", re.I)


def _d(s: str | None) -> str:
    return (s or "")[:10]


def _ms(phase: str, date: str | None, title: str, detail: str | None = None, href: str | None = None,
        origin: str = "record") -> dict | None:
    if not _d(date):
        return None
    return {"phase": phase, "date": _d(date), "title": title, "detail": (detail or "").strip() or None,
            "href": href, "origin": origin}


def detect(conn: sqlite3.Connection) -> list[dict]:
    """One best milestone per phase, drawn from the record."""
    found: dict[str, dict] = {}

    def put(m: dict | None):
        if m and (m["phase"] not in found or m["date"] < found[m["phase"]]["date"]):
            found[m["phase"]] = m

    encounters = db.rows(conn, "SELECT id, start, class, type, reason, location FROM encounter "
                               "WHERE start IS NOT NULL ORDER BY start")
    procedures = db.rows(conn, "SELECT id, performed, display, encounter_id FROM procedure "
                               "WHERE performed IS NOT NULL ORDER BY performed")
    conditions = db.rows(conn, "SELECT id, display, onset, recorded FROM condition")
    reports = db.rows(conn, "SELECT id, effective, kind, display, conclusion FROM diagnostic_report "
                            "WHERE effective IS NOT NULL ORDER BY effective")
    meds = db.rows(conn, "SELECT id, medication, authored, start, requester, reason FROM medication")

    # Presentation: the first emergency contact, else the first encounter of any kind.
    emer = [e for e in encounters if (e["class"] or "").lower().startswith(("emer", "inpatient"))
            or (e["class"] or "").upper() in ("EMER", "IMP")]
    first = (emer or encounters)[:1]
    for e in first:
        reason = e["reason"] or e["type"] or ""
        flags = ", ".join(sorted({m.group(0).lower() for m in RED_FLAG.finditer(reason)}))
        put(_ms("presentation", e["start"], reason or (e["class"] or "First recorded visit"),
                f"{e['class'] or ''} at {e['location'] or 'unknown'}" + (f" · {flags}" if flags else ""),
                f"/encounters/{e['id']}"))

    for p in procedures:
        disp = p["display"] or ""
        if SURGERY.search(disp):
            put(_ms("surgery", p["performed"], disp, None,
                    f"/encounters/{p['encounter_id']}" if p["encounter_id"] else "/timeline"))
        if BIOPSY.search(disp):
            put(_ms("biopsy", p["performed"], disp, None,
                    f"/encounters/{p['encounter_id']}" if p["encounter_id"] else "/timeline"))
        if RADIATION.search(disp):
            put(_ms("radiation", p["performed"], disp, None,
                    f"/encounters/{p['encounter_id']}" if p["encounter_id"] else "/timeline"))

    for e in encounters:
        text = f"{e['type'] or ''} {e['reason'] or ''} {e['location'] or ''}"
        if REHAB.search(text):
            put(_ms("rehab", e["start"], e["type"] or "Rehabilitation", e["reason"], f"/encounters/{e['id']}"))
        if RADIATION.search(text) and "radiation" not in found:
            put(_ms("radiation", e["start"], e["type"] or "Radiation oncology", e["reason"], f"/encounters/{e['id']}"))

    # Work-up: the first draw containing a disease-defining test.
    row = db.one(conn, "SELECT MIN(substr(effective,1,10)) d FROM observation WHERE panel_key IN "
                       f"({','.join('?' * len(WORKUP_KEYS))}) AND value_num IS NOT NULL", list(WORKUP_KEYS))
    if row and row["d"]:
        names = db.rows(conn, "SELECT DISTINCT display FROM observation WHERE substr(effective,1,10)=? "
                              "AND panel_key IS NOT NULL LIMIT 6", (row["d"],))
        put(_ms("workup", row["d"], "Disease-specific bloodwork sent",
                ", ".join(n["display"] for n in names if n["display"]), f"/labs?date={row['d']}"))

    # Separate the presenting finding from the disease that explains it. Prefer a condition naming
    # the disease entity; only fall back to the broader pattern when nothing definitive exists.
    definitive = [c for c in conditions if DEFINITIVE_DX.search(c["display"] or "")
                  and not FINDING_DX.search(c["display"] or "")
                  and not COMPLICATION_OF.search(c["display"] or "")]
    for c in definitive or [c for c in conditions if CANCER_DX.search(c["display"] or "")]:
        put(_ms("diagnosis", c["onset"] or c["recorded"], c["display"], "Recorded diagnosis", "/summary"))
    for c in conditions:
        if FINDING_DX.search(c["display"] or ""):
            put(_ms("finding", c["onset"] or c["recorded"], c["display"],
                    "The presenting problem, before the underlying cause was named", "/summary"))

    for r in reports:
        disp = f"{r['display'] or ''} {r['conclusion'] or ''}"
        if r["kind"] == "pathology" and BIOPSY.search(disp):
            put(_ms("biopsy", r["effective"], r["display"], r["conclusion"], f"/reports/{r['id']}"))
        if r["kind"] == "pathology" and DEFINITIVE_DX.search(r["conclusion"] or "") and not definitive:
            put(_ms("diagnosis", r["effective"], f"Confirmed on pathology: {r['display']}",
                    r["conclusion"], f"/reports/{r['id']}"))
        if r["kind"] == "imaging" and FINDING_DX.search(r["conclusion"] or ""):
            put(_ms("finding", r["effective"], r["display"], r["conclusion"], f"/reports/{r['id']}"))

    tx = sorted((m for m in meds if ANTINEOPLASTIC.search(m["medication"] or "")),
                key=lambda m: _d(m["authored"] or m["start"]) or "9999")
    if tx:
        first_tx = tx[0]
        same_day = {m["medication"] for m in tx if _d(m["authored"] or m["start"]) == _d(first_tx["authored"] or first_tx["start"])}
        put(_ms("treatment", first_tx["authored"] or first_tx["start"], "Systemic therapy started",
                ", ".join(sorted(m.split(" ")[0] for m in same_day if m)), "/medications"))

    # Response: the most recent imaging that reads as a restaging study.
    for r in reversed(reports):
        if r["kind"] == "imaging" and RESPONSE.search(f"{r['display'] or ''} {r['conclusion'] or ''}"):
            found["response"] = _ms("response", r["effective"], r["display"], r["conclusion"], f"/reports/{r['id']}")
            break

    return [m for m in found.values() if m]


def caregiver_milestones(conn: sqlite3.Connection) -> list[dict]:
    """Milestones the caregiver wrote by hand. These win over anything detected."""
    out = []
    for n in db.rows(conn, "SELECT * FROM note WHERE kind=? ORDER BY COALESCE(event_date, created)", (MILESTONE_KIND,)):
        phase = (n["owner"] or "").strip().lower()
        out.append({"phase": phase if phase in PHASE_ORDER else "", "date": _d(n["event_date"] or n["created"]),
                    "title": n["title"], "detail": n["body"] or None, "href": f"/notes#{n['id']}",
                    "origin": "caregiver", "id": n["id"]})
    return out


def build(conn: sqlite3.Connection) -> dict:
    detected = {m["phase"]: m for m in detect(conn)}
    for m in caregiver_milestones(conn):
        if m["phase"]:
            detected[m["phase"]] = m          # a hand-written milestone replaces the detected one
    extra = [m for m in caregiver_milestones(conn) if not m["phase"]]

    steps = []
    for p in PHASES:
        m = detected.get(p.key)
        steps.append({"key": p.key, "label": p.label, "hint": p.hint, "found": bool(m), **(m or {})})
    dated = sorted((s for s in steps if s.get("date")), key=lambda s: s["date"])
    return {
        "steps": steps, "found": dated, "missing": [s for s in steps if not s["found"]],
        "extra": sorted(extra, key=lambda m: m["date"]),
        "start": min((s["date"] for s in dated), default=None),
        "end": max((s["date"] for s in dated), default=None),
        "out_of_order": _out_of_order(dated),
    }


# Phases overlap in real care (rehab runs on through the work-up), so ordering is only worth
# reporting where one milestone genuinely cannot precede another. A hit here means a mis-detection.
MUST_FOLLOW = {"surgery": "presentation", "diagnosis": "presentation", "biopsy": "presentation",
               "finding": "presentation", "treatment": "diagnosis", "response": "treatment"}


def _out_of_order(dated: list[dict]) -> list[str]:
    by_key = {s["key"]: s for s in dated}
    bad = []
    for child, parent in MUST_FOLLOW.items():
        a, b = by_key.get(child), by_key.get(parent)
        if a and b and a["date"] < b["date"]:
            bad.append(f"{a['label']} is dated {a['date']}, before {b['label']} on {b['date']} — "
                       f"one of the two is probably mis-detected")
    return bad
