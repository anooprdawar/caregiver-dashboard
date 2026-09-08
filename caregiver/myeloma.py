"""Disease-specific knowledge: which labs matter for multiple myeloma and how to group them.

This is *display* logic. Reference thresholds are quoted from IMWG definitions so the caregiver
can see where a value sits; they are not a substitute for the treating team's interpretation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class PanelItem:
    key: str
    label: str
    group: str            # "disease", "crab", "counts", "chemistry", "vitals"
    loinc: tuple[str, ...]
    name_patterns: tuple[str, ...]  # case-insensitive regexes on Observation.display
    unit_hint: str = ""
    note: str = ""


# Order here is the display order on the dashboard.
PANEL: list[PanelItem] = [
    # --- Disease burden (what the oncologist actually tracks for response) ---
    PanelItem("m_protein", "M-protein (SPEP)", "disease", ("33358-3", "35559-4", "33647-9"),
              (r"m[- ]?spike", r"m[- ]?protein", r"monoclonal.*(serum|electrophoresis)", r"paraprotein"),
              "g/dL", "IMWG: ≥25% drop = partial response; undetectable = CR (with negative IFE)"),
    PanelItem("kappa_flc", "Kappa free light chain", "disease", ("36916-5",),
              (r"kappa.*free", r"free.*kappa", r"\bkappa\b.*light"), "mg/L"),
    PanelItem("lambda_flc", "Lambda free light chain", "disease", ("33944-0",),
              (r"lambda.*free", r"free.*lambda", r"\blambda\b.*light"), "mg/L"),
    PanelItem("flc_ratio", "Kappa/Lambda FLC ratio", "disease", ("48378-4",),
              (r"kappa.*lambda.*ratio", r"k/l ratio", r"flc ratio", r"free light chain.*ratio"), "",
              "Normal ~0.26–1.65"),
    PanelItem("igg", "IgG", "disease", ("2465-3",), (r"^igg\b", r"immunoglobulin g\b"), "mg/dL"),
    PanelItem("iga", "IgA", "disease", ("2458-8",), (r"^iga\b", r"immunoglobulin a\b"), "mg/dL"),
    PanelItem("igm", "IgM", "disease", ("2472-9",), (r"^igm\b", r"immunoglobulin m\b"), "mg/dL"),
    PanelItem("b2m", "Beta-2 microglobulin", "disease", ("1952-1",), (r"beta.?2.?microglobulin", r"b2m"), "mg/L",
              "R-ISS staging input"),
    PanelItem("ldh", "LDH", "disease", ("2532-0", "14804-9"), (r"lactate dehydrogenase", r"\bldh\b"), "U/L",
              "R-ISS staging input"),
    PanelItem("upep", "Urine M-protein (UPEP)", "disease", ("35560-2", "42482-0"),
              (r"urine.*(m[- ]?protein|monoclonal|bence)", r"bence.?jones"), "mg/24h"),
    # --- CRAB: end-organ damage ---
    PanelItem("calcium", "Calcium", "crab", ("17861-6", "2000-8"), (r"^calcium\b(?!.*ion)",), "mg/dL",
              "CRAB: >11 mg/dL is hypercalcemia criterion"),
    PanelItem("creatinine", "Creatinine", "crab", ("2160-0", "38483-4"), (r"^creatinine\b(?!.*urine)",), "mg/dL",
              "CRAB: >2 mg/dL is renal criterion"),
    PanelItem("egfr", "eGFR", "crab", ("33914-3", "62238-1", "98979-8", "48642-3", "48643-1"),
              (r"\begfr\b", r"glomerular filtration"), "mL/min/1.73m²", "CRAB: <40 is renal criterion"),
    PanelItem("hemoglobin", "Hemoglobin", "crab", ("718-7",), (r"^hemoglobin\b(?!.*a1c)", r"^hgb\b"), "g/dL",
              "CRAB: <10 g/dL is anemia criterion"),
    # --- Counts (chemo toxicity, infection risk) ---
    PanelItem("wbc", "WBC", "counts", ("6690-2", "26464-8"), (r"^wbc\b", r"white blood cell", r"leukocytes"), "K/uL"),
    PanelItem("anc", "Neutrophils (abs)", "counts", ("751-8", "26499-4"), (r"neutrophil.*(abs|#)", r"\banc\b"), "K/uL",
              "<1.0 = neutropenia; <0.5 severe"),
    PanelItem("platelets", "Platelets", "counts", ("777-3", "26515-7"), (r"^platelet", r"^plt\b"), "K/uL"),
    PanelItem("alc", "Lymphocytes (abs)", "counts", ("731-0", "26474-7"), (r"lymphocyte.*(abs|#)",), "K/uL"),
    # --- Chemistry ---
    PanelItem("albumin", "Albumin", "chemistry", ("1751-7",), (r"^albumin\b(?!.*urine)",), "g/dL", "R-ISS input"),
    PanelItem("total_protein", "Total protein", "chemistry", ("2885-2",), (r"^(total )?protein,? total", r"^total protein"), "g/dL"),
    PanelItem("potassium", "Potassium", "chemistry", ("2823-3",), (r"^potassium\b",), "mmol/L"),
    PanelItem("sodium", "Sodium", "chemistry", ("2951-2",), (r"^sodium\b",), "mmol/L"),
    PanelItem("uric_acid", "Uric acid", "chemistry", ("3084-1",), (r"^uric acid",), "mg/dL"),
    PanelItem("glucose", "Glucose", "chemistry", ("2345-7", "2339-0"), (r"^glucose\b",), "mg/dL",
              "Dexamethasone raises glucose"),
    PanelItem("alt", "ALT", "chemistry", ("1742-6",), (r"^alt\b", r"alanine aminotransferase"), "U/L"),
    PanelItem("ast", "AST", "chemistry", ("1920-8",), (r"^ast\b", r"aspartate aminotransferase"), "U/L"),
    PanelItem("bilirubin", "Bilirubin, total", "chemistry", ("1975-2",), (r"^bilirubin.*total", r"^total bilirubin"), "mg/dL"),
    PanelItem("alk_phos", "Alkaline phosphatase", "chemistry", ("6768-6",), (r"alkaline phosphatase", r"^alk phos"), "U/L",
              "Bone turnover"),
    PanelItem("magnesium", "Magnesium", "chemistry", ("19123-9",), (r"^magnesium\b",), "mg/dL"),
    PanelItem("phosphorus", "Phosphorus", "chemistry", ("2777-1",), (r"^phosph(orus|ate)\b",), "mg/dL"),
    # --- Vitals ---
    PanelItem("weight", "Weight", "vitals", ("29463-7", "3141-9"), (r"^(body )?weight\b",), "kg"),
    PanelItem("bp_systolic", "BP systolic", "vitals", ("8480-6",), (r"systolic",), "mmHg"),
    PanelItem("bp_diastolic", "BP diastolic", "vitals", ("8462-4",), (r"diastolic",), "mmHg"),
    PanelItem("temperature", "Temperature", "vitals", ("8310-5",), (r"^(body )?temp",), "°F"),
    PanelItem("spo2", "SpO2", "vitals", ("2708-6", "59408-5"), (r"oxygen saturation", r"spo2"), "%"),
    PanelItem("heart_rate", "Heart rate", "vitals", ("8867-4",), (r"heart rate", r"^pulse\b"), "bpm"),
]

BY_KEY = {p.key: p for p in PANEL}
_LOINC_INDEX = {code: p for p in PANEL for code in p.loinc}
_NAME_INDEX = [(re.compile(pat, re.I), p) for p in PANEL for pat in p.name_patterns]

GROUP_LABELS = {
    "disease": "Disease burden",
    "crab": "CRAB / end-organ",
    "counts": "Blood counts",
    "chemistry": "Chemistry",
    "vitals": "Vitals",
}


def classify(code: str | None, display: str | None) -> str | None:
    """Return a panel key for an observation, or None if it's not one we track."""
    if code and code in _LOINC_INDEX:
        return _LOINC_INDEX[code].key
    if display:
        d = display.strip()
        for rx, item in _NAME_INDEX:
            if rx.search(d):
                return item.key
    return None


