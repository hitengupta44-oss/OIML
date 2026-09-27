/**
 * Backend client.
 *
 * The reporter is a plain FastAPI service on a Hugging Face Docker Space,
 * so this is ordinary REST — no SDK, no queue protocol, no SSE parsing.
 *
 * Every call carries the Supabase access token. The Space validates it
 * before doing any work, because a Space URL is public.
 */

const SPACE_URL = (import.meta.env.VITE_SPACE_URL as string ?? "").replace(/\/$/, "");

export interface RenderResult {
  ok: boolean;
  ref?: string;
  version?: number;
  verdict?: string;
  payload_sha256?: string;
  is_synthetic?: boolean;
  docx_url?: string | null;
  pdf_url?: string | null;
  pdf_available?: boolean;
  note?: string | null;
  error?: string;
  kind?: string;
  divergences?: unknown[];
}

export interface Divergence {
  observation_id?: number;
  load_g: number;
  client_verdict: string;
  server_verdict: string;
  server_Ec_g: number;
  server_mpe_g: number;
}

export interface VerifyResult {
  ok: boolean;
  ref?: string;
  verdict?: string;
  agrees_with_client?: boolean;
  divergences?: Divergence[];
  specification?: { verdict: string; findings: unknown[] };
  tests?: Record<string, { verdict: string; rows: number }>;
  payload_sha256?: string;
  error?: string;
}

async function post<T>(path: string, token: string, body: unknown): Promise<T> {
  if (!SPACE_URL) {
    throw new Error("VITE_SPACE_URL is not set; the reporter is unreachable.");
  }
  const res = await fetch(`${SPACE_URL}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(body),
  });

  // 409 is the deliberate refusal to render over a divergence, and its
  // body carries the divergences — so it is a result, not a failure.
  if (res.status === 409) return (await res.json()) as T;

  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      detail = j.detail ?? j.error ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export const verifyEvaluation = (token: string, ref: string) =>
  post<VerifyResult>("/verify", token, { ref });

export const renderReport = (token: string, ref: string) =>
  post<RenderResult>("/render", token, { ref });

/**
 * Render straight from a posted evaluation, for offline-captured work that
 * has not synced yet. Returns the PDF as a blob rather than a URL.
 */
export async function renderFromPayload(token: string, evaluation: unknown) {
  const res = await fetch(`${SPACE_URL}/render-payload`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ evaluation }),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return {
    blob: await res.blob(),
    verdict: res.headers.get("X-Verdict"),
    sha256: res.headers.get("X-Payload-SHA256"),
  };
}

/**
 * Keep-warm ping. A free CPU Space sleeps after ~48 h idle and takes
 * 30–60 s to wake, so the app pings on load rather than letting a user
 * trigger the first request.
 */
export async function wake(): Promise<boolean> {
  if (!SPACE_URL) return false;
  try {
    const res = await fetch(`${SPACE_URL}/health`);
    return res.ok;
  } catch {
    return false;
  }
}
