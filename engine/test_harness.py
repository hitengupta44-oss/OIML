"""
Known-answer tests for the NAWI engine.

The anchor case is a real, published approval: the Ishida MS-5060S, approved
by Australia's National Standards Commission under Certificate 6/4C/86 as a
self-indicating weighing instrument with Max = 60 kg and e = 0.02 kg.
If the engine derives the same class, n and Min, and the same mpe at each
load, the transcription of Tables 3 and 6 is correct.
"""

from nawi_engine import (
    Instrument, Observation, validate_instrument, mpe_for_load,
    mpe_band_label, suggest_test_loads, evaluate_observations,
    check_repeatability,
)

PASSED = []
FAILED = []


def check(label, got, want, tol=1e-9):
    ok = abs(got - want) <= tol if isinstance(want, (int, float)) else got == want
    (PASSED if ok else FAILED).append(label)
    flag = "ok  " if ok else "FAIL"
    print(f"  [{flag}] {label}: got {got}, want {want}")


def rule(title):
    print("\n" + title)
    print("-" * len(title))


# ==========================================================================
rule("1. Ishida MS-5060S  (NMI Certificate 6/4C/86)  -- derived properties")
# ==========================================================================

ishida = Instrument(
    manufacturer="Ishida Co. Ltd",
    model="MS-5060S",
    accuracy_class="III",
    max_capacity_g=60_000.0,      # 60 kg
    e_g=20.0,                     # 0.02 kg
    jurisdiction="IN",
)

check("n = Max/e", ishida.n, 3000)
check("class symbol printed for India", ishida.class_symbol, "III")
check("Min derived (20 e)", ishida.min_capacity_g, 400.0)   # 0.4 kg

print("\n  Specification validation:")
for f in validate_instrument(ishida):
    print(f"    {f.level:7} {f.code:22} [{f.clause}]  {f.message}")


# ==========================================================================
rule("2. mpe at each band, class III, e = 20 g")
# ==========================================================================
# Class III bands:  0..500 e -> 0.5 e ;  500..2000 e -> 1.0 e ;  2000..10000 e -> 1.5 e
# In grams with e = 20 g:   0..10 kg -> 10 g ;  10..40 kg -> 20 g ;  40..60 kg -> 30 g

check("mpe at Min (400 g = 20 e)",        mpe_for_load(ishida, 400), 10.0)
check("mpe at 5 kg (250 e)",              mpe_for_load(ishida, 5_000), 10.0)
check("mpe AT the 500 e edge (10 kg)",    mpe_for_load(ishida, 10_000), 10.0)
check("mpe just above 500 e (10.02 kg)",  mpe_for_load(ishida, 10_020), 20.0)
check("mpe AT the 2000 e edge (40 kg)",   mpe_for_load(ishida, 40_000), 20.0)
check("mpe just above 2000 e (40.02 kg)", mpe_for_load(ishida, 40_020), 30.0)
check("mpe at Max (60 kg = 3000 e)",      mpe_for_load(ishida, 60_000), 30.0)
check("in-service mpe at Max is doubled",
      mpe_for_load(ishida, 60_000, in_service=True), 60.0)

print("\n  Band labels:")
for load in (400, 10_000, 10_020, 40_000, 60_000):
    print(f"    {load/1000:>6.2f} kg  ->  {mpe_band_label(ishida, load)}")


# ==========================================================================
rule("3. Test load points auto-derived from Max, e and class")
# ==========================================================================

print(f"  {'load (kg)':>10}  {'in e':>8}  {'mpe (g)':>8}   reason")
for p in suggest_test_loads(ishida):
    print(f"  {p['load_g']/1000:>10.2f}  {p['load_in_e']:>8,.0f}  "
          f"{p['mpe_g']:>8.1f}   {p['reason']}")


# ==========================================================================
rule("4. Error computation A.4.4.3:  P = I + 0.5e - dL,  E = P - L,  Ec = E - E0")
# ==========================================================================
# Worked case: true load 10.00 kg, display reads 10.00 kg, and 0.14 kg of
# e/10 weights (7 x 2 g) had to be added before the display flicked to 10.02.
#   P  = 10000 + 10 - 140 = 9870 g
#   E  = 9870 - 10000     = -130 g
# Zero error E0 = -2 g  ->  Ec = -128 g, against an mpe of 10 g  ->  FAIL.

obs = [Observation(load_g=10_000.0, indication_g=10_000.0, delta_load_g=140.0)]
evaluate_observations(ishida, obs, zero_error_g=-2.0)
o = obs[0]
check("P  (corrected indication)", o.P_g, 9_870.0)
check("E  (error)",                o.E_g, -130.0)
check("Ec (zero-corrected error)", o.Ec_g, -128.0)
check("verdict",                   o.verdict, "FAIL")

