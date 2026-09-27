/**
 * NAWI type-evaluation engine — browser build.
 *
 * A direct port of engine/nawi_engine.py. Both read the same
 * standards/*.json, so a band or operator exists in exactly one place.
 *
 * Nothing here hardcodes a threshold: swapping the standards directory is
 * how a revised OIML R 76 is supported.
 */

import accuracyClasses from "../standards/accuracy-classes.json";
import mpeBands from "../standards/mpe-bands.json";

type Op = "ge" | "gt" | "le" | "lt";
type ClassCode = "I" | "II" | "III" | "IIII";
type Jurisdiction = "IN" | "OIML";

const OPS: Record<Op, (a: number, b: number) => boolean> = {
  ge: (a, b) => a >= b,
  gt: (a, b) => a > b,
  le: (a, b) => a <= b,
  lt: (a, b) => a < b,
};

/**
 * Boundary test honouring the exact operator from the standard.
 *
 * The distinction between `le` and `lt` at a band edge is the single most
 * consequential detail in this file: at exactly 500 e a class III instrument
 * is allowed 0.5 e, not 1.0 e.
 */
function cmp(value: number, bound: number | null, op: Op | null): boolean {
  if (bound === null || op === null) return true;
  return OPS[op](value, bound);
}

export interface Instrument {
  manufacturer: string;
  model: string;
  serial?: string;
  accuracyClass: ClassCode;
  maxCapacityG: number;
  eG: number;
  dG?: number;
  minCapacityG?: number;
  isGradingInstrument?: boolean;
  hasAuxiliaryIndicatingDevice?: boolean;
  jurisdiction?: Jurisdiction;
}

export interface Finding {
  level: "ERROR" | "WARNING" | "INFO";
  code: string;
  clause: string;
  message: string;
}

export interface Observation {
  loadG: number;
  indicationG: number | null;
  deltaLoadG?: number;
  direction?: "increasing" | "decreasing";
  position?: string;
}

export interface EvaluatedObservation extends Observation {
  pending: boolean;
  P?: number;
  E?: number;
  Ec?: number;
  mpeG?: number;
  mpeBand?: string;
  verdict?: "PASS" | "FAIL";
  marginE?: number;
  marginal?: boolean;
}

// ---------------------------------------------------------------------------

const d = (i: Instrument) => i.dG ?? i.eG;

export function classDef(i: Instrument) {
  const c = accuracyClasses.classes.find((x: any) => x.code === i.accuracyClass);
  if (!c) throw new Error(`Unknown accuracy class: ${i.accuracyClass}`);
  return c as any;
}

/** Class symbol as it must be printed. India writes IV where OIML writes IIII. */
export function classSymbol(i: Instrument): string {
  const c = classDef(i);
  return (i.jurisdiction ?? "IN") === "IN" ? c.symbol_in : c.symbol_oiml;
}

export const n = (i: Instrument) => i.maxCapacityG / i.eG;

/** The Table 3 row whose e-range contains this instrument's e. */
export function matchingRow(i: Instrument) {
  return (
    classDef(i).rows.find(
      (r: any) =>
        cmp(i.eG, r.e_min_g, r.e_min_op) && cmp(i.eG, r.e_max_g, r.e_max_op)
    ) ?? null
  );
}

/** Min from Table 3. Uses d instead of e when an auxiliary device is fitted (3.4.3). */
export function derivedMinCapacityG(i: Instrument): number | null {
  const row = matchingRow(i);
  if (!row) return null;
  const mult = i.isGradingInstrument
    ? accuracyClasses.min_capacity_rules.grading_instruments_multiplier
    : row.min_capacity_multiplier;
  return mult * (i.hasAuxiliaryIndicatingDevice ? d(i) : i.eG);
}

// ---------------------------------------------------------------------------

