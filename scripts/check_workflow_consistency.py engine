"""
Assert that the workflow in the database matches standards/workflow.json.

    python3 scripts/check_workflow_consistency.py

Why this exists
---------------
The whole project rests on one claim: the standard is data, not code. That
claim holds for the mpe bands, because engine/nawi_engine.py and
engine/nawi-engine.ts both read mpe-bands.json and a value exists in exactly
one place.

It did not hold for the workflow. The states, the legal transitions and the
role gate were written once in standards/workflow.json and typed a second
time, by hand, into supabase/migrations/0001_schema.sql and 0002_functions.sql.
Two copies that agree today and nothing that would notice when they stop.

This script is that something. It parses the SQL and compares it, edge by
edge and role by role, against the JSON. It is the third of the three
consistency checks the audit found missing, and the cheapest.

Exit code 0 if they agree, 1 if they have drifted.
"""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WORKFLOW = os.path.join(ROOT, "standards", "workflow.json")
SCHEMA = os.path.join(ROOT, "supabase", "migrations", "0001_schema.sql")
FUNCTIONS = os.path.join(ROOT, "supabase", "migrations", "0002_functions.sql")

problems = []


def fail(msg):
    problems.append(msg)
    print(f"  [FAIL] {msg}")


def ok(msg):
    print(f"  [ok  ] {msg}")


def rule(title):
    print(f"\n{title}\n" + "-" * len(title))


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# the JSON side
# ---------------------------------------------------------------------------

with open(WORKFLOW, encoding="utf-8") as f:
    wf = json.load(f)

json_states = [s["code"] for s in wf["states"]]
json_roles = [r["code"] for r in wf["roles"]]
json_edges = {
    (s["code"], to)
    for s in wf["states"]
    for to in (s.get("transitions") or [])
}
json_gate = {
    k: set(v)
    for k, v in wf["role_may_set"].items()
    if isinstance(v, list)
}

schema_sql = read(SCHEMA)
functions_sql = read(FUNCTIONS)


# ---------------------------------------------------------------------------
rule("1. The evaluation_status enum matches the states in workflow.json")
# ---------------------------------------------------------------------------

m = re.search(r"create\s+type\s+evaluation_status\s+as\s+enum\s*\((.*?)\)\s*;",
              schema_sql, re.S | re.I)
if not m:
    fail("could not find 'create type evaluation_status as enum' in 0001_schema.sql")
    sql_states = []
else:
    sql_states = re.findall(r"'([a-z_]+)'", m.group(1))
    if set(sql_states) == set(json_states):
        ok(f"{len(sql_states)} statuses, identical in both")
    else:
        for s in sorted(set(json_states) - set(sql_states)):
            fail(f"status '{s}' is in workflow.json but not in the enum")
        for s in sorted(set(sql_states) - set(json_states)):
            fail(f"status '{s}' is in the enum but not in workflow.json")


# ---------------------------------------------------------------------------
rule("2. The lab_role enum matches the roles in workflow.json")
# ---------------------------------------------------------------------------

m = re.search(r"create\s+type\s+lab_role\s+as\s+enum\s*\((.*?)\)\s*;",
              schema_sql, re.S | re.I)
if not m:
    fail("could not find 'create type lab_role as enum' in 0001_schema.sql")
else:
    sql_roles = re.findall(r"'([a-z_]+)'", m.group(1))
    if set(sql_roles) == set(json_roles):
        ok(f"{len(sql_roles)} roles, identical in both")
    else:
        for r in sorted(set(json_roles) - set(sql_roles)):
            fail(f"role '{r}' is in workflow.json but not in the enum")
        for r in sorted(set(sql_roles) - set(json_roles)):
            fail(f"role '{r}' is in the enum but not in workflow.json")


# ---------------------------------------------------------------------------
rule("3. app.transition_allowed() permits exactly the JSON's edges")
# ---------------------------------------------------------------------------

m = re.search(r"function\s+app\.transition_allowed\b.*?\$\$(.*?)\$\$",
              functions_sql, re.S | re.I)
if not m:
    fail("could not find app.transition_allowed() in 0002_functions.sql")
else:
    body = m.group(1)
    # Each edge is written as a ('from','to') pair, in whatever layout.
    sql_edges = {
        (a, b) for a, b in re.findall(r"\(\s*'([a-z_]+)'\s*,\s*'([a-z_]+)'\s*\)", body)
    }
    if not sql_edges:
        fail("found the function but no ('from','to') pairs inside it")
    elif sql_edges == json_edges:
        ok(f"{len(sql_edges)} legal transitions, identical in both")
    else:
        for e in sorted(json_edges - sql_edges):
            fail(f"transition {e[0]} -> {e[1]} is legal in workflow.json "
                 f"but the database refuses it")
        for e in sorted(sql_edges - json_edges):
            fail(f"transition {e[0]} -> {e[1]} is allowed by the database "
                 f"but is not in workflow.json")


# ---------------------------------------------------------------------------
rule("4. app.role_may_set() matches the role gate in workflow.json")
# ---------------------------------------------------------------------------

m = re.search(r"function\s+app\.role_may_set\b.*?\$\$(.*?)\$\$",
              functions_sql, re.S | re.I)
if not m:
    fail("could not find app.role_may_set() in 0002_functions.sql")
else:
    body = m.group(1)
    sql_gate = {}
    for status, roles in re.findall(
        r"when\s+'([a-z_]+)'\s+then\s+app\.has_role\((.*?)\)", body, re.S | re.I
    ):
        sql_gate[status] = set(re.findall(r"'([a-z_]+)'", roles))

    missing = set(json_gate) - set(sql_gate)
    extra = set(sql_gate) - set(json_gate)
    for s in sorted(missing):
        fail(f"status '{s}' has a role gate in workflow.json but none in SQL")
    for s in sorted(extra):
        fail(f"status '{s}' has a role gate in SQL but none in workflow.json")

    same = 0
    for status in sorted(set(json_gate) & set(sql_gate)):
        if json_gate[status] == sql_gate[status]:
            same += 1
        else:
            fail(f"'{status}': workflow.json says "
                 f"{sorted(json_gate[status])}, SQL says {sorted(sql_gate[status])}")
    if same and not missing and not extra:
        ok(f"{same} role gates, identical in both")


# ---------------------------------------------------------------------------
rule("5. Every status is reachable, and every state's edges are real statuses")
# ---------------------------------------------------------------------------

reachable = {"draft"}
changed = True
while changed:
    changed = False
    for a, b in json_edges:
        if a in reachable and b not in reachable:
            reachable.add(b)
            changed = True

stranded = set(json_states) - reachable
if stranded:
    fail(f"unreachable from draft: {sorted(stranded)}")
else:
    ok(f"all {len(json_states)} statuses reachable from draft")

dangling = {b for _, b in json_edges if b not in set(json_states)} | \
           {a for a, _ in json_edges if a not in set(json_states)}
if dangling:
    fail(f"transitions naming unknown statuses: {sorted(dangling)}")
else:
    ok("every transition names a declared status")


# ---------------------------------------------------------------------------
print("\n" + "=" * 62)
if problems:
    print(f"  {len(problems)} inconsistency(ies) between workflow.json and the database")
    print("=" * 62)
    sys.exit(1)
print("  workflow.json and the database agree")
print("=" * 62)
