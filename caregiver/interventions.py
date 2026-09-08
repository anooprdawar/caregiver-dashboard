"""Detect supportive-care interventions and map them onto the lab charts they explain.

A haemoglobin that climbs because of a transfusion is a different fact from one that climbs because
the disease is responding, and the same is true of a white count after growth factor. The record
holds both, but in different tables and never side by side. This finds them and pairs each with the
values it acts on, so the chart shows the intervention rather than implying a spontaneous recovery.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from . import db


@dataclass(frozen=True)
class Intervention:
    key: str
    label: str
    short: str
    affects: tuple[str, ...]          # panel keys whose charts should show this marker
    color: str
    patterns: tuple[str, ...]         # matched against medication and procedure text
    codes: tuple[str, ...] = ()       # RxNorm / SNOMED / CPT
    counts_as_transfusion: bool = False
    exclude: tuple[str, ...] = field(default_factory=tuple)


CATALOG: list[Intervention] = [
    Intervention(
        "rbc", "Red blood cell transfusion", "RBC", ("hemoglobin", "hematocrit"), "#b91c1c",
        (r"\bp?rbc\b", r"packed (red )?(blood )?cells?", r"red blood cells?",
         r"transfus\w*.{0,30}\b(rbc|prbc|red|blood|packed)", r"\b(blood|red cell)\b.{0,20}transfus\w*"),
        ("116859006", "36430", "306960002"), counts_as_transfusion=True,
        exclude=(r"platelet", r"plasma", r"cryoprecipitate", r"type and screen", r"crossmatch")),
    Intervention(
        "platelet", "Platelet transfusion", "PLT", ("platelets",), "#7c3aed",
        (r"platelet.{0,25}transfus\w*", r"transfus\w*.{0,25}platelet", r"\bapheresis platelets?\b",
         r"\bplatelet (concentrate|pheresis|unit)"),
        ("116861002", "36430"), counts_as_transfusion=True),
    Intervention(
        "plasma", "Plasma / cryoprecipitate", "FFP", ("platelets",), "#0891b2",
        (r"fresh frozen plasma", r"\bffp\b", r"cryoprecipitate"), ("116860001",), counts_as_transfusion=True),
    Intervention(
        "gcsf", "Growth factor (G-CSF)", "G-CSF", ("wbc", "anc", "alc"), "#047857",
        (r"filgrastim", r"pegfilgrastim", r"tbo-?filgrastim", r"eflapegrastim", r"efbemalenograstim",
         r"neupogen", r"neulasta", r"zarxio", r"nivestym", r"granix", r"udenyca", r"ziextenzo",
         r"fulphila", r"releuko", r"rolvedon", r"nyvepria", r"stimufend", r"fylnetra", r"sargramostim",
         r"leukine", r"\bg-?csf\b", r"gm-?csf"),
        ("1650272", "68442", "338036")),
    Intervention(
        "esa", "Erythropoiesis-stimulating agent", "ESA", ("hemoglobin",), "#c2410c",
        (r"epoetin", r"darbepoetin", r"procrit", r"aranesp", r"retacrit", r"epogen", r"\besa\b"),
        ("105694", "114851")),
    Intervention(
        "iron", "IV iron", "Fe", ("hemoglobin",), "#a16207",
        (r"ferric (carboxymaltose|gluconate|derisomaltose)", r"iron sucrose", r"ferumoxytol",
         r"venofer", r"feraheme", r"injectafer", r"monoferric", r"iron dextran"), ()),
    Intervention(
        "ivig", "Immune globulin (IVIG)", "IVIG", ("igg",), "#4f46e5",
        (r"immune globulin", r"\bivig\b", r"\bscig\b", r"gamunex", r"privigen", r"octagam",
         r"gammagard", r"hizentra"), ()),
    Intervention(
        "tpo", "Thrombopoietin agonist", "TPO", ("platelets",), "#be185d",
        (r"romiplostim", r"eltrombopag", r"avatrombopag", r"nplate", r"promacta", r"doptelet"), ()),
]

BY_KEY = {i.key: i for i in CATALOG}
_COMPILED = [(i, re.compile("|".join(i.patterns), re.I),
              re.compile("|".join(i.exclude), re.I) if i.exclude else None) for i in CATALOG]


def classify(text: str | None, code: str | None = None) -> Intervention | None:
    """Identify an intervention from a medication or procedure description."""
    for item, rx, ex in _COMPILED:
        if code and code in item.codes:
            return item
        if not text:
            continue
        if ex and ex.search(text) and not re.search(r"transfus", text, re.I):
            continue
        if rx.search(text):
            if ex and ex.search(text):
                continue
            return item
    return None


def _seen(row_date: str | None) -> str:
    return (row_date or "")[:10]


def detect(conn: sqlite3.Connection) -> list[dict]:
    """Every intervention event in the record, de-duplicated to one per type per day."""
    found: dict[tuple[str, str], dict] = {}

    for m in db.rows(conn, "SELECT id, medication, dosage, rxnorm, status, authored, start, requester, reason "
                           "FROM medication"):
        if (m["status"] or "") in ("entered-in-error", "cancelled", "draft"):
            continue
        item = classify(f"{m['medication'] or ''} {m['dosage'] or ''} {m['reason'] or ''}", m["rxnorm"])
        day = _seen(m["authored"] or m["start"])
        if item and day:
            found.setdefault((item.key, day), {
                "key": item.key, "label": item.label, "short": item.short, "color": item.color,
                "affects": list(item.affects), "date": day, "what": m["medication"],
                "detail": m["dosage"], "who": m["requester"], "origin": "medication", "id": m["id"]})

    for p in db.rows(conn, "SELECT id, display, code, performed, performer, status, outcome FROM procedure"):
        if (p["status"] or "") in ("entered-in-error", "not-done"):
            continue
        item = classify(p["display"], p["code"])
        day = _seen(p["performed"])
        if item and day:
            found.setdefault((item.key, day), {
                "key": item.key, "label": item.label, "short": item.short, "color": item.color,
                "affects": list(item.affects), "date": day, "what": p["display"],
                "detail": p["outcome"], "who": p["performer"], "origin": "procedure", "id": p["id"]})

    # Some systems log the administration as an observation with a coded value.
    for o in db.rows(conn, "SELECT id, display, code, value_str, effective FROM observation "
                           "WHERE value_num IS NULL AND display IS NOT NULL"):
        item = classify(f"{o['display']} {o['value_str'] or ''}", o["code"])
        day = _seen(o["effective"])
        if item and day and item.counts_as_transfusion:
            found.setdefault((item.key, day), {
                "key": item.key, "label": item.label, "short": item.short, "color": item.color,
                "affects": list(item.affects), "date": day, "what": o["display"],
                "detail": o["value_str"], "who": None, "origin": "observation", "id": o["id"]})

    return sorted(found.values(), key=lambda e: (e["date"], e["key"]))


def markers_for(events: list[dict], panel_key: str) -> list[dict]:
    """The subset of events that belong on one lab chart."""
    return [{"d": e["date"], "key": e["key"], "short": e["short"], "color": e["color"],
             "label": e["label"], "what": e["what"]}
            for e in events if panel_key in e["affects"]]


def tally(events: list[dict]) -> dict:
    """Counts for the summary tiles: transfusions overall, and each intervention type."""
    per: dict[str, dict] = {}
    for e in events:
        p = per.setdefault(e["key"], {"key": e["key"], "label": e["label"], "short": e["short"],
                                      "color": e["color"], "count": 0, "first": e["date"], "last": e["date"]})
        p["count"] += 1
        p["first"] = min(p["first"], e["date"])
        p["last"] = max(p["last"], e["date"])
    transfusion_keys = {i.key for i in CATALOG if i.counts_as_transfusion}
    tx = [e for e in events if e["key"] in transfusion_keys]
    return {
        "by_type": sorted(per.values(), key=lambda p: -p["count"]),
        "transfusion_days": len(tx),
        "transfusion_first": tx[0]["date"] if tx else None,
        "transfusion_last": tx[-1]["date"] if tx else None,
        "transfusions_by_type": [p for p in per.values() if p["key"] in transfusion_keys],
        "total_events": len(events),
    }
