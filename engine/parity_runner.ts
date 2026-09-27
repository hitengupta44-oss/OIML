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
import type { Instrument } from "./nawi-engine";
import type { StoredObservation } from "./nawi-recompute";

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
