/**
 * Entry for the two tests that had no screen: eccentricity and repeatability.
 *
 * Both were already computed by the engine and printed in the report, but
 * their observations could only arrive through the seed. A laboratory could
 * not record them, which made the report's eccentricity section unreachable
 * from the app that produces the report.
 *
 * Test loads are derived, not typed
 * ---------------------------------
 * The load a technician must apply is fixed by the procedure, so the screen
 * computes it rather than asking. Getting it wrong is the classic defect of a
 * hand-built spreadsheet, and it is silent: the arithmetic is still correct,
 * it is simply correct about the wrong load.
 *
 *   Eccentricity   one third of Max, applied at the centre and at each
 *                  quarter segment of the load receptor, for a receptor with
 *                  four or fewer points of support (NITP 6.1-6.4 clause 5.2.1;
 *                  OIML R 76-1 A.4.7). Each indication is judged against the
 *                  mpe for that load, which is the same comparison a weighing
 *                  row gets, so the same engine function decides it.
 *
 *   Repeatability  a load just under the second mpe change point, or about
 *                  two thirds of Max where the instrument has no second
 *                  change point (NITP clause 5.1), weighed three times. The
 *                  spread must not exceed the absolute mpe for that load
 *                  (R 76-1 clause 3.6.1).
 *
 * Verdicts appear as the technician types, from the same TypeScript engine
 * the weighing screen uses, so this works with no network inside a shielded
 * chamber.
 */

import { useMemo, useState } from "react";
import {
  evaluateObservations, checkRepeatability, mpeForLoad, suggestTestLoads,
  displayUnit, toGrams, type Instrument,
} from "@engine/nawi-engine";

export interface CaptureRow {
  test_code: string;
  sequence_no: number;
  load_g: number;
  indication_g: number;
  delta_load_g?: number;
  position?: string;
  weighing_no?: number;
  series?: number;
  computed?: { verdict?: string };
}

/** The four quarter segments plus the centre, in the order A.4.7 applies them. */
const POSITIONS = ["Centre", "Segment 1", "Segment 2", "Segment 3", "Segment 4"];

/**
 * The load for the repeatability test.
 *
 * NITP clause 5.1: "Use a load which is just less than the second MPE change
 * point. If the instrument has more or less than 2 MPE change points use a
 * load which is approximately two-thirds maximum capacity."
 */
function repeatabilityLoad(inst: Instrument): number {
  const edges = suggestTestLoads(inst)
    .filter((p) => /band edge/i.test(p.reason ?? ""))
    .map((p) => p.loadG)
    .sort((a, b) => a - b);
  if (edges.length === 2) return edges[1];
  return (2 / 3) * inst.maxCapacityG;
}

