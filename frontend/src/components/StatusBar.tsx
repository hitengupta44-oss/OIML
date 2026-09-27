/**
 * The approval workflow, made visible.
 *
 * Every button here is derived from standards/workflow.json and the signed-in
 * user's role -- nothing is hardcoded. A technician sees "Submit for review"
 * on a draft and nothing at all on an approved report; a director sees
 * "Issue" only once a report is approved.
 *
 * The buttons are a convenience, not the control. app.guard_evaluation_update()
 * in supabase/migrations/0002_functions.sql re-checks the edge and the role on
 * every write, so a forged request is refused by Postgres. When that happens
 * the error is shown verbatim rather than swallowed: being told "role
 * technician may not set status approved" is the system demonstrating that it
 * works.
 */

import { useState } from "react";
import { actionsFor, label, state, type Action } from "../lib/workflow";
import { setStatus } from "../lib/supabase";
import type { Me } from "../lib/supabase";

export default function StatusBar({
  refNo,
  status,
  me,
  onChanged,
}: {
  refNo: string;
  status: string;
  me: Me;
  onChanged: (next: string) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<Action | null>(null);

  const here = state(status);
  const actions = actionsFor(status, me.role);

  async function move(a: Action) {
    setConfirming(null);
    setBusy(a.to);
    setError(null);
    try {
      await setStatus(refNo, a.to);
      onChanged(a.to);
    } catch (e) {
      // Show what the database said. The message is the proof.
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="panel">
      <header>
        <h2>Workflow</h2>
        <span className="cl">
          {here?.clause && here.clause !== "\u2014" ? here.clause : ""}
        </span>
      </header>

      <div className="pad">
        <div className="wf-now">
          <span className={`tag ${status === "issued" ? "PASS" : "PENDING"}`}>
            {label(status)}
          </span>
          <span className="note" style={{ marginLeft: 10 }}>
            {here?.editable ? "Observations may still be edited." : "Record is frozen."}
          </span>
        </div>

        {here?.description && (
          <p className="note" style={{ marginTop: 8 }}>{here.description}</p>
        )}

        {error && (
          <div className="finding ERROR" style={{ marginTop: 10 }}>
            <span>{error}</span>
          </div>
        )}
      </div>

      <div className="bar">
        {actions.length === 0 && (
          <span className="note">
            {here?.transitions?.length
              ? `No action available to a ${me.role} at this stage.`
              : "This is a terminal state."}
          </span>
        )}

        {actions.map((a) => (
          <button
            key={a.to}
            className={`btn ${a.grave ? "quiet" : ""}`}
            disabled={busy !== null}
            title={a.description}
            onClick={() => (a.grave ? setConfirming(a) : move(a))}
          >
            {busy === a.to ? "…" : verb(a)}
          </button>
        ))}
      </div>

      {confirming && (
        <div className="pad" style={{ borderTop: "1px solid var(--rule)" }}>
          <div className="finding WARNING">
            <span>
              Move {refNo} to <strong>{confirming.label}</strong>?{" "}
              {confirming.description}
            </span>
          </div>
          <div style={{ display: "flex", gap: 9, marginTop: 9 }}>
            <button className="btn" onClick={() => move(confirming)}>
              Yes, {verb(confirming).toLowerCase()}
            </button>
            <button className="btn quiet" onClick={() => setConfirming(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * What the button says. A status is a noun ("Under review"); a button needs a
 * verb, and the verb is what a laboratory would actually call the act.
 */
function verb(a: Action): string {
  switch (a.to) {
    case "submitted":    return "Submit for review";
    case "under_review": return "Begin review";
    case "approved":     return "Approve";
    case "rejected":     return "Reject";
    case "issued":       return "Issue certificate";
    case "draft":        return "Return to draft";
    case "resubmitted":  return "Resubmit";
    case "suspended":    return "Suspend";
    case "revoked":      return "Revoke";
    case "amended":      return "Amend";
    case "cancelled":    return "Cancel";
    default:             return a.label;
  }
}
