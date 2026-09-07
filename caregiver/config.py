"""Configuration: a TOML file in the data directory plus environment overrides.

Nothing here is secret except the token file, which lives in DATA_DIR with 0600 perms.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_DATA_DIR = Path(os.environ.get("CAREGIVER_DATA_DIR", "data")).resolve()

# Epic's public sandbox. Real orgs are listed at https://open.epic.com/MyApps/Endpoints
EPIC_SANDBOX_R4 = "https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4/"

# Epic patient-facing apps must request the specific resource scopes the app was
# registered with. `patient/*.read` works only if the registration allows it.
DEFAULT_SCOPES = [
    "openid", "fhirUser", "offline_access",
    "patient/Patient.read",
    "patient/Observation.read",
    "patient/DiagnosticReport.read",
    "patient/DocumentReference.read",
    "patient/Binary.read",
    "patient/Condition.read",
    "patient/Encounter.read",
    "patient/Appointment.read",
    "patient/MedicationRequest.read",
    "patient/MedicationStatement.read",
    "patient/Procedure.read",
    "patient/AllergyIntolerance.read",
    "patient/Immunization.read",
    "patient/CarePlan.read",
    "patient/CareTeam.read",
    "patient/Practitioner.read",
    "patient/Goal.read",
    "patient/ServiceRequest.read",
]


@dataclass
class FhirConfig:
    base_url: str = EPIC_SANDBOX_R4
    client_id: str = ""
    redirect_port: int = 8765
    scopes: list[str] = field(default_factory=lambda: list(DEFAULT_SCOPES))
    # Extra search params per resource, e.g. {"Observation": {"category": "laboratory"}}
    search_overrides: dict[str, dict[str, str]] = field(default_factory=dict)

    @property
    def redirect_uri(self) -> str:
        return f"http://localhost:{self.redirect_port}/callback"


@dataclass
class Config:
    data_dir: Path = DEFAULT_DATA_DIR
    patient_label: str = "Patient"
    fhir: FhirConfig = field(default_factory=FhirConfig)
    web_port: int = 8080

    @property
    def db_path(self) -> Path:
        return self.data_dir / "caregiver.db"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def token_path(self) -> Path:
        return self.data_dir / "tokens.json"

    @property
    def imaging_dir(self) -> Path:
        return self.data_dir / "imaging"

    @property
    def imports_dir(self) -> Path:
        return self.data_dir / "imports"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.raw_dir, self.imaging_dir, self.imports_dir, self.exports_dir):
            d.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.data_dir, 0o700)
        except OSError:
            pass


CONFIG_TEMPLATE = """# Caregiver Dashboard configuration. Copy to data/config.toml and edit.
patient_label = "Mom"        # how the dashboard refers to the patient
web_port = 8080

[fhir]
# 1. Register a *patient-facing* app at https://fhir.epic.com (free). Redirect URI must be
#    exactly http://localhost:8765/callback. Copy the Non-Production client ID to test against
#    the sandbox, then the Production client ID once it is live (Epic syncs it to orgs within
#    ~a few business days).
# 2. Find the hospital's R4 endpoint at https://open.epic.com/MyApps/Endpoints and paste it below.
base_url = "https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4/"
client_id = ""
redirect_port = 8765
# scopes = ["openid", "fhirUser", "offline_access", "patient/*.read"]

# [fhir.search_overrides.Observation]
# category = "laboratory"
"""


def load(data_dir: Path | None = None) -> Config:
    cfg = Config(data_dir=(data_dir or DEFAULT_DATA_DIR).resolve())
    path = cfg.data_dir / "config.toml"
    if path.exists():
        raw = tomllib.loads(path.read_text())
        cfg.patient_label = raw.get("patient_label", cfg.patient_label)
        cfg.web_port = int(raw.get("web_port", cfg.web_port))
        f = raw.get("fhir", {})
        cfg.fhir = FhirConfig(
            base_url=f.get("base_url", EPIC_SANDBOX_R4).rstrip("/") + "/",
            client_id=f.get("client_id", ""),
            redirect_port=int(f.get("redirect_port", 8765)),
            scopes=list(f.get("scopes", DEFAULT_SCOPES)),
            search_overrides={k: dict(v) for k, v in f.get("search_overrides", {}).items()},
        )
    # env overrides (useful for scripting / CI)
    if os.environ.get("CAREGIVER_FHIR_BASE"):
        cfg.fhir.base_url = os.environ["CAREGIVER_FHIR_BASE"].rstrip("/") + "/"
    if os.environ.get("CAREGIVER_CLIENT_ID"):
        cfg.fhir.client_id = os.environ["CAREGIVER_CLIENT_ID"]
    return cfg
