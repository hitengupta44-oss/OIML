# Stage 1 — Supabase

Data, auth and roles. Everything downstream reads from here, so finish this
before touching the backend or the frontend.

```
supabase/
  RESET.sql          wipe the schema, keep the project   (only if reinstalling)
  setup.sql          the whole schema in one paste
  verify.sql         15 checks, one query
  seed/seed.sql      120 evaluations, 4 634 observations
  seed/create_users.py   12 demo accounts
  tests/rls_test.sql 12 break-in attempts, all must be refused
  migrations/        the same schema as three ordered files, for the CLI
```

---

## 1 · Create the project

`supabase.com/dashboard` → **New project**

- Name: `nawi-type-evaluation`
- Database password: generate one and **save it** — it can only be reset, not
  retrieved
- Region: Mumbai or Singapore

## 2 · Collect four values

**Settings → API**

| Value | Goes to | Never |
|---|---|---|
| Project URL | backend + frontend | — |
| `anon` `public` key | frontend only | — |
| `service_role` key | **backend secrets only** | any browser bundle |
| JWT Secret (JWT Settings) | backend secrets | anywhere else |

The `service_role` key bypasses row-level security completely. If it reaches a
frontend bundle, every policy below becomes decorative.

## 3 · Reinstalling over an existing schema?

Run **`RESET.sql`** first. It drops `public` and `app` and restores the grants
Supabase sets on a fresh project. Supabase's own machinery lives in `auth`,
`storage`, `realtime` and `extensions` and is untouched, so accounts under
Authentication survive. Ends with a table list that must be empty.

On a brand-new project, skip it.

## 4 · Schema

**SQL Editor → New query** → paste **`setup.sql`** → **Run**.

107 statements. `Success. No rows returned` is the correct output — it is all
DDL.

## 5 · Verify

Paste **`verify.sql`** → **Run**. One query, 15 rows.

Rows 1–11 and 13 must read `ok`. Rows 12, 14 and 15 report no data and no
accounts, which is right until the next two steps.

## 6 · Seed

Paste **`seed/seed.sql`** → **Run**. 724 KB, give it a few seconds.

Expect **120 / 4634 / 120 / 30**. The final query must return **zero rows** —
no synthetic record may carry a certificate number or Gazette date.

Re-running is harmless: all 242 inserts carry a bare `on conflict do nothing`,
which binds to whatever unique index exists rather than naming one.

## 7 · Accounts

```bat
set SUPABASE_URL=https://xxxx.supabase.co
set SUPABASE_SERVICE_KEY=eyJ...
set DEMO_PASSWORD=pick-your-own

python supabase\seed\create_users.py
```

No quotes around the values — `cmd` includes them literally.

12 accounts: technicians, reviewers and lab heads across three RRSLs, plus a
director, an admin and an auditor.

Roles are written to **`app_metadata`**, never `user_metadata`. A user can edit
`user_metadata` through the ordinary client API, so a role living there would
be self-issued and every policy below would be meaningless.

## 8 · Buckets

**Storage → New bucket**, twice, both **Private**:

- `attachments` — drawings, sealing diagrams, undertakings, photographs
- `reports` — rendered DOCX and PDF

Private means signed URLs with an expiry. A public `reports` bucket would make
every test report world-readable by URL.

## 9 · Prove the rules hold

```bash
psql "$SUPABASE_DB_URL" -f supabase/tests/rls_test.sql
```

Connection string: **Settings → Database → Connection string → URI**, the
direct one, not the pooled 6543 port. It also runs in the SQL Editor, but psql
shows each refusal as it fires.

Twelve deliberate break-in attempts, all of which must be refused:

1. Faridabad technician reading Mumbai evaluations
2. technician approving their own work
3. technician issuing a certificate
4. draft jumping straight to issued
5. editing an issued evaluation
6. changing the standard under a report
7. overwriting a recorded indication
8. deleting an observation
9. recording against an issued evaluation
10. forging an audit entry
11. erasing audit history
12. technician promoting themselves to director

This is what to run live if someone asks whether the role-based permissions are
real. Most projects can only point at hidden buttons.

---

## Design decisions worth defending

**Observations are append-only.** No delete policy, and no update path that
touches a reading. A correction inserts a new row and points the old one at it
through `superseded_by`. The original stays legible forever, which is the
difference between a legal record and a spreadsheet.

**`standard_id` is immutable per evaluation.** A trigger rejects any change.
This is what lets a 2019 report re-render under 2019 rules a decade later, and
why supporting a revised R 76 means adding a row to `standard_version` rather
than editing code.

**An issued evaluation is frozen.** Corrections require a formal amendment,
which creates a new `report_version`. Rule 11(2) makes a certificate effective
from its Gazette publication; silently editing the record behind it would be
indefensible.

**The audit log has no write policy at all.** Rows arrive only through a
`security definer` trigger, so no client session can forge or erase history.
`audit_log.actor` is deliberately not a foreign key: an audit trail must never
be able to block the work it records, and it must outlive the accounts it
names.

**Synthetic data flows through the whole workflow but can never carry a
certificate.** A demo dashboard with no issued reports is useless, so the
constraint targets `certificate_no` and `gazette_date` rather than the workflow
status. Status is process; a certificate number is law.

---

## Things that will bite you

**`NULL ≠ NULL`.** `manufacturer` originally had `unique (name, city)` while
the seed inserts `city` as NULL — and a unique constraint never fires on a row
where any column is null. Three seed runs produced three copies of everything.
It is now a unique index over `coalesce(city, '')`, which turns "no city" into
a real value.

**RLS and GRANT are two different gates and both must be open.** A policy
decides which rows a role may touch; a grant decides whether it may touch the
table at all. Supabase issues table grants at project creation for tables that
already exist, but a custom `app` schema needs `grant usage` said explicitly or
every policy fails with *"permission denied for schema app"*.

**`v_*` views set `security_invoker = true`.** Without it a view runs with the
definer's rights and silently bypasses every policy on its base tables. The
easiest RLS mistake in Postgres to make and the hardest to notice.

**Seeding disables two triggers.** The guards refuse exactly what a bulk load
does: writing observations into non-draft evaluations and setting status
without a JWT role. `seed.sql` disables them inside its transaction and
re-enables them before commit. Check the last lines of the file.

**`n` is a generated column.** `n = Max/e` is computed by the database, so it
can never drift from its inputs. Do not try to insert it.

---

## Verified on PostgreSQL 16

```
RESET.sql       13 tables -> 0
setup.sql       107 statements, no errors
seed.sql run 1  120 / 4634 / 120 / 30
seed.sql run 2  120 / 4634 / 120 / 30
seed.sql run 3  120 / 4634 / 120 / 30
verify.sql      12 of 12 real checks ok
rls_test.sql    12 of 12 break-ins refused
```

---

Next: `backend/README.md`.
