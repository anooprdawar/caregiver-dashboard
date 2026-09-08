"""Synthetic multiple-myeloma patient so the dashboard can be evaluated before real data arrives.

Course modeled: back pain -> ED -> MRI shows T8 compression -> emergent decompression -> biopsy ->
IgG-kappa myeloma -> dex + radiation -> D-RVd induction -> response. Numbers are plausible, not real.
"""
from __future__ import annotations

import base64
import random
from datetime import date, datetime, timedelta

from .fhir.sync import store_resources

R = random.Random(7)
PID = "demo-patient"
T0 = date.today() - timedelta(days=200)  # ED presentation


def d(days: int, hour: int = 9) -> str:
    return (datetime.combine(T0, datetime.min.time()) + timedelta(days=days, hours=hour)).isoformat()


PRACS = {
    "p-ed": ("Dr. Elena Vasquez", "Emergency Medicine"),
    "p-nsg": ("Dr. Marcus Okafor", "Neurosurgery"),
    "p-hosp": ("Dr. Priya Raman", "Hospital Medicine"),
    "p-hem": ("Dr. Samuel Chen", "Hematology/Oncology"),
    "p-rad": ("Dr. Aisha Thompson", "Radiation Oncology"),
    "p-pcp": ("Dr. Robert Miller", "Internal Medicine"),
    "p-pt": ("Jordan Lee, DPT", "Physical Therapy"),
    "p-np": ("Karen Walsh, NP", "Hematology/Oncology"),
    "p-radiol": ("Dr. Henry Park", "Diagnostic Radiology"),
    "p-path": ("Dr. Lisa Nguyen", "Hematopathology"),
}


def _cc(code, display, system="http://loinc.org", text=None):
    return {"coding": [{"system": system, "code": code, "display": display}], "text": text or display}


def _obs(oid, day, code, display, value, unit, lo=None, hi=None, enc=None, category="laboratory", hour=7):
    o = {"resourceType": "Observation", "id": oid, "status": "final",
         "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": category}]}],
         "code": _cc(code, display), "subject": {"reference": f"Patient/{PID}"},
         "effectiveDateTime": d(day, hour), "issued": d(day, hour + 3),
         "valueQuantity": {"value": round(value, 2), "unit": unit}}
    if lo is not None or hi is not None:
        rr = {}
        if lo is not None:
            rr["low"] = {"value": lo, "unit": unit}
        if hi is not None:
            rr["high"] = {"value": hi, "unit": unit}
        rr["text"] = f"{lo if lo is not None else ''}-{hi if hi is not None else ''}"
        o["referenceRange"] = [rr]
        if value > (hi if hi is not None else 1e9):
            o["interpretation"] = [{"coding": [{"code": "H", "display": "High"}]}]
        elif value < (lo if lo is not None else -1e9):
            o["interpretation"] = [{"coding": [{"code": "L", "display": "Low"}]}]
    if enc:
        o["encounter"] = {"reference": f"Encounter/{enc}"}
    return o


def _enc(eid, day, days_len, cls, typ, reason, loc, pracs):
    return {"resourceType": "Encounter", "id": eid, "status": "finished",
            "class": {"code": cls, "display": {"EMER": "Emergency", "IMP": "Inpatient", "AMB": "Outpatient"}[cls]},
            "type": [{"text": typ}], "subject": {"reference": f"Patient/{PID}"},
            "participant": [{"individual": {"reference": f"Practitioner/{p}", "display": PRACS[p][0]}} for p in pracs],
            "period": {"start": d(day, 8), "end": d(day + days_len, 16)},
            "reasonCode": [{"text": reason}], "location": [{"location": {"display": loc}}]}


def _report(rid, day, kind_code, kind_disp, display, conclusion, text, enc, performer, category_code):
    return {"resourceType": "DiagnosticReport", "id": rid, "status": "final",
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0074", "code": category_code, "display": kind_disp}]}],
            "code": _cc(kind_code, display), "subject": {"reference": f"Patient/{PID}"},
            "encounter": {"reference": f"Encounter/{enc}"}, "effectiveDateTime": d(day, 14), "issued": d(day, 18),
            "performer": [{"display": PRACS[performer][0]}], "conclusion": conclusion,
            "presentedForm": [{"contentType": "text/plain", "data": base64.b64encode(text.encode()).decode()}]}


