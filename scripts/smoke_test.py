"""
End-to-end smoke test: Supabase -> Space -> rendered report.

    set SUPABASE_URL=https://xxxx.supabase.co
    set SUPABASE_ANON_KEY=eyJ...
    set SPACE_URL=https://your-user-nawi-reporter.hf.space
    set DEMO_PASSWORD=your-password

    python scripts\smoke_test.py

Walks the whole path a real user takes:

  1. GET  /health              is the service awake and configured
  2. sign in to Supabase       as an approver, to get a real JWT
  3. POST /verify              server re-derives every verdict
  4. POST /render              DOCX + PDF into Storage, signed URLs back
  5. role check                a technician must be refused by /render

Needs only the ANON key, never the service key -- this is exactly what the
frontend will do.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SPACE_URL = os.environ.get("SPACE_URL", "").rstrip("/")
PASSWORD = os.environ.get("DEMO_PASSWORD", "Nawi#26035demo")

APPROVER = "head.fbd@example.gov.in"
TECHNICIAN = "tech1.fbd@example.gov.in"

problems = []


def line(state, label, detail=""):
    print(f"  [{state}] {label}" + (f" — {detail}" if detail else ""))
    if state == "FAIL":
        problems.append(label)


def http(url, method="GET", body=None, headers=None, timeout=180):
    req = urllib.request.Request(
        url, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            try:
                return r.status, json.loads(raw)
            except json.JSONDecodeError:
                return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw.decode(errors="replace")


def sign_in(email):
    status, body = http(
        f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
        "POST", {"email": email, "password": PASSWORD},
        {"apikey": ANON_KEY},
    )
    if status != 200:
        return None, f"{status}: {str(body)[:120]}"
    return body.get("access_token"), None


def main():
    for name, val in (("SUPABASE_URL", SUPABASE_URL),
                      ("SUPABASE_ANON_KEY", ANON_KEY),
                      ("SPACE_URL", SPACE_URL)):
        if not val:
            sys.exit(f"set {name} first")

    print("NAWI end-to-end smoke test\n")

    # -- 1 · health ------------------------------------------------------
    print("1 · service health")
    t0 = time.time()
    status, health = http(f"{SPACE_URL}/health", timeout=180)
    took = time.time() - t0
    if status != 200:
        line("FAIL", "GET /health", f"HTTP {status}")
        sys.exit(1)
    if took > 20:
        line("warn", "cold start", f"{took:.0f}s — the Space was asleep")
    for key in ("libreoffice", "template", "supabase_configured", "jwt_configured"):
        line("ok  " if health.get(key) else "FAIL", key, str(health.get(key)))

    # -- 2 · sign in -----------------------------------------------------
    print("\n2 · authentication")
    token, err = sign_in(APPROVER)
    if not token:
        line("FAIL", f"sign in as {APPROVER}", err)
        sys.exit(1)
    line("ok  ", "signed in as approver", APPROVER)

    # -- 3 · pick an evaluation ------------------------------------------
    print("\n3 · pick an evaluation")
    status, rows = http(
        f"{SUPABASE_URL}/rest/v1/evaluation"
        f"?select=ref,status,verdict&status=eq.draft&order=ref&limit=1",
        headers={"apikey": ANON_KEY, "Authorization": f"Bearer {token}"},
    )
    if status != 200 or not rows:
        line("FAIL", "read an evaluation", f"HTTP {status}: {str(rows)[:120]}")
        sys.exit(1)
    ref = rows[0]["ref"]
    line("ok  ", "found", f"{ref} ({rows[0]['status']}, {rows[0]['verdict']})")

    auth = {"Authorization": f"Bearer {token}"}

    # -- 4 · verify ------------------------------------------------------
    print("\n4 · server-side recompute")
    status, res = http(f"{SPACE_URL}/verify", "POST", {"ref": ref}, auth)
    if status != 200:
        line("FAIL", "POST /verify", f"HTTP {status}: {str(res)[:160]}")
        sys.exit(1)
    line("ok  ", "verdict", res.get("verdict"))
    line("ok  " if res.get("agrees_with_client") else "warn",
         "agrees with client", str(res.get("agrees_with_client")))
    line("ok  ", "payload digest", (res.get("payload_sha256") or "")[:16])
    for code, t in (res.get("tests") or {}).items():
        line("ok  ", f"  {code}", f"{t['verdict']} ({t['rows']} rows)")

    # -- 5 · render ------------------------------------------------------
    print("\n5 · render")
    status, out = http(f"{SPACE_URL}/render", "POST", {"ref": ref}, auth)
    if status == 409:
        line("FAIL", "POST /render", "refused: client and server verdicts diverge")
        print("       ", json.dumps(out.get("divergences", [])[:2], indent=2))
        sys.exit(1)
    if status != 200:
        line("FAIL", "POST /render", f"HTTP {status}: {str(out)[:200]}")
        sys.exit(1)
    line("ok  ", "report version", f"v{out.get('version')}")
    line("ok  ", "verdict", out.get("verdict"))
    line("ok  " if out.get("pdf_available") else "warn", "pdf",
         "rendered" if out.get("pdf_available") else out.get("note", "not available"))
    if out.get("docx_url"):
        line("ok  ", "docx url", out["docx_url"][:72] + "…")
    if out.get("pdf_url"):
        line("ok  ", "pdf url", out["pdf_url"][:72] + "…")

    # -- 6 · the signed URL actually serves the file ---------------------
    if out.get("pdf_url"):
        print("\n6 · fetch the stored PDF")
        try:
            with urllib.request.urlopen(out["pdf_url"], timeout=60) as r:
                blob = r.read()
            ok = blob[:4] == b"%PDF"
            line("ok  " if ok else "FAIL", "downloaded",
                 f"{len(blob)/1024:.0f} KB, starts with %PDF: {ok}")
        except Exception as exc:                              # noqa: BLE001
            line("FAIL", "download", str(exc)[:120])

    # -- 7 · role enforcement --------------------------------------------
    print("\n7 · role enforcement")
    tech_token, err = sign_in(TECHNICIAN)
    if not tech_token:
        line("warn", "sign in as technician", err)
    else:
        status, res = http(f"{SPACE_URL}/render", "POST", {"ref": ref},
                           {"Authorization": f"Bearer {tech_token}"})
        line("ok  " if status == 401 else "FAIL",
             "technician refused by /render",
             f"HTTP {status}" + ("" if status == 401 else " — SHOULD BE 401"))

    status, _ = http(f"{SPACE_URL}/render", "POST", {"ref": ref})
    line("ok  " if status == 401 else "FAIL",
         "no token refused", f"HTTP {status}")

    # --------------------------------------------------------------------
    print()
    if problems:
        print(f"{len(problems)} problem(s):")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)
    print("end to end: Supabase -> Space -> rendered report, all green")


if __name__ == "__main__":
    main()
