# PS 26035 — NAWI type-evaluation test report system

Software that records OIML R 76 type-evaluation observations, computes
permissible errors and compliance automatically, and generates standardized
test reports.

---

## Layout

```
standards/    the rules as versioned data — the heart of the project
engine/       nawi_engine.py (backend) + nawi-engine.ts (frontend), same JSON

supabase/     STAGE 1 — database, auth, roles, row-level security
backend/      STAGE 2 — Hugging Face Space (Docker + FastAPI): DOCX/PDF + recompute
frontend/     STAGE 3 — Vercel (Vite + React PWA): the user interface

seed/         reproducible synthetic dataset
extracted/    all 20 source documents as searchable text
scripts/      extraction, SQL validation, preflight
docs/         extraction manifest, calculation methodology
```

## Deploy in this order

| Stage | Folder | Runbook | Why this order |
|---|---|---|---|
| 1 | `supabase/` | `supabase/README.md` | everything reads from it |
| 2 | `backend/` | `backend/README.md` | validates Supabase JWTs, writes to Storage |
| 3 | `frontend/` | `frontend/README.md` | needs both a database and a Space URL |

## Where each job runs

| Job | Where | Why there |
|---|---|---|
| mpe lookup, error computation, verdicts | Browser | must work inside a shielded EMC chamber with no network |
| Spec validation, load-point generation | Browser | same |
| Auth, data, attachments, search | Supabase | Postgres with RLS does this natively |
| DOCX and PDF rendering | Space | needs `docxtpl` and LibreOffice |
| Independent recompute | Space | re-derives every verdict from the raw readings |

---

## Quick start

```bash
cd engine && python3 test_harness.py      # 31 known-answer tests
cd ../seed && python3 generate.py --seed 26035 --evaluations 120
cd ../backend && python3 test_local.py    # renders real DOCX + PDF
python3 scripts/smoke_test.py             # end to end, once deployed
python3 scripts/validate_sql.py           # PostgreSQL's own parser
```

The seed is fixed: a given seed always produces a byte-identical dataset, so
regression tests and demos are reproducible rather than a coin flip.

---

## The one idea to keep

**The standard is data, not code.**

No band value, threshold or comparison operator appears in any `.py` or `.ts`
file. The engine resolves `ge`/`gt`/`le`/`lt` from JSON at runtime, and every
evaluation records the `standard_id` it was judged under.

Three things follow:

1. A 2019 report re-renders under 2019 rules, forever.
2. Supporting the OIML R 76 revision means adding a directory, not editing code.
3. The same JSON drives the Python service and the TypeScript browser build, so
   a value exists in exactly one place.

The revision drafts (R 76-1 through R 76-5 committee drafts) are already in
`extracted/text/`, ready to be transcribed into a second standard directory.

---

## Why the operators matter

R 76-1 Table 6 reads `0 ≤ m ≤ 500`, then `500 < m ≤ 2 000`. Strictly greater on
the lower bound of the second band.

For a class III instrument with e = 20 g, an error of 15 g at exactly 10.00 kg
**fails** (mpe is 0.5 e = 10 g), while the same 15 g at 10.02 kg **passes**
(mpe is 1.0 e = 20 g).

A spreadsheet written with `>= 500` silently inverts the first verdict — and
A.4.4.1 requires testing *at* the band edges. This is the defect the project
exists to eliminate, and `engine/test_harness.py` case 5 proves the engine gets
it right.

The Indian Kanoon text rendering of the Seventh Schedule corrupts this to
`5000 ≤ m ≤ 20000`, giving overlapping bands. All operators were therefore
transcribed from the OIML PDF, and that decision is recorded in the files.

---

## India vs OIML

The Indian Seventh Schedule, Heading A is a near-clone of R 76-1. Values are
identical. The visible difference is the class symbol:

| | OIML | India |
|---|---|---|
| Ordinary accuracy | `IIII` | `IV` |

Both symbols live in `accuracy-classes.json` and are selected by jurisdiction at
render time. `clause-map.csv` maps 35 topics across both documents plus the
R 76-2 section numbers, which drives the dual-compliance footer on the report.

---

## Known-answer validation

The engine is anchored to a real published approval: the Ishida MS-5060S, NMI
Certificate 6/4C/86, Max 60 kg, e = 0.02 kg. The engine derives n = 3000,
class III, Min = 0.4 kg, and mpe stepping 10 → 20 → 30 g at the correct loads.

Matching a regulator's published figures is the correctness argument.

---

## Synthetic data

No public source publishes filled-in RRSL test reports, so demo data has to be
manufactured. Rather than inventing errors directly, `seed/generate.py` gives
each instrument a hidden physical character (zero offset, span error,
hysteresis, creep, eccentricity, noise), computes the error that character
produces, then **inverts A.4.4.3** to recover the raw readings a technician
would have written down — so `ΔL` comes out as a whole number of e/10 weights,
exactly as recorded in practice.

Seven quality profiles: `compliant`, `marginal`, `band_edge`, `span_drift`,
`eccentric`, `creeping`, `emc_fail`. The `marginal` and `band_edge` cases are
the ones to demo.

Every row carries `is_synthetic: true` and a `generator_version`, and a database
constraint makes it impossible for such a row to carry a certificate number or
Gazette date.

---

## Data residency

Vercel, Supabase and Hugging Face all host outside India, and model approval
records are official Government of India records.

This stack is the **demo tier**. The **pilot tier** is the same code on-prem at
an RRSL or on NIC MeghRaj — Postgres, the reporter and the static build, behind
the laboratory's own network. Raising this before a judge does turns a weakness
into evidence of judgement.

---

## Still missing

A filled-in RRSL test report with real observation numbers. The Department
publishes certificates (the outcome), never test reports (the working). Mentor
or manufacturer request only — one PDF would confirm the template matches
Indian practice exactly.
