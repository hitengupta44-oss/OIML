"""
Render real reports from the seed dataset, with no Supabase and no network.

    python3 backend/test_local.py [--out /tmp/nawi]

Picks one passing and one failing evaluation, recomputes both, renders DOCX
and PDF, and checks the things that actually matter:

  * the server's verdicts agree with the generator's
  * the payload digest is stable across two renders of the same data
  * every table marker in the template was replaced
  * the PDF has content

This is what to run before pushing to the Space. It exercises the whole
render path, and it fails loudly rather than producing a document with
silently missing tables.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import paths                # noqa: E402
import recompute as rc      # noqa: E402
import render as rnd        # noqa: E402

SEED = paths.seed_file()


def shape(ev):
    """Match what supabase_io.fetch_evaluation returns."""
    ev = json.loads(json.dumps(ev))
    inst = ev["instrument"]
    inst["instrument_type"] = inst.get("type")
    inst["temp_low_c"], inst["temp_high_c"] = inst.get("temp_range_c", [-10, 40])
    inst.setdefault("has_aux_device", False)
    inst.setdefault("is_grading", False)
    ev["lab"] = {"code": ev["lab_code"], "name": ev["lab_name"]}
    ev["applicant"] = inst["manufacturer"]
    ev["weight_set_id"] = inst.get("weight_set")
    return ev


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/nawi-reports")
    args = ap.parse_args()

    if not SEED or not os.path.exists(SEED):
        sys.exit("seed data missing. Run: python3 seed/generate.py --seed 26035")

    evals = json.load(open(SEED))
    cases = []
    for want in ("PASS", "FAIL"):
        pick = next((e for e in evals
                     if e["verdict"] == want and len(e["observations"]) > 40), None)
        if pick:
            cases.append((want, shape(pick)))

    failures = []
    for expected, ev in cases:
        print(f"\n{'=' * 66}\n{ev['ref']}   {ev['instrument']['manufacturer']} "
              f"{ev['instrument']['model']}\n{'=' * 66}")
        i = ev["instrument"]
        print(f"  class {i['accuracy_class']}  Max {i['max_capacity_g'] / 1000:g} kg  "
              f"e {i['e_g']:g} g  n {i['n']:.0f}  profile {ev['quality_profile']}")

        computed = rc.recompute(ev)
        payload = rc.canonical_payload(ev, computed)
        sha = rc.payload_sha256(payload)

        for code, t in computed["tests"].items():
            n = len(t.get("rows") or t.get("series") or [])
            print(f"    {code:26} {t['verdict']:8} {n:3} rows   [{t['clause']}]")
        print(f"    {'overall':26} {computed['verdict']}")

        if computed["verdict"] != expected:
            failures.append(f"{ev['ref']}: expected {expected}, "
                            f"server says {computed['verdict']}")

        # digest must be stable across renders of identical data
        sha2 = rc.payload_sha256(rc.canonical_payload(ev, rc.recompute(ev)))
        if sha != sha2:
            failures.append(f"{ev['ref']}: payload digest is not stable")
        print(f"    digest                     {sha[:16]}  "
              f"{'stable' if sha == sha2 else 'UNSTABLE'}")

        try:
            docx_path, pdf_path = rnd.render(ev, computed, sha, args.out)
        except RuntimeError as exc:
            failures.append(f"{ev['ref']}: {exc}")
            continue

        docx_kb = os.path.getsize(docx_path) / 1024
        print(f"    docx                       {docx_path}  ({docx_kb:.0f} KB)")
        if pdf_path:
            pdf_kb = os.path.getsize(pdf_path) / 1024
            print(f"    pdf                        {pdf_path}  ({pdf_kb:.0f} KB)")
            if pdf_kb < 20:
                failures.append(f"{ev['ref']}: PDF is suspiciously small "
                                f"({pdf_kb:.0f} KB) -- tables may be missing")
        else:
            print("    pdf                        skipped (LibreOffice not found)")

        # no marker should survive into the finished document
        from docx import Document
        leftover = [p.text for p in Document(docx_path).paragraphs
                    if "[[TABLE:" in (p.text or "")]
        if leftover:
            failures.append(f"{ev['ref']}: unreplaced markers {leftover}")

    print(f"\n{'=' * 66}")
    if failures:
        print(f"{len(failures)} problem(s):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print(f"{len(cases)} report(s) rendered, all checks passed")
    print(f"output: {args.out}")


if __name__ == "__main__":
    main()
