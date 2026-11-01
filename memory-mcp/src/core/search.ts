import type { IndexDb } from "./index-db.js";
import type { IndexRow, Scope, SearchHit, SearchOptions } from "./types.js";

const HALF_LIFE_DAYS = 90;
const PINNED_BOOST = 0.5;
const SCOPE_BOOST: Record<Scope, number> = { session: 1.15, project: 1.1, global: 1.0 };
const SNIPPET_RADIUS = 140;

const CJK = /[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]/;

/** Builds an FTS5 MATCH expression: quoted phrases, prefix matching, OR between terms. */
export function buildMatchExpression(query: string): string {
  const terms = query
    .split(/\s+/)
    .map((term) => term.replace(/["*()]/g, "").trim())
    .filter((term) => term.length > 0);

  if (terms.length === 0) return "";

  return terms
    .map((term) => {
      const quoted = `"${term.replace(/"/g, '""')}"`;
      // Prefix matching helps partial words; CJK terms are matched as whole phrases.
      return CJK.test(term) ? quoted : `${quoted}*`;
    })
    .join(" OR ");
}

function recencyFactor(updatedAt: string, now: number): number {
  const ts = Date.parse(updatedAt);
  if (Number.isNaN(ts)) return 0.5;
  const ageDays = Math.max(0, (now - ts) / 86_400_000);
  return Math.exp(-ageDays / HALF_LIFE_DAYS);
}

function makeSnippet(body: string, query: string): string {
  const flat = body.replace(/\s+/g, " ").trim();
  if (flat.length === 0) return "";
  const terms = query
    .split(/\s+/)
    .map((term) => term.toLowerCase())
    .filter((term) => term.length > 0);
  const haystack = flat.toLowerCase();

  let index = -1;
  for (const term of terms) {
    const found = haystack.indexOf(term);
    if (found >= 0 && (index < 0 || found < index)) index = found;
  }
  if (index < 0) return flat.slice(0, SNIPPET_RADIUS * 2);

  const start = Math.max(0, index - SNIPPET_RADIUS);
  const end = Math.min(flat.length, index + SNIPPET_RADIUS);
  const prefix = start > 0 ? "…" : "";
  const suffix = end < flat.length ? "…" : "";
  return `${prefix}${flat.slice(start, end)}${suffix}`;
}

function parseTags(raw: string): string[] {
  return raw ? raw.split(",").filter((tag) => tag.length > 0) : [];
}

function toHit(row: IndexRow, score: number, snippet: string): SearchHit {
  return {
    id: row.id,
    title: row.title,
    score: Number(score.toFixed(4)),
    snippet,
    scope: row.scope,
    scope_key: row.scope_key,
    tags: parseTags(row.tags),
    pinned: row.pinned === 1,
    updated_at: row.updated_at,
    path: row.path,
  };
}

export interface SearchContext {
  scopes: Array<{ scope: Scope; scopeKey: string }>;
}

export function searchMemories(
  db: IndexDb,
  options: SearchOptions,
  context: SearchContext,
): SearchHit[] {
  const limit = Math.min(Math.max(options.limit ?? 10, 1), 100);
  const query = options.query.trim();
  if (query.length === 0) return [];

  const candidateLimit = Math.max(limit * 8, 50);
  const raw = new Map<string, number>();
  let usedFts = false;

  const match = buildMatchExpression(query);
  if (match) {
    try {
      for (const row of db.ftsMatch(match, candidateLimit)) {
        // SQLite's bm25() is negative and lower-is-better; flip so higher is better.
        raw.set(row.id, -row.bm25);
      }
      usedFts = raw.size > 0;
    } catch {
      // Malformed MATCH expression: fall through to the LIKE fallback below.
    }
  }

  if (!usedFts) {
    for (const row of db.likeMatch(query, candidateLimit)) raw.set(row.id, 1);
    for (const row of db.bodyLikeMatch(query, candidateLimit)) {
      if (!raw.has(row.id)) raw.set(row.id, 0.8);
    }
  }

  if (raw.size === 0) return [];

  // bm25 magnitudes vary wildly with corpus size, so normalize against the best hit.
  const best = Math.max(...raw.values());
  const scores = new Map<string, number>();
  for (const [id, value] of raw) {
    scores.set(id, best > 0 ? value / best : 1);
  }

  const rows = db.rowsByIds([...scores.keys()]);
  const now = Date.now();
  const scopeSet = new Set(context.scopes.map((entry) => `${entry.scope}:${entry.scopeKey}`));
  const sinceTs = options.since ? Date.parse(options.since) : Number.NaN;

  const hits: SearchHit[] = [];
  for (const row of rows) {
    if (scopeSet.size > 0 && !scopeSet.has(`${row.scope}:${row.scope_key}`)) continue;
    if (!Number.isNaN(sinceTs) && Date.parse(row.updated_at) < sinceTs) continue;

    const tags = parseTags(row.tags);
    if (options.tags && options.tags.length > 0) {
      const wanted = options.tags.map((tag) => tag.toLowerCase());
      if (!wanted.every((tag) => tags.some((have) => have.toLowerCase() === tag))) continue;
    }

    const base = scores.get(row.id) ?? 0;
    const score =
      base *
      recencyFactor(row.updated_at, now) *
      (1 + (row.pinned === 1 ? PINNED_BOOST : 0)) *
      SCOPE_BOOST[row.scope];

    hits.push(toHit(row, score, makeSnippet(db.bodyFor(row.id), query)));
  }

  hits.sort((a, b) => b.score - a.score);
  return hits.slice(0, limit);
}