export function validateInstrument(i: Instrument): Finding[] {
  const out: Finding[] = [];
  const cd = classDef(i);
  const row = matchingRow(i);
  const F = (
    level: Finding["level"],
    code: string,
    clause: string,
    message: string
  ) => out.push({ level, code, clause, message });

  const mantissa = i.eG / Math.pow(10, Math.floor(Math.log10(i.eG)));
  if (!accuracyClasses.scale_interval_form.permitted_mantissas
        .includes(Number(mantissa.toFixed(6)))) {
    F("ERROR", "E_FORM_INVALID", "3.2 / 4.4.1",
      `e = ${i.eG} g is not of the form 1, 2 or 5 × 10ᵏ.`);
  }

  if (!row) {
    const ranges = cd.rows
      .map((r: any) =>
        `${r.e_min_g} g${r.e_max_g === null ? " and above" : ` to ${r.e_max_g} g`}`)
      .join(", ");
    F("ERROR", "E_OUT_OF_CLASS_RANGE", "3.2 / Table 3",
      `e = ${i.eG} g is outside the permitted range for class ${classSymbol(i)} (${ranges}).`);
    return out;
  }

  const nn = n(i);
  if (nn < row.n_min) {
    F(cd.n_min_has_exception ? "WARNING" : "ERROR", "N_BELOW_MIN", "3.2 / Table 3",
      `n = ${nn.toLocaleString()} is below the minimum ${row.n_min.toLocaleString()} ` +
      `for class ${classSymbol(i)}.` +
      (cd.n_min_has_exception
        ? " Permitted only if d < 0.1 mg (3.4.4); record the justification."
        : ""));
  }
  if (row.n_max !== null && nn > row.n_max) {
    F("ERROR", "N_ABOVE_MAX", "3.2 / Table 3",
      `n = ${nn.toLocaleString()} exceeds the maximum ${row.n_max.toLocaleString()} for class ${classSymbol(i)}.`);
  }

  const derived = derivedMinCapacityG(i);
  const min = i.minCapacityG ?? derived ?? 0;
  if (derived !== null && min < derived - 1e-9) {
    F("ERROR", "MIN_TOO_LOW", "3.2 / Table 3 / 3.4.3",
      `Min = ${min} g is below the lower limit ${derived} g ` +
      `(${row.min_capacity_multiplier} ${i.hasAuxiliaryIndicatingDevice ? "d" : "e"}).`);
  }

  if (i.hasAuxiliaryIndicatingDevice) {
    if (!(d(i) < i.eG && i.eG <= 10 * d(i))) {
      F("ERROR", "E_D_RATIO_INVALID", "3.4.2",
        `With an auxiliary indicating device, d < e ≤ 10 d is required; got d = ${d(i)} g, e = ${i.eG} g.`);
    }
    if (!["I", "II"].includes(i.accuracyClass)) {
      F("ERROR", "AUX_DEVICE_CLASS", "3.4.1",
        `Only instruments of classes I and II may be fitted with an auxiliary indicating device; this one is class ${classSymbol(i)}.`);
    }
  } else if (Math.abs(d(i) - i.eG) > 1e-12) {
    F("ERROR", "E_D_MISMATCH", "3.4.1 / Table 5",
      `For a graduated instrument without an auxiliary indicating device, e = d is required; got e = ${i.eG} g, d = ${d(i)} g.`);
  }

  if (out.length === 0) {
    F("INFO", "SPEC_OK", "3.2",
      `Specification conforms: class ${classSymbol(i)}, n = ${nn.toLocaleString()}, Min = ${min} g.`);
  }
  return out;
}

// ---------------------------------------------------------------------------

/** Absolute mpe in grams for a given true test load. Unsigned; the interval is ±. */
export function mpeForLoad(i: Instrument, loadG: number, inService = false): number {
  const m = loadG / i.eG;
  for (const b of (mpeBands as any).bands[i.accuracyClass]) {
    if (cmp(m, b.lo ?? b.lower, b.loOp ?? b.lower_op) &&
        cmp(m, b.hi ?? b.upper, b.hiOp ?? b.upper_op)) {
      const mpe = (b.mpe ?? b.mpe_e) * i.eG;
      return inService ? mpe * (mpeBands as any).in_service_multiplier : mpe;
    }
  }
  throw new Error(
    `Load ${loadG} g = ${m.toLocaleString()} e falls outside every mpe band for ` +
    `class ${classSymbol(i)}. Check that Max does not exceed the class limit.`
  );
}

/** Human-readable band, for the "why this verdict" explainer. */
export function mpeBandLabel(i: Instrument, loadG: number): string {
  const m = loadG / i.eG;
  for (const b of (mpeBands as any).bands[i.accuracyClass]) {
    const lo = b.lo ?? b.lower, hi = b.hi ?? b.upper;
    const loOp = b.loOp ?? b.lower_op, hiOp = b.hiOp ?? b.upper_op;
    if (cmp(m, lo, loOp) && cmp(m, hi, hiOp)) {
      const l = loOp === "ge" ? "≤" : "<";
      if (hi === null) return `${lo.toLocaleString()} e ${l} m`;
      return `${lo.toLocaleString()} e ${l} m ${hiOp === "le" ? "≤" : "<"} ${hi.toLocaleString()} e`;
    }
  }
  return "out of range";
}

// ---------------------------------------------------------------------------

