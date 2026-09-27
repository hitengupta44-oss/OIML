---
title: NAWI Reporter
emoji: ⚖️
colorFrom: blue
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
---

# Stage 2 — Hugging Face Docker Space

A FastAPI service that renders OIML R 76 type-evaluation reports and
independently verifies them.

```
backend/
  Dockerfile          python:3.11-slim + LibreOffice
  app.py              FastAPI — four endpoints
  auth.py             Supabase JWT validation
  recompute.py        re-derives every verdict from the raw readings
  render.py           DOCX via docxtpl, PDF via LibreOffice
  supabase_io.py      reads evaluations, writes reports to Storage
  paths.py            finds standards/ and engine/ in either layout
  build_template.py   regenerates templates/r76-2.docx
  test_local.py       renders real reports with no cloud at all
  templates/r76-2.docx
  requirements.txt
```

## What this does, and what it deliberately does not

Two jobs the browser genuinely cannot do:

1. **`POST /render`** — DOCX needs `docxtpl`, PDF needs LibreOffice.
2. **`POST /verify`** — re-derives every verdict from `I`, `ΔL`, `L` and `E₀`
   alone, using the same engine and the same standards JSON as the client, and
   reports any divergence.

Everything else — mpe lookup, error computation, load-point generation — runs
in the browser, because it has to keep working inside a shielded EMC chamber
with no network.

---

## 1 · Create the Space

`huggingface.co/new-space`

| Field | Value |
|---|---|
| Space name | `nawi-reporter` |
| SDK | **Docker** → **Blank** |
| Hardware | CPU basic · free |
| Visibility | either; endpoints validate tokens regardless |

## 2 · Upload

Everything in `backend/`, plus two directories from the project root, because
the engine reads them at runtime:

```
Dockerfile  app.py  auth.py  paths.py  recompute.py  render.py
supabase_io.py  build_template.py  requirements.txt  README.md
templates/r76-2.docx
standards/*.json        ← from the project root
engine/nawi_engine.py   ← from the project root
```

`paths.py` resolves `standards/` and `engine/` whether they sit beside the
backend (the repository) or inside it (the Space), so nothing needs editing.

## 3 · Secrets

**Settings → Repository secrets**, *not* Variables. Variables are visible to
anyone who can view the Space.

| Secret | Purpose |
|---|---|
| `SUPABASE_JWT_SECRET` | validates access tokens |
| `SUPABASE_URL` | project URL |
| `SUPABASE_SERVICE_KEY` | bypasses RLS; must never reach a browser |
| `ALLOWED_ORIGINS` | your Vercel URL, e.g. `https://nawi.vercel.app` |

`ALLOWED_ORIGINS` defaults to `*`. Set it once the frontend is deployed — a
Space URL is public, and an open CORS policy lets any page on the internet
spend this container's CPU.

## 4 · Wait for the build

**The first build takes 5–10 minutes** because the Dockerfile installs
LibreOffice. Later builds reuse that layer and are much faster.

## 5 · Check it

Open `https://<user>-nawi-reporter.hf.space/health`:

```json
{
  "ok": true,
  "libreoffice": true,
  "template": true,
  "supabase_configured": true,
  "jwt_configured": true
}
```

All five true means it is ready. Interactive docs are at `/docs`.

---

## Endpoints

| Method | Path | Roles | Returns |
|---|---|---|---|
| `GET` | `/health` | public | service status |
| `POST` | `/verify` | any signed-in role | verdicts, divergences, payload digest |
| `POST` | `/render` | reviewer, approver, director, admin | signed DOCX and PDF URLs |
| `POST` | `/render-payload` | technician and above | the PDF itself, for unsynced work |

Plain REST — no SDK, no queue protocol:

```ts
const res = await fetch(`${SPACE_URL}/render`, {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    Authorization: `Bearer ${jwt}`,
  },
  body: JSON.stringify({ ref: "RRSL-FBD/NAWI/2026/0042" }),
});
```

---

## Things that will bite you

**The Space URL is public.** Every endpoint validates the JWT before doing any
work. Without that, anyone can burn the CPU quota and render other
laboratories' reports.

**Cold starts.** Free CPU Spaces sleep after about 48 hours idle and take
30–60 s to wake. Run a cron against `/health` every 6 hours, and open the Space
ten minutes before any demo. Never let a user trigger the first request.

**Spaces run as uid 1000, not root.** The Dockerfile creates that user and
gives it `/tmp/reports`. Writing anywhere else at runtime fails with a
permission error.

**LibreOffice and concurrency.** It refuses to run two instances against one
user profile — exactly what concurrent requests do. `render.py` gives each
conversion a throwaway profile directory.

**Fonts matter.** `fonts-dejavu` and `fonts-liberation` are installed
deliberately. Without them LibreOffice substitutes and the report's table
columns shift.

**`/render` refuses to render over a divergence.** If the server's verdicts
disagree with the client's, it returns `409` with the divergences instead of a
document. Producing a signed report over numbers the two sides disagree on
would be the worst thing this system could do.

**Tables are not Jinja.** Two approaches were tried and rejected: `{%tr%}` row
loops get mangled because Word splits text across runs, and docxtpl
subdocuments inline their XML inside a `<w:t>`, producing nested paragraphs
that **LibreOffice silently drops** — the tables simply vanish from the PDF
with no error anywhere. So the template carries `[[TABLE:x]]` markers and
`render.py` swaps them for real tables after the Jinja pass. `render_docx`
raises if the marker count and the spec count disagree, so template drift fails
loudly instead of shipping a report with missing sections.

---

## Local run

```bash
pip install -r requirements.txt
python3 build_template.py     # regenerate templates/r76-2.docx
python3 test_local.py         # render real reports from the seed data
uvicorn app:app --port 7860   # http://localhost:7860/docs
```

Or with Docker, which is what the Space actually runs:

```bash
docker build -t nawi-reporter .
docker run -p 7860:7860 \
  -e SUPABASE_JWT_SECRET=... \
  -e SUPABASE_URL=... \
  -e SUPABASE_SERVICE_KEY=... \
  nawi-reporter
```

### Verified

```
GET  /health          200, libreoffice true, template true
POST /render-payload  200, 205 KB PDF, 4 pages A4
POST /render          401 for a technician (needs reviewer+)
POST /verify          401 for an expired token
```

---

Next: `frontend/README.md`.
