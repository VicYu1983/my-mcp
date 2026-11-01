import { listAllMemoryFiles, readMemoryFile, statFile } from "./store.js";
import type { IndexDb } from "./index-db.js";

export interface SyncReport {
  indexed: number;
  removed: number;
  unchanged: number;
  skipped: number;
  durationMs: number;
}

const THROTTLE_MS = 2000;

/**
 * Reconciles the Markdown files (source of truth) with the SQLite index.
 * Files are re-read only when their mtime/size changed, so this stays cheap.
 */
export async function fullScan(home: string, db: IndexDb): Promise<SyncReport> {
  const started = Date.now();
  const files = await listAllMemoryFiles(home);
  const known = db.allPaths();
  const seen = new Set<string>();
  let indexed = 0;
  let unchanged = 0;
  let skipped = 0;

  for (const filePath of files) {
    seen.add(filePath);
    const stat = await statFile(filePath);
    if (!stat) {
      skipped += 1;
      continue;
    }
    const existing = known.get(filePath);
    if (existing && existing.mtime_ms === stat.mtimeMs && existing.size === stat.size) {
      unchanged += 1;
      continue;
    }
    const parsed = await readMemoryFile(filePath);
    if (!parsed) {
      skipped += 1;
      continue;
    }
    db.upsert({
      meta: parsed.meta,
      body: parsed.body,
      path: filePath,
      mtimeMs: stat.mtimeMs,
      size: stat.size,
    });
    indexed += 1;
  }

  let removed = 0;
  for (const [filePath, row] of known) {
    if (seen.has(filePath)) continue;
    db.purge(row.id);
    removed += 1;
  }

  return { indexed, removed, unchanged, skipped, durationMs: Date.now() - started };
}

export class Syncer {
  private lastRun = 0;
  private inFlight: Promise<SyncReport> | null = null;

  constructor(
    private readonly home: string,
    private readonly db: IndexDb,
  ) {}

  /** Runs a full scan at most once per throttle window. */
  async syncIfStale(force = false): Promise<SyncReport | null> {
    if (this.inFlight) return this.inFlight;
    const now = Date.now();
    if (!force && now - this.lastRun < THROTTLE_MS) return null;
    this.inFlight = fullScan(this.home, this.db).finally(() => {
      this.lastRun = Date.now();
      this.inFlight = null;
    });
    return this.inFlight;
  }
}
