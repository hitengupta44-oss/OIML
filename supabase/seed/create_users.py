"""
Create the demo users, with roles where they actually matter.

    export SUPABASE_URL=https://xxxx.supabase.co
    export SUPABASE_SERVICE_KEY=eyJ...          # service_role, never shipped
    python3 supabase/seed/create_users.py

Roles go in app_metadata, NOT user_metadata
-------------------------------------------
A signed-in user can edit their own user_metadata through the normal client
API. If lab_role lived there, any technician could promote themselves to
director and the entire RLS layer would be decorative. app_metadata is
writable only with the service-role key, so it is the only safe home for an
authorisation claim.

app.role() and app.lab_id() in 0002_functions.sql read exactly these two
claims, so the users created here line up with the policies in 0003_rls.sql.
"""

import json
import os
import sys
import urllib.request

URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
PASSWORD = os.environ.get("DEMO_PASSWORD", "Nawi#26035demo")

if not URL or not KEY:
    sys.exit("set SUPABASE_URL and SUPABASE_SERVICE_KEY first")

# lab code -> (email, full name, designation, role)
USERS = [
    ("RRSL-FBD", "tech1.fbd@example.gov.in",  "R. Sharma",     "Test Engineer",        "technician"),
    ("RRSL-FBD", "tech2.fbd@example.gov.in",  "P. Verma",      "Test Engineer",        "technician"),
    ("RRSL-FBD", "review.fbd@example.gov.in", "S. Iyer",       "Metrologist",          "reviewer"),
    ("RRSL-FBD", "head.fbd@example.gov.in",   "A. Banerjee",   "Laboratory Head",      "approver"),
    ("RRSL-MUM", "tech1.mum@example.gov.in",  "K. Deshpande",  "Test Engineer",        "technician"),
    ("RRSL-MUM", "review.mum@example.gov.in", "N. Fernandes",  "Metrologist",          "reviewer"),
    ("RRSL-MUM", "head.mum@example.gov.in",   "V. Rao",        "Laboratory Head",      "approver"),
    ("RRSL-BLR", "tech1.blr@example.gov.in",  "M. Gowda",      "Test Engineer",        "technician"),
    ("RRSL-BLR", "review.blr@example.gov.in", "T. Krishnan",   "Metrologist",          "reviewer"),
    (None,       "director@example.gov.in",   "Dr. L. Mehta",  "Director of Legal Metrology", "director"),
    (None,       "admin@example.gov.in",      "System Admin",  "Administrator",        "admin"),
    (None,       "auditor@example.gov.in",    "CAG Observer",  "Auditor",              "auditor"),
]


def call(method, path, body=None):
    req = urllib.request.Request(
        f"{URL}{path}", method=method,
        data=json.dumps(body).encode() if body else None,
        headers={
            "apikey": KEY,
            "Authorization": f"Bearer {KEY}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read() or "{}")


def lab_ids():
    rows = call("GET", "/rest/v1/lab?select=id,code")
    return {r["code"]: r["id"] for r in rows}


def main():
    labs = lab_ids()
    print(f"{len(labs)} laboratories found\n")

    for lab_code, email, name, designation, role in USERS:
        lab_id = labs.get(lab_code) if lab_code else None
        try:
            user = call("POST", "/auth/v1/admin/users", {
                "email": email,
                "password": PASSWORD,
                "email_confirm": True,
                # the two claims the RLS policies read
                "app_metadata": {"lab_role": role, "lab_id": lab_id},
                "user_metadata": {"full_name": name},
            })
            uid = user["id"]
            call("POST", "/rest/v1/profile", {
                "id": uid, "full_name": name, "designation": designation,
                "lab_id": lab_id, "role": role,
            })
            print(f"  {role:11} {email:32} {lab_code or '—'}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:120]
            print(f"  SKIP {email}: {e.code} {detail}")

    print(f"\npassword for all demo accounts: {PASSWORD}")
    print("change DEMO_PASSWORD before any deployment that is not a demo")


if __name__ == "__main__":
    main()
