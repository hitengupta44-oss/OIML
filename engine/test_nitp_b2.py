"""
Second known-answer anchor: NITP 6.1 to 6.4, Appendix B.2.

The first anchor (test_harness.py) is a published *approval* -- it fixes the
instrument's derived properties. This one is a published *worked example*: the
Australian National Measurement Institute's own National Instrument Test
Procedures print a complete substitution-load weighing test for a class 3
weighbridge, with every indication, every additional load and every computed
error shown. Reproducing that arithmetic exactly is the strongest correctness
argument available short of a real filled-in RRSL report.

    NITP 6.1 to 6.4, First edition, second revision, January 2014
    Appendix B.2, "Weighing Performance using Substitution Load Material"

Three things are anchored here:

  1. The mpe at seven loads, including 10 t.
  2. The error formula E = I + 0.5e - dL - L (NITP clause 5.4.2, step 8),
     which is the same relation this project writes as P = I + 1/2 e - dL,
     E = P - L. An independent regulator stating it identically is worth more
     than our own derivation.
  3. The substitution formula L_sub = I_sub + 0.5e - E (clause 5.4.2 A(e)).

Why the 10 t row is the important one
-------------------------------------
At e = 0.02 t, a load of 10 t is exactly 500 e. NMI prints its mpe as
+/- 0.01 t, which is 0.5 e -- so 500 e falls in the FIRST band. That is only
true if the second band opens at strictly greater than 500, exactly as
mpe-bands.json records it.

Anyone transcribing Table 6 from the Indian Kanoon rendering of the Seventh
Schedule gets overlapping bands and would print +/- 0.02 t here. This single
row is independent, third-party confirmation that the operators in
mpe-bands.json are right and that rendering is corrupt.

    python3 engine/test_nitp_b2.py
"""

from nawi_engine import Instrument, mpe_for_load

T = 1_000_000.0          # grams in a tonne
E_T = 0.02               # verification scale interval, in tonnes

PASSED = []
FAILED = []


def check(label, got, want, tol=1e-9):
    ok = abs(got - want) <= tol if isinstance(want, (int, float)) else got == want
    (PASSED if ok else FAILED).append(label)
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}: got {got}, want {want}")


def rule(title):
    print("\n" + title)
    print("-" * len(title))


weighbridge = Instrument(
    manufacturer="(NITP 6.1 to 6.4, Appendix B.2)",
    model="class 3 static weighbridge",
    accuracy_class="III",
    max_capacity_g=60 * T,
    e_g=E_T * T,
    d_g=E_T * T,
    min_capacity_g=0.4 * T,
)


# ==========================================================================
rule("1. mpe at every load NMI prints in the Appendix B.2 report")
# ==========================================================================
# (load in tonnes, mpe in tonnes as printed by NMI)
for load_t, want_t in [
    (0.40,  0.01),       # 20 e      -> 0.5 e
    (10.00, 0.01),       # 500 e     -> 0.5 e   <-- the band edge
    (16.00, 0.02),       # 800 e     -> 1.0 e
    (31.78, 0.02),       # 1 589 e   -> 1.0 e
    (39.08, 0.02),       # 1 954 e   -> 1.0 e
    (47.08, 0.03),       # 2 354 e   -> 1.5 e
    (59.86, 0.03),       # 2 993 e   -> 1.5 e
]:
    check(f"mpe at {load_t:g} t ({load_t / E_T:,.0f} e)",
          mpe_for_load(weighbridge, load_t * T) / T, want_t)


# ==========================================================================
rule("2. The band edge, stated on its own because it is the whole argument")
# ==========================================================================
check("500 e lands in the first band, so mpe = 0.5 e",
      mpe_for_load(weighbridge, 10 * T) / T, 0.5 * E_T)
check("just past it, 500.5 e, mpe steps to 1.0 e",
      mpe_for_load(weighbridge, 10.01 * T) / T, 1.0 * E_T)


# ==========================================================================
rule("3. E = I + 0.5e - dL - L   (NITP clause 5.4.2, step 8)")
# ==========================================================================
def error_t(indication_t, delta_load_t, load_t):
    """The project computes P = I + 1/2 e - dL, then E = P - L."""
    return (indication_t + 0.5 * E_T - delta_load_t) - load_t


for L, I, dL, want in [
    (16.00, 16.02, 0.012, +0.018),
    (31.78, 31.80, 0.014, +0.016),
    (39.08, 39.10, 0.016, +0.014),
    (47.08, 47.10, 0.016, +0.014),
    (59.86, 59.88, 0.018, +0.012),
]:
    check(f"E at L = {L:g} t (I = {I:g}, dL = {dL:g})", error_t(I, dL, L), want)


# ==========================================================================
rule("4. L_sub = I_sub + 0.5e - E   (NITP clause 5.4.2, method A(e))")
# ==========================================================================
for I_sub, E, want in [
    (15.78, 0.018, 15.772),      # Sub 1, test rig + forklift
    (31.08, 0.016, 31.074),      # Sub 2, gravel truck
    (46.86, 0.014, 46.856),      # Sub 3, Sub 1 + Sub 2
]:
    check(f"L_sub from I_sub = {I_sub:g} t, E = {E:+g} t",
          I_sub + 0.5 * E_T - E, want)


# ==========================================================================
rule("5. Every load in the report is inside its mpe, as NMI records")
# ==========================================================================
# NMI marks every weighing row "pass". The errors above must therefore all
# sit within the mpe for their load -- a end-to-end check of both tables.
for L, I, dL in [(16.00, 16.02, 0.012), (31.78, 31.80, 0.014),
                 (39.08, 39.10, 0.016), (47.08, 47.10, 0.016),
                 (59.86, 59.88, 0.018)]:
    E = error_t(I, dL, L)
    mpe = mpe_for_load(weighbridge, L * T) / T
    check(f"|E| <= mpe at {L:g} t", abs(E) <= mpe + 1e-9, True)


# ==========================================================================
print("\n" + "=" * 62)
print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
for f in FAILED:
    print(f"    FAILED: {f}")
print("=" * 62)
