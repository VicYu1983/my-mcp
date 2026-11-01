import fs from "node:fs";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import type { IndexRow, MemoryMeta, Scope } from "./types.js";

const SCHEMA_VERSION = 1;

const SCHEMA = `
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memories (
  id TEXT PRIMARY KEY,
  scope TEXT NOT NULL,
  scope_key TEXT NOT NULL,
  path TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL DEFAULT '',
  tags TEXT NOT NULL DEFAULT '',
  source TEXT,
  confidence REAL NOT NULL DEFAULT 0.8,
  pinned INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  mtime_ms INTEGER NOT NULL DEFAULT 0,
  size INTEGER NOT NULL DEFAULT 0,
  deleted INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope, scope_key, deleted);
CREATE INDEX IF NOT EXISTS idx_memories_hash ON memories(content_hash);
CREATE INDEX IF NOT EXISTS idx_memories_updated ON memories(updated_at);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
  id UNINDEXED,
  title,
  body,
  tags,
  tokenize='unicode61 remove_diacritics 2'
);
`;

export interface UpsertRecord {
  meta: MemoryMeta;
  body: string;
  path: string;
  mtimeMs: number;
  size: number;
}

export class IndexDb {
  readonly db: DatabaseSync;

  constructor(filePath: string) {
    this.db = new DatabaseSync(filePath);
    this.db.exec("PRAGMA journal_mode = WAL");
    this.db.exec("PRAGMA busy_timeout = 5000");
    this.db.exec("PRAGMA synchronous = NORMAL");
    this.db.exec(SCHEMA);
    this.db
      .prepare("INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)")
      .run(String(SCHEMA_VERSION));
  }

  static open(home: string): IndexDb {
    // The index lives inside MEMORY_HOME, which may not exist on first run.
    fs.mkdirSync(home, { recursive: true });
    return new IndexDb(path.join(home, "index.db"));
  }

  close(): void {
    this.db.close();
  }

  transaction<T>(fn: () => T): T {
    this.db.exec("BEGIN IMMEDIATE");
    try {
      const result = fn();
      this.db.exec("COMMIT");
      return result;
    } catch (error) {
      this.db.exec("ROLLBACK");
      throw error;
    }
  }

  upsert(record: UpsertRecord): void {
    const { meta, body, path: filePath, mtimeMs, size } = record;
    this.db
      .prepare(
        `INSERT INTO memories (id, scope, scope_key, path, title, tags, source, confidence, pinned,
           created_at, updated_at, content_hash, mtime_ms, size, deleted)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
         ON CONFLICT(id) DO UPDATE SET
           scope=excluded.scope, scope_key=excluded.scope_key, path=excluded.path,
           title=excluded.title, tags=excluded.tags, source=excluded.source,
           confidence=excluded.confidence, pinned=excluded.pinned,
           created_at=excluded.created_at, updated_at=excluded.updated_at,
           content_hash=excluded.content_hash, mtime_ms=excluded.mtime_ms,
           size=excluded.size, deleted=0`,
      )
      .run(
        meta.id,
        meta.scope,
        meta.scope_key,
        filePath,
        meta.title,
        meta.tags.join(","),
        meta.source,
        meta.confidence,
        meta.pinned ? 1 : 0,
        meta.created_at,
        meta.updated_at,
        meta.content_hash,
        mtimeMs,
        size,
      );
    this.db.prepare("DELETE FROM memories_fts WHERE id = ?").run(meta.id);
    this.db
      .prepare("INSERT INTO memories_fts (id, title, body, tags) VALUES (?, ?, ?, ?)")
      .run(meta.id, meta.title, body, meta.tags.join(" "));
  }

  markDeleted(id: string): void {
    this.db.prepare("UPDATE memories SET deleted = 1 WHERE id = ?").run(id);
    this.db.prepare("DELETE FROM memories_fts WHERE id = ?").run(id);
  }

  purge(id: string): void {
    this.db.prepare("DELETE FROM memories WHERE id = ?").run(id);
    this.db.prepare("DELETE FROM memories_fts WHERE id = ?").run(id);
  }

  get(id: string): IndexRow | undefined {
    return this.db.prepare("SELECT * FROM memories WHERE id = ?").get(id) as unknown as IndexRow | undefined;
  }

  getByPath(filePath: string): IndexRow | undefined {
    return this.db.prepare("SELECT * FROM memories WHERE path = ?").get(filePath) as
      | IndexRow
      | undefined;
  }

  findByHash(scope: Scope, scopeKey: string, hash: string): IndexRow | undefined {
    return this.db
      .prepare(
        "SELECT * FROM memories WHERE scope = ? AND scope_key = ? AND content_hash = ? AND deleted = 0 LIMIT 1",
      )
      .get(scope, scopeKey, hash) as unknown as IndexRow | undefined;
  }

