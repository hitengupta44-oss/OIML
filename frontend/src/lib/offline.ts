/**
 * Offline capture.
 *
 * This is not a nicety. EMC and climatic testing happens inside shielded
 * chambers -- a Faraday cage has no Wi-Fi by design -- temperature runs
 * last hours, damp heat days, span stability 28 days, and weighbridge
 * testing happens outdoors. Software that needs connectivity to record a
 * reading is software the laboratory will work around.
 *
 * So: observations are written to IndexedDB first and always. Sync is a
 * background reconciliation, not a precondition for doing the work.
 *
 * The engine runs locally too, so verdicts appear immediately with no
 * network. The server recomputes on submission and flags any divergence.
 */

import Dexie, { type Table } from "dexie";
import { insertObservations } from "./supabase";

export interface QueuedObservation {
  id?: number;
  localId: string;
  evaluationId: string;
  ref: string;
  row: Record<string, unknown>;
  capturedAt: number;
  syncedAt?: number;
  error?: string;
}

export interface CachedEvaluation {
  ref: string;
  data: unknown;
  cachedAt: number;
}

class NawiDB extends Dexie {
  queue!: Table<QueuedObservation, number>;
  evaluations!: Table<CachedEvaluation, string>;

  constructor() {
    super("nawi");
    this.version(1).stores({
      queue: "++id, localId, evaluationId, ref, syncedAt",
      evaluations: "ref, cachedAt",
    });
  }
}

export const db = new NawiDB();

export const isOnline = () => navigator.onLine;

/** Record a reading. Always succeeds; the network is irrelevant here. */
export async function captureObservation(
  evaluationId: string,
  ref: string,
  row: Record<string, unknown>
): Promise<string> {
  const localId = crypto.randomUUID();
  await db.queue.add({
    localId, evaluationId, ref, row, capturedAt: Date.now(),
  });
  if (isOnline()) void sync();
  return localId;
}

export const pendingCount = () =>
  db.queue.filter((q) => !q.syncedAt).count();

export const pendingForRef = (ref: string) =>
  db.queue.where("ref").equals(ref).filter((q) => !q.syncedAt).toArray();

let syncing = false;

/** Push everything unsynced. Safe to call repeatedly. */
export async function sync(): Promise<{ sent: number; failed: number }> {
  if (syncing || !isOnline()) return { sent: 0, failed: 0 };
  syncing = true;
  let sent = 0;
  let failed = 0;

  try {
    const pending = await db.queue.filter((q) => !q.syncedAt).toArray();
    // Group by evaluation so each POST is one round trip.
    const byEval = new Map<string, QueuedObservation[]>();
    for (const item of pending) {
      const list = byEval.get(item.evaluationId) ?? [];
      list.push(item);
      byEval.set(item.evaluationId, list);
    }

    for (const [, items] of byEval) {
      try {
        await insertObservations(items.map((i) => i.row));
        const now = Date.now();
        await db.queue.bulkUpdate(
          items.map((i) => ({ key: i.id!, changes: { syncedAt: now, error: undefined } }))
        );
        sent += items.length;
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        await db.queue.bulkUpdate(
          items.map((i) => ({ key: i.id!, changes: { error: message } }))
        );
        failed += items.length;
      }
    }
  } finally {
    syncing = false;
  }
  return { sent, failed };
}

export async function cacheEvaluation(ref: string, data: unknown) {
  await db.evaluations.put({ ref, data, cachedAt: Date.now() });
}

export async function readCachedEvaluation(ref: string) {
  return (await db.evaluations.get(ref))?.data ?? null;
}

/** Clear rows that synced more than a week ago. */
export async function prune(olderThanMs = 7 * 24 * 60 * 60 * 1000) {
  const cutoff = Date.now() - olderThanMs;
  await db.queue.filter((q) => !!q.syncedAt && q.syncedAt < cutoff).delete();
}

export function watchConnectivity(onChange: (online: boolean) => void) {
  const up = () => { onChange(true); void sync(); };
  const down = () => onChange(false);
  window.addEventListener("online", up);
  window.addEventListener("offline", down);
  return () => {
    window.removeEventListener("online", up);
    window.removeEventListener("offline", down);
  };
}
