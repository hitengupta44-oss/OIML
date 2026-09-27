# Deployment

Three stages, in order. Each is testable before the next exists.

| Stage | Folder | Host | Runbook |
|---|---|---|---|
| 1 | `supabase/` | Supabase | `supabase/README.md` |
| 2 | `backend/` | Hugging Face **Docker** Space | `backend/README.md` |
| 3 | `frontend/` | Vercel | `frontend/README.md` |

## Why this order

The Space validates Supabase JWTs, reads evaluations with the service-role key
and writes rendered files into Supabase Storage — so the schema has to exist
first. The frontend needs both a database to read and a Space URL to call.

## Secrets, and where each one goes

| Value | Supabase | Backend | Frontend |
|---|---|---|---|
| Project URL | — | `SUPABASE_URL` | `VITE_SUPABASE_URL` |
| **anon** key | — | — | `VITE_SUPABASE_ANON_KEY` |
| **service_role** key | — | `SUPABASE_SERVICE_KEY` | never |
| JWT secret | — | `SUPABASE_JWT_SECRET` | never |
| Space URL | — | — | `VITE_SPACE_URL` |
| Frontend origin | — | `ALLOWED_ORIGINS` | — |

Nothing in this repository contains a credential. Every secret is read from the
environment, and the only `.env` files present are `.env.example` with blank
values.

- **Hugging Face:** Settings → **Repository secrets**, not Variables.
  Variables are visible to anyone who can view the Space.
- **Vercel:** Settings → Environment Variables. `VITE_` variables are compiled
  into the browser bundle by design — fine for the URL and anon key, and
  exactly why the service key must never be one.

## Checks at each stage

```bash
# Stage 1, in the SQL Editor
verify.sql          15 checks, one query
security_test.sql   13 break-in attempts, all must be refused

# Stage 2, once the Space is up
curl https://<user>-<space>.hf.space/health      all four true
python3 scripts/smoke_test.py                    Supabase -> Space -> PDF

# Stage 3, before every deploy
cd frontend && npm run build && npm run check:secrets
```

## Before you present

- [ ] Space woken — `/health` returns 200 (it sleeps after ~48 h idle, 30–60 s to wake)
- [ ] Seed loaded — dashboard shows 120 evaluations
- [ ] `RRSL-JAI/NAWI/2026/0004` bookmarked — the `band_edge` case that fails at exactly 500 e
- [ ] `npm run check:secrets` clean — no `service_role` in the bundle
- [ ] Offline capture tested with the network off
- [ ] `security_test.sql` run — all thirteen attempts refused

## Verified

```
engine/test_harness.py   31 of 31 known-answer tests
scripts/validate_sql.py  7 files, PostgreSQL's own parser
backend/test_local.py    2 reports, 40 KB DOCX + 205 KB PDF

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

Backend runs in both layouts:
  repository (backend/ beside standards/)  ok
  Hugging Face Space (everything flat)     ok
```

## Data residency

Vercel, Supabase and Hugging Face all host outside India, and model approval
records are official Government of India records.

This stack is the **demo tier**. The **pilot tier** is the same code on-prem at
an RRSL or on NIC MeghRaj — Postgres, the reporter container and the static
build, behind the laboratory's own network. The backend is already a
Dockerfile, so that move is a compose file rather than a rewrite.

Raising this before a judge does turns a weakness into evidence of judgement.
