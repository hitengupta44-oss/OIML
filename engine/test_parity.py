"""
Assert the TypeScript engine and the Python engine reach the same verdicts.

    python3 engine/test_parity.py

Why this exists
---------------
Two engines decide the same thing. nawi-engine.ts runs in the browser so a
technician inside a shielded EMC chamber still sees verdicts with no network;
nawi_engine.py runs in the reporter service and decides what the signed PDF
says. They read the same standards JSON, which keeps the band values in one
place -- but reading the same numbers is not the same as computing the same
answer, and nothing checked that they did.

The cost of not checking showed up as a real defect: the browser folded its
overall verdict from the weighing observations alone while the service folded
in all five tests, so 34 of the 120 seed evaluations displayed CONFORMS on
screen and DOES NOT CONFORM in the report. Both halves were individually
correct and nothing failed.

This harness runs both engines over every seed evaluation and compares the
overall verdict and all five test verdicts. It needs node and esbuild, both of
which are already in frontend/node_modules.

Exit code 0 if they agree on every evaluation, 1 on the first divergence.
"""

import json
import os
import tempfile
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "backend"))

ESBUILD = os.path.join(ROOT, "frontend", "node_modules", ".bin", "esbuild")
RUNNER = os.path.join(HERE, "parity_runner.ts")


def seed_path():
    import paths                                             # noqa: E402
    return paths.seed_file()


def run_typescript(seed):
    """Bundle parity_runner.ts and run it, one JSON object per evaluation."""
    if not os.path.exists(ESBUILD):
        sys.exit("esbuild not found. Run 'npm install' in frontend/ first.")

    bundle = subprocess.run(
        [ESBUILD, RUNNER, "--bundle", "--platform=node", "--format=cjs",
         "--log-level=error", "--external:node:fs"],
        capture_output=True, text=True,
    )
    if bundle.returncode != 0:
        sys.exit(f"esbuild failed:\n{bundle.stderr}")

    out = subprocess.run(["node", "-", seed], input=bundle.stdout,
                         capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"the TypeScript engine crashed:\n{out.stderr[:2000]}")

    return {
        row["ref"]: row
        for row in (json.loads(line) for line in out.stdout.splitlines() if line.strip())
    }


def run_python(seed):
    import recompute as rc                                   # noqa: E402
    from test_local import shape                             # noqa: E402

    results = {}
    for e in json.load(open(seed)):
        try:
            c = rc.recompute(shape(e))
        except Exception as exc:                             # noqa: BLE001
            results[e["ref"]] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        results[e["ref"]] = {
            "verdict": c["verdict"],
            "tests": {k: v["verdict"] for k, v in c["tests"].items()},
        }
    return results


