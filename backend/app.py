"""
NAWI reporter -- FastAPI service.

Runs as a Docker Space on Hugging Face, or anywhere else a container runs.

Two jobs the browser genuinely cannot do:

  1. POST /render    DOCX needs docxtpl; PDF needs LibreOffice.
  2. POST /verify    Independently re-derive every verdict from the raw
                     readings, so a submission is never accepted on the
                     client's word alone.

Everything else -- mpe lookup, error computation, load-point generation --
runs in the browser, because it must keep working inside a shielded EMC
chamber with no network.

Calling it
----------
Plain REST. No queue protocol, no SDK:

    const r = await fetch(`${SPACE_URL}/render`, {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 Authorization: `Bearer ${jwt}` },
      body: JSON.stringify({ ref: "RRSL-FBD/NAWI/2026/0042" }),
    });

Secrets (Space Settings -> Repository secrets, never Variables):
    SUPABASE_JWT_SECRET    validates access tokens
    SUPABASE_URL
    SUPABASE_SERVICE_KEY   bypasses RLS; never ship this to a browser
"""

import json
import os
import tempfile
import traceback
from typing import Any, Dict, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import auth
import recompute as rc
import render as rnd
import supabase_io as db

VERSION = "1.2.0"
OUT_DIR = os.environ.get("REPORT_OUT_DIR", tempfile.gettempdir())

# Lock CORS to the frontend. A Space URL is public, so an open policy
# would let any page on the internet spend this container's CPU.
ALLOWED = [o.strip() for o in
           os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()]