# A clean instrument at the same load.
obs2 = [Observation(load_g=10_000.0, indication_g=10_000.0, delta_load_g=8.0)]
evaluate_observations(ishida, obs2, zero_error_g=0.0)
check("clean instrument Ec", obs2[0].Ec_g, 2.0)
check("clean instrument verdict", obs2[0].verdict, "PASS")


# ==========================================================================
rule("5. The band-edge trap -- identical error, opposite verdicts")
# ==========================================================================
# Ec = 15 g at exactly 500 e (10 kg) vs at 500 e + 1 e (10.02 kg).
# At the edge the mpe is still 0.5 e = 10 g, so 15 g FAILS.
# One increment higher the mpe becomes 1.0 e = 20 g, so 15 g PASSES.
# A spreadsheet using ">= 500" instead of "> 500" gets the first one wrong.

edge = [Observation(load_g=10_000.0, indication_g=10_015.0, delta_load_g=10.0),
        Observation(load_g=10_020.0, indication_g=10_035.0, delta_load_g=10.0)]
evaluate_observations(ishida, edge)
check("Ec at 500 e",            edge[0].Ec_g, 15.0)
check("verdict at 500 e",       edge[0].verdict, "FAIL")
check("Ec at 501 e",            edge[1].Ec_g, 15.0)
check("verdict at 501 e",       edge[1].verdict, "PASS")


# ==========================================================================
rule("6. Repeatability (3.6.1) at ~50% of Max")
# ==========================================================================

rep = check_repeatability(ishida, [30_000, 30_020, 30_000, 30_020, 30_000, 30_000],
                          load_g=30_000)
print(f"  spread = {rep['spread_g']} g ({rep['spread_e']:.2f} e), "
      f"limit = {rep['limit_g']} g  ->  {rep['verdict']}")
check("repeatability verdict", rep["verdict"], "PASS")

rep2 = check_repeatability(ishida, [30_000, 30_060, 30_000, 30_020],
                           load_g=30_000)
check("repeatability fails on a 60 g spread", rep2["verdict"], "FAIL")


# ==========================================================================
rule("7. Specification validation catches bad declarations")
# ==========================================================================

bad_n = Instrument("Acme", "XL-9000", "III", max_capacity_g=60_000.0, e_g=2.0)
print("  Class III, Max 60 kg, e = 2 g  ->  n = 30 000:")
for f in validate_instrument(bad_n):
    print(f"    {f.level:7} {f.code:22} {f.message}")
check("n above class III max is an ERROR",
      any(f.code == "N_ABOVE_MAX" and f.level == "ERROR" for f in validate_instrument(bad_n)),
      True)

bad_e = Instrument("Acme", "XL-3", "IIII", max_capacity_g=10_000.0, e_g=1.0)
print("\n  Class IIII with e = 1 g (class IIII requires e >= 5 g):")
for f in validate_instrument(bad_e):
    print(f"    {f.level:7} {f.code:22} {f.message}")
check("e below class IIII range is an ERROR",
      any(f.code == "E_OUT_OF_CLASS_RANGE" for f in validate_instrument(bad_e)), True)

bad_form = Instrument("Acme", "XL-7", "III", max_capacity_g=30_000.0, e_g=3.0)
print("\n  e = 3 g (not of the form 1/2/5 x 10^k):")
for f in validate_instrument(bad_form):
    print(f"    {f.level:7} {f.code:22} {f.message}")
check("e = 3 g rejected", any(f.code == "E_FORM_INVALID" for f in validate_instrument(bad_form)), True)


# ==========================================================================
rule("8. Indian class symbol deviation (IIII -> IV)")
# ==========================================================================

ord_in = Instrument("Acme", "Bulk-500", "IIII", max_capacity_g=500_000.0,
                    e_g=1000.0, jurisdiction="IN")
ord_oiml = Instrument("Acme", "Bulk-500", "IIII", max_capacity_g=500_000.0,
                      e_g=1000.0, jurisdiction="OIML")
check("India prints class IV", ord_in.class_symbol, "IV")
check("OIML prints class IIII", ord_oiml.class_symbol, "IIII")
check("n = 500 for both", ord_in.n, 500)
check("Min = 10 e", ord_in.min_capacity_g, 10_000.0)
check("mpe at Max (500 e) is 1.5 e", mpe_for_load(ord_in, 500_000), 1500.0)


# ==========================================================================
print("\n" + "=" * 62)
print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
if FAILED:
    for f in FAILED:
        print(f"    FAILED: {f}")
print("=" * 62)