def boundary_cases():
    """Evaluations that sit exactly on each rule's limit.

    The seed dataset is realistic, which makes it a poor boundary test: its
    creep deviations are either well under 0.25 e or over 0.9 e, so a creep
    limit wrongly set anywhere between the two would pass every seed
    evaluation in both engines and this harness would report agreement.

    A limit is only tested by a value that straddles it. These cases place
    one reading on each side of every constant the two engines share, so a
    constant that drifts in one language and not the other is caught by
    construction rather than by luck.
    """
    E = 20.0                     # e = 20 g, class III, Max 60 kg, n = 3000
    base = {
        "is_synthetic": True,
        "standard_id": "OIML_R76-1_2006",
        "lab_code": "TEST", "lab_name": "boundary cases",
        "status": "draft", "verdict": "PENDING", "zero_error_g": 0.0,
        "instrument": {
            "manufacturer": "boundary", "model": "case",
            "accuracy_class": "III", "type": "table_top",
            "indication_type": "self_indicating",
            "max_capacity_g": 60_000.0, "e_g": E, "d_g": E,
            "min_capacity_g": 400.0, "n": 3000,
            "temp_range_c": [-10, 40],
            "has_aux_device": False, "is_grading": False,
        },
    }

    def ev(ref, observations):
        out = json.loads(json.dumps(base))
        out["ref"] = ref
        out["observations"] = observations
        return out

    cases = []

    # -- creep, A.4.11.1: limit is 0.5 e within the first 30 minutes -------
    # 0.5 e exactly must PASS (the rule is "not more than"); a hair over
    # must FAIL. A limit of 0.4 e or 0.9 e breaks one of these.
    for name, dev in [("creep_at_limit", 0.5 * E),
                      ("creep_over_limit", 0.5 * E + E / 100)]:
        cases.append(ev(f"BOUND/{name}", [
            {"test_code": "time_dependence", "variant": "creep",
             "elapsed_minutes": 0, "load_g": 30_000.0, "indication_g": 30_000.0},
            {"test_code": "time_dependence", "variant": "creep",
             "elapsed_minutes": 30, "load_g": 30_000.0,
             "indication_g": 30_000.0 + dev},
        ]))

    # A deviation past the limit but AFTER 30 minutes is outside the rule
    # and must not fail. An engine that forgets the window fails this.
    cases.append(ev("BOUND/creep_after_window", [
        {"test_code": "time_dependence", "variant": "creep",
         "elapsed_minutes": 0, "load_g": 30_000.0, "indication_g": 30_000.0},
        {"test_code": "time_dependence", "variant": "creep",
         "elapsed_minutes": 60, "load_g": 30_000.0, "indication_g": 30_000.0 + 5 * E},
    ]))

    # -- disturbances, B.3: difference <= 1 e, OR a fault is detected ------
    for name, diff, detected in [
        ("dist_at_limit", E, False),
        ("dist_over_limit", E + E / 100, False),
        ("dist_over_but_detected", 5 * E, True),     # the OR branch
    ]:
        cases.append(ev(f"BOUND/{name}", [
            {"test_code": "electrical_disturbances",
             "disturbance": "mains_dips_interruptions", "load_g": 30_000.0,
             "indication_without_g": 30_000.0,
             "indication_with_g": 30_000.0 + diff,
             "significant_fault_detected": detected},
        ]))

    # -- mpe band edge, Table 6: 500 e takes 0.5 e, 500.5 e takes 1.0 e ----
    # The defect the whole project exists to eliminate. At e = 20 g,
    # 500 e is 10.00 kg and the next interval up is 10.02 kg.
    for name, load, err in [
        ("mpe_edge_inside", 10_000.0, 0.5 * E),       # exactly mpe at 500 e
        ("mpe_edge_outside", 10_000.0, 0.5 * E + E / 100),
        ("mpe_past_edge", 10_020.0, 1.0 * E),         # 501 e, mpe is now 1 e
    ]:
        cases.append(ev(f"BOUND/{name}", [
            {"test_code": "weighing_performance", "load_g": load,
             "indication_g": load + err - 0.5 * E, "delta_load_g": 0.0,
             "direction": "increasing"},
        ]))

    # -- repeatability, 3.6.1: spread <= |mpe| for that load ---------------
    for name, spread in [("rep_at_limit", 1.0 * E), ("rep_over_limit", 1.0 * E + E / 100)]:
        cases.append(ev(f"BOUND/{name}", [
            {"test_code": "repeatability", "series": 1, "load_g": 20_000.0,
             "indication_g": 20_000.0, "weighing_no": 1},
            {"test_code": "repeatability", "series": 1, "load_g": 20_000.0,
             "indication_g": 20_000.0 + spread, "weighing_no": 2},
            {"test_code": "repeatability", "series": 1, "load_g": 20_000.0,
             "indication_g": 20_000.0, "weighing_no": 3},
        ]))

    # -- eccentricity, A.4.7: each position within the mpe for that load ---
    for name, err in [("ecc_at_limit", 1.0 * E), ("ecc_over_limit", 1.0 * E + E / 100)]:
        cases.append(ev(f"BOUND/{name}", [
            {"test_code": "eccentricity", "position": p,
             "load_g": 20_000.0,
             "indication_g": 20_000.0 + (err if p == "Segment 1" else 0.0)
                             - 0.5 * E,
             "delta_load_g": 0.0}
            for p in ("Centre", "Segment 1", "Segment 2")
        ]))

    return cases


def main():
    seed = seed_path()
    if not seed or not os.path.exists(seed):
        sys.exit("seed data missing. Run: python3 seed/generate.py --seed 26035")

    print("Engine parity: TypeScript (browser) vs Python (reporter service)\n")

    # The seed dataset plus cases sitting exactly on every shared limit. The
    # two are written to one file so both engines see identical input.
    dataset = json.load(open(seed)) + boundary_cases()
    combined = os.path.join(tempfile.gettempdir(), "nawi_parity_input.json")
    with open(combined, "w") as f:
        json.dump(dataset, f)
    print(f"  {len(dataset)} evaluations "
          f"({len(dataset) - len(boundary_cases())} from the seed, "
          f"{len(boundary_cases())} boundary cases)\n")

    ts = run_typescript(combined)
    py = run_python(combined)

    only_py = sorted(set(py) - set(ts))
    only_ts = sorted(set(ts) - set(py))
    for ref in only_py[:5]:
        print(f"  [FAIL] {ref}: the TypeScript engine produced no result")
    for ref in only_ts[:5]:
        print(f"  [FAIL] {ref}: the Python engine produced no result")

    problems = len(only_py) + len(only_ts)
    compared = 0
    test_rows = 0

    for ref in sorted(set(py) & set(ts)):
        p, t = py[ref], ts[ref]
        if "error" in p:
            print(f"  [FAIL] {ref}: Python raised {p['error']}")
            problems += 1
            continue
        compared += 1

        if p["verdict"] != t["verdict"]:
            print(f"  [FAIL] {ref}: overall — Python {p['verdict']}, "
                  f"TypeScript {t['verdict']}")
            problems += 1

        for code in sorted(set(p["tests"]) | set(t["tests"])):
            pv, tv = p["tests"].get(code), t["tests"].get(code)
            test_rows += 1
            if pv != tv:
                print(f"  [FAIL] {ref}: {code} — Python {pv}, TypeScript {tv}")
                problems += 1

    print(f"\n  {compared} evaluations compared")
    print(f"  {test_rows} per-test verdicts compared")

    print("\n" + "=" * 62)
    if problems:
        print(f"  {problems} divergence(s) between the two engines")
        print("=" * 62)
        sys.exit(1)
    print("  the browser and the reporter service agree on every verdict")
    print("=" * 62)


if __name__ == "__main__":
    main()