/**
 * Recommend the loads at which the weighing performance test should be run.
 *
 * A.4.4.1 requires at least five loads including Min, Max, and the loads at
 * which the mpe changes band. Those breakpoints are exactly where a manually
 * built spreadsheet tends to omit a point, and exactly where a marginal
 * instrument passes or fails.
 */
export function suggestTestLoads(i: Instrument, extraPoints = 2) {
  const min = i.minCapacityG ?? derivedMinCapacityG(i) ?? 0;
  const pts: { loadG: number; reason: string }[] = [];

  const add = (loadG: number, reason: string) => {
    if (loadG < min - 1e-9 || loadG > i.maxCapacityG + 1e-9) return;
    const hit = pts.find((p) => Math.abs(p.loadG - loadG) < 1e-9);
    if (hit) {
      if (!hit.reason.includes(reason)) hit.reason += "; " + reason;
      return;
    }
    pts.push({ loadG, reason });
  };

  add(min, "Min");
  for (const bp of (mpeBands as any).derived_rules
        .band_breakpoints_in_e[i.accuracyClass] as number[]) {
    const edge = bp * i.eG;
    if (edge > min && edge < i.maxCapacityG) {
      add(edge, `mpe band edge at ${bp.toLocaleString()} e (last load with the lower mpe)`);
      add(edge + i.eG, `first load above the ${bp.toLocaleString()} e band edge`);
    }
  }
  for (let k = 1; k <= extraPoints; k++) {
    add(Math.round((i.maxCapacityG * k) / (extraPoints + 1) / i.eG) * i.eG,
        "intermediate load");
  }
  add(i.maxCapacityG, "Max");

  pts.sort((a, b) => a.loadG - b.loadG);
  return pts.map((p) => ({
    ...p,
    loadInE: p.loadG / i.eG,
    mpeG: mpeForLoad(i, p.loadG),
    mpeBand: mpeBandLabel(i, p.loadG),
  }));
}

// ---------------------------------------------------------------------------

/**
 * Apply A.4.4.3 and assign a verdict.
 *
 *   P  = I + ½e − ΔL
 *   E  = P − L
 *   Ec = E − E₀
 *
 * Ec is what gets compared against the mpe.
 */
export function evaluateObservations(
  i: Instrument,
  observations: Observation[],
  zeroErrorG = 0,
  inService = false
): EvaluatedObservation[] {
  return observations.map((o) => {
    if (o.indicationG === null || o.indicationG === undefined || Number.isNaN(o.indicationG)) {
      return { ...o, pending: true };
    }
    const P = o.indicationG + 0.5 * i.eG - (o.deltaLoadG ?? 0);
    const E = P - o.loadG;
    const Ec = E - zeroErrorG;
    const mpeG = mpeForLoad(i, o.loadG, inService);
    const verdict: "PASS" | "FAIL" = Math.abs(Ec) <= mpeG + 1e-9 ? "PASS" : "FAIL";
    const marginE = (mpeG - Math.abs(Ec)) / i.eG;
    return {
      ...o, pending: false, P, E, Ec, mpeG,
      mpeBand: mpeBandLabel(i, o.loadG),
      verdict, marginE,
      marginal: verdict === "PASS" && marginE < 0.1,
    };
  });
}

/**
 * 3.6.1 — the spread of repeated weighings of the same load shall not exceed
 * the absolute value of the mpe for that load.
 */
export function checkRepeatability(i: Instrument, indicationsG: number[], loadG: number) {
  const v = indicationsG.filter((x) => x !== null && !Number.isNaN(x));
  if (v.length < 2) return { pending: true as const };
  const spread = Math.max(...v) - Math.min(...v);
  const limit = mpeForLoad(i, loadG);
  return {
    pending: false as const,
    loadG, nWeighings: v.length, spread,
    spreadE: spread / i.eG, limit,
    verdict: (spread <= limit + 1e-9 ? "PASS" : "FAIL") as "PASS" | "FAIL",
    clause: (mpeBands as any).source_clauses.repeatability,
  };
}

/**
 * Annex B disturbances use a different rule from the weighing tests: the
 * difference with and without the disturbance shall not exceed 1 e, or the
 * instrument shall detect and react to a significant fault.
 */
export function checkDisturbance(
  i: Instrument,
  indicationWithoutG: number,
  indicationWithG: number,
  significantFaultDetected: boolean
) {
  const difference = Math.abs(indicationWithG - indicationWithoutG);
  const limit = i.eG;
  const withinLimit = difference <= limit + 1e-9;
  return {
    difference, differenceE: difference / i.eG, limit,
    withinLimit, significantFaultDetected,
    verdict: (withinLimit || significantFaultDetected ? "PASS" : "FAIL") as "PASS" | "FAIL",
    clause: "B.3",
  };
}
