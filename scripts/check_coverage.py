"""
Assert the project's metadata matches what the code actually does.

    python3 scripts/check_coverage.py

Why this exists
---------------
standards/test-catalogue.json marked thirteen tests "engine": "implemented"
when five were. Nine of them had no code path at all. Nothing noticed, because
nothing read the file: the catalogue was documentation that had quietly become
fiction, and the fiction was flattering.

A claim nothing checks is a claim that will drift. These are the cheapest
checks that would have caught it:

  1. every test the catalogue calls "implemented" is computed AND rendered
  2. every test that is computed and rendered is marked "implemented"
  3. every section of report-layout.json appears in the built template
  4. every table the template asks for is one render.py knows how to build

Check 4 is the one that keeps the report from failing at render time: the
template and render.py agree on a set of [[TABLE:x]] markers, and adding a
section to the layout without a renderer would otherwise blow up in front of
a user rather than here.

Exit code 0 if the metadata is honest, 1 if it is not.
"""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

CATALOGUE = os.path.join(ROOT, "standards", "test-catalogue.json")
LAYOUT = os.path.join(ROOT, "standards", "report-layout.json")
RECOMPUTE = os.path.join(ROOT, "backend", "recompute.py")
RENDER = os.path.join(ROOT, "backend", "render.py")
TEMPLATE_SRC = os.path.join(ROOT, "backend", "build_template.py")

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


catalogue = json.load(open(CATALOGUE, encoding="utf-8"))
tests = catalogue["tests"] if isinstance(catalogue.get("tests"), list) else catalogue
layout = json.load(open(LAYOUT, encoding="utf-8"))

recompute_src = read(RECOMPUTE)
render_src = read(RENDER)
template_src = read(TEMPLATE_SRC)

claimed = {t["code"] for t in tests if t.get("engine") == "implemented"}

# A test is computed if recompute.py filters observations by its code, and
# rendered if render.py names it in CLAUSES.
computed = {
    t["code"] for t in tests
    if re.search(rf'test_code"\]\s*==\s*"{re.escape(t["code"])}"', recompute_src)
}
clauses_block = re.search(r"CLAUSES\s*=\s*\{(.*?)\n\}", render_src, re.S)
rendered = set(re.findall(r'"([a-z_]+)":', clauses_block.group(1))) if clauses_block else set()


# ---------------------------------------------------------------------------
rule("1. Every test claimed 'implemented' is computed and rendered")
# ---------------------------------------------------------------------------
bad = sorted(claimed - (computed & rendered))
if bad:
    for code in bad:
        where = []
        if code not in computed:
            where.append("not computed in recompute.py")
        if code not in rendered:
            where.append("not rendered in render.py")
        fail(f"'{code}' is marked implemented but is {' and '.join(where)}")
else:
    ok(f"all {len(claimed)} claimed tests are computed and rendered")


# ---------------------------------------------------------------------------
rule("2. Every test that is computed and rendered is claimed")
# ---------------------------------------------------------------------------
unclaimed = sorted((computed & rendered) - claimed)
if unclaimed:
    for code in unclaimed:
        fail(f"'{code}' is fully implemented but the catalogue does not say so")
else:
    ok(f"{len(computed & rendered)} implemented tests, all declared")


# ---------------------------------------------------------------------------
rule("3. The catalogue's codes and the layout's test_codes agree")
# ---------------------------------------------------------------------------
cat_codes = {t["code"] for t in tests}
layout_codes = {s["test_code"] for s in layout["sections"] if s.get("test_code")}
orphans = sorted(layout_codes - cat_codes)
if orphans:
    for c in orphans:
        fail(f"report-layout.json section references unknown test '{c}'")
else:
    ok(f"all {len(layout_codes)} layout test codes exist in the catalogue")


# ---------------------------------------------------------------------------
rule("4. The template is built from every section of the layout")
# ---------------------------------------------------------------------------
# The loop must read the layout rather than a hardcoded list. This catches a
# regression to the earlier state, where the layout was opened and ignored.
if 'for spec in layout["sections"]' not in template_src:
    fail("build_template.py no longer iterates layout['sections']; "
         "sections would be hardcoded again")
else:
    ok(f"build_template.py emits all {len(layout['sections'])} layout sections")


# ---------------------------------------------------------------------------
rule("5. Every table marker the template emits has a renderer")
# ---------------------------------------------------------------------------
markers = set(re.findall(r'table_slot\(doc,\s*"([a-z_]+)"\)', template_src))
markers |= set(re.findall(r'"(tbl_[a-z_]+)"', template_src))
specs = set(re.findall(r'^\s{8}"([a-z_]+)": spec\(', render_src, re.M))
missing = sorted({m[4:] if m.startswith("tbl_") else m for m in markers} - specs)
if missing:
    for m in missing:
        fail(f"template asks for table '{m}' but render.py has no spec for it")
else:
    ok(f"{len(markers)} table markers, every one with a renderer")


# ---------------------------------------------------------------------------
rule("6. Tests not implemented are honestly labelled")
# ---------------------------------------------------------------------------
not_impl = sorted(cat_codes - claimed)
mislabelled = [t["code"] for t in tests
               if t["code"] in not_impl and t.get("engine") == "implemented"]
if mislabelled:
    for c in mislabelled:
        fail(f"'{c}' still claims to be implemented")
else:
    ok(f"{len(not_impl)} unimplemented tests, all marked otherwise")
    print(f"         (they appear in the report summary as NOT COVERED)")


# ---------------------------------------------------------------------------
print("\n" + "=" * 62)
print(f"  implemented: {len(computed & rendered)} of {len(cat_codes)} catalogued tests")
if problems:
    print(f"  {len(problems)} dishonest claim(s) in project metadata")
    print("=" * 62)
    sys.exit(1)
print("  project metadata matches what the code does")
print("=" * 62)
