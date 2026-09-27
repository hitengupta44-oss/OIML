"""
Independent recompute.

The browser computes verdicts as the technician types, so the work is
instant and keeps working inside a shielded EMC chamber with no network.
That client result is convenience, not authority.

On submission this module re-derives every verdict from the raw readings
alone -- I, dL, L and E0 -- using the same engine and the same standards
JSON, and compares. A divergence means a stale client, a tampered payload,
or a bug; any of the three should stop the submission rather than quietly
issue a report.

The redundancy is deliberate. In a legal-metrology record system, "the
server independently reproduced every verdict from the raw readings" is a
sentence worth being able to write.
"""

import hashlib
import json
import os
import sys
from typing import Any, Dict, List

import paths  # noqa: F401  -- puts the engine on sys.path, either layout

from nawi_engine import (  # noqa: E402
    Instrument, Observation, evaluate_observations, mpe_for_load,
    mpe_band_label, validate_instrument, check_repeatability,
)

ECC_ORDER = ["Centre", "Segment 1", "Segment 2", "Segment 3", "Segment 4"]


def _instrument(ev: Dict[str, Any]) -> Instrument:
    i = ev["instrument"]
    return Instrument(
        manufacturer=i["manufacturer"],
        model=i["model"],
        accuracy_class=i["accuracy_class"],
        max_capacity_g=float(i["max_capacity_g"]),
        e_g=float(i["e_g"]),
        d_g=float(i.get("d_g") or i["e_g"]),
        min_capacity_g=float(i["min_capacity_g"]) if i.get("min_capacity_g") else None,
        has_auxiliary_indicating_device=bool(i.get("has_aux_device")),
        is_grading_instrument=bool(i.get("is_grading")),
        jurisdiction=ev.get("jurisdiction", "IN"),
    )


