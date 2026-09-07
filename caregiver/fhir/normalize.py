"""FHIR R4 (and tolerant of DSTU2 exports) -> normalized rows.

Every function takes a resource dict and returns a list of (table, row) tuples. Unknown shapes
degrade to partial rows rather than raising: the raw JSON is always preserved, so nothing is lost
if a mapping is imperfect.
"""
from __future__ import annotations

import base64
import html
import json
import re
from typing import Any

from .. import myeloma

Row = tuple[str, dict[str, Any]]


# ----------------------------------------------------------------- helpers
def _ref_id(ref: dict | None) -> str | None:
    if not ref:
        return None
    r = ref.get("reference") or ""
    return r.split("/")[-1] if r else None


def _coding_first(cc: dict | None, system_hint: str | None = None) -> tuple[str | None, str | None, str | None]:
    """Return (code, system, display). Prefers LOINC for observations."""
    if not cc:
        return None, None, None
    codings = cc.get("coding") or []
    pick = None
    if system_hint:
        for c in codings:
            if system_hint in (c.get("system") or ""):
                pick = c
                break
    if pick is None and codings:
        pick = codings[0]
    display = cc.get("text") or (pick or {}).get("display")
    if pick is None:
        return None, None, display
    return pick.get("code"), pick.get("system"), display or pick.get("code")


def _display(cc: dict | None) -> str | None:
    return _coding_first(cc)[2]


def _codes(cc_list: list | None) -> str:
    return "; ".join(filter(None, (_display(c) for c in (cc_list or []))))


def _effective(res: dict, *keys: str) -> str | None:
    for k in keys:
        v = res.get(k)
        if isinstance(v, str):
            return v
        if isinstance(v, dict):
            return v.get("start") or v.get("end")
    return None


def _strip_html(s: str | None) -> str:
    if not s:
        return ""
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</(p|div|tr|li|h\d)>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s).strip()


def _quantity(q: dict | None) -> tuple[float | None, str | None]:
    if not q:
        return None, None
    v = q.get("value")
    try:
        v = float(v) if v is not None else None
    except (TypeError, ValueError):
        v = None
    return v, q.get("unit") or q.get("code")


def _human_name(names: list | None) -> str | None:
    if not names:
        return None
    n = names[0]
    if n.get("text"):
        return n["text"]
    parts = [*(n.get("given") or []), n.get("family") or ""]
    return " ".join(p for p in parts if p).strip() or None


def _dosage_text(dosages: list | None) -> tuple[str, str | None, str | None]:
    if not dosages:
        return "", None, None
    d = dosages[0]
    text = d.get("text") or d.get("patientInstruction") or ""
    route = _display(d.get("route"))
    freq = None
    t = (d.get("timing") or {}).get("code")
    if t:
        freq = _display(t)
    elif (d.get("timing") or {}).get("repeat"):
        r = d["timing"]["repeat"]
        if r.get("frequency") and r.get("period"):
            freq = f"{r['frequency']}x per {r['period']} {r.get('periodUnit', '')}".strip()
    if not text:
        dq = (d.get("doseAndRate") or [{}])[0].get("doseQuantity") or d.get("doseQuantity")
        v, u = _quantity(dq)
        if v is not None:
            text = f"{v:g} {u or ''}".strip()
    return text, route, freq


# ----------------------------------------------------------------- resources
def patient(res: dict, source: str) -> list[Row]:
    mrn = None
    for ident in res.get("identifier") or []:
        typ = (ident.get("type") or {}).get("text") or _display(ident.get("type")) or ""
        if "MRN" in typ.upper() or "medical record" in typ.lower():
            mrn = ident.get("value")
    return [("patient", {
        "id": res["id"], "name": _human_name(res.get("name")), "birth_date": res.get("birthDate"),
        "sex": res.get("gender"), "mrn": mrn, "source": source, "raw": res})]


def practitioner(res: dict, source: str) -> list[Row]:
    spec = None
    if res.get("qualification"):
        spec = _display(res["qualification"][0].get("code"))
    phone = None
    for t in res.get("telecom") or []:
        if t.get("system") == "phone":
            phone = t.get("value")
            break
    return [("practitioner", {"id": res["id"], "name": _human_name(res.get("name")), "specialty": spec,
                              "phone": phone, "source": source, "raw": res})]


