# Caregiver Dashboard

A local-first, single source of truth for a caregiver managing a complex diagnosis (built around
multiple myeloma presenting as spinal cord compression). It pulls everything from MyChart into a
SQLite database on your machine and renders one dashboard: labs trended over time, imaging and
pathology reports, clinical notes, medication history with every change, visits, the care team, and
a caregiver log for the questions, handoffs and discrepancies that no hospital system tracks.

Nothing leaves your computer. There is no cloud, no account, no telemetry.

```
     Epic FHIR R4 direct (needs org)  ──sync──┐
   Apple Health Records export      ──import──┤   ← the path that works for an individual
   MyChart "Download" C-CDA XML/ZIP  ──import──┼──▶  data/raw/*.json  +  data/caregiver.db  ──▶  http://127.0.0.1:8080
   Radiology CD (DICOM) / PDFs       ──index───┘
```

## What you get

| Page | What it answers |
|---|---|
| **Overview** | Latest disease-burden and CRAB labs with change vs previous draw, open items for the team, active meds, care team, upcoming appointments, recent imaging/path |
| **Timeline** | Every event from every source in one chronological stream, filterable by kind and text |
| **Labs** | Trend charts with reference bands for the myeloma panel (M-protein, free light chains, ratio, immunoglobulins, B2M, LDH), CRAB labs, counts, chemistry, vitals. Plus every test on record |
| **Imaging & Path** | Full report text and impressions for MRI, CT, PET, biopsy, marrow, FISH |
| **Notes** | Full text of clinical notes (consults, discharge summaries, progress notes) |
| **Meds** | Reconciliation: one row per drug with the *current* instructions, plus every order that changed it, who wrote it and why. Allergies shown above |
| **Visits** | Each encounter with its notes, results, orders, procedures, diagnoses |
| **Care team** | Every clinician seen, specialty, visit count, first/last seen; handoff log |
| **Image files** | Index of DICOM studies and PDFs you drop in, linked to their reports by date |
| **Caregiver log** | Questions, issues, decisions, handoffs, symptoms, todos, with due dates and owners |
| **Brief** | Printable one-page summary to hand to the next doctor (also `/brief.md`) |

## Quick start (5 minutes, synthetic patient)

```bash
git clone https://github.com/anooprdawar/caregiver-dashboard && cd caregiver-dashboard
python3 -m venv .venv && . .venv/bin/activate && pip install -e .
caregiver demo          # loads a synthetic myeloma course: ED -> decompression -> dx -> D-RVd -> response
caregiver serve         # open http://127.0.0.1:8080
```

## Connecting to real MyChart data

There are three routes. Do the first one today; the second is the "synced" one.

### Route 1 — offline export (works today, no registration)

In MyChart: **Menu → Sharing Hub / Document Center → Download** (or **Visit Summary → Download**) gives
a ZIP of C-CDA XML. Then:

```bash
caregiver init                                # creates data/config.toml
caregiver import ccda ~/Downloads/MyChart.zip
caregiver serve
```

If the patient has an iPhone with Health Records connected: iPhone → Health → profile → **Export All
Health Data**, then `caregiver import fhir export/clinical-records/`.

Exports contain what the hospital chose to include. Notes and full report text are often missing
from C-CDA. That is why Route 2 exists.

### Route 2 — Apple Health Records (the practical sync path)

**This is the route that works for an individual with only a MyChart login.** Registering your own
Epic app does not work: a production client ID is inert until someone at the hospital downloads it
into their Epic environment, and there is no self-serve path. Apple already did that onboarding at
essentially every Epic health system, so you ride on their registration.

1. iPhone → **Health** → profile picture → **Health Records** → **Add Account** → pick the health
   system → log in with the patient's MyChart credentials.
2. The first sync can take **hours** if the record is large. That is normal, not a hang. Records
   appear under Health → Browse as they land.
3. Epic keeps feeding the phone from then on. Apple requires health systems to issue renewable
   tokens (3+ months) or long-lived tokens (1+ year), so the connection survives without re-login.
4. To pull it onto the laptop: Health → profile picture → **Export All Health Data** → share the
   `export.zip` to your machine, then:

```bash
caregiver import fhir ~/Downloads/export.zip     # reads the zip directly
```

Apple syncs `Observation` (labs and vitals), `Condition`, `MedicationRequest`, `AllergyIntolerance`,
`Immunization`, `Procedure`, `Patient`, and **clinical notes** via `Binary`, `DocumentReference` and
`DiagnosticReport`. That last group is what carries MRI/PET report text and consult notes.

