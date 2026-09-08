"""Parse C-CDA XML (the 'Download' / 'Visit Summary' format MyChart produces) into the same tables.

Zero-registration fallback: MyChart -> Sharing -> Download summary. Also parses the XML inside the
ZIP MyChart hands you. Only the sections a caregiver cares about are mapped; the full XML is kept.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from .. import db, myeloma
from ..config import Config

NS = {"c": "urn:hl7-org:v3", "xsi": "http://www.w3.org/2001/XMLSchema-instance"}
SECTION = {  # LOINC section codes
    "results": "30954-2", "vitals": "8716-3", "medications": "10160-0", "problems": "11450-4",
    "encounters": "46240-8", "procedures": "47519-4", "allergies": "48765-2", "immunizations": "11369-6",
    "plan": "18776-5", "notes": "34109-9", "hospital_course": "8648-8", "assessment": "51848-0",
    "reason": "29299-5", "hpi": "10164-2", "discharge_instructions": "8653-8",
    "studies": "55110-1", "imaging": "18748-4",
}

# SNOMED codes used by the C-CDA "Problem Status" observation (LOINC 33999-4).
PROBLEM_STATUS = {"55561003": "active", "73425007": "inactive", "413322009": "resolved",
                  "246455001": "recurrence", "277022003": "remission"}
STATUS_OBS_CODES = {"33999-4", "condition-status"}


def _t(el, path: str, attr: str | None = None) -> str | None:
    n = el.find(path, NS) if el is not None else None
    if n is None:
        return None
    return n.get(attr) if attr else (n.text or "").strip() or None


def _date(s: str | None) -> str | None:
    if not s:
        return None
    s = re.sub(r"[+-]\d{4}$", "", s)
    if len(s) >= 8:
        d = f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
        if len(s) >= 12:
            d += f"T{s[8:10]}:{s[10:12]}"
        return d
    return s


def _eff(el) -> str | None:
    return _date(_t(el, "c:effectiveTime", "value") or _t(el, "c:effectiveTime/c:low", "value")
                 or _t(el, "c:effectiveTime/c:center", "value"))


def _eff_end(el) -> str | None:
    return _date(_t(el, "c:effectiveTime/c:high", "value"))


def _id(el, prefix: str) -> str:
    root = _t(el, "c:id", "root") or ""
    ext = _t(el, "c:id", "extension") or ""
    if not root and not ext:
        raw = ET.tostring(el)[:2000]
        return f"{prefix}-{hashlib.sha1(raw).hexdigest()[:12]}"
    return f"{prefix}-{(root + ':' + ext).strip(':')}"


def _text_of(el) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip() if el is not None else ""


def _ref_text(section, el) -> str | None:
    """Resolve <reference value="#id"/> into the narrative text block."""
    ref = _t(el, ".//c:reference", "value")
    if ref and ref.startswith("#") and section is not None:
        for node in section.iter():
            if node.get("ID") == ref[1:]:
                return _text_of(node)
    return None


def _sections(root):
    for sec in root.iter(f"{{{NS['c']}}}section"):
        code = _t(sec, "c:code", "code")
        yield code, sec


def parse(xml_bytes: bytes, source: str) -> list[tuple[str, dict]]:
    root = ET.fromstring(xml_bytes)
    rows: list[tuple[str, dict]] = []
    # Patient
    pr = root.find("c:recordTarget/c:patientRole", NS)
    patient_id = None
    if pr is not None:
        patient_id = _t(pr, "c:id", "extension") or _t(pr, "c:id", "root") or "ccda-patient"
        p = pr.find("c:patient", NS)
        name = " ".join(filter(None, [_t(p, "c:name/c:given"), _t(p, "c:name/c:family")])) if p is not None else None
        rows.append(("patient", {"id": patient_id, "name": name or None, "birth_date": _date(_t(p, "c:birthTime", "value")),
                                 "sex": {"M": "male", "F": "female"}.get(_t(p, "c:administrativeGenderCode", "code") or "", None),
                                 "mrn": _t(pr, "c:id", "extension"), "source": source}))
    doc_id = _t(root, "c:id", "root") or hashlib.sha1(xml_bytes).hexdigest()[:16]
    doc_title = _t(root, "c:title") or "C-CDA document"
    doc_date = _date(_t(root, "c:effectiveTime", "value"))
    author = _t(root, "c:author/c:assignedAuthor/c:assignedPerson/c:name/c:given")
    fam = _t(root, "c:author/c:assignedAuthor/c:assignedPerson/c:name/c:family")
    author = " ".join(filter(None, [author, fam])) or _t(root, "c:author/c:assignedAuthor/c:representedOrganization/c:name")

    narrative_parts: list[tuple[str, str, str]] = []
    for code, sec in _sections(root):
        title = _t(sec, "c:title") or code or ""
        if code == SECTION["results"]:
            for org in sec.findall("c:entry/c:organizer", NS):
                panel = _t(org, "c:code", "displayName")
                kind = myeloma.report_kind(title, panel)
                if kind in ("imaging", "pathology"):
                    rows.append(_narrative_report(sec, org, panel, kind, patient_id, source, _eff(org)))
                    continue
                for obs in org.findall("c:component/c:observation", NS):
                    rows.append(_result(obs, patient_id, source, panel))
            # some systems put results directly in the section with no organizer
            for obs in sec.findall("c:entry/c:observation", NS):
                rows.append(_result(obs, patient_id, source, title))
        elif code == SECTION["vitals"]:
            for org in sec.findall("c:entry/c:organizer", NS):
                for obs in org.findall("c:component/c:observation", NS):
                    r = _result(obs, patient_id, source, "Vital signs")
                    r[1]["category"] = "vital-signs"
                    rows.append(r)
        elif code == SECTION["medications"]:
            for sa in sec.findall("c:entry/c:substanceAdministration", NS):
                mat = sa.find("c:consumable/c:manufacturedProduct/c:manufacturedMaterial", NS)
                name = _t(mat, "c:code", "displayName") or _t(mat, "c:name") or _ref_text(sec, mat.find("c:code", NS) if mat is not None else None)
                dose = _t(sa, "c:doseQuantity", "value")
                unit = _t(sa, "c:doseQuantity", "unit")
                rows.append(("medication", {
                    "id": _id(sa, "ccda-med"), "patient_id": patient_id, "kind": "ccda",
                    "status": _t(sa, "c:statusCode", "code"), "medication": name,
                    "rxnorm": _t(mat, "c:code", "code") if mat is not None else None,
                    "dosage": (_ref_text(sec, sa) or f"{dose or ''} {unit or ''}".strip()) or None,
                    "route": _t(sa, "c:routeCode", "displayName"),
                    "start": _date(_t(sa, "c:effectiveTime/c:low", "value")),
                    "end": _date(_t(sa, "c:effectiveTime/c:high", "value")), "source": source}))
        elif code == SECTION["problems"]:
            for obs in _problem_observations(sec):
                val = obs.find("c:value", NS)
                if val is None:
                    continue
                abatement = _date(_t(obs, "c:effectiveTime/c:high", "value"))
                rows.append(("condition", {
                    "id": _id(obs, "ccda-cond"), "patient_id": patient_id, "code": val.get("code"),
                    "display": val.get("displayName") or _ref_text(sec, obs),
                    "clinical_status": _problem_status(obs, abatement),
                    "verification_status": _t(obs, "c:statusCode", "code"), "category": "problem-list-item",
                    "onset": _date(_t(obs, "c:effectiveTime/c:low", "value")),
                    "abatement": abatement, "source": source}))
        elif code == SECTION["encounters"]:
            for enc in sec.findall("c:entry/c:encounter", NS):
                perf = enc.find("c:performer/c:assignedEntity", NS)
                pname = " ".join(filter(None, [_t(perf, "c:assignedPerson/c:name/c:given"), _t(perf, "c:assignedPerson/c:name/c:family")])) if perf is not None else None
                rows.append(("encounter", {
                    "id": _id(enc, "ccda-enc"), "patient_id": patient_id, "start": _eff(enc), "end": _eff_end(enc),
                    "type": _t(enc, "c:code", "displayName") or _ref_text(sec, enc),
                    "location": _t(enc, ".//c:participantRole/c:playingEntity/c:name") or _t(perf, "c:representedOrganization/c:name"),
                    "practitioners": [{"id": None, "name": pname}] if pname else [], "source": source}))
        elif code == SECTION["procedures"]:
            for proc in sec.findall("c:entry/*", NS):
                rows.append(("procedure", {
                    "id": _id(proc, "ccda-proc"), "patient_id": patient_id, "performed": _eff(proc),
                    "code": _t(proc, "c:code", "code"), "display": _t(proc, "c:code", "displayName") or _ref_text(sec, proc),
                    "status": _t(proc, "c:statusCode", "code"), "source": source}))
        elif code == SECTION["allergies"]:
            for obs in sec.findall(".//c:entry//c:observation", NS):
                sub = obs.find(".//c:participant//c:playingEntity/c:code", NS)
                if sub is None:
                    continue
                rows.append(("allergy", {
                    "id": _id(obs, "ccda-alg"), "patient_id": patient_id,
                    "substance": sub.get("displayName") or _ref_text(sec, obs),
                    "reaction": _t(obs, ".//c:entryRelationship/c:observation/c:value", "displayName"),
                    "severity": _t(obs, ".//c:entryRelationship/c:observation/c:entryRelationship/c:observation/c:value", "displayName"),
                    "status": _t(obs, "c:statusCode", "code"), "recorded": _eff(obs), "source": source}))
        elif code == SECTION["immunizations"]:
            for sa in sec.findall("c:entry/c:substanceAdministration", NS):
                rows.append(("immunization", {
                    "id": _id(sa, "ccda-imm"), "patient_id": patient_id,
                    "vaccine": _t(sa, ".//c:manufacturedMaterial/c:code", "displayName"), "date": _eff(sa),
                    "status": _t(sa, "c:statusCode", "code"), "source": source}))
        else:
            txt = _text_of(sec.find("c:text", NS))
            if not txt:
                continue
            kind = myeloma.report_kind(title, title)
            sec_id = f"{doc_id}-{code or title}"
            if kind in ("imaging", "pathology") or code in (SECTION["studies"], SECTION["imaging"]):
                rows.append(("diagnostic_report", {
                    "id": f"ccda-rep-{sec_id}", "patient_id": patient_id, "effective": _eff(sec) or doc_date,
                    "category": title, "code": code, "display": title,
                    "kind": kind if kind in ("imaging", "pathology") else "imaging", "status": "final",
                    "conclusion": _impression(txt), "text": txt, "performer": author, "source": source}))
            else:
                narrative_parts.append((title, txt, sec_id))
    for title, txt, sec_id in narrative_parts:
        rows.append(("document", {
            "id": f"ccda-{sec_id}", "patient_id": patient_id, "date": doc_date, "type": title,
            "category": "clinical-note", "title": f"{title} ({doc_title})" if doc_title else title,
            "author": author, "status": "final", "content_text": txt, "content_type": "text/plain",
            "source": source}))
    return rows


def _problem_observations(sec):
    """Yield the problem observations in a Problem List section.

    A C-CDA problem entry nests a "Problem Status" observation inside the problem observation.
    Both match a naive .//observation search, which is why status codes previously appeared as
    diagnoses named "Active" and "Resolved".
    """
    nested = {id(child) for obs in sec.iter(f"{{{NS['c']}}}observation")
              for child in obs.iter(f"{{{NS['c']}}}observation") if child is not obs}
    for obs in sec.iter(f"{{{NS['c']}}}observation"):
        if id(obs) in nested:
            continue
        if (_t(obs, "c:code", "code") or "") in STATUS_OBS_CODES:
            continue
        yield obs


def _problem_status(obs, abatement: str | None) -> str:
    """C-CDA statusCode is the status of the *act* (nearly always 'completed'), not of the problem.

    Prefer the nested Problem Status observation; otherwise infer from an abatement date.
    """
    for rel in obs.findall("c:entryRelationship/c:observation", NS):
        if (_t(rel, "c:code", "code") or "") in STATUS_OBS_CODES or rel.find("c:value", NS) is not None:
            val = rel.find("c:value", NS)
            if val is not None:
                mapped = PROBLEM_STATUS.get(val.get("code") or "")
                if mapped:
                    return mapped
                name = (val.get("displayName") or "").strip().lower()
                if name in ("active", "resolved", "inactive", "recurrence", "remission"):
                    return name
    return "resolved" if abatement else "active"


def _impression(text: str) -> str | None:
    """Pull the impression/diagnosis line out of a report narrative for the summary views."""
    m = re.search(r"(?:IMPRESSION|DIAGNOSIS|CONCLUSION|FINDINGS)\s*:?\s*(.+)", text, re.I | re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip()[:1000] if m else re.sub(r"\s+", " ", text)[:300] or None


def _narrative_report(sec, org, display, kind, patient_id, source, effective) -> tuple[str, dict]:
    """An imaging or pathology 'result' whose payload is narrative text, not a number."""
    parts = []
    for obs in org.findall("c:component/c:observation", NS):
        val = obs.find("c:value", NS)
        txt = _ref_text(sec, obs) or (_text_of(val) if val is not None else "")
        if txt:
            parts.append(txt)
    text = "\n\n".join(dict.fromkeys(parts)) or _ref_text(sec, org) or ""
    return ("diagnostic_report", {
        "id": _id(org, "ccda-rep"), "patient_id": patient_id, "effective": effective,
        "category": kind, "code": _t(org, "c:code", "code"), "display": display, "status": "final",
        "kind": kind, "conclusion": _impression(text), "text": text or None, "source": source})


def _result(obs, patient_id, source, panel) -> tuple[str, dict]:
    code = _t(obs, "c:code", "code")
    display = _t(obs, "c:code", "displayName") or panel
    val = obs.find("c:value", NS)
    vnum, vunit, vstr = None, None, None
    if val is not None:
        typ = val.get(f"{{{NS['xsi']}}}type", "")
        if typ.endswith("PQ") or val.get("value") is not None:
            try:
                vnum = float(val.get("value"))
            except (TypeError, ValueError):
                vstr = val.get("value")
            vunit = val.get("unit")
        else:
            vstr = _text_of(val) or val.get("displayName")
    rr = obs.find("c:referenceRange/c:observationRange", NS)
    lo = hi = None
    rtxt = None
    if rr is not None:
        rtxt = _t(rr, "c:text")
        try:
            lo = float(_t(rr, "c:value/c:low", "value") or "")
        except ValueError:
            pass
        try:
            hi = float(_t(rr, "c:value/c:high", "value") or "")
        except ValueError:
            pass
    return ("observation", {
        "id": _id(obs, "ccda-obs"), "patient_id": patient_id, "effective": _eff(obs), "category": "laboratory",
        "code": code, "code_system": "http://loinc.org" if code else None, "display": display,
        "value_num": vnum, "value_unit": vunit, "value_str": vstr, "ref_low": lo, "ref_high": hi, "ref_text": rtxt,
        "interpretation": _t(obs, "c:interpretationCode", "code"), "status": _t(obs, "c:statusCode", "code"),
        "panel_key": myeloma.classify(code, display), "source": source})


def import_path(cfg: Config, conn: sqlite3.Connection, path: Path) -> dict[str, int]:
    blobs: list[tuple[str, bytes]] = []
    if path.is_dir():
        blobs = [(f.name, f.read_bytes()) for f in sorted(path.rglob("*.xml"))]
    elif path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            blobs = [(n, z.read(n)) for n in z.namelist() if n.lower().endswith(".xml")]
    else:
        blobs = [(path.name, path.read_bytes())]
    counts: dict[str, int] = {}
    keep = cfg.imports_dir / "ccda"
    keep.mkdir(parents=True, exist_ok=True)
    with db.tx(conn):
        for name, blob in blobs:
            if b"urn:hl7-org:v3" not in blob[:4000]:
                continue
            (keep / Path(name).name).write_bytes(blob)
            for table, row in parse(blob, f"ccda:{Path(name).name}"):
                db.upsert(conn, table, row)
                counts[table] = counts.get(table, 0) + 1
    return counts
