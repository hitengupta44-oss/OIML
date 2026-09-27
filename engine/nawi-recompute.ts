/**
 * Fold a set of observations into one verdict per test, and one overall.
 *
 * The TypeScript twin of backend/recompute.py. Same tests, same order, same
 * folding rule, same clause references.
 *
 * Why this file exists
 * --------------------
 * It did not, and that was a real defect. The browser computed its headline
 * verdict from the weighing observations alone while the server folded in all
 * five tests, so a report whose eccentricity failed showed CONFORMS on screen
 * and DOES NOT CONFORM in the signed PDF. On the seed dataset that was 34
 * evaluations out of 120 -- and /verify never caught it, because it compares
 * only per-observation weighing verdicts.
 *
 * Two engines in two languages will agree on a value only if they run the same
 * steps. nawi-engine.ts and nawi_engine.py already guaranteed that for a single
 * observation. This file extends the guarantee to the whole evaluation, and
 * engine/test_parity.py asserts the two stay in step.
 *
 * The folding rule, from recompute.py:
 *
 *   a test   FAIL if any row fails, PASS if it has rows, otherwise PENDING
 *   overall  FAIL if the specification is invalid or any test fails,
 *            PENDING if any test is pending, otherwise PASS
 *
 * PENDING is not a soft pass. A report with tests still unrecorded cannot
 * conform, and the conclusion in the DOCX says so in as many words.
 */

import {
  evaluateObservations, checkRepeatability, mpeForLoad,
  validateInstrument, type Instrument, type Observation,
} from "./nawi-engine";

export type Verdict = "PASS" | "FAIL" | "PENDING";

/** An observation as stored, before it is sorted by test. */
export interface StoredObservation {
  test_code: string;
  load_g?: number | string | null;
  indication_g?: number | string | null;
  delta_load_g?: number | string | null;
  direction?: string | null;
  position?: string | null;
  elapsed_minutes?: number | null;
  indication_without_g?: number | string | null;
  indication_with_g?: number | string | null;
  significant_fault_detected?: boolean | null;
  severity?: { disturbance?: string } | null;
  variant?: string | null;
  superseded_by?: number | null;
}

export interface TestResult {
  clause: string;
  verdict: Verdict;
  /** How many rows this test was decided on. Zero means PENDING. */
  count: number;
  failed: number;
}

export interface Recomputed {
  tests: Record<string, TestResult>;
  specificationValid: boolean;
  verdict: Verdict;
  /** Tests with no observations yet -- what still has to be done. */
  pending: string[];
  /** Tests with at least one failing row. */
  failing: string[];
}

const num = (v: unknown, fallback = 0): number => {
  const n = typeof v === "number" ? v : parseFloat(String(v ?? ""));
  return Number.isFinite(n) ? n : fallback;
};

/** Same shape of answer as recompute.py, for one set of rows. */
function fold(clause: string, verdicts: Verdict[]): TestResult {
  const failed = verdicts.filter((v) => v === "FAIL").length;
  return {
    clause,
    count: verdicts.length,
    failed,
    verdict: failed > 0 ? "FAIL" : verdicts.length > 0 ? "PASS" : "PENDING",
  };
}

/**
 * The clause references are the report's, so a verdict shown on screen can be
 * traced to the same clause the PDF prints. Order matches recompute.py.
 */
const CLAUSES: Record<string, string> = {
  weighing_performance: "A.4.4.1, A.4.4.3",
  repeatability: "3.6.1",
  eccentricity: "A.4.7",
  time_dependence: "A.4.11.1",
  electrical_disturbances: "B.3",
};