**Verify the export actually contains clinical records before relying on it.** There are recurring
reports of `Export All Health Data` omitting them:

```bash
unzip -l ~/Downloads/export.zip | grep -c clinical-records   # expect a large number, not 0
```

If it is 0, use Route 1's C-CDA download instead. The per-record **Export PDF** button in Health is
useful for handing a single result to a doctor, but it is not an import path: the structure is gone.

### Route 2b — your own Epic client ID (organizations only)

Kept for completeness. Register at <https://fhir.epic.com> (Application Audience = Patients, redirect
URI `http://localhost:8765/callback`), put the client ID and the org's R4 endpoint from
<https://open.epic.com/MyApps/Endpoints> into `data/config.toml`, then `caregiver connect` and
`caregiver sync --loop 24h`. This is fully implemented and works against Epic's sandbox today
(Non-Production client ID, sandbox patient `fhircamila` / `epicepic1`). It only reaches a real
hospital if that hospital's Epic administrator loads your client ID, which realistically requires an
institutional relationship.

### Route 3 — imaging pixels

MyChart never gives you the images, only the radiologist's report. Ask each radiology department's
film library for the studies (they must provide them under HIPAA right of access). Put the CD/download
contents under `data/imaging/` and run `caregiver imaging index`. Studies are linked to reports by date
and listed on the Image files page. View them with Horos (Mac), MicroDicom (Windows) or Weasis.

## What is not verified, and what will break first

This was built against the FHIR R4 spec and Epic's documented patient-API behaviour, **not against a
live Epic tenant**. Expect to fix these on first contact:

- **Epic-direct is gated, not slow.** A production client ID does nothing until a health system
  downloads it. Route 2 (Apple) exists because of this. Everything below applies only if you have an
  institutional path to Route 2b.
- **Search parameter quirks.** Epic rejects some searches without a `category`. The variants tried
  per resource are in `caregiver/fhir/client.py` (`EPIC_SPECS`). If a resource type shows
  `partial`/`error` in `caregiver status`, add the working params under
  `[fhir.search_overrides.<Resource>]` in `config.toml`.
- **Refresh tokens.** Epic issues them to patient apps only when the app is configured for
  `offline_access` and the org permits it. Without one, `caregiver sync` re-opens the browser each time
  the hour-long access token expires.
- **Notes.** `DocumentReference` and `Binary` are available at most Epic orgs but some restrict them.
  If notes are empty after a sync, Route 1's C-CDA usually includes visit narratives.
- **Multiple health systems.** Each Epic org is a separate endpoint and a separate login. One
  `config.toml` = one org today. Run separate data dirs (`--data`) per org, or import the second
  org's C-CDA into the first.
- **Proxy access.** If the caregiver, not the patient, is the MyChart user, the FHIR authorization must
  still be done under the *patient's* identity or via a MyChart proxy grant that the org exposes to
  third-party apps. Some orgs do not.

## Safety and privacy

- `data/` is git-ignored. It contains PHI. Back it up like you would a passport.
- The dashboard binds to `127.0.0.1` only. Do not put it behind a public port.
- Reference thresholds shown (CRAB criteria, IMWG response) are quoted for orientation. This tool
  organizes information so the caregiver can ask better questions; it does not interpret results.

## Layout

```
caregiver/
  cli.py            caregiver init|connect|sync|import|imaging|serve|demo|brief|reload|status
  config.py         data dir, Epic endpoint, scopes
  db.py             SQLite schema + upserts (raw JSON kept alongside)
  myeloma.py        which labs matter, LOINC codes + name patterns, CRAB flags
  fhir/smart.py     SMART on FHIR standalone launch, PKCE, token refresh
  fhir/client.py    paging FHIR client, Epic search variants
  fhir/normalize.py FHIR resources -> tables (R4, tolerant of DSTU2 exports)
  fhir/sync.py      pull all resources, fetch note Binaries, log per resource type
  importers/        ccda.py (MyChart download), fhir_bundle.py (Apple Health), imaging.py (DICOM)
  web/              FastAPI app, queries (read models), templates, vendored Chart.js
  brief.py          one-page appointment brief (markdown + HTML)
  demo.py           synthetic myeloma patient
tests/              34 tests: normalizer, C-CDA + Apple Health import, every route, note lifecycle
```

`caregiver reload` re-normalizes `data/raw/` after you improve a mapping; nothing pulled is ever lost.
