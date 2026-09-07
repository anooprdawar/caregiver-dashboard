# Caregiver Dashboard

A local-first, single source of truth for a caregiver managing a complex diagnosis (built around
multiple myeloma presenting as spinal cord compression). It pulls everything from MyChart into a
SQLite database on your machine and renders one dashboard: labs trended over time, imaging and
pathology reports, clinical notes, medication history with every change, visits, the care team, and
a caregiver log for the questions, handoffs and discrepancies that no hospital system tracks.

Nothing leaves your computer. There is no cloud, no account, no telemetry.

```
                MyChart (Epic FHIR R4)  ──sync──┐
   MyChart "Download" C-CDA XML/ZIP  ──import──┤
   Apple Health export (FHIR JSON)   ──import──┼──▶  data/raw/*.json  +  data/caregiver.db  ──▶  http://127.0.0.1:8080
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

### Route 2 — live sync via Epic's patient FHIR API

1. Create a free developer account at <https://fhir.epic.com>. Create an app: **Application Audience = Patients**,
   redirect URI exactly `http://localhost:8765/callback`, select the R4 resources listed in
   `caregiver/config.py` (`DEFAULT_SCOPES`). Save. You get a **Non-Production** client ID immediately;
   the **Production** client ID becomes usable at hospitals after Epic's sync, typically several
   business days.
2. Find the hospital's R4 base URL at <https://open.epic.com/MyApps/Endpoints> (search the health
   system name).
3. Edit `data/config.toml`: paste `client_id` and `base_url`.
4. `caregiver connect` opens a browser; **the patient logs in with their MyChart credentials** and
   approves. Tokens are stored in `data/tokens.json` (mode 0600).
5. `caregiver sync` pulls everything. Run it whenever you like; it is idempotent. `caregiver sync --loop 6h`
   keeps it running, or put it in cron/launchd.

To test against Epic's sandbox first: leave `base_url` at the default, use the Non-Production client
ID, and log in as sandbox patient `fhircamila` / `epicepic1`.

### Route 3 — imaging pixels

MyChart never gives you the images, only the radiologist's report. Ask each radiology department's
film library for the studies (they must provide them under HIPAA right of access). Put the CD/download
contents under `data/imaging/` and run `caregiver imaging index`. Studies are linked to reports by date
and listed on the Image files page. View them with Horos (Mac), MicroDicom (Windows) or Weasis.

## What is not verified, and what will break first

This was built against the FHIR R4 spec and Epic's documented patient-API behaviour, **not against a
live Epic tenant**. Expect to fix these on first contact:

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
tests/              33 tests: normalizer, C-CDA import, every route, note lifecycle
```

`caregiver reload` re-normalizes `data/raw/` after you improve a mapping; nothing pulled is ever lost.
