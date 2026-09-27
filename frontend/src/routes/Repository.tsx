import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listEvaluations } from "../lib/supabase";

/**
 * The repository. RLS decides what appears here — a technician sees their
 * own laboratory, a director sees everything — so there is no lab filter
 * in this file. A filter the user can edit is not a security boundary.
 */
export default function Repository() {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [rows, setRows] = useState<any[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const t = setTimeout(() => {
      setLoading(true);
      listEvaluations(search, status || undefined)
        .then((r) => { setRows(r); setError(null); })
        .catch((e) => setError(e instanceof Error ? e.message : String(e)))
        .finally(() => setLoading(false));
    }, 250);
    return () => clearTimeout(t);
  }, [search, status]);

  return (
    <div className="panel">
      <header>
        <h2>Test reports</h2>
        <span className="cl">{rows.length} record{rows.length === 1 ? "" : "s"}</span>
      </header>
      <div className="pad" style={{ paddingBottom: 6 }}>
        <div className="row" style={{ gridTemplateColumns: "1fr 200px" }}>
          <input
            placeholder="Search by report number, manufacturer or model"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Search reports"
          />
          <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
            <option value="">All statuses</option>
            {["draft","submitted","under_review","approved","issued","rejected","amended"]
              .map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}
          </select>
        </div>
      </div>

      {error && <div className="pad"><div className="finding ERROR"><span>{error}</span></div></div>}
      {loading && <div className="empty">Loading…</div>}
      {!loading && rows.length === 0 && <div className="empty">No reports match.</div>}

      {rows.map((r) => (
        <div className="repo-row" key={r.ref}>
          <div>
            <div className="ref">
              {r.ref}
              {r.is_synthetic && (
                <span className="tag MARGINAL" style={{ marginLeft: 8 }}>synthetic</span>
              )}
            </div>
            <div className="nm">{r.manufacturer} — {r.model}</div>
            <div className="mt">
              Class {r.accuracy_class === "IIII" ? "IV" : r.accuracy_class} ·
              {" "}Max {Number(r.max_capacity_g) / 1000} kg ·
              {" "}e {r.e_g} g · n {Number(r.n).toLocaleString()} · {r.lab_name} ·
              {" "}{new Date(r.created_at).toLocaleDateString("en-IN",
                    { day: "numeric", month: "short", year: "numeric" })}
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <span className={`tag ${r.verdict}`}>{r.verdict}</span>
            <span className="note">{String(r.status).replace("_", " ")}</span>
            <Link className="btn quiet" to={`/evaluate/${encodeURIComponent(r.ref)}`}>
              Open
            </Link>
          </div>
        </div>
      ))}
    </div>
  );
}