def _med(mid, day, name, rx, dosage, status, requester, enc, reason=None, end_day=None):
    m = {"resourceType": "MedicationRequest", "id": mid, "status": status, "intent": "order",
         "medicationCodeableConcept": _cc(rx, name, "http://www.nlm.nih.gov/research/umls/rxnorm"),
         "subject": {"reference": f"Patient/{PID}"}, "encounter": {"reference": f"Encounter/{enc}"},
         "authoredOn": d(day, 11), "requester": {"display": PRACS[requester][0]},
         "dosageInstruction": [{"text": dosage}]}
    if reason:
        m["reasonCode"] = [{"text": reason}]
    if end_day is not None:
        m["dispenseRequest"] = {"validityPeriod": {"start": d(day), "end": d(end_day)}}
    return m


def _doc(did, day, typ, title, author, enc, text):
    return {"resourceType": "DocumentReference", "id": did, "status": "current", "docStatus": "final",
            "type": {"text": typ}, "category": [{"text": "Clinical Note"}], "subject": {"reference": f"Patient/{PID}"},
            "date": d(day, 15), "author": [{"display": PRACS[author][0]}], "description": title,
            "context": {"encounter": [{"reference": f"Encounter/{enc}"}]},
            "content": [{"attachment": {"contentType": "text/plain", "data": base64.b64encode(text.encode()).decode()}}]}


