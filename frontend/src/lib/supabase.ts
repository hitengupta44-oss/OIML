/**
 * Supabase client — anon key only.
 *
 * The service-role key bypasses row-level security entirely and lives on
 * the Space, never here. `npm run check:secrets` greps the built bundle
 * for it before every deploy.
 *
 * Every query below relies on RLS for isolation rather than filtering by
 * lab in the client: a filter a user can edit is not a security boundary.
 */

import { createClient, type Session } from "@supabase/supabase-js";

const url = import.meta.env.VITE_SUPABASE_URL as string;
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY as string;

if (!url || !anonKey) {
  console.warn(
    "VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY are not set. " +
      "Copy frontend/.env.example to frontend/.env.local."
  );
}

export const supabase = createClient(url ?? "", anonKey ?? "", {
  auth: { persistSession: true, autoRefreshToken: true },
});

export type LabRole =
  | "technician" | "reviewer" | "approver" | "director" | "admin" | "auditor";

export interface Me {
  userId: string;
  email?: string;
  role: LabRole;
  labId?: string;
  accessToken: string;
}

/** Roles live in app_metadata; user_metadata is user-editable and unsafe. */
export function meFromSession(session: Session | null): Me | null {
  if (!session?.user) return null;
  const meta = (session.user.app_metadata ?? {}) as Record<string, unknown>;
  return {
    userId: session.user.id,
    email: session.user.email ?? undefined,
    role: (meta.lab_role as LabRole) ?? "auditor",
    labId: (meta.lab_id as string) ?? undefined,
    accessToken: session.access_token,
  };
}

export async function getMe(): Promise<Me | null> {
  const { data } = await supabase.auth.getSession();
  return meFromSession(data.session);
}

export async function signIn(email: string, password: string) {
  const { data, error } = await supabase.auth.signInWithPassword({ email, password });
  if (error) throw error;
  return meFromSession(data.session);
}

export const signOut = () => supabase.auth.signOut();

// --- queries ---------------------------------------------------------------

export async function listEvaluations(search = "", status?: string) {
  let q = supabase
    .from("v_evaluation_summary")
    .select("*")
    .order("created_at", { ascending: false })
    .limit(200);
  if (status) q = q.eq("status", status);
  if (search.trim()) {
    const s = `%${search.trim()}%`;
    q = q.or(`ref.ilike.${s},manufacturer.ilike.${s},model.ilike.${s}`);
  }
  const { data, error } = await q;
  if (error) throw error;
  return data ?? [];
}

export async function dashboardCounts() {
  const { data, error } = await supabase
    .from("v_dashboard_counts")
    .select("*")
    .order("total", { ascending: false });
  if (error) throw error;
  return data ?? [];
}

export async function fetchEvaluation(ref: string) {
  const { data, error } = await supabase
    .from("evaluation")
    .select(
      `id, ref, status, verdict, standard_id, jurisdiction, zero_error_g,
       approval_mark, serial_no, is_synthetic, lab_id,
       lab:lab_id ( code, name ),
       model:model_id (
         model, instrument_type, accuracy_class, max_capacity_g, e_g, d_g,
         min_capacity_g, n, indication_type, has_aux_device, is_grading,
         manufacturer:manufacturer_id ( name )
       )`
    )
    .eq("ref", ref)
    .single();
  if (error) throw error;
  return data;
}

export async function fetchObservations(evaluationId: string) {
  const { data, error } = await supabase
    .from("observation")
    .select("*")
    .eq("evaluation_id", evaluationId)
    .is("superseded_by", null)          // superseded rows stay for the audit trail
    .order("test_code")
    .order("sequence_no");
  if (error) throw error;
  return data ?? [];
}

export async function insertObservations(rows: Record<string, unknown>[]) {
  const { data, error } = await supabase.from("observation").insert(rows).select();
  if (error) throw error;
  return data ?? [];
}

/**
 * Corrections never overwrite. A new row is inserted and the old one is
 * pointed at it; the trigger in 0002_functions.sql rejects any attempt to
 * change a reading in place.
 */
export async function supersedeObservation(
  oldId: number,
  replacement: Record<string, unknown>,
  reason: string
) {
  const inserted = await insertObservations([replacement]);
  const newId = (inserted[0] as { id: number }).id;
  const { error } = await supabase
    .from("observation")
    .update({ superseded_by: newId, supersede_reason: reason })
    .eq("id", oldId);
  if (error) throw error;
  return newId;
}

export async function setStatus(ref: string, status: string) {
  const { error } = await supabase.from("evaluation").update({ status }).eq("ref", ref);
  if (error) throw error;
}
