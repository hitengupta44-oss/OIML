# Stage 3 — Vercel frontend

Vite + React PWA. Talks to Supabase for data and auth, and to the FastAPI backend on a
Hugging Face Docker Space for rendering and independent verification.

```
frontend/
  src/lib/supabase.ts   anon key only; RLS does the isolation
  src/lib/space.ts      plain fetch against the FastAPI backend
  src/lib/offline.ts    IndexedDB queue — capture works with no network
  src/routes/           Evaluate · Repository · Dashboard
  src/App.tsx           shell, auth, connectivity banner
  vite.config.ts        PWA, engine and standards aliases
```

The engine is imported from `../engine/nawi-engine.ts` and the rules from
`../standards/*.json` through Vite aliases — **the same JSON the Python service
reads**. A band value exists in exactly one place.

---

## 1 · Local run

```bash
cd frontend
cp .env.example .env.local     # fill in the three values
npm install
npm run dev
```

`.env.local`:

```
VITE_SUPABASE_URL=https://xxxx.supabase.co
VITE_SUPABASE_ANON_KEY=eyJ...
VITE_SPACE_URL=https://your-user-nawi-reporter.hf.space
```

Sign in with any account `create_users.py` made.

## 2 · Icons

Add `icon-192.png` and `icon-512.png` to `frontend/public/`. The PWA manifest
references them; without them the install prompt is skipped.

## 3 · Deploy

`vercel.com` → **New Project** → import the repository.

| Setting | Value |
|---|---|
| Root directory | `frontend` |
| Framework | Vite |
| Build command | `npm run build` |
| Output directory | `dist` |

Environment variables: the same three as `.env.local`. **Only the anon key.**

## 4 · Check the bundle before every deploy

```bash
npm run build && npm run check:secrets
```

That greps `dist/` for `service_role`. The service-role key bypasses RLS
entirely; if it ever lands in a bundle, every policy in Stage 1 becomes
decorative because anyone can read the built JavaScript.

Note that `VITE_` variables are compiled into the browser bundle by design —
they are public. That is fine for the URL and the anon key, and exactly why the
service key must never be one.

---

## Why offline is not optional

EMC and climatic testing happens inside shielded chambers — a Faraday cage has
no Wi-Fi by design. Temperature runs take hours, damp heat days, span stability
28 days, and weighbridges are outdoors.

Software that needs connectivity to record a reading is software the laboratory
will work around.

So observations are written to IndexedDB first and always, and the engine runs
locally, so verdicts appear with no network. Sync is background reconciliation,
not a precondition for doing the work. On submission the Space recomputes from
the raw readings and reports any divergence, which is shown prominently rather
than logged.

---

## Design notes

The **tolerance envelope** is the hero: the stepped funnel the mpe traces
across the weighing range, with each measured error inside it or outside it. It
updates as you type.

Mono is reserved for measured values so report columns align digit-for-digit.
Verdicts never rely on colour alone — every PASS/FAIL carries the word too, and
increasing loads are circles while decreasing are squares.

Test loads are **derived, not typed**. Change the class or Max and the list
rebuilds with the new band edges included, which is exactly what a hand-built
spreadsheet forgets.

---

## Not built yet

- The remaining 13 tests from `test-catalogue.json` (weighing, repeatability,
  eccentricity, creep and disturbances are wired)
- Attachment upload to the typed slots in `workflow.json`
- The status-transition UI — the database enforces the state machine already
- Client-side PDF fallback for when the Space is asleep