def build() -> list[dict]:
    res: list[dict] = []
    res.append({"resourceType": "Patient", "id": PID, "name": [{"text": "Demo Patient"}], "gender": "female",
                "birthDate": "1961-04-12", "identifier": [{"type": {"text": "MRN"}, "value": "0000000"}]})
    for pid, (name, spec) in PRACS.items():
        res.append({"resourceType": "Practitioner", "id": pid, "name": [{"text": name}],
                    "qualification": [{"code": {"text": spec}}]})

    # --- Encounters
    res += [
        _enc("e-ed", 0, 0, "EMER", "Emergency visit", "Acute back pain, bilateral leg weakness, urinary retention", "University Hospital ED", ["p-ed"]),
        _enc("e-inpt", 0, 9, "IMP", "Inpatient admission", "Spinal cord compression T8, pathologic fracture", "University Hospital 7 West", ["p-nsg", "p-hosp", "p-hem", "p-pt"]),
        _enc("e-rad1", 12, 0, "AMB", "Radiation oncology consult", "Palliative RT planning T7-T9", "Cancer Center - Radiation Oncology", ["p-rad"]),
        _enc("e-hem1", 16, 0, "AMB", "Hematology/Oncology new patient", "Newly diagnosed IgG kappa multiple myeloma, R-ISS II", "Cancer Center - Hematology", ["p-hem", "p-np"]),
        _enc("e-nsg-fu", 30, 0, "AMB", "Neurosurgery post-op", "6-week post-op check", "Neurosurgery Clinic", ["p-nsg"]),
        _enc("e-pcp", 45, 0, "AMB", "Primary care", "Hyperglycemia on dexamethasone; BP", "Family Medicine", ["p-pcp"]),
    ]
    for c in range(1, 7):  # six 28-day D-RVd cycles
        day = 21 + (c - 1) * 28
        res.append(_enc(f"e-c{c}", day, 0, "AMB", f"Infusion visit - cycle {c} day 1", "D-RVd induction", "Cancer Center Infusion", ["p-hem" if c % 2 else "p-np"]))
    res.append(_enc("e-er2", 75, 2, "EMER", "Emergency visit", "Fever 38.9C on day 10 cycle 2 - neutropenic fever r/o", "University Hospital ED", ["p-ed", "p-hosp"]))
    res.append(_enc("e-pt", 40, 0, "AMB", "Physical therapy", "Gait training, core strengthening post-decompression", "Rehab Services", ["p-pt"]))

    # --- Conditions
    conds = [
        ("c-mm", "C90.00", "Multiple myeloma not having achieved remission", 10, "active", "problem-list-item"),
        ("c-scc", "G95.20", "Spinal cord compression at T8 due to plasmacytoma", 0, "resolved", "encounter-diagnosis"),
        ("c-fx", "M84.58XA", "Pathological fracture T8 vertebra in neoplastic disease", 0, "active", "problem-list-item"),
        ("c-anemia", "D63.0", "Anemia in neoplastic disease", 1, "active", "problem-list-item"),
        ("c-ckd", "N18.2", "Chronic kidney disease stage 2 (myeloma kidney, improving)", 3, "active", "problem-list-item"),
        ("c-htn", "I10", "Essential hypertension", -2000, "active", "problem-list-item"),
        ("c-hyperglyc", "R73.9", "Steroid-induced hyperglycemia", 45, "active", "problem-list-item"),
        ("c-neuro", "G62.0", "Chemotherapy-induced peripheral neuropathy (grade 1)", 105, "active", "problem-list-item"),
        ("c-fever", "R50.81", "Fever presenting with conditions classified elsewhere", 75, "resolved", "encounter-diagnosis"),
    ]
    for cid, code, disp, day, status, cat in conds:
        r = {"resourceType": "Condition", "id": cid, "code": _cc(code, disp, "http://hl7.org/fhir/sid/icd-10-cm"),
             "subject": {"reference": f"Patient/{PID}"}, "onsetDateTime": d(day)[:10], "recordedDate": d(day)[:10],
             "clinicalStatus": {"coding": [{"code": status}]}, "category": [{"text": cat}]}
        if status == "resolved":
            r["abatementDateTime"] = d(day + 14)[:10]
        res.append(r)

    # --- Procedures
    res += [
        {"resourceType": "Procedure", "id": "pr-decomp", "status": "completed", "code": {"text": "T7-T9 posterior decompression and instrumented fusion"},
         "subject": {"reference": f"Patient/{PID}"}, "encounter": {"reference": "Encounter/e-inpt"}, "performedDateTime": d(1, 6),
         "performer": [{"actor": {"display": PRACS["p-nsg"][0]}}]},
        {"resourceType": "Procedure", "id": "pr-bmbx", "status": "completed", "code": {"text": "Bone marrow aspiration and biopsy, iliac crest"},
         "subject": {"reference": f"Patient/{PID}"}, "encounter": {"reference": "Encounter/e-inpt"}, "performedDateTime": d(4, 10),
         "performer": [{"actor": {"display": PRACS["p-hem"][0]}}]},
        {"resourceType": "Procedure", "id": "pr-rt", "status": "completed", "code": {"text": "External beam radiation therapy T7-T9, 20 Gy / 5 fx"},
         "subject": {"reference": f"Patient/{PID}"}, "encounter": {"reference": "Encounter/e-rad1"}, "performedPeriod": {"start": d(14), "end": d(20)},
         "performer": [{"actor": {"display": PRACS["p-rad"][0]}}]},
        {"resourceType": "Procedure", "id": "pr-port", "status": "completed", "code": {"text": "Port-a-cath placement, right chest"},
         "subject": {"reference": f"Patient/{PID}"}, "encounter": {"reference": "Encounter/e-hem1"}, "performedDateTime": d(19, 9)},
    ]

    # --- Supportive care: transfusions and growth factor
    for i, (day, kind, desc, enc) in enumerate([
        (0, "rbc", "Transfusion of 2 units packed red blood cells", "e-ed"),
        (2, "rbc", "Transfusion of 1 unit packed red blood cells", "e-inpt"),
        (5, "rbc", "Transfusion of 2 units packed red blood cells", "e-inpt"),
        (7, "platelet", "Platelet transfusion, apheresis (1 unit)", "e-inpt"),
        (44, "rbc", "Transfusion of 1 unit packed red blood cells", "e-c1"),
        (73, "rbc", "Transfusion of 2 units packed red blood cells", "e-er2"),
        (74, "platelet", "Platelet transfusion, apheresis (1 unit)", "e-er2"),
        (128, "rbc", "Transfusion of 1 unit packed red blood cells", "e-c5"),
    ]):
        res.append({"resourceType": "Procedure", "id": f"pr-tx{i}", "status": "completed",
                    "code": {"coding": [{"system": "http://snomed.info/sct",
                                         "code": "116859006" if kind == "rbc" else "116861002"}], "text": desc},
                    "subject": {"reference": f"Patient/{PID}"}, "encounter": {"reference": f"Encounter/{enc}"},
                    "performedDateTime": d(day, 13)})
    for i, day in enumerate([78, 79, 80, 106, 134, 162]):
        res.append(_med(f"m-gcsf{i}", day, "pegfilgrastim 6 MG/0.6ML injection", "1546451",
                        "6 mg SC once, 24h after chemotherapy", "completed", "p-hem",
                        "e-er2" if day < 100 else "e-c5", "Neutropenia prophylaxis", day + 1))

    # --- Reports: imaging + pathology
    res += [
        _report("dr-mri", 0, "36471-4", "Radiology", "MRI thoracic and lumbar spine with and without contrast",
                "T8 vertebral body pathologic fracture with epidural soft tissue mass causing severe canal stenosis and cord compression with cord signal change. Multiple additional lytic lesions T3, T11, L2, L4 concerning for multiple myeloma vs metastatic disease.",
                "FINDINGS: There is a pathologic compression fracture of T8 with 60% height loss. An enhancing epidural soft tissue mass extends from T7-T9 posteriorly, effacing the thecal sac and compressing the spinal cord, which demonstrates T2 hyperintensity consistent with cord edema. Additional T1-hypointense, enhancing marrow lesions at T3, T11, L2 and L4. No paraspinal abscess.\n\nIMPRESSION: 1. Severe spinal cord compression at T8 from pathologic fracture and epidural tumor — EMERGENT neurosurgical evaluation recommended. 2. Multifocal osseous lesions; differential includes multiple myeloma and metastatic disease.",
                "e-ed", "p-radiol", "RAD"),
        _report("dr-ct", 0, "24725-4", "Radiology", "CT chest/abdomen/pelvis with contrast",
                "Diffuse lytic osseous lesions throughout the axial skeleton. No primary visceral malignancy identified. Mild bilateral renal cortical thinning.",
                "IMPRESSION: Innumerable punched-out lytic lesions of the ribs, pelvis and spine, pattern most consistent with multiple myeloma. No lymphadenopathy. No visceral mass.",
                "e-ed", "p-radiol", "RAD"),
        _report("dr-path", 6, "33717-0", "Pathology", "Surgical pathology - T8 epidural mass",
                "Plasma cell neoplasm, kappa light chain restricted. CD138+, CD56+, cyclin D1 negative. Ki-67 15%.",
                "DIAGNOSIS: Epidural mass, T8: PLASMA CELL NEOPLASM consistent with plasmacytoma / multiple myeloma. Immunostains: CD138 positive, kappa restricted, lambda negative, CD20 negative.",
                "e-inpt", "p-path", "PAT"),
        _report("dr-bm", 8, "33717-0", "Pathology", "Bone marrow biopsy with flow cytometry, FISH, cytogenetics",
                "Hypercellular marrow with 45% kappa-restricted plasma cells. FISH: t(11;14) present; del(17p) NOT detected; 1q gain NOT detected. Standard-risk cytogenetics.",
                "BONE MARROW, ILIAC CREST: Plasma cell myeloma, 45% plasma cells by CD138 IHC. Flow: 38% abnormal plasma cells (CD38+/CD138+/CD56+/CD19-/cytoplasmic kappa+).\nFISH: IGH::CCND1 t(11;14) detected. TP53 deletion not detected. 1q21 gain not detected. Hyperdiploidy not detected.\nConventional karyotype: 46,XX[20].",
                "e-inpt", "p-path", "PAT"),
        _report("dr-pet", 12, "44139-4", "Radiology", "PET/CT skull base to mid-thigh (FDG)",
                "FDG-avid lesions at T8 (post-surgical), T3, T11, L2, L4, right iliac, and left 6th rib; SUVmax 8.2 at L2. No extramedullary disease.",
                "IMPRESSION: Multifocal FDG-avid osseous lesions consistent with multiple myeloma. Baseline for response assessment. No extramedullary plasmacytoma.",
                "e-rad1", "p-radiol", "RAD"),
        _report("dr-mri2", 95, "36471-4", "Radiology", "MRI thoracic spine with and without contrast",
                "Interval decompression T7-T9 with stable hardware. Resolution of epidural mass. Cord signal normalized. Treated lesions show decreased enhancement.",
                "IMPRESSION: Expected post-operative and post-radiation appearance. No residual cord compression. No new lesions.",
                "e-nsg-fu", "p-radiol", "RAD"),
        _report("dr-pet2", 160, "44139-4", "Radiology", "PET/CT skull base to mid-thigh (FDG) - response assessment",
                "Marked decrease in FDG avidity of all previously seen lesions; L2 SUVmax now 2.1 (from 8.2). No new lesions. Deauville-equivalent complete metabolic response.",
                "IMPRESSION: Complete metabolic response compared with baseline PET/CT.",
                "e-c6", "p-radiol", "RAD"),
    ]

    # --- Labs over time
    def series(oid_prefix, days, code, display, unit, fn, lo, hi, enc_fn=None, noise=0.04):
        for i, day in enumerate(days):
            v = fn(day) * (1 + R.uniform(-noise, noise))
            res.append(_obs(f"{oid_prefix}-{i}", day, code, display, max(v, 0), unit, lo, hi, enc_fn(day) if enc_fn else None))

    lab_days = [0, 1, 2, 4, 6, 9] + list(range(16, 190, 7))
    cbc_days = lab_days + [75]
    inf_days = [16] + [21 + 28 * c for c in range(6)] + [188]

    def decay(start, floor, half_life_days, offset=16):
        return lambda t: floor + (start - floor) * (0.5 ** (max(t - offset, 0) / half_life_days)) if t >= offset else start

    series("mspike", inf_days, "33358-3", "M-spike (SPEP)", "g/dL", decay(4.2, 0.15, 30), 0, 0)
    series("kflc", inf_days, "36916-5", "Kappa free light chain", "mg/L", decay(1850, 25, 25), 3.3, 19.4)
    series("lflc", inf_days, "33944-0", "Lambda free light chain", "mg/L", lambda t: 6.5, 5.7, 26.3, noise=0.15)
    series("flcr", inf_days, "48378-4", "Kappa/Lambda free light chain ratio", "", decay(280, 3.0, 25), 0.26, 1.65)
    series("igg", inf_days, "2465-3", "IgG", "mg/dL", decay(5600, 820, 30), 700, 1600)
    series("iga", inf_days, "2458-8", "IgA", "mg/dL", lambda t: 42, 70, 400, noise=0.1)
    series("igm", inf_days, "2472-9", "IgM", "mg/dL", lambda t: 18, 40, 230, noise=0.1)
    series("b2m", [4, 100, 188], "1952-1", "Beta-2 microglobulin", "mg/L", decay(4.9, 1.8, 60), 0.6, 2.4)
    series("ldh", [0, 4, 100, 188], "2532-0", "Lactate dehydrogenase", "U/L", decay(310, 170, 40), 120, 250)
    series("ca", lab_days, "17861-6", "Calcium", "mg/dL", decay(11.6, 9.3, 5, offset=1), 8.6, 10.3)
    series("cr", lab_days, "2160-0", "Creatinine", "mg/dL", decay(2.3, 1.1, 20, offset=2), 0.6, 1.1)
    series("egfr", lab_days, "98979-8", "eGFR", "mL/min/1.73m2", decay(24, 58, 20, offset=2), 60, None)
    series("hgb", cbc_days, "718-7", "Hemoglobin", "g/dL", lambda t: 8.4 if t < 10 else min(8.4 + (t - 10) * 0.025, 12.1) - (1.2 if 70 <= t <= 80 else 0), 12.0, 15.5)
    series("wbc", cbc_days, "6690-2", "WBC", "K/uL", lambda t: 3.6 if t < 10 else (1.4 if 72 <= t <= 78 else 4.1), 4.0, 11.0, noise=0.12)
    series("anc", cbc_days, "751-8", "Neutrophils, absolute", "K/uL", lambda t: 2.4 if t < 10 else (0.42 if 72 <= t <= 78 else 2.5), 1.5, 7.5, noise=0.15)
    series("plt", cbc_days, "777-3", "Platelets", "K/uL", lambda t: 118 if t < 10 else (96 if 72 <= t <= 78 else 165), 150, 400, noise=0.1)
    series("alb", lab_days, "1751-7", "Albumin", "g/dL", decay(3.1, 4.0, 40), 3.5, 5.0)
    series("tp", lab_days, "2885-2", "Total protein", "g/dL", decay(10.8, 6.9, 30), 6.4, 8.3)
    series("k", lab_days, "2823-3", "Potassium", "mmol/L", lambda t: 4.1, 3.5, 5.1, noise=0.05)
    series("glu", lab_days, "2345-7", "Glucose", "mg/dL", lambda t: 98 if t < 8 else (188 if t < 60 else 142), 70, 99, noise=0.1)
    series("alp", [0, 4, 44, 100, 188], "6768-6", "Alkaline phosphatase", "U/L", decay(168, 92, 50), 40, 130)
    series("wt", [0, 16, 44, 72, 100, 128, 160, 188], "29463-7", "Body weight", "kg", lambda t: 68 - 4 * (1 - 0.5 ** (t / 40)) + (2 if t > 120 else 0), None, None,
           noise=0.005)
    for i, day in enumerate([0, 16, 45, 72, 100, 128, 160, 188]):
        res.append({"resourceType": "Observation", "id": f"bp-{i}", "status": "final",
                    "category": [{"coding": [{"code": "vital-signs"}]}], "code": _cc("85354-9", "Blood pressure panel"),
                    "subject": {"reference": f"Patient/{PID}"}, "effectiveDateTime": d(day, 8),
                    "component": [
                        {"code": _cc("8480-6", "Systolic blood pressure"), "valueQuantity": {"value": round(R.uniform(128, 158)), "unit": "mmHg"}},
                        {"code": _cc("8462-4", "Diastolic blood pressure"), "valueQuantity": {"value": round(R.uniform(76, 94)), "unit": "mmHg"}}]})

    # --- Medications (the handoff problem lives here)
    res += [
        _med("m-dex-ed", 0, "dexamethasone 10 MG/mL injection", "1116927", "10 mg IV x1 then 4 mg IV q6h", "completed", "p-ed", "e-ed", "Cord compression", 9),
        _med("m-dex-po", 9, "dexamethasone 4 MG tablet", "197577", "4 mg PO q6h, taper over 2 weeks per neurosurgery", "completed", "p-hosp", "e-inpt", "Post-decompression taper", 23),
        _med("m-dex-mm", 16, "dexamethasone 20 MG tablet", "309684", "40 mg PO weekly (days 1,8,15,22) with D-RVd", "active", "p-hem", "e-hem1", "Multiple myeloma"),
        _med("m-oxy", 1, "oxycodone 5 MG tablet", "1049621", "5-10 mg PO q4h PRN pain", "stopped", "p-nsg", "e-inpt", "Post-op pain", 40),
        _med("m-gaba", 2, "gabapentin 300 MG capsule", "310431", "300 mg PO TID", "active", "p-nsg", "e-inpt", "Neuropathic pain / radiculopathy"),
        _med("m-senna", 1, "senna 8.6 MG tablet", "312935", "2 tabs PO nightly while on opioids", "stopped", "p-hosp", "e-inpt", None, 40),
        _med("m-ppi", 9, "omeprazole 20 MG capsule", "198051", "20 mg PO daily while on dexamethasone", "active", "p-hosp", "e-inpt", "GI prophylaxis on steroids"),
        _med("m-dara", 16, "daratumumab-hyaluronidase 1800 MG/30ML injection", "2374449", "1800 mg SC weekly cycles 1-2, q2w cycles 3-6, then q4w", "active", "p-hem", "e-hem1", "Multiple myeloma"),
        _med("m-bort", 16, "bortezomib 3.5 MG injection", "358258", "1.3 mg/m2 SC days 1,8,15 of 28-day cycle", "active", "p-hem", "e-hem1", "Multiple myeloma"),
        _med("m-len", 16, "lenalidomide 25 MG capsule", "643037", "25 mg PO daily days 1-21 of 28 (REMS)", "active", "p-hem", "e-hem1", "Multiple myeloma"),
        _med("m-len15", 105, "lenalidomide 15 MG capsule", "643035", "15 mg PO daily days 1-21 of 28 — DOSE REDUCED for neuropathy/cytopenias", "active", "p-hem", "e-c4", "Multiple myeloma"),
        _med("m-acv", 16, "acyclovir 400 MG tablet", "197310", "400 mg PO BID (zoster prophylaxis while on bortezomib/dara)", "active", "p-hem", "e-hem1", "Antiviral prophylaxis"),
        _med("m-asa", 16, "aspirin 81 MG tablet", "243670", "81 mg PO daily (VTE prophylaxis on lenalidomide)", "active", "p-hem", "e-hem1", "VTE prophylaxis"),
        _med("m-zol", 21, "zoledronic acid 4 MG/100ML injection", "1116635", "4 mg IV monthly (renally adjusted)", "active", "p-hem", "e-c1", "Myeloma bone disease"),
        _med("m-lis", -2000, "lisinopril 20 MG tablet", "314077", "20 mg PO daily", "active", "p-pcp", "e-pcp", "Hypertension"),
        _med("m-met", 45, "metformin 500 MG tablet", "861007", "500 mg PO BID with meals", "active", "p-pcp", "e-pcp", "Steroid-induced hyperglycemia"),
        _med("m-cefe", 75, "cefepime 2 GM injection", "309090", "2 g IV q8h x 48h", "completed", "p-hosp", "e-er2", "Neutropenic fever", 77),
        _med("m-gcsf", 77, "filgrastim 480 MCG/0.8ML injection", "1650272", "480 mcg SC daily x3 after neutropenic fever; consider for future cycles", "completed", "p-hem", "e-er2", "Neutropenia", 80),
        _med("m-bact", 16, "sulfamethoxazole-trimethoprim 800-160 MG tablet", "198335", "1 tab PO MWF (PJP prophylaxis)", "active", "p-hem", "e-hem1", "PJP prophylaxis"),
    ]

    # --- Notes / documents
    res += [
        _doc("doc-ed", 0, "ED Provider Note", "ED Provider Note", "p-ed", "e-ed",
             "CC: 3 weeks progressive mid-back pain, 2 days bilateral leg weakness, unable to void x12h.\nExam: 3/5 strength bilateral LE, sensory level ~T10, hyperreflexic, post-void residual 650 mL.\nMRI: T8 pathologic fx with cord compression. Dexamethasone 10 mg IV given. Neurosurgery at bedside.\nCa 11.6, Cr 2.3, Hgb 8.4, total protein 10.8 with globulin gap — c/f myeloma. SPEP/UPEP/FLC sent."),
        _doc("doc-op", 1, "Operative Note", "Operative Note - T7-T9 decompression/fusion", "p-nsg", "e-inpt",
             "Pre-op dx: T8 pathologic fracture with epidural tumor and cord compression.\nProcedure: T8 laminectomy, transpedicular decompression, T7-T9 posterior instrumented fusion.\nFindings: friable soft grey tumor in epidural space, sent for frozen (plasma cell neoplasm) and permanent.\nEBL 450 mL. No complications. Plan: mobilize with PT POD1, TLSO brace when out of bed, steroid taper, heme/onc consult."),
        _doc("doc-heme-consult", 4, "Consult Note", "Hematology/Oncology Inpatient Consult", "p-hem", "e-inpt",
             "Reason: plasma cell neoplasm on frozen section; hypercalcemia; AKI; anemia.\nAssessment: presumed multiple myeloma with CRAB features (Ca 11.6, Cr 2.3, Hgb 8.4, lytic bone disease). SPEP: M-spike 4.2 g/dL IgG kappa. FLC kappa 1850, ratio 280. B2M 4.9, albumin 3.1, LDH 310 — R-ISS II pending FISH.\nPlan: bone marrow biopsy today. Aggressive IVF, calcitonin x48h, zoledronic acid once Cr <2. Hold on systemic therapy until path back; dex already on board. Outpatient new-patient visit in ~10 days to start D-RVd. Radiation oncology consult for T7-T9."),
        _doc("doc-dc", 9, "Discharge Summary", "Discharge Summary", "p-hosp", "e-inpt",
             "Hospital course: admitted with T8 cord compression from plasmacytoma, s/p decompression/fusion POD8. Path: plasma cell neoplasm, kappa. BM biopsy 45% plasma cells, t(11;14), standard risk. Ca normalized with fluids/calcitonin. Cr improved 2.3 -> 1.6. Hgb stable 8.9.\nDischarge meds: dexamethasone 4 mg q6h taper (see schedule), oxycodone PRN, gabapentin 300 TID, senna, omeprazole, lisinopril (held, resume when SBP >140 consistently).\nFollow-up: Neurosurgery 3 wk; Rad Onc 3 days; Heme/Onc 1 wk (Dr. Chen); PT/OT home health.\nNOTE: lisinopril held at discharge — ensure someone owns the decision to restart."),
        _doc("doc-hem1", 16, "Progress Notes", "Heme/Onc New Patient - treatment plan", "p-hem", "e-hem1",
             "Dx: IgG kappa multiple myeloma, R-ISS stage II, standard-risk FISH (t(11;14)). Transplant-eligible.\nPlan: D-RVd x 4-6 cycles then ASCT consult vs continue. Start cycle 1 day 1 next week after port placement.\nSupportive: acyclovir, Bactrim MWF, aspirin 81, zoledronic acid monthly (renal dosing), dex 40 weekly (reduce to 20 if poorly tolerated given age), PPI.\nStop the neurosurgery dex taper — will be replaced by weekly myeloma dosing. Patient/caregiver counseled on REMS for lenalidomide, neuropathy monitoring, fever precautions (call for T>38.0).\nQuestions to revisit: glucose monitoring on dex — coordinate with PCP."),
        _doc("doc-er2", 75, "ED Provider Note", "ED Note - neutropenic fever", "p-ed", "e-er2",
             "Day 10 of cycle 2 D-RVd. T 38.9, HR 108, BP 118/70. ANC 0.42. Cultures drawn, cefepime started within 45 min. Admitted to hospitalist service.\nCultures negative at 48h; defervesced. Discharged on day 3 with filgrastim x3. Heme/Onc notified — will consider growth factor support and/or lenalidomide dose reduction next cycle."),
        _doc("doc-c4", 105, "Progress Notes", "Heme/Onc - cycle 4 day 1", "p-hem", "e-c4",
             "Response: M-spike 4.2 -> 0.6 g/dL (VGPR trajectory). FLC ratio normalizing. Cr 1.2.\nToxicity: grade 1 peripheral neuropathy (fingertip tingling), grade 2 neutropenia. Reduce lenalidomide to 15 mg. Continue bortezomib but monitor closely — switch to weekly if progresses.\nGlucose 180s on dex days — PCP started metformin. Consider dex 20 mg.\nASCT: referred to transplant program for consult after cycle 4-6. Restaging PET after cycle 6."),
        _doc("doc-c6", 161, "Progress Notes", "Heme/Onc - cycle 6, restaging", "p-hem", "e-c6",
             "M-spike 0.2 g/dL, FLC ratio 3.1, IgG 900. PET/CT: complete metabolic response. Marrow biopsy to confirm CR planned.\nPlan: proceed to autologous stem cell transplant consult (Dr. Ito) vs continued D-RVd then lenalidomide maintenance — decision pending patient preference and transplant eligibility assessment. Stem cell collection tentatively in 4-6 weeks if proceeding."),
    ]

    # --- Appointments (future)
    for i, (day, typ, who, loc) in enumerate([
        (196, "Bone marrow biopsy (restaging)", "p-hem", "Cancer Center Procedure Suite"),
        (203, "Transplant consult - Dr. Ito", None, "BMT Clinic"),
        (203, "Hematology/Oncology follow-up", "p-hem", "Cancer Center - Hematology"),
        (208, "Neurosurgery 6-month follow-up with XR", "p-nsg", "Neurosurgery Clinic"),
        (215, "Primary care - diabetes follow-up", "p-pcp", "Family Medicine"),
    ]):
        parts = [{"actor": {"reference": f"Patient/{PID}"}, "status": "accepted"},
                 {"actor": {"reference": f"Location/{i}", "display": loc}, "status": "accepted"}]
        if who:
            parts.append({"actor": {"reference": f"Practitioner/{who}", "display": PRACS[who][0]}, "status": "accepted"})
        res.append({"resourceType": "Appointment", "id": f"appt-{i}", "status": "booked", "start": d(day, 9 + i % 3), "end": d(day, 10 + i % 3),
                    "serviceType": [{"text": typ}], "participant": parts})

    res += [
        {"resourceType": "AllergyIntolerance", "id": "al-1", "clinicalStatus": {"coding": [{"code": "active"}]},
         "code": {"text": "Sulfa (sulfonamide antibiotics)"}, "patient": {"reference": f"Patient/{PID}"},
         "reaction": [{"manifestation": [{"text": "Rash"}], "severity": "moderate"}], "recordedDate": "2009-03-01"},
        {"resourceType": "AllergyIntolerance", "id": "al-2", "clinicalStatus": {"coding": [{"code": "active"}]},
         "code": {"text": "Iodinated contrast"}, "patient": {"reference": f"Patient/{PID}"},
         "reaction": [{"manifestation": [{"text": "Hives"}], "severity": "mild"}], "recordedDate": "2015-06-10"},
    ]
    return res


