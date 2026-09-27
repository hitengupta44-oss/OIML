/**
 * Run the TypeScript engine over the seed dataset and print one JSON line
 * per evaluation. test_parity.py runs the Python engine over the same data
 * and compares, verdict by verdict.
 *
 * This is not a test on its own -- it is the TypeScript half of one. It is
 * bundled by esbuild and executed by node; see engine/test_parity.py.
 */

import { readFileSync } from "node:fs";
import { recompute } from "./nawi-recompute";
import { displayUnit, toGrams, fromGrams } from "./nawi-engine";
import type { Instrument } from "./nawi-engine";
import type { StoredObservation } from "./nawi-recompute";

/**
 * Unit round-trip, checked before anything else.
 *
 * A screen enters readings in the instrument's own unit; the engine stores
 * grams. This crossing was once made in two places and made inconsistently,
 * so on a kilogram instrument a 21 kg reading was stored as 21 g and the
 * verdict came back PASS on an error wrong by a factor of a thousand.
 * Failing loudly here is cheap; the alternative is a plausible wrong verdict
 * on a signed document.
 */
function checkUnits(): string[] {
  const bad: string[] = [];
  const kgInst = { eG: 20, maxCapacityG: 60_000 } as Instrument;   // Ishida
  const gInst = { eG: 0.1, maxCapacityG: 200 } as Instrument;      // class II

  const eq = (label: string, got: unknown, want: unknown) => {
    if (got !== want) bad.push(`${label}: got ${String(got)}, want ${String(want)}`);
  };

  eq("displayUnit(e=20 g)", displayUnit(kgInst), "kg");
  eq("displayUnit(e=0.1 g)", displayUnit(gInst), "g");

  // The exact case that was wrong: 21 typed on a kilogram instrument.
  eq("toGrams(kg, '21')", toGrams(kgInst, "21"), 21000);
  eq("toGrams(kg, '0.4')", toGrams(kgInst, "0.4"), 400);
  eq("toGrams(kg, '-0.36')", toGrams(kgInst, "-0.36"), -360);
  eq("toGrams(g, '21')", toGrams(gInst, "21"), 21);

  // Blank is not zero: an unrecorded reading must stay unrecorded.
  eq("toGrams(kg, '')", toGrams(kgInst, ""), null);
  eq("toGrams(kg, '  ')", toGrams(kgInst, "  "), null);
  eq("toGrams(kg, 'abc')", toGrams(kgInst, "abc"), null);

  eq("fromGrams(kg, 21000)", fromGrams(kgInst, 21000), "21");
  eq("fromGrams(kg, 400)", fromGrams(kgInst, 400), "0.4");
  eq("fromGrams(kg, null)", fromGrams(kgInst, null), "");
  eq("fromGrams(g, 21)", fromGrams(gInst, 21), "21");

  // Round trip, including the e/10 steps a delta-load is made of.
  for (const g of [0, 2, 400, 10_000, 21_000, 59_860]) {
    const back = toGrams(kgInst, fromGrams(kgInst, g));
    if (back === null || Math.abs(back - g) > 1e-6) {
      bad.push(`round trip kg at ${g} g: came back ${String(back)}`);
    }
  }
  return bad;
}

const unitProblems = checkUnits();
if (unitProblems.length) {
  process.stderr.write("unit conversion is wrong:\n");
  for (const p of unitProblems) process.stderr.write(`  ${p}\n`);
  process.exit(1);
}

const seedPath = process.argv[2];
const evals = JSON.parse(readFileSync(seedPath, "utf8")) as any[];

for (const e of evals) {
  const m = e.instrument;
  const inst: Instrument = {
    manufacturer: m.manufacturer,
    model: m.model,
    accuracyClass: m.accuracy_class,
    maxCapacityG: Number(m.max_capacity_g),
    eG: Number(m.e_g),
    dG: Number(m.d_g),
    minCapacityG: Number(m.min_capacity_g),
    hasAuxiliaryIndicatingDevice: Boolean(m.has_aux_device),
    isGradingInstrument: Boolean(m.is_grading),
    jurisdiction: e.jurisdiction,
  };

  const r = recompute(
    inst,
    (e.observations ?? []) as StoredObservation[],
    Number(e.zero_error_g ?? 0)
  );

  const tests: Record<string, string> = {};
  for (const [code, t] of Object.entries(r.tests)) tests[code] = t.verdict;

  process.stdout.write(
    JSON.stringify({ ref: e.ref, verdict: r.verdict, tests }) + "\n"
  );
}