app = FastAPI(
    title="NAWI reporter",
    version=VERSION,
    description="OIML R 76 type-evaluation report rendering and verification",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


# ---------------------------------------------------------------------------
# request models
# ---------------------------------------------------------------------------

class RefRequest(BaseModel):
    ref: str


class PayloadRequest(BaseModel):
    evaluation: Dict[str, Any]


def _caller(authorization: Optional[str], *roles: str) -> auth.Caller:
    """Validate the bearer token, or refuse before doing any work."""
    if not authorization:
        raise HTTPException(401, "missing Authorization header")
    try:
        return auth.require(authorization, *roles)
    except auth.AuthError as exc:
        raise HTTPException(401, str(exc))


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    """Landing page, so opening the Space in a browser explains itself."""
    return {
        "service": "nawi-reporter",
        "version": VERSION,
        "docs": "/docs",
        "endpoints": {
            "GET  /health": "public — service status",
            "POST /verify": "re-derive every verdict from the raw readings",
            "POST /render": "render DOCX + PDF, store them, return signed URLs",
            "POST /render-payload": "render straight from a posted evaluation",
        },
    }


@app.get("/health")
def health():
    """Public. Also what the keep-warm cron hits.

    A free CPU Space sleeps after about 48 hours idle and takes 30 to 60
    seconds to wake. Never let a user trigger the first request.
    """
    return {
        "ok": True,
        "service": "nawi-reporter",
        "version": VERSION,
        "libreoffice": bool(rnd._soffice()),
        "template": os.path.exists(rnd.TEMPLATE),
        "supabase_configured": db.configured(),
        "jwt_configured": auth.configured() != "none",
        "jwt_modes": auth.configured(),
    }


@app.post("/verify")
def verify(body: RefRequest, authorization: str = Header(None)):
    """Re-derive every verdict from the raw observations.

    The browser already computed these while the technician typed. This is
    the authoritative pass: same engine, same standards JSON, but starting
    from I, dL, L and E0 alone. A divergence means a stale client, a
    tampered payload, or a bug -- any of which should stop a submission.
    """
    caller = _caller(authorization, "technician", "reviewer", "approver",
                     "director", "admin", "auditor")
    try:
        ev = db.fetch_evaluation(body.ref, caller)
        computed = rc.recompute(ev)
        payload = rc.canonical_payload(ev, computed)
        return {
            "ok": True,
            "ref": ev["ref"],
            "standard_id": ev.get("standard_id"),
            "verdict": computed["verdict"],
            "agrees_with_client": computed["agrees_with_client"],
            "divergences": computed["divergences"],
            "specification": computed["specification"],
            "derived": computed["derived"],
            "tests": {k: {"verdict": v["verdict"],
                          "rows": len(v.get("rows") or v.get("series") or [])}
                      for k, v in computed["tests"].items()},
            "payload_sha256": rc.payload_sha256(payload),
            "checked_by": {"user_id": caller.user_id, "role": caller.role},
        }
    except db.SupabaseError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:                                  # noqa: BLE001
        traceback.print_exc()
        raise HTTPException(500, f"{type(exc).__name__}: {exc}")


@app.post("/render")
def render(body: RefRequest, authorization: str = Header(None)):
    """Render DOCX and PDF, store them, return signed links."""
    caller = _caller(authorization, "reviewer", "approver", "director", "admin")
    try:
        ev = db.fetch_evaluation(body.ref, caller)
        computed = rc.recompute(ev)
        payload = rc.canonical_payload(ev, computed)
        sha = rc.payload_sha256(payload)

        # Refuse to render a report whose numbers the client and server do
        # not agree on. Producing a signed document over a divergence would
        # be the single worst failure mode this system has.
        if computed["divergences"]:
            return JSONResponse(
                status_code=409,
                content={"ok": False, "kind": "divergence",
                         "error": "client and server verdicts diverge; not rendering",
                         "divergences": computed["divergences"]},
            )

        docx_path, pdf_path = rnd.render(ev, computed, sha, OUT_DIR)

        version = db.next_version(ev["id"])
        docx_store = db.upload_report(
            docx_path, ev["ref"], version,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        pdf_store = db.upload_report(pdf_path, ev["ref"], version,
                                     "application/pdf") if pdf_path else None

        db.record_report_version(ev["id"], version, ev.get("standard_id"),
                                 payload, sha, computed["verdict"],
                                 docx_store, pdf_store, caller)

        return {
            "ok": True,
            "ref": ev["ref"],
            "version": version,
            "verdict": computed["verdict"],
            "payload_sha256": sha,
            "is_synthetic": bool(ev.get("is_synthetic")),
            "docx_url": db.signed_url(docx_store),
            "pdf_url": db.signed_url(pdf_store) if pdf_store else None,
            "pdf_available": bool(pdf_store),
            "note": None if pdf_store else
                    "LibreOffice unavailable; DOCX only. Use the browser "
                    "print fallback for a PDF.",
        }
    except db.SupabaseError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:                                  # noqa: BLE001
        traceback.print_exc()
        raise HTTPException(500, f"{type(exc).__name__}: {exc}")


@app.post("/render-payload")
def render_payload(body: PayloadRequest, authorization: str = Header(None)):
    """Render straight from a posted evaluation, without touching Supabase.

    Used for offline-captured work that has not synced yet, and for local
    development before the database exists. Returns the PDF as a file.
    """
    caller = _caller(authorization, "technician", "reviewer", "approver",
                     "director", "admin")
    ev = body.evaluation
    if not ev.get("ref") or not ev.get("instrument"):
        raise HTTPException(422, "evaluation needs at least 'ref' and 'instrument'")
    try:
        computed = rc.recompute(ev)
        sha = rc.payload_sha256(rc.canonical_payload(ev, computed))
        docx_path, pdf_path = rnd.render(ev, computed, sha, OUT_DIR)
        out = pdf_path or docx_path
        return FileResponse(
            out, filename=os.path.basename(out),
            media_type="application/pdf" if pdf_path else
                       "application/vnd.openxmlformats-officedocument."
                       "wordprocessingml.document",
            headers={"X-Verdict": computed["verdict"],
                     "X-Payload-SHA256": sha,
                     "X-Rendered-By": caller.role},
        )
    except Exception as exc:                                  # noqa: BLE001
        traceback.print_exc()
        raise HTTPException(500, f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 7860)))
