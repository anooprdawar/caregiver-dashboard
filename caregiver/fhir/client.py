"""Minimal FHIR R4 client with paging, retries, and Epic search quirks encoded in one table."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterator

import httpx


class FhirError(RuntimeError):
    pass


@dataclass
class ResourceSpec:
    type: str
    params: dict[str, str] = field(default_factory=dict)
    # Epic rejects some searches without a category and returns 400; we try each variant in turn
    # and union the results. Values here are alternative `category` (or other) param sets.
    variants: list[dict[str, str]] = field(default_factory=list)
    patient_param: str = "patient"


# Encodes what Epic's R4 patient-facing API accepts. Adjust via config.fhir.search_overrides.
EPIC_SPECS: list[ResourceSpec] = [
    ResourceSpec("Patient", patient_param="_id"),
    ResourceSpec("Observation", variants=[
        {"category": "laboratory"}, {"category": "vital-signs"}, {"category": "social-history"},
        {"category": "core-characteristics"}, {"category": "functional-mental-status"},
        {"category": "smartdata"}, {"category": "LDA"}]),
    ResourceSpec("DiagnosticReport"),
    ResourceSpec("DocumentReference", variants=[{"category": "clinical-note"}, {"category": "imaging-result"},
                                                {"category": "correspondence"}]),
    ResourceSpec("Condition", variants=[{"category": "problem-list-item"}, {"category": "encounter-diagnosis"},
                                        {"category": "health-concern"}, {"category": "medical-history"},
                                        {"category": "genomics"}, {"category": "infection"}]),
    ResourceSpec("Encounter"),
    ResourceSpec("Appointment", variants=[{"service-category": "appointment"}, {}]),
    ResourceSpec("MedicationRequest"),
    ResourceSpec("MedicationStatement"),
    ResourceSpec("Procedure", variants=[{"date": "ge1900-01-01"}, {}]),
    ResourceSpec("AllergyIntolerance"),
    ResourceSpec("Immunization"),
    ResourceSpec("CarePlan", variants=[{"category": "38717003"}, {"category": "assess-plan"}, {"category": "736378000"}, {}]),
    ResourceSpec("CareTeam", variants=[{"status": "active"}, {}]),
]


class FhirClient:
    def __init__(self, base_url: str, access_token: str, page_size: int = 100):
        self.base = base_url.rstrip("/") + "/"
        self.page_size = page_size
        self._c = httpx.Client(timeout=60, follow_redirects=True, headers={
            "Authorization": f"Bearer {access_token}", "Accept": "application/fhir+json"})

    def close(self):
        self._c.close()

    def _get(self, url: str, params: dict | None = None) -> httpx.Response:
        delay = 1.0
        for attempt in range(6):
            r = self._c.get(url, params=params)
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(min(delay, 30))
                delay *= 2
                continue
            return r
        return r

    def read(self, rtype: str, rid: str) -> dict | None:
        r = self._get(self.base + f"{rtype}/{rid}")
        if r.status_code == 200:
            return r.json()
        if r.status_code in (404, 410, 403):
            return None
        raise FhirError(f"GET {rtype}/{rid} -> {r.status_code}: {r.text[:300]}")

    def read_binary(self, bid: str) -> tuple[bytes, str] | None:
        r = self._c.get(self.base + f"Binary/{bid}", headers={"Accept": "*/*"})
        if r.status_code != 200:
            return None
        return r.content, r.headers.get("content-type", "application/octet-stream")

    def search(self, rtype: str, params: dict) -> Iterator[dict]:
        """Yield resources across all pages. Tolerates OperationOutcome-only bundles."""
        url = self.base + rtype
        p = {**params, "_count": str(self.page_size)}
        while url:
            r = self._get(url, p)
            p = None
            if r.status_code == 400:
                # Epic: "missing required search parameter" and similar. Caller handles variants.
                raise FhirError(f"400 on {rtype}: {r.text[:300]}")
            if r.status_code in (403, 404):
                return
            if r.status_code != 200:
                raise FhirError(f"{r.status_code} on {rtype}: {r.text[:300]}")
            b = r.json()
            if b.get("resourceType") == "OperationOutcome":
                return
            for e in b.get("entry") or []:
                res = e.get("resource")
                if res and res.get("resourceType") == rtype:
                    yield res
            url = next((l.get("url") for l in (b.get("link") or []) if l.get("relation") == "next"), None)

    def search_patient(self, spec: ResourceSpec, patient_id: str, overrides: dict[str, str] | None = None
                       ) -> tuple[list[dict], list[str]]:
        """Run every variant for one spec; de-duplicate by id. Returns (resources, errors)."""
        seen: dict[str, dict] = {}
        errors: list[str] = []
        variants = spec.variants or [{}]
        if overrides:
            variants = [overrides]
        for v in variants:
            params = {spec.patient_param: patient_id, **spec.params, **v}
            try:
                for res in self.search(spec.type, params):
                    seen[res["id"]] = res
            except FhirError as e:
                errors.append(str(e))
        return list(seen.values()), errors
