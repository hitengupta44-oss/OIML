import { useEffect, useState } from "react";
import { dashboardCounts } from "../lib/supabase";

export default function Dashboard() {
  const [rows, setRows] = useState<any[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    dashboardCounts()
      .then(setRows)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  const sum = (k: string) => rows.reduce((a, r) => a + Number(r[k] ?? 0), 0);
  const max = Math.max(1, ...rows.map((r) => Number(r.total)));

  return (
    <div className="stack">
      <div className="stats">
        <div className="stat"><div className="v">{sum("total")}</div>
          <div className="k">Evaluations</div></div>
        <div className="stat"><div className="v" style={{ color: "var(--pass)" }}>{sum("passing")}</div>
          <div className="k">Conforming</div></div>
        <div className="stat"><div className="v" style={{ color: "var(--fail)" }}>{sum("failing")}</div>
          <div className="k">Not conforming</div></div>
        <div className="stat"><div className="v" style={{ color: "var(--slate)" }}>{sum("under_review")}</div>
          <div className="k">Under review</div></div>
        <div className="stat"><div className="v">{sum("issued")}</div>
          <div className="k">Certificates issued</div></div>
        <div className="stat"><div className="v">{sum("last_30_days")}</div>
          <div className="k">Last 30 days</div></div>
      </div>

      {error && <div className="panel"><div className="pad finding ERROR"><span>{error}</span></div></div>}

      <div className="panel">
        <header><h2>By laboratory</h2></header>
        <div className="pad">
          {rows.length === 0 && <p className="note">No evaluations yet.</p>}
          {rows.map((r) => (
            <div key={r.lab_code}
              style={{ display: "grid", gridTemplateColumns: "180px 1fr 40px",
                       gap: 11, alignItems: "center", marginBottom: 8 }}>
              <span style={{ fontSize: 12.5 }}>{r.lab_name}</span>
              <span style={{ height: 9, background: "var(--rule-2)", borderRadius: 2, overflow: "hidden" }}>
                <span style={{ display: "block", height: "100%",
                               width: `${(Number(r.total) / max) * 100}%`,
                               background: "var(--teal)" }} />
              </span>
              <span className="n" style={{ fontFamily: "var(--mono)", fontSize: 12 }}>{r.total}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="panel">
        <header><h2>Active standard</h2></header>
        <div className="pad">
          <div style={{ display: "grid", gridTemplateColumns: "190px 1fr", gap: "6px 14px", fontSize: 12.5 }}>
            <span className="note">Standard</span><span>OIML R 76-1:2006 (E)</span>
            <span className="note">National adoption</span>
            <span>Legal Metrology (General) Rules, 2011 — Seventh Schedule, Heading A</span>
            <span className="note">Error method</span>
            <span>Changeover point, clause A.4.4.3</span>
            <span className="note">In-service factor</span>
            <span>2 × the permissible error on initial verification</span>
          </div>
          <p className="note" style={{ marginTop: 12 }}>
            Every band, threshold and comparison operator is read from the standards
            configuration at runtime, and each evaluation records the standard it was
            judged under. Supporting a revised R 76 means adding a configuration, not
            changing the engine.
          </p>
        </div>
      </div>
    </div>
  );
}