export function recompute(
  inst: Instrument,
  observations: StoredObservation[],
  zeroErrorG = 0
): Recomputed {
  const obs = observations.filter((o) => !o.superseded_by);
  const of = (code: string) => obs.filter((o) => o.test_code === code);

  const findings = validateInstrument(inst);
  const specificationValid = !findings.some((f) => f.level === "ERROR");

  const tests: Record<string, TestResult> = {};

  // -- weighing performance -------------------------------------------
  const weigh: Observation[] = of("weighing_performance").map((o) => ({
    loadG: num(o.load_g),
    indicationG: o.indication_g == null ? null : num(o.indication_g),
    deltaLoadG: num(o.delta_load_g),
    direction: (o.direction as Observation["direction"]) ?? "increasing",
  }));
  // A row with no indication yet is not a failure, it is unrecorded.
  const weighDone = evaluateObservations(inst, weigh, zeroErrorG)
    .filter((r) => !r.pending);
  tests.weighing_performance = fold(
    CLAUSES.weighing_performance,
    weighDone.map((r) => r.verdict as Verdict)
  );

  // -- repeatability ---------------------------------------------------
  // Grouped by load, exactly as recompute.py does: one series per load.
  const series = new Map<number, number[]>();
  for (const o of of("repeatability")) {
    if (o.indication_g == null) continue;
    const load = num(o.load_g);
    series.set(load, [...(series.get(load) ?? []), num(o.indication_g)]);
  }
  tests.repeatability = fold(
    CLAUSES.repeatability,
    [...series.entries()]
      .sort((a, b) => a[0] - b[0])
      .flatMap(([load, vals]) => {
        // Fewer than two weighings is not a series yet: the engine returns
        // pending, and a pending series must not be folded in as a PASS.
        const r = checkRepeatability(inst, vals, load);
        return r.pending ? [] : [r.verdict as Verdict];
      })
  );

  // -- eccentricity ----------------------------------------------------
  // A.4.7 judges each position against the mpe for that load, which is the
  // same comparison as a weighing row -- so the same function decides it.
  const ecc: Observation[] = of("eccentricity")
    .filter((o) => o.indication_g != null)
    .map((o) => ({
      loadG: num(o.load_g),
      indicationG: num(o.indication_g),
      deltaLoadG: num(o.delta_load_g),
    }));
  tests.eccentricity = fold(
    CLAUSES.eccentricity,
    evaluateObservations(inst, ecc, zeroErrorG)
      .filter((r) => !r.pending)
      .map((r) => r.verdict as Verdict)
  );

  // -- time-dependence (creep) -----------------------------------------
  // A.4.11.1: within the first 30 minutes the indication shall not differ
  // from the first reading by more than 0.5 e.
  const creepRows = of("time_dependence")
    .filter((o) => o.indication_g != null)
    .sort((a, b) => (a.elapsed_minutes ?? 0) - (b.elapsed_minutes ?? 0));
  if (creepRows.length === 0) {
    tests.time_dependence = fold(CLAUSES.time_dependence, []);
  } else {
    const base = num(creepRows[0].indication_g);
    let worst30 = 0;
    for (const o of creepRows) {
      if ((o.elapsed_minutes ?? 0) <= 30) {
        worst30 = Math.max(worst30, Math.abs(num(o.indication_g) - base));
      }
    }
    const pass = worst30 <= 0.5 * inst.eG + 1e-9;
    tests.time_dependence = {
      clause: CLAUSES.time_dependence,
      count: creepRows.length,
      failed: pass ? 0 : 1,
      verdict: pass ? "PASS" : "FAIL",
    };
  }

  // -- electrical disturbances -----------------------------------------
  // Annex B uses a different rule from the weighing tests: the difference
  // with and without the disturbance shall not exceed 1 e, OR the
  // instrument shall detect and react to a significant fault.
  const dist = of("electrical_disturbances")
    .filter((o) => o.indication_with_g != null && o.indication_without_g != null)
    .map((o): Verdict => {
      const diff = Math.abs(num(o.indication_with_g) - num(o.indication_without_g));
      const ok = diff <= inst.eG + 1e-9 || Boolean(o.significant_fault_detected);
      return ok ? "PASS" : "FAIL";
    });
  tests.electrical_disturbances = fold(CLAUSES.electrical_disturbances, dist);

  // -- overall ----------------------------------------------------------
  const all = Object.values(tests);
  const verdict: Verdict =
    !specificationValid || all.some((t) => t.verdict === "FAIL")
      ? "FAIL"
      : all.some((t) => t.verdict === "PENDING")
        ? "PENDING"
        : "PASS";

  return {
    tests,
    specificationValid,
    verdict,
    pending: Object.keys(tests).filter((k) => tests[k].verdict === "PENDING"),
    failing: Object.keys(tests).filter((k) => tests[k].verdict === "FAIL"),
  };
}

/** Report-facing titles, so the UI and the DOCX name a test identically. */
export const TEST_TITLES: Record<string, string> = {
  weighing_performance: "Weighing performance",
  repeatability: "Repeatability",
  eccentricity: "Eccentricity",
  time_dependence: "Time-dependence (creep)",
  electrical_disturbances: "Electrical disturbances",
};

/** Used by the capture UI: the mpe the instrument must meet at a load. */
export const mpeAt = (inst: Instrument, loadG: number) => mpeForLoad(inst, loadG);