def load(cfg, conn) -> int:
    from . import db
    from .web import queries as Q
    n = store_resources(conn, build(), "demo")
    # a few caregiver notes so the notebook isn't empty
    if not db.rows(conn, "SELECT id FROM note LIMIT 1"):
        Q.add_note(conn, "issue", "Sulfa allergy on chart, yet Bactrim (a sulfonamide) is prescribed for PJP prophylaxis",
                   "Discharge summary and heme/onc note both list Bactrim MWF. Allergy list says sulfa -> rash (2009). Nobody has addressed this. Ask Dr. Chen: intentional (desensitized?) or oversight? Alternative: atovaquone.",
                   due=d(196)[:10], owner="caregiver")
        Q.add_note(conn, "handoff", "Lisinopril held at discharge; no one has said to restart",
                   "Discharge summary: 'resume when SBP >140 consistently'. Clinic BPs 128-158. PCP visit didn't mention it. Who owns this?",
                   owner="caregiver")
        Q.add_note(conn, "question", "Dex 40 mg vs 20 mg weekly — glucose in 180s on dex days",
                   "Dr. Chen wrote 'consider dex 20 mg' twice (cycle 1 and cycle 4 notes) but dose was never changed. Metformin added by PCP instead. Raise at next visit.",
                   due=d(203)[:10], owner="caregiver")
        Q.add_note(conn, "decision", "Transplant vs continued D-RVd + maintenance",
                   "Consult with Dr. Ito scheduled. Questions: expected PFS benefit at this age/risk; collection logistics; caregiver requirement for 2-3 weeks post-transplant.",
                   due=d(203)[:10], owner="caregiver")
        Q.add_note(conn, "symptom", "Fingertip tingling worse after cycle 5", "Grade 1 -> maybe grade 2? Dropping small objects occasionally. Bortezomib-related. Report at cycle 7 / consider weekly dosing.",
                   event_date=d(168)[:10], owner="caregiver")
        Q.add_note(conn, "todo", "Request DICOM images of MRI (day 0) and both PET/CTs from radiology film library",
                   "Needed for transplant center second opinion. Drop the CD contents into data/imaging/ and run `caregiver imaging index`.",
                   owner="caregiver")
        conn.execute("UPDATE note SET source='demo'")
    db.set_meta(conn, "demo", "1")
    db.set_meta(conn, "last_sync", db.now_iso())
    conn.commit()
    return n
