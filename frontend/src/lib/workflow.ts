/**
 * The approval workflow, read from standards/workflow.json.
 *
 * Same principle as the mpe bands: the state machine is data, not code. No
 * status name, legal edge or role gate is written into this file. It reads
 * the same JSON that supabase/migrations/0002_functions.sql was written
 * from, and scripts/check_workflow_consistency.py fails if the two drift.
 *
 * The gate here decides which buttons a user SEES. It is not security --
 * the database trigger app.guard_evaluation_update() is, and it re-checks
 * every transition and every role server-side. A user who forges a request
 * past this file gets a 'insufficient_privilege' error from Postgres, which
 * is exactly as it should be.
 */

import workflow from "@standards/workflow.json";

export interface WorkflowState {
  code: string;
  label: string;
  clause: string;
  description: string;
  editable: boolean;
  transitions?: string[];
}

interface WorkflowDoc {
  states: WorkflowState[];
  roles: { code: string; label: string }[];
  role_may_set: Record<string, string[] | string>;
}

const doc = workflow as unknown as WorkflowDoc;

const STATES = new Map<string, WorkflowState>(
  doc.states.map((s) => [s.code, s])
);

export function state(code: string): WorkflowState | undefined {
  return STATES.get(code);
}

export function label(code: string): string {
  return STATES.get(code)?.label ?? code;
}

export function allStates(): WorkflowState[] {
  return doc.states;
}

export function isEditable(code: string): boolean {
  return STATES.get(code)?.editable ?? false;
}

/** Roles permitted to move an evaluation INTO `status`. */
function rolesFor(status: string): string[] {
  const v = doc.role_may_set[status];
  return Array.isArray(v) ? v : [];
}

export interface Action {
  to: string;
  label: string;
  clause: string;
  description: string;
  /** Destructive or terminal: the UI confirms before sending these. */
  grave: boolean;
}

/**
 * The transitions `role` may make from `from`.
 *
 * Two gates, both from the JSON: the edge must be legal for the current
 * state, and the role must be permitted to set the target. A director can
 * issue an approved report but cannot issue a draft, because `draft` does
 * not list `issued` as a transition.
 */
export function actionsFor(from: string, role: string): Action[] {
  const s = STATES.get(from);
  if (!s || !role) return [];
  return (s.transitions ?? [])
    .filter((to) => rolesFor(to).includes(role))
    .map((to) => {
      const t = STATES.get(to);
      return {
        to,
        label: t?.label ?? to,
        clause: t?.clause ?? "",
        description: t?.description ?? "",
        grave: GRAVE.has(to),
      };
    });
}

/**
 * Statuses that end or undo work. Sending one of these asks first: 'revoked'
 * is terminal, and a report bounced back to 'draft' loses its place in the
 * queue. 'rejected' is deliberately not here -- rejecting is a normal review
 * outcome and a reviewer should not have to fight the UI to record one.
 */
const GRAVE = new Set(["revoked", "cancelled", "suspended", "draft"]);

/** True once a certificate of approval may exist for this evaluation. */
export function mayCarryCertificate(status: string): boolean {
  return status === "issued";
}
