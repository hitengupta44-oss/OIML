# PS 26035 — NAWI type-evaluation test report system

Software that records OIML R 76 type-evaluation observations, computes
permissible errors and compliance automatically, and generates standardized
test reports.

---

## Layout

```
standards/    the rules as versioned data — the heart of the project
engine/       nawi_engine.py + nawi-engine.ts, same JSON; recompute twins too
sources/      the published documents the code cites

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
python3 engine/test_harness.py                 # 31 known-answer, Ishida MS-5060S
python3 engine/test_nitp_b2.py                 # 22 known-answer, NMI worked example
python3 engine/test_parity.py                  # TS and Python agree, 665 verdicts
python3 scripts/check_workflow_consistency.py  # workflow.json == the database
python3 scripts/check_coverage.py              # metadata == what the code does
python3 scripts/validate_sql.py                # PostgreSQL's own parser
cd backend && python3 test_local.py            # renders real DOCX + PDF
cd frontend && npm run build && npm run check:secrets
```

Every one of those has been checked for teeth: a deliberate defect was
injected into each and confirmed to fail it. A check that has never failed has
not been tested.

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

Two independent anchors, both published by regulators.

**The Ishida MS-5060S**, NMI Certificate 6/4C/86, Max 60 kg, e = 0.02 kg. The
engine derives n = 3000, class III, Min = 0.4 kg, and mpe stepping
10 → 20 → 30 g at the correct loads.

**NITP 6.1 to 6.4 Appendix B.2**, a complete worked substitution-load test on a
class 3 weighbridge with every number printed. The engine reproduces all of it,
including the formulas `E = I + 0.5e − ΔL − L` and `L_sub = I_sub + 0.5e − E`
that the NMI states in the same form this project uses. See `sources/README.md`
for why its 10 t row settles the band-edge question.

Matching a regulator's published figures is the correctness argument. Matching
two regulators is a better one.

## Two engines, one answer

The browser decides verdicts so a technician inside a shielded chamber keeps
working with no network; the service decides what the signed PDF says. Reading
the same JSON keeps the *values* in one place, but it does not make two
implementations compute the same answer — and for a while they did not. The
browser folded its headline verdict from the weighing observations alone while
the service folded in all five tests, so 34 of the 120 seed evaluations showed
CONFORMS on screen and DOES NOT CONFORM in the report.

`engine/nawi-recompute.ts` is now the twin of `backend/recompute.py`, and
`engine/test_parity.py` runs both over 133 evaluations comparing 665 per-test
verdicts. Thirteen of those evaluations are constructed to sit exactly on each
shared limit, because the realistic seed data never approached some of them:
its creep deviations are either well under 0.25 e or over 0.9 e, so a creep
limit wrongly set anywhere between the two passed every seed case in both
languages. A limit is only tested by a value that straddles it.

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

## Scope, stated plainly

The report carries all 17 numbered sections of the R 76-2 form, read from
`standards/report-layout.json`. **Five of them are computed and tabulated**:
weighing performance, repeatability, eccentricity, time-dependence and
electrical disturbances. The other twelve print a line saying the test was not
carried out, and the summary table marks them NOT COVERED — so the scope of a
report is stated in the report rather than inferred from what is missing, and
the section numbering matches the published form.

**Three of the five can be entered through the app** (weighing, eccentricity,
repeatability). Creep and disturbances are computed and printed but arrive only
through the seed.

`scripts/check_coverage.py` fails the build if any of those counts drift from
what `test-catalogue.json` claims. The catalogue used to mark thirteen tests
"implemented" when five were.

## Still missing

**A filled-in RRSL test report with real observation numbers.** The Department
publishes certificates (the outcome) — three are in `sources/certificates/` —
but never test reports (the working). Mentor or manufacturer request only; one
PDF would confirm the template matches Indian practice exactly.

**Reachable from the database but not from the app:** attachment upload,
report version history, the audit log, creating an evaluation from scratch, and
correcting an observation. Each has its table, its RLS policies and in most
cases its trigger; none has a screen.

**The certificate of approval itself.** `approval_mark`, `certificate_no` and
`gazette_date` are in the schema, the nine Rule 11(1) fields are in
`workflow.json`, and three exemplars are now in `sources/`. Generating one from
an issued report is the natural next deliverable.
