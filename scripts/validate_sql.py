r"""
Validate every .sql file with PostgreSQL's own parser.

    python3 scripts/validate_sql.py

Why pglast and not sqlglot: sqlglot is a translator and is deliberately
lenient, so it happily accepted two files that the server rejected --
a psql meta-command (\echo) and a reserved word used as a column name
(check). pglast wraps libpg_query, the actual parser PostgreSQL uses, so
what passes here is what the server will accept.

    pip install pglast
"""

import glob
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

try:
    import pglast
except ImportError:
    sys.exit("pip install pglast --break-system-packages")

# psql meta-commands are fine in psql and fatal in the Supabase SQL Editor.
META = re.compile(r"^\s*\\[a-z]", re.M)

failures = 0
for path in sorted(glob.glob(os.path.join(ROOT, "supabase", "**", "*.sql"),
                             recursive=True)):
    rel = os.path.relpath(path, ROOT)
    sql = open(path).read()

    metas = META.findall(sql)
    if metas:
        print(f"  FAIL  {rel:42} psql meta-command; will not run in the SQL Editor")
        failures += 1
        continue

    try:
        stmts = pglast.parse_sql(sql)
        print(f"  ok    {rel:42} {len(stmts)} statements")
    except Exception as exc:
        print(f"  FAIL  {rel:42} {str(exc)[:100]}")
        failures += 1

print()
if failures:
    sys.exit(f"{failures} file(s) would fail on the server")
print("all SQL parses with PostgreSQL's own parser")