export default function TestCapture({
  inst, zeroError, frozen, existing, onRecord,
}: {
  inst: Instrument;
  zeroError: number;
  frozen: boolean;
  /** Test codes that already have stored observations, so they are not re-entered. */
  existing: Set<string>;
  onRecord: (rows: CaptureRow[]) => Promise<void>;
}) {
  const [tab, setTab] = useState<"eccentricity" | "repeatability">("eccentricity");
  const [ecc, setEcc] = useState<string[]>(() => POSITIONS.map(() => ""));
  const [rep, setRep] = useState<string[]>(["", "", ""]);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const unit = displayUnit(inst);
  const kg = unit === "kg";
  // EvaluatedObservation.pending is a plain boolean rather than a literal
  // discriminant, so TypeScript cannot narrow Ec to a number from it. The
  // formatter takes the undefined instead of the call site pretending.
  const show = (g: number | undefined) =>
    g === undefined ? "\u2014" : `${(kg ? g / 1000 : g).toFixed(kg ? 3 : 2)} ${unit}`;
  const toG = (t: string) => toGrams(inst, t);

  const eccLoad: number = inst.maxCapacityG / 3;
  const repLoad: number = repeatabilityLoad(inst) ?? (2 / 3) * inst.maxCapacityG;

  // -- live verdicts --------------------------------------------------
  const eccRows = useMemo(() => {
    const obs = POSITIONS.map((_, i) => ({
      loadG: eccLoad,
      indicationG: toG(ecc[i]),
      deltaLoadG: 0,
    }));
    return evaluateObservations(inst, obs, zeroError);
  }, [inst, ecc, eccLoad, zeroError]);

  const repResult = useMemo(() => {
    const vals = rep.map(toG).filter((v): v is number => v !== null);
    if (vals.length < 2) return null;
    const r = checkRepeatability(inst, vals, repLoad);
    return r.pending ? null : r;
  }, [inst, rep, repLoad]);

  async function record() {
    setBusy(true);
    setNote(null);
    try {
      const rows: CaptureRow[] =
        tab === "eccentricity"
          ? POSITIONS.flatMap((position, i) => {
              const g = toG(ecc[i]);
              return g === null
                ? []
                : [{
                    test_code: "eccentricity",
                    sequence_no: i + 1,
                    load_g: eccLoad,
                    indication_g: g,
                    delta_load_g: 0,
                    position,
                    computed: { verdict: eccRows[i]?.verdict },
                  }];
            })
          : rep.flatMap((t, i) => {
              const g = toG(t);
              return g === null
                ? []
                : [{
                    test_code: "repeatability",
                    sequence_no: i + 1,
                    load_g: repLoad,
                    indication_g: g,
                    weighing_no: i + 1,
                    series: 1,
                    computed: { verdict: repResult?.verdict },
                  }];
            });

      if (rows.length === 0) {
        setNote("Nothing to record — enter at least one indication.");
        return;
      }
      await onRecord(rows);
      setNote(`${rows.length} observation${rows.length === 1 ? "" : "s"} recorded.`);
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const alreadyStored = existing.has(tab);

  return (
    <div className="panel">
      <header>
        <h2>{tab === "eccentricity" ? "Eccentricity" : "Repeatability"}</h2>
        <span className="cl">{tab === "eccentricity" ? "A.4.7" : "3.6.1"}</span>
      </header>

      <div className="bar" style={{ borderTop: 0, paddingBottom: 0 }}>
        <button
          className={`btn ${tab === "eccentricity" ? "" : "quiet"}`}
          onClick={() => setTab("eccentricity")}
        >
          Eccentricity
        </button>
        <button
          className={`btn ${tab === "repeatability" ? "" : "quiet"}`}
          onClick={() => setTab("repeatability")}
        >
          Repeatability
        </button>
      </div>

      {tab === "eccentricity" ? (
        <>
          <div className="pad">
            <p className="note">
              Apply <strong>{show(eccLoad)}</strong> — one third of Max — at the
              centre and at each quarter segment. Each indication must stay
              within <strong>± {show(mpeForLoad(inst, eccLoad))}</strong> of the
              applied load.
            </p>
          </div>
          <div className="scroll">
            <table>
              <thead>
                <tr>
                  <th>Position</th>
                  <th className="n">Load</th>
                  <th className="n">Indication</th>
                  <th className="n">Error Ec</th>
                  <th>Result</th>
                </tr>
              </thead>
              <tbody>
                {POSITIONS.map((p, i) => {
                  const r = eccRows[i];
                  // Narrow explicitly: optional chaining on r does not tell
                  // TypeScript that Ec and verdict are present afterwards.
                  const waiting = !r || r.pending;
                  return (
                    <tr key={p}>
                      <td>{p}</td>
                      <td className="n">{show(eccLoad)}</td>
                      <td>
                        <input
                          type="number" step="any" value={ecc[i]}
                          disabled={frozen}
                          aria-label={`Indication at ${p}`}
                          onChange={(e) =>
                            setEcc((prev) =>
                              prev.map((v, k) => (k === i ? e.target.value : v))
                            )
                          }
                        />
                      </td>
                      <td className="n">{waiting ? "—" : show(r.Ec)}</td>
                      <td>
                        <span className={`tag ${waiting ? "PENDING" : r.verdict}`}>
                          {waiting ? "—" : r.verdict}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <>
          <div className="pad">
            <p className="note">
              Weigh <strong>{show(repLoad)}</strong> three times, removing the
              load and re-zeroing between weighings. The spread must not exceed{" "}
              <strong>{show(mpeForLoad(inst, repLoad))}</strong>, the absolute
              value of the mpe for that load.
            </p>
          </div>
          <div className="pad" style={{ paddingTop: 0 }}>
            <div className="row" style={{ gridTemplateColumns: "1fr 1fr 1fr" }}>
              {rep.map((v, i) => (
                <div className="field" key={i}>
                  <label htmlFor={`rep${i}`}>Weighing {i + 1} ({unit})</label>
                  <input
                    id={`rep${i}`} type="number" step="any" value={v}
                    disabled={frozen}
                    onChange={(e) =>
                      setRep((prev) => prev.map((x, k) => (k === i ? e.target.value : x)))
                    }
                  />
                </div>
              ))}
            </div>
            {repResult && (
              <div className={`verdict ${repResult.verdict}`} style={{ marginTop: 4 }}>
                <div className="big">{repResult.verdict}</div>
                <div className="d">
                  Spread {show(repResult.spread)} ({repResult.spreadE.toFixed(2)} e)
                  against a limit of {show(repResult.limit)}.
                </div>
              </div>
            )}
          </div>
        </>
      )}

      <div className="bar">
        <button className="btn" disabled={busy || frozen} onClick={record}>
          {busy ? "Recording…" : "Record observations"}
        </button>
        {note && <span className="note">{note}</span>}
        {alreadyStored && !note && (
          <span className="note">
            This test already has stored observations. Recording again adds a
            further set rather than replacing them.
          </span>
        )}
      </div>
    </div>
  );
}