  findSuperseding(id: string): IndexRow[] {
    return this.db
      .prepare("SELECT * FROM memories WHERE supersedes = ? AND deleted = 0")
      .all(id) as unknown as IndexRow[];
  }

  allPaths(): Map<string, IndexRow> {
    const rows = this.db.prepare("SELECT * FROM memories").all() as unknown as IndexRow[];
    return new Map(rows.map((row) => [row.path, row]));
  }

  countByScope(): Array<{ scope: string; scope_key: string; count: number }> {
    return this.db
      .prepare(
        "SELECT scope, scope_key, COUNT(*) AS count FROM memories WHERE deleted = 0 GROUP BY scope, scope_key ORDER BY scope, scope_key",
      )
      .all() as unknown as Array<{ scope: string; scope_key: string; count: number }>;
  }

  totalCount(): number {
    const row = this.db
      .prepare("SELECT COUNT(*) AS count FROM memories WHERE deleted = 0")
      .get() as unknown as { count: number };
    return row.count;
  }

  /** Raw FTS5 match; returns ids with bm25 scores (lower is better in SQLite). */
  ftsMatch(match: string, limit: number): Array<{ id: string; bm25: number }> {
    return this.db
      .prepare(
        `SELECT f.id AS id, bm25(memories_fts) AS bm25
         FROM memories_fts f
         JOIN memories m ON m.id = f.id
         WHERE memories_fts MATCH ? AND m.deleted = 0
         ORDER BY bm25 LIMIT ?`,
      )
      .all(match, limit) as unknown as Array<{ id: string; bm25: number }>;
  }

  /** Fallback for CJK and other queries FTS5 tokenizes poorly. */
  likeMatch(needle: string, limit: number): Array<{ id: string }> {
    const pattern = `%${needle.replace(/[%_]/g, (c) => `\\${c}`)}%`;
    return this.db
      .prepare(
        `SELECT id FROM memories
         WHERE deleted = 0 AND (title LIKE ? ESCAPE '\\' OR tags LIKE ? ESCAPE '\\')
         LIMIT ?`,
      )
      .all(pattern, pattern, limit) as unknown as Array<{ id: string }>;
  }

  bodyLikeMatch(needle: string, limit: number): Array<{ id: string }> {
    const pattern = `%${needle.replace(/[%_]/g, (c) => `\\${c}`)}%`;
    return this.db
      .prepare(
        `SELECT f.id AS id FROM memories_fts f
         JOIN memories m ON m.id = f.id
         WHERE m.deleted = 0 AND f.body LIKE ? ESCAPE '\\'
         LIMIT ?`,
      )
      .all(pattern, limit) as unknown as Array<{ id: string }>;
  }

  rowsByIds(ids: string[]): IndexRow[] {
    if (ids.length === 0) return [];
    const placeholders = ids.map(() => "?").join(",");
    return this.db
      .prepare(`SELECT * FROM memories WHERE id IN (${placeholders}) AND deleted = 0`)
      .all(...ids) as unknown as IndexRow[];
  }

  listRows(options: {
    scopes?: Array<{ scope: Scope; scopeKey: string }> | undefined;
    tags?: string[] | undefined;
    limit: number;
    offset: number;
    sort: "updated" | "created" | "title";
  }): { rows: IndexRow[]; total: number } {
    const where: string[] = ["deleted = 0"];
    const params: Array<string | number> = [];

    if (options.scopes && options.scopes.length > 0) {
      const clauses = options.scopes.map(() => "(scope = ? AND scope_key = ?)");
      where.push(`(${clauses.join(" OR ")})`);
      for (const entry of options.scopes) params.push(entry.scope, entry.scopeKey);
    }
    for (const tag of options.tags ?? []) {
      where.push("(',' || tags || ',') LIKE ?");
      params.push(`%,${tag},%`);
    }

    const whereSql = where.join(" AND ");
    const orderSql =
      options.sort === "title"
        ? "title COLLATE NOCASE ASC"
        : options.sort === "created"
          ? "created_at DESC"
          : "updated_at DESC";

    const total = (
      this.db.prepare(`SELECT COUNT(*) AS count FROM memories WHERE ${whereSql}`).get(...params) as {
        count: number;
      }
    ).count;

    const rows = this.db
      .prepare(
        `SELECT * FROM memories WHERE ${whereSql} ORDER BY ${orderSql} LIMIT ? OFFSET ?`,
      )
      .all(...params, options.limit, options.offset) as unknown as IndexRow[];

    return { rows, total };
  }

  bodyFor(id: string): string {
    const row = this.db.prepare("SELECT body FROM memories_fts WHERE id = ?").get(id) as
      | { body: string }
      | undefined;
    return row?.body ?? "";
  }
}
