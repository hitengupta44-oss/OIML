"""
NAWI type-evaluation engine -- core metrology.

Everything here reads from the versioned JSON in ../standards/.
No band value, no operator and no threshold is hardcoded in this module:
swapping the standard files is how you support a revised OIML R 76.
"""

import json
import math
import os
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional

STD_DIR = os.path.join(os.path.dirname(__file__), "..", "standards")


def _load(name):
    with open(os.path.join(STD_DIR, name)) as f:
        return json.load(f)


CLASSES = _load("accuracy-classes.json")
MPE = _load("mpe-bands.json")

_OPS = {
    "ge": lambda a, b: a >= b,
    "gt": lambda a, b: a > b,
    "le": lambda a, b: a <= b,
    "lt": lambda a, b: a < b,
}


def _cmp(value, bound, op):
    """Boundary test honouring the exact operator from the standard.

    The distinction between 'le' and 'lt' at a band edge is the single most
    consequential detail in this file: at exactly 500 e a class III instrument
    is allowed 0.5 e, not 1.0 e.
    """
    if bound is None or op is None:
        return True
    return _OPS[op](value, bound)


# --------------------------------------------------------------------------
# Instrument
# --------------------------------------------------------------------------

@dataclass
class Instrument:
    """A single-range non-automatic weighing instrument under evaluation."""
    manufacturer: str
    model: str
    accuracy_class: str            # OIML symbol: I, II, III, IIII
    max_capacity_g: float          # Max, in grams
    e_g: float                     # verification scale interval, in grams
    d_g: Optional[float] = None    # actual scale interval; defaults to e
    min_capacity_g: Optional[float] = None   # Min; derived if not supplied
    is_grading_instrument: bool = False
    has_auxiliary_indicating_device: bool = False
    jurisdiction: str = "IN"

    def __post_init__(self):
        if self.d_g is None:
            self.d_g = self.e_g
        if self.min_capacity_g is None:
            self.min_capacity_g = self.derived_min_capacity_g()

    # -- derived metrological properties ----------------------------------

    @property
    def n(self) -> float:
        """Number of verification scale intervals, n = Max / e."""
        return self.max_capacity_g / self.e_g

    @property
    def class_def(self):
        for c in CLASSES["classes"]:
            if c["code"] == self.accuracy_class:
                return c
        raise ValueError(f"Unknown accuracy class: {self.accuracy_class}")

    @property
    def class_symbol(self) -> str:
        """Class symbol as it must be printed for this jurisdiction.

        India writes the ordinary class as IV; OIML writes IIII.
        """
        cd = self.class_def
        return cd["symbol_in"] if self.jurisdiction == "IN" else cd["symbol_oiml"]

    def matching_row(self):
        """The Table 3 row whose e-range contains this instrument's e."""
        for row in self.class_def["rows"]:
            if _cmp(self.e_g, row["e_min_g"], row["e_min_op"]) and \
               _cmp(self.e_g, row["e_max_g"], row["e_max_op"]):
                return row
        return None

    def derived_min_capacity_g(self) -> Optional[float]:
        """Min from Table 3. Uses d instead of e when an auxiliary device is fitted (3.4.3)."""
        row = self.matching_row()
        if row is None:
            return None
        if self.is_grading_instrument:
            mult = CLASSES["min_capacity_rules"]["grading_instruments_multiplier"]
        else:
            mult = row["min_capacity_multiplier"]
        interval = self.d_g if self.has_auxiliary_indicating_device else self.e_g
        return mult * interval


# --------------------------------------------------------------------------
# Validation of the declared specification
# --------------------------------------------------------------------------

@dataclass
class Finding:
    level: str      # ERROR | WARNING | INFO
    code: str
    clause: str
    message: str