def recompute(ev: Dict[str, Any]) -> Dict[str, Any]:
    """Re-derive every verdict for one evaluation.

    `ev` carries the instrument, zero_error_g and the raw observations, in
    the shape returned by fetch_evaluation() in supabase_io.py.
    """
    inst = _instrument(ev)
    E0 = float(ev.get("zero_error_g") or 0.0)
    obs = [o for o in ev["observations"] if not o.get("superseded_by")]

    findings = validate_instrument(inst)
    spec_errors = [f for f in findings if f.level == "ERROR"]

    results: Dict[str, Any] = {"tests": {}, "divergences": []}

    # -- weighing performance ------------------------------------------
    weigh_rows = [o for o in obs if o["test_code"] == "weighing_performance"]
    weigh = [
        Observation(
            load_g=float(o["load_g"]),
            indication_g=float(o["indication_g"]),
            delta_load_g=float(o.get("delta_load_g") or 0.0),
            direction=o.get("direction") or "increasing",
        )
        for o in weigh_rows
    ]
    evaluate_observations(inst, weigh, zero_error_g=E0)

    weigh_out = []
    for src, r in zip(weigh_rows, weigh):
        row = {
            "observation_id": src.get("id"),
            "load_g": r.load_g, "indication_g": r.indication_g,
            "delta_load_g": r.delta_load_g, "direction": r.direction,
            "P_g": round(r.P_g, 6), "E_g": round(r.E_g, 6),
            "Ec_g": round(r.Ec_g, 6), "mpe_g": round(r.mpe_g, 6),
            "mpe_band": r.mpe_band, "margin_e": round(r.margin_e, 4),
            "verdict": r.verdict,
        }
        weigh_out.append(row)

        # compare against what the client recorded, if anything
        claimed = (src.get("computed") or {}).get("verdict")
        if claimed and claimed != r.verdict:
            results["divergences"].append({
                "observation_id": src.get("id"),
                "test_code": "weighing_performance",
                "load_g": r.load_g,
                "client_verdict": claimed,
                "server_verdict": r.verdict,
                "server_Ec_g": round(r.Ec_g, 6),
                "server_mpe_g": round(r.mpe_g, 6),
            })

    results["tests"]["weighing_performance"] = {
        "clause": "A.4.4.1, A.4.4.3",
        "rows": weigh_out,
        "failed": sum(1 for r in weigh if r.verdict == "FAIL"),
        "total": len(weigh),
        "verdict": "FAIL" if any(r.verdict == "FAIL" for r in weigh)
                   else ("PASS" if weigh else "PENDING"),
    }

    # -- repeatability --------------------------------------------------
    rep_rows = [o for o in obs if o["test_code"] == "repeatability"]
    series: Dict[float, List[float]] = {}
    for o in rep_rows:
        series.setdefault(float(o["load_g"]), []).append(float(o["indication_g"]))
    rep_out = []
    for load_g, vals in sorted(series.items()):
        r = check_repeatability(inst, vals, load_g)
        rep_out.append({
            "load_g": load_g, "n_weighings": r["n_weighings"],
            "spread_g": round(r["spread_g"], 6),
            "spread_e": round(r["spread_e"], 4),
            "limit_g": round(r["limit_g"], 6),
            "verdict": r["verdict"],
        })
    results["tests"]["repeatability"] = {
        "clause": "3.6.1",
        "series": rep_out,
        "verdict": "FAIL" if any(r["verdict"] == "FAIL" for r in rep_out)
                   else ("PASS" if rep_out else "PENDING"),
    }

    # -- eccentricity ---------------------------------------------------
    ecc_rows = [o for o in obs if o["test_code"] == "eccentricity"]
    ecc = [
        Observation(load_g=float(o["load_g"]),
                    indication_g=float(o["indication_g"]),
                    delta_load_g=float(o.get("delta_load_g") or 0.0))
        for o in ecc_rows
    ]
    evaluate_observations(inst, ecc, zero_error_g=E0)
    results["tests"]["eccentricity"] = {
        "clause": "A.4.7",
        "rows": [
            {"position": src.get("position"), "load_g": r.load_g,
             "indication_g": r.indication_g, "Ec_g": round(r.Ec_g, 6),
             "mpe_g": round(r.mpe_g, 6), "verdict": r.verdict}
            for src, r in zip(ecc_rows, ecc)
        ],
        "verdict": "FAIL" if any(r.verdict == "FAIL" for r in ecc)
                   else ("PASS" if ecc else "PENDING"),
    }

    # -- creep ----------------------------------------------------------
    creep_rows = sorted(
        [o for o in obs if o["test_code"] == "time_dependence"],
        key=lambda o: o.get("elapsed_minutes") or 0,
    )
    creep_out, creep_verdict = [], "PENDING"
    if creep_rows:
        base = float(creep_rows[0]["indication_g"])
        worst_30 = 0.0
        for o in creep_rows:
            mins = o.get("elapsed_minutes") or 0
            dev = float(o["indication_g"]) - base
            creep_out.append({
                "elapsed_minutes": mins,
                "indication_g": float(o["indication_g"]),
                "deviation_g": round(dev, 6),
                "deviation_e": round(dev / inst.e_g, 4),
            })
            if mins <= 30:
                worst_30 = max(worst_30, abs(dev))
        # A.4.11.1: within the first 30 minutes the indication shall not
        # differ by more than 0.5 e.
        creep_verdict = "PASS" if worst_30 <= 0.5 * inst.e_g + 1e-9 else "FAIL"
    results["tests"]["time_dependence"] = {
        "clause": "A.4.11.1",
        "rows": creep_out,
        "limit_e": 0.5,
        "verdict": creep_verdict,
    }

    # -- disturbances ---------------------------------------------------
    # Annex B uses a different rule from the weighing tests: the difference
    # with and without the disturbance shall not exceed 1 e, OR the
    # instrument shall detect and react to a significant fault.
    dist_rows = [o for o in obs if o["test_code"] == "electrical_disturbances"]
    dist_out = []
    for o in dist_rows:
        without = float(o["indication_without_g"])
        with_ = float(o["indication_with_g"])
        diff = abs(with_ - without)
        detected = bool(o.get("significant_fault_detected"))
        ok = diff <= inst.e_g + 1e-9 or detected
        dist_out.append({
            "disturbance": (o.get("severity") or {}).get("disturbance") or o.get("variant"),
            "load_g": float(o["load_g"]) if o.get("load_g") else None,
            "indication_without_g": without, "indication_with_g": with_,
            "difference_g": round(diff, 6),
            "difference_e": round(diff / inst.e_g, 4),
            "limit_g": inst.e_g,
            "significant_fault_detected": detected,
            "verdict": "PASS" if ok else "FAIL",
        })
    results["tests"]["electrical_disturbances"] = {
        "clause": "B.3",
        "rule": "difference <= 1 e, or a significant fault is detected",
        "rows": dist_out,
        "verdict": "FAIL" if any(r["verdict"] == "FAIL" for r in dist_out)
                   else ("PASS" if dist_out else "PENDING"),
    }

    # -- overall --------------------------------------------------------
    verdicts = [t["verdict"] for t in results["tests"].values()]
    if spec_errors or "FAIL" in verdicts:
        overall = "FAIL"
    elif "PENDING" in verdicts or not verdicts:
        overall = "PENDING"
    else:
        overall = "PASS"

    results["specification"] = {
        "findings": [
            {"level": f.level, "code": f.code, "clause": f.clause,
             "message": f.message}
            for f in findings
        ],
        "verdict": "FAIL" if spec_errors else "PASS",
    }
    results["derived"] = {
        "n": inst.n,
        "min_capacity_g": inst.min_capacity_g,
        "class_symbol": ("IV" if inst.accuracy_class == "IIII"
                         and inst.jurisdiction == "IN" else inst.accuracy_class),
        "mpe_at_max_g": mpe_for_load(inst, inst.max_capacity_g),
        "mpe_band_at_max": mpe_band_label(inst, inst.max_capacity_g),
    }
    results["verdict"] = overall
    results["standard_id"] = ev.get("standard_id")
    results["agrees_with_client"] = not results["divergences"]
    return results


def canonical_payload(ev: Dict[str, Any], computed: Dict[str, Any]) -> Dict[str, Any]:
    """The exact object a report is rendered from.

    Everything that could change a printed number belongs here, and nothing
    that could not. Re-rendering the same evaluation must produce the same
    digest, so timestamps of the render itself are excluded.
    """
    return {
        "ref": ev["ref"],
        "standard_id": ev.get("standard_id"),
        "jurisdiction": ev.get("jurisdiction", "IN"),
        "lab": ev.get("lab"),
        "instrument": ev["instrument"],
        "zero_error_g": ev.get("zero_error_g"),
        "observations": [
            {k: o.get(k) for k in (
                "test_code", "variant", "sequence_no", "load_g", "indication_g",
                "delta_load_g", "direction", "position", "elapsed_minutes",
                "weighing_no", "indication_without_g", "indication_with_g",
                "significant_fault_detected", "temperature_c", "rh_percent",
                "voltage_v")}
            for o in ev["observations"] if not o.get("superseded_by")
        ],
        "computed": computed,
    }


def payload_sha256(payload: Dict[str, Any]) -> str:
    """Stable digest: sorted keys, no incidental whitespace."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"),
                   default=str).encode()
    ).hexdigest()
