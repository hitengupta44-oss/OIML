# Every file in this bundle

89 files. Three deployment stages plus the data and engine they share.

---

## Root

Start here.

| File | Size | What it is |
|---|---|---|
| `.env.example` | 0 KB | blank template — values live in each host's secret store |
| `.gitignore` | 0 KB | keeps secrets and generated data out of version control |
| `DEPLOY.md` | 4 KB | three-stage deployment order, secret placement, pre-demo checklist |
| `MANIFEST.json` | 9 KB | every file with size and SHA-256 |
| `README.md` | 6 KB | project overview, the design principle, why the band operators matter |

## standards/

The rules as versioned data. No band value or operator appears in any .py or .ts file.

| File | Size | What it is |
|---|---|---|
| `accuracy-classes.json` | 5 KB | R 76-1 Tables 3 & 4 — class e-ranges, n limits, Min multipliers |
| `clause-map.csv` | 3 KB | 35 topics mapped OIML ↔ Indian Seventh Schedule ↔ R 76-2 |
| `influence-tests.json` | 13 KB | Annex B + D 11 — 10 influence factors, 8 disturbances, severities |
| `master-data.json` | 7 KB | 8 RRSLs, 4 real approval marks, 30 manufacturers, load cells |
| `mpe-bands.json` | 4 KB | R 76-1 Table 6 — permissible error bands with exact operators |
| `report-layout.json` | 12 KB | R 76-2 report structure — cover fields, 17 sections, columns |
| `software-checklist.json` | 8 KB | WELMEC 7.2 risk classes A–F, types P/U, extensions L/T/S/D |
| `test-catalogue.json` | 24 KB | all 18 tests: clause refs, inputs, load strategies, tolerance kinds |
| `weight-classes.json` | 6 KB | R 111-1 Table 1 — 30 nominals × 9 classes |
| `workflow.json` | 10 KB | Approval of Models Rules 2011 — states, roles, certificate fields |

## engine/

One engine, two languages, the same JSON.

| File | Size | What it is |
|---|---|---|
| `nawi-engine.ts` | 12 KB | the same engine for the browser, reading the same JSON |
| `nawi_engine.py` | 15 KB | the metrology engine (backend) |
| `test_harness.py` | 8 KB | 31 known-answer tests anchored to a published approval |

## supabase/

STAGE 1 — database, auth, roles, row-level security.

| File | Size | What it is |
|---|---|---|
| `README.md` | 7 KB | Stage 1 runbook |
| `RESET.sql` | 2 KB | wipe public and app, keep the project and its keys |
| `setup.sql` | 40 KB | the whole schema in one paste — 107 statements |
| `verify.sql` | 8 KB | 15 checks as one query |
| `seed/create_users.py` | 4 KB | 12 demo accounts with roles in app_metadata |
| `seed/make_seed_sql.py` | 10 KB | regenerates seed.sql from the dataset |
| `seed/seed.sql` | 734 KB | 120 evaluations, 4 634 observations |
| `migrations/0001_schema.sql` | 15 KB | tables, enums, constraints, reference rows |
| `migrations/0002_functions.sql` | 14 KB | triggers, state machine, audit, views, grants |
| `migrations/0003_rls.sql` | 10 KB | row-level security and table grants |
| `tests/rls_test.sql` | 10 KB | the same attempts via RAISE NOTICE — for psql |
| `tests/security_test.sql` | 9 KB | 13 break-in attempts as a result table — runs in the SQL Editor |

## backend/

STAGE 2 — Hugging Face Docker Space. FastAPI, DOCX/PDF rendering, independent recompute.

| File | Size | What it is |
|---|---|---|
| `.dockerignore` | 0 KB | keeps __pycache__ and .env out of the image |
| `.env.example` | 0 KB |  |
| `Dockerfile` | 2 KB | python:3.11-slim + LibreOffice, uid 1000, port 7860 |
| `README.md` | 6 KB | Stage 2 runbook |
| `app.py` | 9 KB | FastAPI — health, verify, render, render-payload |
| `auth.py` | 3 KB | Supabase JWT validation; roles from app_metadata |
| `build_template.py` | 12 KB | regenerates templates/r76-2.docx from report-layout.json |
| `paths.py` | 2 KB | resolves standards/ and engine/ in repo or Space layout |
| `recompute.py` | 11 KB | re-derives every verdict from the raw readings |
| `render.py` | 16 KB | DOCX via docxtpl, PDF via LibreOffice headless |
| `requirements.txt` | 0 KB | fastapi, uvicorn, docxtpl, PyJWT |
| `supabase_io.py` | 7 KB | reads evaluations, writes reports to Storage |
| `test_local.py` | 5 KB | renders real reports with no cloud at all |
| `templates/r76-2.docx` | 39 KB | the report template |

## frontend/

STAGE 3 — Vercel. Vite + React PWA with offline capture.