def validate_instrument(inst: Instrument) -> List[Finding]:
    """Check the declared specification against Table 3 / Table 4 before any test data is entered.

    Catching an invalid e/Max/class combination here saves the lab a full
    test campaign against a specification that could never have been approved.
    """
    out: List[Finding] = []
    cd = inst.class_def
    row = inst.matching_row()

    # -- e must be of the form 1/2/5 x 10^k
    form = CLASSES["scale_interval_form"]
    mantissa = inst.e_g / (10 ** math.floor(math.log10(inst.e_g)))
    if round(mantissa, 6) not in [float(m) for m in form["permitted_mantissas"]]:
        out.append(Finding(
            "ERROR", "E_FORM_INVALID", "3.2 / 4.4.1",
            f"e = {inst.e_g} g is not of the form 1, 2 or 5 x 10^k."))

    # -- e must fall in a permitted range for the class
    if row is None:
        ranges = ", ".join(
            f"{r['e_min_g']} g{'' if r['e_max_g'] is None else ' to ' + str(r['e_max_g']) + ' g'}"
            for r in cd["rows"])
        out.append(Finding(
            "ERROR", "E_OUT_OF_CLASS_RANGE", "3.2 / Table 3",
            f"e = {inst.e_g} g is outside the permitted range for class "
            f"{inst.class_symbol} ({ranges})."))
        return out

    # -- n within the class limits
    n = inst.n
    if n < row["n_min"]:
        lvl = "WARNING" if cd["n_min_has_exception"] else "ERROR"
        msg = (f"n = {n:,.0f} is below the minimum {row['n_min']:,} for class "
               f"{inst.class_symbol}.")
        if cd["n_min_has_exception"]:
            msg += " Permitted only if d < 0.1 mg (3.4.4); confirm and record the justification."
        out.append(Finding(lvl, "N_BELOW_MIN", "3.2 / Table 3", msg))
    if row["n_max"] is not None and n > row["n_max"]:
        out.append(Finding(
            "ERROR", "N_ABOVE_MAX", "3.2 / Table 3",
            f"n = {n:,.0f} exceeds the maximum {row['n_max']:,} for class {inst.class_symbol}."))

    # -- Min at or above the Table 3 lower limit
    derived_min = inst.derived_min_capacity_g()
    if derived_min is not None and inst.min_capacity_g < derived_min - 1e-9:
        interval = "d" if inst.has_auxiliary_indicating_device else "e"
        out.append(Finding(
            "ERROR", "MIN_TOO_LOW", "3.2 / Table 3 / 3.4.3",
            f"Min = {inst.min_capacity_g} g is below the lower limit "
            f"{derived_min} g ({row['min_capacity_multiplier']} {interval})."))

    # -- d / e relationship
    if inst.has_auxiliary_indicating_device:
        if not (inst.d_g < inst.e_g <= 10 * inst.d_g):
            out.append(Finding(
                "ERROR", "E_D_RATIO_INVALID", "3.4.2",
                f"With an auxiliary indicating device, d < e <= 10 d is required; "
                f"got d = {inst.d_g} g, e = {inst.e_g} g."))
        if inst.accuracy_class not in ("I", "II"):
            out.append(Finding(
                "ERROR", "AUX_DEVICE_CLASS", "3.4.1",
                f"Only instruments of classes I and II may be fitted with an auxiliary "
                f"indicating device; this one is class {inst.class_symbol}."))
    else:
        if abs(inst.d_g - inst.e_g) > 1e-12:
            out.append(Finding(
                "ERROR", "E_D_MISMATCH", "3.4.1 / Table 5",
                f"For a graduated instrument without an auxiliary indicating device, "
                f"e = d is required; got e = {inst.e_g} g, d = {inst.d_g} g."))

    if not out:
        out.append(Finding(
            "INFO", "SPEC_OK", "3.2",
            f"Specification conforms: class {inst.class_symbol}, n = {n:,.0f}, "
            f"Min = {inst.min_capacity_g} g."))
    return out


# --------------------------------------------------------------------------
# MPE lookup
# --------------------------------------------------------------------------

def mpe_for_load(inst: Instrument, load_g: float, in_service: bool = False) -> float:
    """Absolute mpe in grams for a given true test load.

    Returns the unsigned value; the permitted interval is +/- this.
    """
    m = load_g / inst.e_g
    for band in MPE["bands"][inst.accuracy_class]:
        if _cmp(m, band["lower"], band["lower_op"]) and \
           _cmp(m, band["upper"], band["upper_op"]):
            mpe = band["mpe_e"] * inst.e_g
            if in_service:
                mpe *= MPE["in_service_multiplier"]
            return mpe
    raise ValueError(
        f"Load {load_g} g = {m:,.1f} e falls outside every mpe band for class "
        f"{inst.class_symbol}. Check that Max does not exceed the class limit.")


def mpe_band_label(inst: Instrument, load_g: float) -> str:
    """Human-readable band, for the 'why this verdict' explainer in the report."""
    m = load_g / inst.e_g
    for band in MPE["bands"][inst.accuracy_class]:
        if _cmp(m, band["lower"], band["lower_op"]) and \
           _cmp(m, band["upper"], band["upper_op"]):
            lo_op = "<=" if band["lower_op"] == "ge" else "<"
            if band["upper"] is None:
                return f"{band['lower']:,} e {lo_op} m"
            hi_op = "<=" if band["upper_op"] == "le" else "<"
            return f"{band['lower']:,} e {lo_op} m {hi_op} {band['upper']:,} e"
    return "out of range"


# --------------------------------------------------------------------------
# Test load point generation (A.4.4.1)
# --------------------------------------------------------------------------