# IMWG CRAB thresholds for display flags. Units assumed as in unit_hint.
CRAB_FLAGS = {
    "calcium": lambda v: v > 11.0,
    "creatinine": lambda v: v > 2.0,
    "egfr": lambda v: v < 40,
    "hemoglobin": lambda v: v < 10.0,
}


def crab_flag(key: str, value: float | None) -> bool:
    fn = CRAB_FLAGS.get(key)
    return bool(fn and value is not None and fn(value))


# Which direction is the bad one, for trend interpretation. Absent = no clear direction.
WORSE_WHEN = {
    "m_protein": "high", "kappa_flc": "high", "lambda_flc": "high", "flc_ratio": "high", "igg": "high",
    "b2m": "high", "ldh": "high", "upep": "high",
    "calcium": "high", "creatinine": "high", "egfr": "low", "hemoglobin": "low",
    "wbc": "low", "anc": "low", "platelets": "low", "alc": "low",
    "albumin": "low", "glucose": "high", "alt": "high", "ast": "high", "bilirubin": "high",
    "alk_phos": "high", "uric_acid": "high",
}

# IMWG response thresholds applied to the disease markers, for describing direction of travel only.
RESPONSE_DROP = 0.25   # >=25% fall in a disease marker is the partial-response threshold
PROGRESSION_RISE = 0.25  # >=25% rise from nadir is the progression threshold


def direction_meaning(key: str, pct_change: float) -> str:
    """Return 'better', 'worse' or 'flat' for a percent change in a tracked value."""
    if abs(pct_change) < 10:
        return "flat"
    worse = WORSE_WHEN.get(key)
    if not worse:
        return "changed"
    rising = pct_change > 0
    return "worse" if (rising and worse == "high") or (not rising and worse == "low") else "better"


# Words in a DiagnosticReport / DocumentReference that mark it as imaging.
IMAGING_HINTS = re.compile(
    r"\b(mri|mr\b|ct\b|pet|pet/ct|pet-ct|x-?ray|radiograph|ultrasound|us\b|bone scan|nuclear|"
    r"skeletal survey|dexa|echocardiogram|echo\b|imaging|radiology)\b", re.I)
PATHOLOGY_HINTS = re.compile(r"\b(pathology|biopsy|bone marrow|cytogenetic|fish\b|flow cytometry|surgical path)\b", re.I)


def report_kind(category: str | None, display: str | None) -> str:
    text = f"{category or ''} {display or ''}"
    if re.search(r"\b(RAD|imaging|radiology)\b", text, re.I) or IMAGING_HINTS.search(text):
        return "imaging"
    if re.search(r"\b(PAT|pathology)\b", text, re.I) or PATHOLOGY_HINTS.search(text):
        return "pathology"
    if re.search(r"\b(LAB|laboratory|hematology|chemistry|microbiology)\b", text, re.I):
        return "lab"
    return "other"