| File | Size | What it is |
|---|---|---|
| `.env.example` | 0 KB |  |
| `README.md` | 4 KB | Stage 3 runbook |
| `index.html` | 1 KB |  |
| `package.json` | 1 KB |  |
| `tsconfig.json` | 1 KB |  |
| `vite.config.ts` | 2 KB | PWA config, engine and standards aliases |
| `src/App.tsx` | 5 KB | shell, auth, connectivity banner |
| `src/main.tsx` | 0 KB |  |
| `src/styles.css` | 7 KB | instrument-panel palette, graduation motif |
| `src/lib/offline.ts` | 4 KB | IndexedDB queue — capture works with no network |
| `src/lib/space.ts` | 3 KB | plain fetch against the FastAPI backend |
| `src/lib/supabase.ts` | 5 KB | anon key only; RLS does the isolation |
| `src/routes/Dashboard.tsx` | 4 KB | counts by lab, active standard |
| `src/routes/Evaluate.tsx` | 20 KB | data entry, tolerance envelope, verdicts |
| `src/routes/Repository.tsx` | 3 KB | search and open saved reports |
| `public/README.txt` | 0 KB |  |

## seed/

Reproducible synthetic dataset. A given seed always yields a byte-identical result.

| File | Size | What it is |
|---|---|---|
| `generate.py` | 17 KB | reproducible synthetic dataset generator |
| `out/evaluations.json` | 856 KB | 120 evaluations, 4 634 observations |
| `out/observations.csv` | 318 KB | the same observations, flat |

## scripts/

Extraction and pre-deployment checks.

| File | Size | What it is |
|---|---|---|
| `extract_all.sh` | 2 KB | re-run extraction from the source PDFs |
| `preflight.py` | 8 KB | checks credentials and connectivity before deploying |
| `slice_indian_schedule.py` | 1 KB | cuts the NAWI schedule out of the General Rules |
| `smoke_test.py` | 7 KB | end to end: Supabase → Space → rendered PDF |
| `validate_sql.py` | 1 KB | validates SQL with PostgreSQL's own parser |

## docs/

Technical documentation.

| File | Size | What it is |
|---|---|---|
| `CALCULATION_METHODOLOGY.md` | 9 KB | every formula and the clause it comes from |
| `EXTRACTION_MANIFEST.md` | 7 KB | every source document, what came out, where it went |

## extracted/

All 20 source documents as searchable text. Four were image-only scans, OCR'd with Tesseract. The original PDFs are not included; they are large and the text is what the project actually uses.

| File | Size | What it is |
|---|---|---|
| `IN_7th_schedule_A_NAWI.txt` | 615 KB | the Indian NAWI specification, 8 386 lines |
| `text/ASEAN-Guideline-for-NAWI.txt` | 61 KB |  |
| `text/The_Legal_Metrology_General_Rules_2011.txt` | 1.3 MB |  |
| `text/WELMEC_Guide_7.2_version_v2022.txt` | 385 KB |  |
| `text/approval_of_models_rules_2011.txt` | 31 KB |  |
| `text/d011-e13.txt` | 274 KB |  |
| `text/gatc_rules_2013_OCR.txt` | 35 KB |  |
| `text/lm_faq_OCR.txt` | 30 KB |  |
| `text/national_standards_rules_2011_OCR.txt` | 45 KB |  |
| `text/nmi_r_76-2_2015_-_test_report_format.txt` | 181 KB |  |
| `text/numeration_rules_2011_OCR.txt` | 9 KB |  |
| `text/r076-1-e06.txt` | 436 KB |  |
| `text/r076-2-e07.txt` | 179 KB |  |
| `text/r111-1-e04.txt` | 222 KB |  |
| `text/r76_1_1_1_cd_requirements.txt` | 370 KB |  |
| `text/r76_2_1_1_cd_test_procedures.txt` | 237 KB |  |
| `text/r76_3_1_1_cd_test_report_format.txt` | 135 KB |  |
| `text/r76_4_1_1_cd_type_evaluation_report_format.txt` | 107 KB |  |
| `text/r76_5_1_1_cd_verification_and_in_service_inspection.txt` | 36 KB |  |

---

## Verified before packaging

```
engine/test_harness.py   31 of 31 known-answer tests
scripts/validate_sql.py  7 SQL files, PostgreSQL's own parser
backend/test_local.py    2 reports, 40 KB DOCX + 205 KB PDF
seed/generate.py         sha256 a4e30ba61b9251b1, stable

On PostgreSQL 16:
  setup.sql    107 statements, no errors
  seed.sql     120 / 4634 / 120 / 30, identical across three runs,
               both as one transaction and statement-by-statement
  verify.sql   12 of 12 real checks ok
  security     13 of 13 break-ins refused

Against a running FastAPI server:
  GET  /health          200, libreoffice true, template true
  POST /render-payload  200, 205 KB PDF, 4 pages A4
  POST /render          401 for a technician (needs reviewer+)
  POST /verify          401 for an expired token

No credential anywhere: every secret is read from the environment,
and the only .env files are .env.example with blank values.
```
