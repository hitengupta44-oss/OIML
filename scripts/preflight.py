"""
Preflight: check the credentials and the environment before deploying.

    python3 scripts/preflight.py

Every failure here is one you would otherwise hit halfway through a stage,
with a less obvious error message. Runs against whatever is set; skips what
is not, so it is useful at every point in the three-stage build.

    Stage 1   SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_KEY
    Stage 2   + SUPABASE_JWT_SECRET
    Stage 3   + VITE_SPACE_URL
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.request

OK, WARN, BAD = "ok  ", "warn", "FAIL"
problems: list[str] = []


def line(state: str, label: str, detail: str = "") -> None:
    print(f"  [{state}] {label}" + (f" — {detail}" if detail else ""))
    if state == BAD:
        problems.append(label)


def section(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def jwt_claims(token: str) -> dict:
    """Decode without verifying — we only want to read the role claim."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def get(url: str, headers: dict, timeout: int = 15):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


# ---------------------------------------------------------------------------

def check_local() -> None:
    section("Local toolchain")

    if sys.version_info >= (3, 9):
        line(OK, "python", f"{sys.version_info.major}.{sys.version_info.minor}")
    else:
        line(BAD, "python", "3.9 or newer required")

    for mod, why in (("docx", "DOCX rendering"),
                     ("docxtpl", "template fill"),
                     ("jwt", "token validation")):
        try:
            __import__(mod)
            line(OK, mod, why)
        except ImportError:
            line(WARN, mod, f"missing — pip install -r backend/requirements.txt ({why})")

    import shutil
    if shutil.which("soffice") or shutil.which("libreoffice"):
        line(OK, "libreoffice", "PDF conversion available locally")
    else:
        line(WARN, "libreoffice", "absent locally; the Space installs it via packages.txt")

    root = os.path.join(os.path.dirname(__file__), "..")
    for path, why in (
        ("standards/mpe-bands.json", "permissible error bands"),
        ("standards/accuracy-classes.json", "class definitions"),
        ("engine/nawi_engine.py", "server engine"),
        ("engine/nawi-engine.ts", "browser engine"),
        ("backend/templates/r76-2.docx", "report template"),
        ("supabase/setup.sql", "schema"),
        ("seed/out/evaluations.json", "seed dataset"),
    ):
        full = os.path.join(root, path)
        if os.path.exists(full):
            line(OK, path, why)
        elif path.startswith("seed/out"):
            line(WARN, path, "run: python3 seed/generate.py --seed 26035")
        elif path.endswith(".docx"):
            line(WARN, path, "run: python3 backend/build_template.py")
        else:
            line(BAD, path, f"missing — {why}")


def check_supabase() -> None:
    section("Supabase")
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    anon = os.environ.get("SUPABASE_ANON_KEY", "")
    service = os.environ.get("SUPABASE_SERVICE_KEY", "")
    secret = os.environ.get("SUPABASE_JWT_SECRET", "")

    if not url:
        line(WARN, "SUPABASE_URL", "not set — skipping the rest of Stage 1")
        return
    if not url.startswith("https://") or ".supabase.co" not in url:
        line(WARN, "SUPABASE_URL", f"unusual form: {url}")
    else:
        line(OK, "SUPABASE_URL", url)

    # The two keys must be different, and each must carry its own role.
    for name, key, expect in (("SUPABASE_ANON_KEY", anon, "anon"),
                              ("SUPABASE_SERVICE_KEY", service, "service_role")):
        if not key:
            line(WARN, name, "not set")
            continue
        role = jwt_claims(key).get("role")
        if role == expect:
            line(OK, name, f"role={role}")
        elif role:
            line(BAD, name, f"role={role}, expected {expect} — keys are swapped")
        else:
            line(BAD, name, "not a readable JWT")

    if anon and service and anon == service:
        line(BAD, "keys", "anon and service keys are identical")

    if secret:
        line(OK if len(secret) >= 32 else BAD, "SUPABASE_JWT_SECRET",
             f"{len(secret)} chars" + ("" if len(secret) >= 32 else " — too short"))
    else:
        line(WARN, "SUPABASE_JWT_SECRET", "not set — the Space cannot validate tokens")

    if not service:
        return

    headers = {"apikey": service, "Authorization": f"Bearer {service}"}
    try:
        status, body = get(f"{url}/rest/v1/lab?select=code,name&order=code", headers)
        labs = json.loads(body)
        if len(labs) == 8:
            line(OK, "schema", "8 laboratories — migrations applied")
        elif labs:
            line(WARN, "schema", f"{len(labs)} laboratories, expected 8")
        else:
            line(BAD, "schema", "lab table is empty — run supabase/setup.sql")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            line(BAD, "schema", "lab table not found — run supabase/setup.sql")
        else:
            line(BAD, "schema", f"HTTP {exc.code}: {exc.read().decode()[:120]}")
        return
    except Exception as exc:
        line(BAD, "schema", f"cannot reach the project: {exc}")
        return

    try:
        _, body = get(f"{url}/rest/v1/evaluation?select=ref&limit=1", headers)
        rows = json.loads(body)
        line(OK if rows else WARN, "seed",
             "loaded" if rows else "no evaluations — run supabase/seed/seed.sql")
    except Exception:
        line(WARN, "seed", "could not read the evaluation table")

    # RLS check that matters: the anon key must see nothing without a session.
    if anon:
        try:
            _, body = get(f"{url}/rest/v1/evaluation?select=ref&limit=1",
                          {"apikey": anon, "Authorization": f"Bearer {anon}"})
            leaked = json.loads(body)
            line(BAD if leaked else OK, "RLS",
                 "anon key can read evaluations — policies are not protecting data"
                 if leaked else "anon key sees nothing without a session")
        except urllib.error.HTTPError:
            line(OK, "RLS", "anon key refused, as expected")
        except Exception:
            line(WARN, "RLS", "could not test with the anon key")

    for bucket in ("attachments", "reports"):
        try:
            _, body = get(f"{url}/storage/v1/bucket/{bucket}", headers)
            info = json.loads(body)
            line(OK if not info.get("public") else BAD, f"bucket:{bucket}",
                 "private" if not info.get("public") else "PUBLIC — reports would be world-readable")
        except urllib.error.HTTPError as exc:
            line(WARN if exc.code == 404 else BAD, f"bucket:{bucket}",
                 "not created yet" if exc.code == 404 else f"HTTP {exc.code}")
        except Exception:
            line(WARN, f"bucket:{bucket}", "could not check")


def check_space() -> None:
    section("Hugging Face Space")
    space = os.environ.get("VITE_SPACE_URL", "").rstrip("/")
    if not space:
        line(WARN, "VITE_SPACE_URL", "not set — skipping Stage 2")
        return
    line(OK, "VITE_SPACE_URL", space)
    try:
        status, _ = get(space, {}, timeout=70)
        line(OK if status == 200 else WARN, "reachable", f"HTTP {status}")
    except Exception as exc:
        line(WARN, "reachable",
             f"{exc} — a sleeping Space takes 30-60 s to wake; try again")


def main() -> None:
    print("PS 26035 preflight")
    check_local()
    check_supabase()
    check_space()

    print()
    if problems:
        print(f"{len(problems)} blocking problem(s):")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)
    print("no blocking problems")


if __name__ == "__main__":
    main()