def practitioner_role(res: dict, source: str) -> list[Row]:
    pid = _ref_id(res.get("practitioner"))
    if not pid:
        return []
    return [("practitioner", {
        "id": pid, "name": (res.get("practitioner") or {}).get("display"),
        "specialty": _codes(res.get("specialty")), "role": _codes(res.get("code")),
        "organization": (res.get("organization") or {}).get("display"), "source": source, "raw": res})]


def encounter(res: dict, source: str) -> list[Row]:
    period = res.get("period") or {}
    pracs = []
    for p in res.get("participant") or []:
        ind = p.get("individual") or {}
        pracs.append({"id": _ref_id(ind), "name": ind.get("display"), "type": _codes(p.get("type"))})
    loc = None
    if res.get("location"):
        loc = (res["location"][0].get("location") or {}).get("display")
    if not loc and res.get("serviceProvider"):
        loc = res["serviceProvider"].get("display")
    cls = res.get("class")
    cls_disp = cls.get("display") or cls.get("code") if isinstance(cls, dict) else cls
    reason = _codes(res.get("reasonCode")) or "; ".join(
        (r.get("display") or "") for r in (res.get("reasonReference") or []))
    rows: list[Row] = [("encounter", {
        "id": res["id"], "patient_id": _ref_id(res.get("subject") or res.get("patient")),
        "start": period.get("start"), "end": period.get("end"), "class": cls_disp,
        "type": _codes(res.get("type")), "status": res.get("status"), "reason": reason or None,
        "location": loc, "practitioners": pracs, "source": source, "raw": res})]
    for p in pracs:  # make sure every practitioner we see is at least stub-listed
        if p["id"] and p["name"]:
            rows.append(("practitioner", {"id": p["id"], "name": p["name"], "source": source}))
    return rows


def condition(res: dict, source: str) -> list[Row]:
    code, _, display = _coding_first(res.get("code"))
    return [("condition", {
        "id": res["id"], "patient_id": _ref_id(res.get("subject") or res.get("patient")),
        "encounter_id": _ref_id(res.get("encounter") or res.get("context")), "code": code, "display": display,
        "clinical_status": _coding_first(res.get("clinicalStatus"))[0] if isinstance(res.get("clinicalStatus"), dict)
        else res.get("clinicalStatus"),
        "verification_status": _coding_first(res.get("verificationStatus"))[0]
        if isinstance(res.get("verificationStatus"), dict) else res.get("verificationStatus"),
        "category": _codes(res.get("category")),
        "onset": _effective(res, "onsetDateTime", "onsetPeriod"),
        "recorded": res.get("recordedDate") or res.get("dateRecorded") or res.get("assertedDate"),
        "abatement": _effective(res, "abatementDateTime", "abatementPeriod"),
        "source": source, "raw": res})]


def _obs_value(o: dict) -> tuple[float | None, str | None, str | None]:
    if "valueQuantity" in o:
        v, u = _quantity(o["valueQuantity"])
        return v, u, None
    if "valueString" in o:
        s = o["valueString"]
        m = re.match(r"^\s*([<>]?=?)\s*(-?\d+(?:\.\d+)?)\s*([A-Za-z/%µ²]*)\s*$", s or "")
        if m and not m.group(1):
            return float(m.group(2)), m.group(3) or None, s
        return None, None, s
    if "valueCodeableConcept" in o:
        return None, None, _display(o["valueCodeableConcept"])
    if "valueInteger" in o:
        return float(o["valueInteger"]), None, None
    if "valueBoolean" in o:
        return None, None, str(o["valueBoolean"])
    if "valueDateTime" in o:
        return None, None, o["valueDateTime"]
    if "valueRatio" in o:
        n = (o["valueRatio"].get("numerator") or {}).get("value")
        d = (o["valueRatio"].get("denominator") or {}).get("value")
        if n is not None and d:
            return float(n) / float(d), None, f"{n}/{d}"
    return None, None, None


def _ref_range(o: dict) -> tuple[float | None, float | None, str | None]:
    rr = (o.get("referenceRange") or [{}])[0]
    lo, _ = _quantity(rr.get("low"))
    hi, _ = _quantity(rr.get("high"))
    txt = rr.get("text")
    if txt is None and (lo is not None or hi is not None):
        txt = f"{'' if lo is None else f'{lo:g}'}–{'' if hi is None else f'{hi:g}'}"
    return lo, hi, txt


