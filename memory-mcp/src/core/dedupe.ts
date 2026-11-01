import { contentHash, normalizeContent } from "./frontmatter.js";
import type { IndexDb } from "./index-db.js";
import type { Scope } from "./types.js";

export { contentHash, normalizeContent };

export function findDuplicate(
  db: IndexDb,
  scope: Scope,
  scopeKey: string,
  content: string,
): string | null {
  const row = db.findByHash(scope, scopeKey, contentHash(content));
  return row?.id ?? null;
}

/**
 * Walks the supersedes chain forward: every memory that (transitively) replaced `id`.
 * Used to report what a new write invalidated.
 */
export function supersededIds(db: IndexDb, id: string): string[] {
  const out: string[] = [];
  const seen = new Set<string>([id]);
  const queue = [id];
  while (queue.length > 0) {
    const current = queue.shift()!;
    for (const row of db.findSuperseding(current)) {
      if (seen.has(row.id)) continue;
      seen.add(row.id);
      out.push(row.id);
      queue.push(row.id);
    }
  }
  return out;
}

const SECRET_PATTERNS: Array<{ name: string; pattern: RegExp }> = [
  { name: "private key block", pattern: /-----BEGIN [A-Z ]*PRIVATE KEY-----/ },
  { name: "AWS access key", pattern: /\bAKIA[0-9A-Z]{16}\b/ },
  { name: "GitHub token", pattern: /\bgh[pousr]_[A-Za-z0-9]{20,}\b/ },
  { name: "OpenAI-style key", pattern: /\bsk-[A-Za-z0-9_-]{20,}\b/ },
  { name: "Slack token", pattern: /\bxox[abprs]-[A-Za-z0-9-]{10,}\b/ },
  { name: "JWT", pattern: /\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b/ },
  {
    name: "assigned password/secret",
    pattern: /\b(password|passwd|secret|api[_-]?key|token)\s*[:=]\s*["']?[^\s"']{8,}/i,
  },
];

/** Best-effort warning only: memory content is never blocked, but the caller is told. */
export function detectSecrets(content: string): string[] {
  return SECRET_PATTERNS.filter((entry) => entry.pattern.test(content)).map((entry) => entry.name);
}
