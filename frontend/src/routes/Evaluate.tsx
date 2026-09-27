import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import {
  evaluateObservations, mpeForLoad, suggestTestLoads,
  validateInstrument, classSymbol, n as nOf,
  displayUnit, toGrams, fromGrams,
  derivedMinCapacityG, type Instrument, type Observation,
} from "@engine/nawi-engine";
import { fetchEvaluation, fetchObservations, type Me } from "../lib/supabase";
import { captureObservation, cacheEvaluation } from "../lib/offline";
import { renderReport, verifyEvaluation, type VerifyResult } from "../lib/space";
import StatusBar from "../components/StatusBar";
import TestCapture, { type CaptureRow } from "../components/TestCapture";
import {
  recompute as recomputeAll, TEST_TITLES,
  type StoredObservation,
} from "@engine/nawi-recompute";
import { isEditable } from "../lib/workflow";

const DEMO: Instrument = {
  manufacturer: "Ishida Co. Ltd",
  model: "MS-5060S",
  serial: "SN-2024-0117",
  accuracyClass: "III",
  maxCapacityG: 60_000,
  eG: 20,
  dG: 20,
  jurisdiction: "IN",
};

type Row = Observation & { reason: string; indText: string; dlText: string };

export default function Evaluate({ me }: { me: Me }) {
  const { ref } = useParams();
  const [inst, setInst] = useState<Instrument>(DEMO);
  const [evaluationId, setEvaluationId] = useState<string | null>(null);
  const [status, setStatusLocal] = useState<string>("draft");
  // Every observation, all five tests. The weighing rows below are the
  // editable view; these are what the overall verdict is folded from, so
  // the banner cannot disagree with the rendered report.
  const [allObs, setAllObs] = useState<StoredObservation[]>([]);
  const [zeroError, setZeroError] = useState(0);
  const [rows, setRows] = useState<Row[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [verify, setVerify] = useState<VerifyResult | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  // load an existing evaluation, or start from the demo instrument
  useEffect(() => {
    if (!ref) return;
    (async () => {
      try {
        const ev = (await fetchEvaluation(ref)) as any;
        await cacheEvaluation(ref, ev);
        setEvaluationId(ev.id);
        setStatusLocal(String(ev.status ?? "draft"));
        setZeroError(Number(ev.zero_error_g ?? 0));
        setInst({
          manufacturer: ev.model.manufacturer.name,
          model: ev.model.model,
          serial: ev.serial_no,
          accuracyClass: ev.model.accuracy_class,
          maxCapacityG: Number(ev.model.max_capacity_g),
          eG: Number(ev.model.e_g),
          dG: Number(ev.model.d_g),
          minCapacityG: Number(ev.model.min_capacity_g),
          hasAuxiliaryIndicatingDevice: ev.model.has_aux_device,
          isGradingInstrument: ev.model.is_grading,
          jurisdiction: ev.jurisdiction,
        });
        const obs = await fetchObservations(ev.id);
        setAllObs(obs as StoredObservation[]);

        // The text fields hold the display unit, the database holds grams.
        // Derive the unit from the model just loaded rather than from the
        // component's `unit`, which still describes the previous instrument
        // at the moment this callback runs.
        // Against the model just loaded, not the component's `inst`, which
        // still describes the previous instrument at this point.
        const loaded = { eG: Number(ev.model.e_g) } as any;
        const text = (g: unknown) =>
          fromGrams(loaded, g === null || g === undefined ? null : Number(g));

        setRows(
          obs
            .filter((o: any) => o.test_code === "weighing_performance")
            .map((o: any) => ({
              loadG: Number(o.load_g),
              indicationG: o.indication_g === null ? null : Number(o.indication_g),
              deltaLoadG: Number(o.delta_load_g ?? 0),
              direction: o.direction ?? "increasing",
              reason: "",
              indText: text(o.indication_g),
              dlText: text(o.delta_load_g),
            }))
        );
      } catch (err) {
        setMessage(err instanceof Error ? err.message : String(err));
      }
    })();
  }, [ref]);

  const findings = useMemo(() => {
    try {
      return validateInstrument(inst);
    } catch (err) {
      return [{ level: "ERROR" as const, code: "ENGINE", clause: "—",
                message: err instanceof Error ? err.message : String(err) }];
    }
  }, [inst]);

  const specOk = !findings.some((f) => f.level === "ERROR");

  // Test loads are derived, never typed: A.4.4.1 requires Min, Max and
  // every load at which the mpe changes band, which is exactly what a
  // hand-built spreadsheet forgets.
  useEffect(() => {
    if (ref || !specOk) return;
    try {
      const pts = suggestTestLoads(inst);
      const make = (dir: "increasing" | "decreasing") =>
        pts.map((p) => ({
          loadG: p.loadG, indicationG: null, deltaLoadG: 0,
          direction: dir, reason: p.reason, indText: "", dlText: "",
        }));
      setRows([...make("increasing"), ...make("decreasing").reverse()]);
    } catch {
      setRows([]);
    }
  }, [inst, specOk, ref]);

  const evaluated = useMemo(() => {
    if (!specOk) return [];
    try {
      return evaluateObservations(inst, rows, zeroError);
    } catch {
      return [];
    }
  }, [inst, rows, zeroError, specOk]);

  const done = evaluated.filter((o) => !o.pending);

  /**
   * The verdict for the whole instrument, folded from all five tests exactly
   * as backend/recompute.py folds it.
   *
   * The weighing rows are taken from what is on screen rather than from the
   * database, so the banner moves as the technician types; every other test
   * comes from the stored observations. Before this, the banner was computed
   * from the weighing rows alone and announced CONFORMS on reports whose
   * eccentricity had failed -- 34 of the 120 seed evaluations.
   */
  const full = useMemo(() => {
    const live: StoredObservation[] = rows.map((r) => ({
      test_code: "weighing_performance",
      load_g: r.loadG,
      indication_g: r.indicationG,
      delta_load_g: r.deltaLoadG,
      direction: r.direction,
    }));
    const others = allObs.filter((o) => o.test_code !== "weighing_performance");
    return recomputeAll(inst, [...live, ...others], zeroError);
  }, [inst, rows, allObs, zeroError]);

  const overall = specOk ? full.verdict : "FAIL";

  /** A stored record past draft is append-only; the database refuses writes. */
  const frozen = !!ref && !isEditable(status);

  function edit(i: number, field: "indText" | "dlText", value: string) {
    setRows((prev) => {
      const next = [...prev];
      const r = { ...next[i], [field]: value };
      r.indicationG = toG(r.indText);
      r.deltaLoadG = toG(r.dlText) ?? 0;
      next[i] = r;
      return next;
    });
  }

  /** Writes to IndexedDB first and always; sync is background. */
  async function saveOffline() {
    if (!evaluationId) {
      setMessage("Open an evaluation from the repository before recording.");
      return;
    }
    setBusy("saving");
    let count = 0;
    for (const [i, r] of rows.entries()) {
      if (r.indicationG === null) continue;
      await captureObservation(evaluationId, ref!, {
        evaluation_id: evaluationId,
        test_code: "weighing_performance",
        sequence_no: i + 1,
        load_g: r.loadG,
        indication_g: r.indicationG,
        delta_load_g: r.deltaLoadG ?? 0,
        direction: r.direction,
        computed: { verdict: evaluated[i]?.verdict },
      });
      count += 1;
    }
    setBusy(null);
    setMessage(`${count} observation${count === 1 ? "" : "s"} recorded locally.`);
  }

  async function runVerify() {
    if (!ref) return;
    setBusy("verifying");
    try {
      setVerify(await verifyEvaluation(me.accessToken, ref));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  async function runRender() {
    if (!ref) return;
    setBusy("rendering");
    try {
      const res = await renderReport(me.accessToken, ref);
      if (!res.ok) {
        setMessage(res.error ?? "render failed");
      } else {
        setMessage(`Report v${res.version} rendered. Digest ${res.payload_sha256?.slice(0, 16)}.`);
        if (res.pdf_url) window.open(res.pdf_url, "_blank", "noopener");
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  /**
   * The unit every field on this screen is entered and shown in.
   *
   * An instrument whose e is a gram or more is worked in kilograms, matching
   * how its display reads; a finer one is worked in grams. The engine stores
   * grams throughout, so the two conversions below are the only place the
   * boundary is crossed.
   *
   * They exist because the boundary used to be crossed inconsistently: the
   * zero-error field converted, the indication and delta-load fields did not.
   * On the Ishida (e = 20 g, so kilogram mode) a technician typing 21 for a
   * 21 kg reading had it stored as 21 grams, and the verdict came back PASS
   * on an error that was wrong by a factor of a thousand. A wrong verdict
   * that looks plausible is the worst kind this screen can produce, so the
   * unit is now written in every column heading as well.
   */
  const unit = displayUnit(inst);
  const toG = (text: string) => toGrams(inst, text);

  const fmt = (g: number | undefined | null) => {
    if (g === null || g === undefined) return "—";
    return inst.eG >= 1 ? `${(g / 1000).toFixed(decimals(inst.eG / 1000))} kg`
                        : `${g.toFixed(decimals(inst.eG))} g`;
  };

  return (
    <div className="cols">
      <div className="stack">
        <Spec inst={inst} setInst={setInst} locked={!!ref} />
        <Derived inst={inst} findings={findings} fmt={fmt} />
        {ref && (
          <StatusBar
            refNo={ref}
            status={status}
            me={me}
            onChanged={setStatusLocal}
          />
        )}
      </div>

      <div className="stack">
        <div className={`verdict ${overall}`}>
          <div className="big">
            {overall === "PASS" ? "CONFORMS"
              : overall === "FAIL" ? "DOES NOT CONFORM" : "IN PROGRESS"}
          </div>
          <div className="d">
            {!specOk
              ? "The declared specification does not satisfy the standard."
              : full.failing.length > 0
                ? `Failing: ${full.failing.map((k) => TEST_TITLES[k] ?? k).join(", ")}.`
                : full.pending.length > 0
                  ? `Not yet recorded: ${full.pending
                      .map((k) => TEST_TITLES[k] ?? k)
                      .join(", ")}.`
                  : `All five tests recorded and within permissible error.`}
          </div>
        </div>

        <div className="panel">
          <header>
            <h2>Tests</h2>
            <span className="cl">
              {done.length} of {evaluated.length} weighing rows recorded
            </span>
          </header>
          <div className="legend">
            {Object.entries(full.tests).map(([code, t]) => (
              <span key={code} title={`Clause ${t.clause}`}>
                <span className={`tag ${t.verdict}`}>{t.verdict}</span>{" "}
                {TEST_TITLES[code] ?? code}
                {t.count > 0 && <span className="note"> · {t.count}</span>}
              </span>
            ))}
          </div>
        </div>

        <div className="panel">
          <header>
            <h2>Weighing performance</h2>
            <span className="cl">A.4.4.1 · A.4.4.3</span>
          </header>
          <Envelope inst={inst} rows={evaluated} />
          <div className="pad" style={{ paddingTop: 0 }}>
            <div className="field" style={{ maxWidth: 200 }}>
              <label htmlFor="e0">Zero error E₀ ({unit})</label>
              <input
                id="e0" type="number" step="any" disabled={frozen}
                value={unit === "kg" ? zeroError / 1000 : zeroError}
                onChange={(e) =>
                  setZeroError(unit === "kg" ? Number(e.target.value) * 1000 : Number(e.target.value))
                }
              />
            </div>
            <p className="note" style={{ marginBottom: 10 }}>
              Enter the display reading as <code>I</code> and the additional{" "}
              <code>e/10</code> weights added before it stepped up as <code>ΔL</code>, both in {unit}.
              The corrected error is <code>Ec = (I + ½e − ΔL) − L − E₀</code>.
            </p>
            <div className="scroll">
              <table>
                <thead>
                  <tr>
                    <th>#</th><th className="n">Load L</th><th>Direction</th>
                    <th className="n">Indication I ({unit})</th>
                    <th className="n">ΔL ({unit})</th>
                    <th className="n">Error Ec</th><th className="n">mpe</th>
                    <th>Result</th>
                  </tr>
                </thead>
                <tbody>
                  {evaluated.map((o, i) => (
                    <tr key={i}>
                      <td className="n" style={{ color: "var(--slate-2)" }}>{i + 1}</td>
                      <td className="n">{fmt(o.loadG)}</td>
                      <td className="note">{o.direction}</td>
                      <td>
                        <input type="number" step="any" value={rows[i]?.indText ?? ""}
                          disabled={frozen}
                          aria-label={`Indication at ${fmt(o.loadG)}`}
                          onChange={(e) => edit(i, "indText", e.target.value)} />
                      </td>
                      <td>
                        <input type="number" step="any" value={rows[i]?.dlText ?? ""}
                          disabled={frozen}
                          aria-label="Additional weights"
                          onChange={(e) => edit(i, "dlText", e.target.value)} />
                      </td>
                      <td className="n">{o.pending ? "—" : fmt(o.Ec)}</td>
                      <td className="n">{o.pending ? "—" : `± ${fmt(o.mpeG)}`}</td>
                      <td>
                        <span className={`tag ${o.pending ? "PENDING" : o.marginal ? "MARGINAL" : o.verdict}`}>
                          {o.pending ? "—" : o.verdict}
                        </span>
                      </td>
                    </tr>
                  ))}
                  {evaluated.length === 0 && (
                    <tr><td colSpan={8} className="empty">
                      Correct the specification to generate test loads.
                    </td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
          <div className="bar">
            <button
              className="btn"
              disabled={!!busy || frozen}
              title={
                frozen
                  ? `A record at "${status}" is frozen. Return it to draft to record more.`
                  : undefined
              }
              onClick={saveOffline}
            >
              {busy === "saving" ? "Recording…" : "Record observations"}
            </button>
            <button className="btn ghost" disabled={!!busy || !ref} onClick={runVerify}>
              {busy === "verifying" ? "Verifying…" : "Verify on server"}
            </button>
            <button
              className="btn ghost"
              disabled={!!busy || !ref || !["reviewer", "approver", "director", "admin"].includes(me.role)}
              onClick={runRender}
            >
              {busy === "rendering" ? "Rendering…" : "Render report"}
            </button>
            <span className="note sp">
              {frozen
                ? "This record is frozen at its current status. Observations are append-only and the database refuses edits."
                : "Loads include Min, Max and every load at which the permissible error changes."}
            </span>
          </div>
        </div>

        {ref && (
          <TestCapture
            inst={inst}
            zeroError={zeroError}
            frozen={frozen}
            existing={new Set(allObs.map((o) => o.test_code))}
            onRecord={async (captured: CaptureRow[]) => {
              if (!evaluationId) throw new Error("No evaluation open.");
              for (const row of captured) {
                await captureObservation(evaluationId, ref, {
                  evaluation_id: evaluationId,
                  ...row,
                });
              }
              // Fold them in immediately: the verdict banner is computed from
              // this list, so a recorded eccentricity failure must show up
              // without waiting for a reload.
              setAllObs((prev) => [...prev, ...(captured as StoredObservation[])]);
            }}
          />
        )}

        {message && <div className="panel"><div className="pad note">{message}</div></div>}
        {verify && <Divergence verify={verify} />}
      </div>
    </div>
  );
}

function decimals(step: number) {
  let d = 0, x = step;
  while (Math.abs(x - Math.round(x)) > 1e-9 && d < 6) { x *= 10; d += 1; }
  return d;
}

/**
 * The declared specification.
 *
 * Read-only once an evaluation is open. The fields used to be editable on a
 * stored evaluation, but nothing wrote them back: a technician could correct
 * Max, watch every verdict on screen recompute against the new value, record
 * observations against it -- and the database, and therefore the rendered
 * report, still held the old one. Silently showing one number and printing
 * another is the worst failure this screen could have.
 *
 * The specification belongs to the instrument model, which is shared by every
 * evaluation of that model, so it is not an evaluation-level edit in any case.
 * Editing stays available on the unsaved scratch view, where nothing is
 * claimed to be stored.
 */
function Spec({
  inst, setInst, locked,
}: { inst: Instrument; setInst: (i: Instrument) => void; locked: boolean }) {
  const unit = inst.eG >= 1 ? "kg" : "g";
  const toG = (v: number) => (unit === "kg" ? v * 1000 : v);
  const fromG = (v: number) => (unit === "kg" ? v / 1000 : v);
  return (
    <div className="panel">
      <header><h2>Instrument</h2><span className="cl">3.2 / Table 3</span></header>
      {locked && (
        <div className="pad" style={{ paddingBottom: 0 }}>
          <p className="note">
            Declared by the manufacturer and held against the instrument model.
            Shown here as recorded; it is not editable from a test record.
          </p>
        </div>
      )}
      <div className="pad">
        <div className="field">
          <label htmlFor="mfr">Manufacturer</label>
          <input id="mfr" disabled={locked} value={inst.manufacturer}
            onChange={(e) => setInst({ ...inst, manufacturer: e.target.value })} />
        </div>
        <div className="row">
          <div className="field">
            <label htmlFor="model">Model</label>
            <input id="model" disabled={locked} value={inst.model}
              onChange={(e) => setInst({ ...inst, model: e.target.value })} />
          </div>
          <div className="field">
            <label htmlFor="cls">Accuracy class</label>
            <select id="cls" disabled={locked} value={inst.accuracyClass}
              onChange={(e) => setInst({ ...inst, accuracyClass: e.target.value as any })}>
              {["I", "II", "III", "IIII"].map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
        </div>
        <div className="row">
          <div className="field">
            <label htmlFor="max">Max ({unit})</label>
            <input id="max" disabled={locked} type="number" step="any" value={fromG(inst.maxCapacityG)}
              onChange={(e) => setInst({ ...inst, maxCapacityG: toG(Number(e.target.value)) })} />
          </div>
          <div className="field">
            <label htmlFor="e">e ({unit})</label>
            <input id="e" disabled={locked} type="number" step="any" value={fromG(inst.eG)}
              onChange={(e) => {
                const v = toG(Number(e.target.value));
                setInst({ ...inst, eG: v, dG: v, minCapacityG: undefined });
              }} />
          </div>
        </div>
        <div className="field" style={{ marginBottom: 0 }}>
          <label htmlFor="juris">Report to</label>
          <select id="juris" disabled={locked} value={inst.jurisdiction ?? "IN"}
            onChange={(e) => setInst({ ...inst, jurisdiction: e.target.value as any })}>
            <option value="IN">India — Legal Metrology</option>
            <option value="OIML">OIML</option>
          </select>
        </div>
      </div>
    </div>
  );
}

function Derived({ inst, findings, fmt }: any) {
  let min: number | null = null;
  try { min = inst.minCapacityG ?? derivedMinCapacityG(inst); } catch { /* invalid spec */ }
  return (
    <div className="panel">
      <header><h2>Derived from the standard</h2><span className="cl">Table 3 · Table 6</span></header>
      <div className="pad">
        <div className="readout">
          <div className="grid">
            <div><div className="k">Class</div><div className="v">{safe(() => classSymbol(inst))}</div></div>
            <div><div className="k">n = Max/e</div>
              <div className="v">{safe(() => nOf(inst).toLocaleString())}</div></div>
            <div><div className="k">Min</div><div className="v">{fmt(min)}</div></div>
          </div>
        </div>
        {findings.map((f: any, i: number) => (
          <div key={i} className={`finding ${f.level}`}>
            <span>{f.message}<span className="cl">{f.clause} · {f.code}</span></span>
          </div>
        ))}
      </div>
    </div>
  );
}

const safe = (fn: () => string) => { try { return fn(); } catch { return "—"; } };

/**
 * The tolerance envelope: the stepped funnel the mpe traces across the
 * weighing range, with every measured error inside it or outside it. This
 * is the one picture that makes a verdict obvious at a glance.
 */
function Envelope({ inst, rows }: { inst: Instrument; rows: any[] }) {
  const W = 920, H = 320, ml = 56, mr = 18, mt = 16, mb = 40;
  const pw = W - ml - mr, ph = H - mt - mb;
  let bandUp = "", bandDn = "";
  const done = rows.filter((r) => !r.pending);
  const yMax = Math.max(2, ...done.map((r) => Math.abs(r.Ec!) / inst.eG + 0.3));
  const x = (L: number) => ml + (L / inst.maxCapacityG) * pw;
  const y = (v: number) => mt + ph / 2 - (v / yMax) * (ph / 2);

  try {
    const xs: number[] = [];
    for (let k = 0; k <= 300; k++) xs.push((inst.maxCapacityG * k) / 300);
    // sample either side of each breakpoint so the step lands exactly
    for (const p of suggestTestLoads(inst)) {
      xs.push(Math.max(0, p.loadG - 1e-6), p.loadG, p.loadG + 1e-6);
    }
    xs.sort((a, b) => a - b);
    const up: string[] = [], dn: string[] = [];
    for (const L of xs) {
      const m = mpeForLoad(inst, L) / inst.eG;
      up.push(`${x(L).toFixed(1)},${y(m).toFixed(1)}`);
      dn.push(`${x(L).toFixed(1)},${y(-m).toFixed(1)}`);
    }
    bandUp = up.join(" ");
    bandDn = dn.reverse().join(" ");
  } catch {
    return <div className="empty">Enter a valid specification to draw the tolerance envelope.</div>;
  }

  return (
    <>
      <svg className="env" viewBox={`0 0 ${W} ${H}`} role="img"
        aria-label="Error against applied load with the maximum permissible error envelope">
        <polygon points={`${bandUp} ${bandDn}`} fill="var(--teal)" opacity="0.10" />
        <polyline points={bandUp} fill="none" stroke="var(--teal)" strokeWidth="1.6" />
        <polyline points={bandDn} fill="none" stroke="var(--teal)" strokeWidth="1.6" />
        {[-1.5, -1, -0.5, 0, 0.5, 1, 1.5].filter((v) => Math.abs(v) <= yMax).map((v) => (
          <g key={v}>
            <line x1={ml} y1={y(v)} x2={ml + pw} y2={y(v)} stroke="var(--rule-2)" />
            <text x={ml - 8} y={y(v) + 3.5} textAnchor="end" fontSize="10"
              fontFamily="var(--mono)" fill="var(--slate)">
              {v > 0 ? "+" : ""}{v} e
            </text>
          </g>
        ))}
        <line x1={ml} y1={y(0)} x2={ml + pw} y2={y(0)} stroke="var(--slate-2)" />
        <line x1={ml} y1={mt + ph} x2={ml + pw} y2={mt + ph} stroke="var(--slate-2)" />
        {done.map((r, i) => {
          const col = r.verdict === "FAIL" ? "var(--fail)"
            : r.marginal ? "var(--warn)" : "var(--pass)";
          return r.direction === "decreasing" ? (
            <rect key={i} x={x(r.loadG) - 3.6} y={y(r.Ec! / inst.eG) - 3.6}
              width="7.2" height="7.2" fill={col} stroke="var(--card)" strokeWidth="1.2" />
          ) : (
            <circle key={i} cx={x(r.loadG)} cy={y(r.Ec! / inst.eG)} r="4.1"
              fill={col} stroke="var(--card)" strokeWidth="1.2" />
          );
        })}
      </svg>
      <div className="legend">
        <span><i style={{ background: "var(--teal)", opacity: 0.35 }} />Permissible error</span>
        <span><i style={{ background: "var(--pass)", borderRadius: "50%" }} />Within tolerance</span>
        <span><i style={{ background: "var(--warn)", borderRadius: "50%" }} />Within 0.1 e of the limit</span>
        <span><i style={{ background: "var(--fail)", borderRadius: "50%" }} />Outside tolerance</span>
        <span>● increasing &nbsp; ■ decreasing</span>
      </div>
    </>
  );
}

/**
 * If the server's verdicts disagree with the client's, that is either a
 * stale client or a tampered payload. Neither should be allowed to become
 * a signed report, so it is shown prominently rather than logged.
 */
function Divergence({ verify }: { verify: VerifyResult }) {
  if (!verify.ok) {
    return <div className="panel"><div className="pad finding ERROR">
      <span>{verify.error}</span></div></div>;
  }
  const ok = verify.agrees_with_client;
  return (
    <div className="panel">
      <header>
        <h2>Server verification</h2>
        <span className="cl">{verify.payload_sha256?.slice(0, 16)}</span>
      </header>
      <div className="pad">
        <div className={`finding ${ok ? "INFO" : "ERROR"}`}>
          <span>
            {ok
              ? "The server independently reproduced every verdict from the raw readings."
              : `${verify.divergences?.length} observation(s) where the server disagrees with this device.`}
          </span>
        </div>
        {!ok && (
          <div className="scroll">
            <table>
              <thead><tr>
                <th className="n">Load</th><th>This device</th><th>Server</th>
                <th className="n">Server Ec</th><th className="n">mpe</th>
              </tr></thead>
              <tbody>
                {verify.divergences?.map((d, i) => (
                  <tr key={i}>
                    <td className="n">{d.load_g}</td>
                    <td><span className={`tag ${d.client_verdict}`}>{d.client_verdict}</span></td>
                    <td><span className={`tag ${d.server_verdict}`}>{d.server_verdict}</span></td>
                    <td className="n">{d.server_Ec_g}</td>
                    <td className="n">± {d.server_mpe_g}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