def observation(res: dict, source: str) -> list[Row]:
    code, system, display = _coding_first(res.get("code"), "loinc")
    base = {
        "patient_id": _ref_id(res.get("subject") or res.get("patient")),
        "encounter_id": _ref_id(res.get("encounter") or res.get("context")),
        "effective": _effective(res, "effectiveDateTime", "effectivePeriod", "effectiveInstant") or res.get("issued"),
        "issued": res.get("issued"),
        "category": _codes(res.get("category")) if isinstance(res.get("category"), list) else _display(res.get("category")),
        "status": res.get("status"), "source": source,
    }
    rows: list[Row] = []
    comps = res.get("component") or []
    if comps and "valueQuantity" not in res and "valueString" not in res:
        # e.g. blood pressure: emit one row per component, keep the parent raw once
        for i, c in enumerate(comps):
            ccode, csys, cdisp = _coding_first(c.get("code"), "loinc")
            v, u, s = _obs_value(c)
            lo, hi, txt = _ref_range(c)
            rows.append(("observation", {**base, "id": f"{res['id']}-c{i}", "code": ccode, "code_system": csys,
                                         "display": cdisp, "value_num": v, "value_unit": u, "value_str": s,
                                         "ref_low": lo, "ref_high": hi, "ref_text": txt,
                                         "interpretation": _codes(c.get("interpretation")) or None,
                                         "panel_key": myeloma.classify(ccode, cdisp),
                                         "raw": res if i == 0 else None}))
        return rows
    v, u, s = _obs_value(res)
    lo, hi, txt = _ref_range(res)
    interp = res.get("interpretation")
    interp_s = _codes(interp) if isinstance(interp, list) else _display(interp)
    rows.append(("observation", {**base, "id": res["id"], "code": code, "code_system": system, "display": display,
                                 "value_num": v, "value_unit": u, "value_str": s, "ref_low": lo, "ref_high": hi,
                                 "ref_text": txt, "interpretation": interp_s or None,
                                 "panel_key": myeloma.classify(code, display), "raw": res}))
    return rows


def diagnostic_report(res: dict, source: str) -> list[Row]:
    code, _, display = _coding_first(res.get("code"), "loinc")
    cat = _codes(res.get("category")) if isinstance(res.get("category"), list) else _display(res.get("category"))
    text = ""
    for pf in res.get("presentedForm") or []:
        if pf.get("data") and (pf.get("contentType") or "").startswith(("text/", "application/xhtml")):
            try:
                text += _strip_html(base64.b64decode(pf["data"]).decode("utf-8", "replace")) + "\n"
            except Exception:
                pass
    if not text and res.get("text", {}).get("div"):
        text = _strip_html(res["text"]["div"])
    performer = "; ".join(filter(None, ((p.get("display") or "") for p in (res.get("performer") or []))))
    if not performer and res.get("resultsInterpreter"):
        performer = "; ".join(filter(None, ((p.get("display") or "") for p in res["resultsInterpreter"])))
    return [("diagnostic_report", {
        "id": res["id"], "patient_id": _ref_id(res.get("subject") or res.get("patient")),
        "encounter_id": _ref_id(res.get("encounter") or res.get("context")),
        "effective": _effective(res, "effectiveDateTime", "effectivePeriod") or res.get("issued"),
        "issued": res.get("issued"), "category": cat, "code": code, "display": display,
        "status": res.get("status"), "kind": myeloma.report_kind(cat, display),
        "conclusion": res.get("conclusion"), "text": text.strip() or None, "performer": performer or None,
        "result_ids": [_ref_id(r) for r in (res.get("result") or [])], "source": source, "raw": res})]