def suggest_test_loads(inst: Instrument, extra_points: int = 2) -> List[dict]:
    """Recommend the loads at which the weighing performance test should be run.

    R 76-1 A.4.4.1 requires at least five loads, including Min, Max, and the
    loads at or near which the mpe changes band. Those breakpoints are exactly
    where a manually-built spreadsheet tends to omit a point -- and exactly
    where a marginal instrument passes or fails.
    """
    pts = []

    def add(load_g, reason):
        if load_g < inst.min_capacity_g - 1e-9 or load_g > inst.max_capacity_g + 1e-9:
            return
        for p in pts:
            if abs(p["load_g"] - load_g) < 1e-9:
                if reason not in p["reason"]:
                    p["reason"] += "; " + reason
                return
        pts.append({"load_g": load_g, "reason": reason})

    add(inst.min_capacity_g, "Min")

    for bp in MPE["derived_rules"]["band_breakpoints_in_e"][inst.accuracy_class]:
        edge = bp * inst.e_g
        if inst.min_capacity_g < edge < inst.max_capacity_g:
            add(edge, f"mpe band edge at {bp:,} e (last load with the lower mpe)")
            add(edge + inst.e_g, f"first load above the {bp:,} e band edge")

    for i in range(1, extra_points + 1):
        add(round(inst.max_capacity_g * i / (extra_points + 1) / inst.e_g) * inst.e_g,
            "intermediate load")

    add(inst.max_capacity_g, "Max")

    pts.sort(key=lambda p: p["load_g"])
    for p in pts:
        p["load_in_e"] = p["load_g"] / inst.e_g
        p["mpe_g"] = mpe_for_load(inst, p["load_g"])
        p["mpe_band"] = mpe_band_label(inst, p["load_g"])
    return pts


# --------------------------------------------------------------------------
# Observation -> verdict
# --------------------------------------------------------------------------

@dataclass
class Observation:
    """One reading in a weighing performance test.

    indication_g : I, what the display showed
    load_g       : L, the true value of the applied test load
    delta_load_g : dL, sum of the small additional weights (each e/10) added
                   until the indication increased by one increment.
                   Leave at 0.0 for an instrument where the rounding error
                   need not be eliminated (d <= 0.2 e, see 3.5.3.2).
    """
    load_g: float
    indication_g: float
    delta_load_g: float = 0.0
    direction: str = "increasing"     # increasing | decreasing
    note: str = ""

    # populated by evaluate()
    P_g: float = field(default=0.0, init=False)
    E_g: float = field(default=0.0, init=False)
    Ec_g: float = field(default=0.0, init=False)
    mpe_g: float = field(default=0.0, init=False)
    mpe_band: str = field(default="", init=False)
    verdict: str = field(default="", init=False)
    margin_e: float = field(default=0.0, init=False)


def evaluate_observations(inst: Instrument,
                          observations: List[Observation],
                          zero_error_g: float = 0.0,
                          in_service: bool = False) -> List[Observation]:
    """Apply A.4.4.3 to each observation and assign a verdict.

        P  = I + 0.5e - dL
        E  = P - L
        Ec = E - E0

    Ec is what gets compared against the mpe.
    """
    for o in observations:
        o.P_g = o.indication_g + 0.5 * inst.e_g - o.delta_load_g
        o.E_g = o.P_g - o.load_g
        o.Ec_g = o.E_g - zero_error_g
        o.mpe_g = mpe_for_load(inst, o.load_g, in_service=in_service)
        o.mpe_band = mpe_band_label(inst, o.load_g)
        o.verdict = "PASS" if abs(o.Ec_g) <= o.mpe_g + 1e-9 else "FAIL"
        o.margin_e = (o.mpe_g - abs(o.Ec_g)) / inst.e_g
    return observations


def check_repeatability(inst: Instrument, indications_g: List[float],
                        load_g: float) -> dict:
    """3.6.1: the spread of repeated weighings of the same load shall not exceed
    the absolute value of the mpe for that load."""
    spread = max(indications_g) - min(indications_g)
    limit = mpe_for_load(inst, load_g)
    return {
        "load_g": load_g,
        "n_weighings": len(indications_g),
        "spread_g": spread,
        "spread_e": spread / inst.e_g,
        "limit_g": limit,
        "verdict": "PASS" if spread <= limit + 1e-9 else "FAIL",
        "clause": MPE["source_clauses"]["repeatability"],
    }


# NOTE: backend/recompute.py does not call this. A.4.7 judges each position
# against the mpe for that load, which is exactly what evaluate_observations
# already does, so recompute.py calls that and folds the verdicts itself.
# This helper remains because it also reports worst_Ec_g, which the report
# does not currently print. If it is ever wired up, delete the inline fold in
# recompute.py rather than keeping two implementations of one clause.
def check_eccentricity(inst: Instrument, observations: List[Observation],
                       zero_error_g: float = 0.0) -> dict:
    """A.4.7: each indication with the load off-centre shall be within the mpe
    for that load."""
    evaluate_observations(inst, observations, zero_error_g)
    return {
        "positions": len(observations),
        "worst_Ec_g": max((abs(o.Ec_g) for o in observations), default=0.0),
        "verdict": "FAIL" if any(o.verdict == "FAIL" for o in observations) else "PASS",
        "observations": observations,
        "clause": "A.4.7",
    }


def fmt(value_g: float, inst: Instrument, unit: str = "g") -> str:
    """Format a mass for the report at the resolution the instrument actually has."""
    if unit == "kg":
        value_g = value_g / 1000.0
    places = max(0, -int(math.floor(math.log10(inst.e_g / (1000.0 if unit == "kg" else 1.0)))))
    q = Decimal(10) ** -places
    return str(Decimal(value_g).quantize(q, rounding=ROUND_HALF_UP))