def document_reference(res: dict, source: str, binaries: dict[str, bytes] | None = None) -> list[Row]:
    """DocumentReference. If `binaries` maps Binary ids -> bytes, note text is inlined."""
    content = res.get("content") or []
    text, path, ctype = None, None, None
    for c in content:
        att = c.get("attachment") or {}
        ctype = att.get("contentType")
        if att.get("data"):
            try:
                raw = base64.b64decode(att["data"])
                text = _strip_html(raw.decode("utf-8", "replace")) if (ctype or "").startswith(
                    ("text/", "application/xhtml", "application/xml")) else None
            except Exception:
                text = None
        elif att.get("url") and binaries is not None:
            bid = att["url"].split("/")[-1]
            if bid in binaries:
                blob = binaries[bid]
                if (ctype or "").startswith(("text/", "application/xhtml", "application/xml")):
                    text = _strip_html(blob.decode("utf-8", "replace"))
                else:
                    path = f"binary/{bid}"
        if text:
            break
    ctx = res.get("context") or {}
    enc = ctx.get("encounter")
    enc_id = _ref_id(enc[0]) if isinstance(enc, list) and enc else _ref_id(enc if isinstance(enc, dict) else None)
    date = res.get("date") or (ctx.get("period") or {}).get("start") or res.get("created") or res.get("indexed")
    author = "; ".join(filter(None, ((a.get("display") or "") for a in (res.get("author") or []))))
    return [("document", {
        "id": res["id"], "patient_id": _ref_id(res.get("subject")), "encounter_id": enc_id, "date": date,
        "type": _display(res.get("type")), "category": _codes(res.get("category")) or _display(res.get("class")),
        "title": res.get("description") or _display(res.get("type")), "author": author or None,
        "status": res.get("docStatus") or res.get("status"), "content_text": text, "content_path": path,
        "content_type": ctype, "source": source, "raw": res})]


def medication_request(res: dict, source: str) -> list[Row]:
    med = res.get("medicationCodeableConcept")
    rx, _, name = _coding_first(med, "rxnorm") if med else (None, None, None)
    if not name and res.get("medicationReference"):
        name = res["medicationReference"].get("display")
    dosage, route, freq = _dosage_text(res.get("dosageInstruction"))
    disp = res.get("dispenseRequest") or {}
    validity = disp.get("validityPeriod") or {}
    reason = _codes(res.get("reasonCode")) or "; ".join(
        (r.get("display") or "") for r in (res.get("reasonReference") or []))
    return [("medication", {
        "id": res["id"], "patient_id": _ref_id(res.get("subject") or res.get("patient")),
        "encounter_id": _ref_id(res.get("encounter") or res.get("context")), "kind": "request",
        "authored": res.get("authoredOn") or res.get("dateWritten"), "status": res.get("status"),
        "intent": res.get("intent"), "medication": name, "rxnorm": rx, "dosage": dosage, "route": route,
        "frequency": freq, "requester": ((res.get("requester") or {}).get("display")
                                          or ((res.get("requester") or {}).get("agent") or {}).get("display")
                                          or (res.get("prescriber") or {}).get("display")),
        "reason": reason or None, "start": validity.get("start"), "end": validity.get("end"),
        "source": source, "raw": res})]


def medication_statement(res: dict, source: str) -> list[Row]:
    med = res.get("medicationCodeableConcept")
    rx, _, name = _coding_first(med, "rxnorm") if med else (None, None, None)
    if not name and res.get("medicationReference"):
        name = res["medicationReference"].get("display")
    dosage, route, freq = _dosage_text(res.get("dosage"))
    eff = res.get("effectivePeriod") or {}
    return [("medication", {
        "id": res["id"], "patient_id": _ref_id(res.get("subject") or res.get("patient")),
        "encounter_id": _ref_id(res.get("context")), "kind": "statement",
        "authored": res.get("dateAsserted"), "status": res.get("status"), "medication": name, "rxnorm": rx,
        "dosage": dosage, "route": route, "frequency": freq,
        "requester": (res.get("informationSource") or {}).get("display"),
        "reason": _codes(res.get("reasonCode")) or None,
        "start": eff.get("start") or res.get("effectiveDateTime"), "end": eff.get("end"),
        "source": source, "raw": res})]


def procedure(res: dict, source: str) -> list[Row]:
    code, _, display = _coding_first(res.get("code"))
    return [("procedure", {
        "id": res["id"], "patient_id": _ref_id(res.get("subject") or res.get("patient")),
        "encounter_id": _ref_id(res.get("encounter") or res.get("context")),
        "performed": _effective(res, "performedDateTime", "performedPeriod"), "code": code, "display": display,
        "status": res.get("status"),
        "performer": "; ".join(filter(None, ((p.get("actor") or {}).get("display") or "" for p in (res.get("performer") or [])))) or None,
        "body_site": _codes(res.get("bodySite")) or None, "outcome": _display(res.get("outcome")),
        "source": source, "raw": res})]


def appointment(res: dict, source: str) -> list[Row]:
    parts = []
    pid = None
    for p in res.get("participant") or []:
        actor = p.get("actor") or {}
        ref = actor.get("reference") or ""
        if ref.startswith("Patient/"):
            pid = ref.split("/")[-1]
        else:
            parts.append({"name": actor.get("display"), "ref": ref, "type": _codes(p.get("type"))})
    loc = next((p["name"] for p in parts if p["ref"].startswith("Location/")), None)
    return [("appointment", {
        "id": res["id"], "patient_id": pid, "start": res.get("start"), "end": res.get("end"),
        "status": res.get("status"),
        "type": _codes(res.get("serviceType")) or _codes(res.get("appointmentType") and [res["appointmentType"]]) or _codes(res.get("type") and [res["type"]]),
        "description": res.get("description") or res.get("comment") or res.get("patientInstruction"),
        "participants": parts, "location": loc, "source": source, "raw": res})]


def allergy(res: dict, source: str) -> list[Row]:
    reactions = []
    sev = None
    for r in res.get("reaction") or []:
        reactions.append(_codes(r.get("manifestation")))
        sev = sev or r.get("severity")
    return [("allergy", {
        "id": res["id"], "patient_id": _ref_id(res.get("patient")),
        "substance": _display(res.get("code") or res.get("substance")), "reaction": "; ".join(filter(None, reactions)) or None,
        "severity": sev or res.get("criticality"),
        "status": _coding_first(res.get("clinicalStatus"))[0] if isinstance(res.get("clinicalStatus"), dict) else res.get("status"),
        "recorded": res.get("recordedDate") or res.get("onsetDateTime"), "source": source, "raw": res})]


def immunization(res: dict, source: str) -> list[Row]:
    return [("immunization", {
        "id": res["id"], "patient_id": _ref_id(res.get("patient")), "vaccine": _display(res.get("vaccineCode")),
        "date": res.get("occurrenceDateTime") or res.get("date"), "status": res.get("status"),
        "source": source, "raw": res})]


def care_plan(res: dict, source: str) -> list[Row]:
    acts = []
    for a in res.get("activity") or []:
        d = a.get("detail") or {}
        acts.append({"desc": d.get("description") or _display(d.get("code")), "status": d.get("status"),
                     "ref": (a.get("reference") or {}).get("display")})
    period = res.get("period") or {}
    return [("care_plan", {
        "id": res["id"], "patient_id": _ref_id(res.get("subject")), "title": res.get("title") or _codes(res.get("category")),
        "category": _codes(res.get("category")), "status": res.get("status"), "start": period.get("start"),
        "end": period.get("end"), "description": res.get("description"), "activities": acts,
        "source": source, "raw": res})]


def care_team(res: dict, source: str) -> list[Row]:
    rows: list[Row] = []
    for p in res.get("participant") or []:
        member = p.get("member") or {}
        mid = _ref_id(member)
        if mid and (member.get("reference") or "").startswith("Practitioner"):
            rows.append(("practitioner", {"id": mid, "name": member.get("display"), "role": _codes(p.get("role")),
                                          "source": source}))
    return rows


HANDLERS = {
    "Patient": patient, "Practitioner": practitioner, "PractitionerRole": practitioner_role,
    "Encounter": encounter, "Condition": condition, "Observation": observation,
    "DiagnosticReport": diagnostic_report, "DocumentReference": document_reference,
    "MedicationRequest": medication_request, "MedicationOrder": medication_request,  # DSTU2
    "MedicationStatement": medication_statement, "Procedure": procedure, "Appointment": appointment,
    "AllergyIntolerance": allergy, "Immunization": immunization, "CarePlan": care_plan, "CareTeam": care_team,
}


def normalize(res: dict, source: str, **kw) -> list[Row]:
    fn = HANDLERS.get(res.get("resourceType"))
    if not fn or not res.get("id"):
        return []
    if fn is document_reference:
        return fn(res, source, kw.get("binaries"))
    return fn(res, source)


def iter_resources(obj: Any):
    """Yield resources from a Bundle, a list, or a single resource."""
    if isinstance(obj, list):
        for o in obj:
            yield from iter_resources(o)
    elif isinstance(obj, dict):
        if obj.get("resourceType") == "Bundle":
            for e in obj.get("entry") or []:
                if e.get("resource"):
                    yield from iter_resources(e["resource"])
        elif obj.get("resourceType"):
            yield obj
