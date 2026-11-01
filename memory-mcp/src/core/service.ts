import fsp from "node:fs/promises";
import path from "node:path";
import { loadConfig, defaultSearchScopes, scopeKeyFor, type Config } from "../config.js";
import { contentHash, detectSecrets, findDuplicate, supersededIds } from "./dedupe.js";
import { parseMemory, serializeMemory } from "./frontmatter.js";
import { IndexDb } from "./index-db.js";
import { newId } from "./paths.js";
import { searchMemories } from "./search.js";
import {
  ensureDir,
  listMemoryFiles,
  memoryDir,
  moveToTrash,
  readMemoryFile,
  removeFile,
  statFile,
  writeMemoryFile,
} from "./store.js";
import { fullScan, Syncer, type SyncReport } from "./sync.js";
import type {
  ListOptions,
  Memory,
  MemoryInput,
  MemoryMeta,
  Scope,
  SearchHit,
  SearchOptions,
  WriteResult,
} from "./types.js";

export interface ServiceOptions {
  config?: Config | undefined;
}

export interface ExportResult {
  path: string;
  count: number;
  format: "json" | "markdown";
}

export interface ImportResult {
  imported: number;
  skipped: number;
  conflicts: string[];
  dryRun: boolean;
}

export interface StatsResult {
  total: number;
  byScope: Array<{ scope: string; scope_key: string; count: number }>;
  indexSizeBytes: number;
  home: string;
  projectKey: string;
  sessionId: string;
}

export class MemoryService {
  readonly config: Config;
  readonly db: IndexDb;
  private readonly syncer: Syncer;

  constructor(options: ServiceOptions = {}) {
    this.config = options.config ?? loadConfig();
    this.db = IndexDb.open(this.config.home);
    this.syncer = new Syncer(this.config.home, this.db);
  }

  static async create(options: ServiceOptions = {}): Promise<MemoryService> {
    const service = new MemoryService(options);
    await ensureDir(service.config.home);
    await service.sync(true);
    return service;
  }

  close(): void {
    this.db.close();
  }

  async sync(force = false): Promise<SyncReport | null> {
    return this.syncer.syncIfStale(force);
  }

  private scopeKey(scope: Scope): string {
    return scopeKeyFor(this.config, scope);
  }

  private dirFor(scope: Scope): string {
    return memoryDir(this.config.home, scope, this.scopeKey(scope));
  }

  async write(input: MemoryInput): Promise<WriteResult & { warnings: string[] }> {
    await this.sync();
    const scope = input.scope ?? this.config.defaultScope;
    const scopeKey = this.scopeKey(scope);
    const now = new Date().toISOString();
    const body = input.content;
    const hash = contentHash(body);
    const warnings = detectSecrets(body).map(
      (kind) => `Possible secret detected (${kind}); consider removing it before committing.`,
    );

    if (input.id) {
      const existing = this.db.get(input.id);
      if (!existing) throw new Error(`Memory not found: ${input.id}`);
      const parsed = await readMemoryFile(existing.path);
      if (!parsed) throw new Error(`Memory file is unreadable: ${existing.path}`);
      const meta: MemoryMeta = {
        ...parsed.meta,
        title: input.title ?? parsed.meta.title,
        tags: input.tags ?? parsed.meta.tags,
        source: input.source ?? parsed.meta.source,
        confidence: input.confidence ?? parsed.meta.confidence,
        pinned: input.pinned ?? parsed.meta.pinned,
        supersedes: input.supersedes ?? parsed.meta.supersedes,
        updated_at: now,
        content_hash: hash,
      };
      const filePath = await writeMemoryFile(this.dirFor(meta.scope), meta, body, existing.path);
      const stat = await statFile(filePath);
      this.db.upsert({
        meta,
        body,
        path: filePath,
        mtimeMs: stat?.mtimeMs ?? Date.now(),
        size: stat?.size ?? 0,
      });
      return { id: meta.id, path: filePath, action: "updated", warnings };
    }

    const duplicate = findDuplicate(this.db, scope, scopeKey, body);
    if (duplicate) {
      const row = this.db.get(duplicate)!;
      const parsed = await readMemoryFile(row.path);
      if (parsed) {
        const meta: MemoryMeta = { ...parsed.meta, updated_at: now };
        const filePath = await writeMemoryFile(this.dirFor(scope), meta, parsed.body, row.path);
        const stat = await statFile(filePath);
        this.db.upsert({
          meta,
          body: parsed.body,
          path: filePath,
          mtimeMs: stat?.mtimeMs ?? Date.now(),
          size: stat?.size ?? 0,
        });
      }
      return { id: duplicate, path: row.path, action: "deduped", warnings };
    }

    const meta: MemoryMeta = {
      id: newId(),
      scope,
      scope_key: scopeKey,
      title: input.title?.trim() || deriveTitle(body),
      tags: input.tags ?? [],
      source: input.source ?? null,
      confidence: input.confidence ?? 0.8,
      pinned: input.pinned ?? false,
      supersedes: input.supersedes ?? null,
      created_at: now,
      updated_at: now,
      content_hash: hash,
    };
    const filePath = await writeMemoryFile(this.dirFor(scope), meta, body);
    const stat = await statFile(filePath);
    this.db.upsert({
      meta,
      body,
      path: filePath,
      mtimeMs: stat?.mtimeMs ?? Date.now(),
      size: stat?.size ?? 0,
    });

    const superseded = input.supersedes ? supersededIds(this.db, input.supersedes) : [];
    return {
      id: meta.id,
      path: filePath,
      action: "created",
      ...(superseded.length > 0 ? { superseded } : {}),
      warnings,
    };
  }

  async get(id: string): Promise<Memory | null> {
    await this.sync();
    const row = this.db.get(id);
    if (!row || row.deleted === 1) return null;
    const parsed = await readMemoryFile(row.path);
    if (!parsed) return null;
    return { ...parsed.meta, body: parsed.body, path: row.path };
  }

  async search(options: SearchOptions): Promise<SearchHit[]> {
    await this.sync();
    const scopes = (options.scopes ?? defaultScopes(this.config)).map((scope) => ({
      scope,
      scopeKey: this.scopeKey(scope),
    }));
    return searchMemories(this.db, options, { scopes });
  }

  async list(options: ListOptions = {}): Promise<{ items: Memory[]; total: number }> {
    await this.sync();
    const scopes = (options.scopes ?? defaultScopes(this.config)).map((scope) => ({
      scope,
      scopeKey: this.scopeKey(scope),
    }));
    const { rows, total } = this.db.listRows({
      scopes,
      tags: options.tags,
      limit: Math.min(Math.max(options.limit ?? 20, 1), 200),
      offset: Math.max(options.offset ?? 0, 0),
      sort: options.sort ?? "updated",
    });

    const items: Memory[] = [];
    for (const row of rows) {
      const parsed = await readMemoryFile(row.path);
      if (parsed) items.push({ ...parsed.meta, body: parsed.body, path: row.path });
    }
    return { items, total };
  }

  async delete(id: string, hard = false): Promise<boolean> {
    await this.sync();
    const row = this.db.get(id);
    if (!row || row.deleted === 1) return false;
    if (hard) {
      await removeFile(row.path);
      this.db.purge(id);
    } else {
      await moveToTrash(this.config.home, row.path);
      this.db.markDeleted(id);
    }
    return true;
  }

  async export(options: {
    scopes?: Scope[] | undefined;
    format: "json" | "markdown";
    outPath?: string | undefined;
  }): Promise<ExportResult> {
    await this.sync();
    const scopes = options.scopes ?? defaultScopes(this.config);
    const memories: Memory[] = [];
    for (const scope of scopes) {
      for (const filePath of await listMemoryFiles(this.dirFor(scope))) {
        const parsed = await readMemoryFile(filePath);
        if (parsed) memories.push({ ...parsed.meta, body: parsed.body, path: filePath });
      }
    }

    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    const defaultName = `memory-export-${stamp}.${options.format === "json" ? "json" : "md"}`;
    const outPath = path.resolve(options.outPath ?? path.join(this.config.home, "exports", defaultName));
    await ensureDir(path.dirname(outPath));

    if (options.format === "json") {
      const payload = {
        version: 1,
        exported_at: new Date().toISOString(),
        memories: memories.map((memory) => ({
          ...memory,
          path: undefined,
        })),
      };
      await fsp.writeFile(outPath, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
    } else {
      const sections = memories.map((memory) => serializeMemory(memory, memory.body));
      await fsp.writeFile(outPath, sections.join("\n"), "utf8");
    }

    return { path: outPath, count: memories.length, format: options.format };
  }

  async import(options: {
    data?: string | undefined;
    filePath?: string | undefined;
    format: "json" | "markdown";
    scope?: Scope | undefined;
    dryRun?: boolean | undefined;
  }): Promise<ImportResult> {
    await this.sync();
    const raw = options.data ?? (options.filePath ? await fsp.readFile(options.filePath, "utf8") : "");
    if (!raw.trim()) throw new Error("Nothing to import: provide `data` or `path`.");

    const parsedMemories: Array<{ meta: MemoryMeta; body: string }> = [];
    if (options.format === "json") {
      const payload = JSON.parse(raw) as { memories?: unknown };
      const list = Array.isArray(payload.memories) ? payload.memories : [];
      for (const entry of list) {
        if (!entry || typeof entry !== "object") continue;
        const record = entry as Record<string, unknown>;
        const body = typeof record["body"] === "string" ? record["body"] : "";
        if (!body.trim()) continue;
        parsedMemories.push({
          meta: {
            id: typeof record["id"] === "string" ? record["id"] : newId(),
            scope: options.scope ?? (record["scope"] as Scope) ?? this.config.defaultScope,
            scope_key: "",
            title: typeof record["title"] === "string" ? record["title"] : deriveTitle(body),
            tags: Array.isArray(record["tags"]) ? (record["tags"] as string[]) : [],
            source: typeof record["source"] === "string" ? record["source"] : null,
            confidence: typeof record["confidence"] === "number" ? record["confidence"] : 0.8,
            pinned: record["pinned"] === true,
            supersedes: typeof record["supersedes"] === "string" ? record["supersedes"] : null,
            created_at:
              typeof record["created_at"] === "string" ? record["created_at"] : new Date().toISOString(),
            updated_at:
              typeof record["updated_at"] === "string" ? record["updated_at"] : new Date().toISOString(),
            content_hash: contentHash(body),
          },
          body,
        });
      }
    } else {
      for (const chunk of splitMarkdownMemories(raw)) {
        const parsed = parseMemory(chunk);
        if (!parsed) continue;
        parsedMemories.push({
          meta: { ...parsed.meta, scope: options.scope ?? parsed.meta.scope },
          body: parsed.body,
        });
      }
    }

    const conflicts: string[] = [];
    let imported = 0;
    let skipped = 0;

    for (const entry of parsedMemories) {
      const scope = entry.meta.scope;
      const scopeKey = this.scopeKey(scope);
      const existing = this.db.get(entry.meta.id);
      if (existing) {
        conflicts.push(entry.meta.id);
        skipped += 1;
        continue;
      }
      const duplicate = findDuplicate(this.db, scope, scopeKey, entry.body);
      if (duplicate) {
        conflicts.push(duplicate);
        skipped += 1;
        continue;
      }
      if (options.dryRun) {
        imported += 1;
        continue;
      }
      const meta: MemoryMeta = { ...entry.meta, scope, scope_key: scopeKey };
      const filePath = await writeMemoryFile(this.dirFor(scope), meta, entry.body);
      const stat = await statFile(filePath);
      this.db.upsert({
        meta,
        body: entry.body,
        path: filePath,
        mtimeMs: stat?.mtimeMs ?? Date.now(),
        size: stat?.size ?? 0,
      });
      imported += 1;
    }

    return { imported, skipped, conflicts, dryRun: options.dryRun === true };
  }

  async reindex(): Promise<SyncReport> {
    return fullScan(this.config.home, this.db);
  }

  async stats(): Promise<StatsResult> {
    await this.sync();
    let indexSizeBytes = 0;
    for (const suffix of ["", "-wal", "-shm"]) {
      const stat = await statFile(path.join(this.config.home, `index.db${suffix}`));
      if (stat) indexSizeBytes += stat.size;
    }
    return {
      total: this.db.totalCount(),
      byScope: this.db.countByScope(),
      indexSizeBytes,
      home: this.config.home,
      projectKey: this.config.projectKey,
      sessionId: this.config.sessionId,
    };
  }
}

function defaultScopes(config: Config): Scope[] {
  return defaultSearchScopes(config);
}

export function deriveTitle(body: string): string {
  const firstLine = body
    .split("\n")
    .map((line) => line.replace(/^#+\s*/, "").trim())
    .find((line) => line.length > 0);
  if (!firstLine) return "Untitled memory";
  return firstLine.length > 80 ? `${firstLine.slice(0, 77)}...` : firstLine;
}

/** Splits a concatenated Markdown export back into individual frontmatter blocks. */
export function splitMarkdownMemories(raw: string): string[] {
  const normalized = raw.replace(/\r\n/g, "\n");
  const starts: number[] = [];
  const pattern = /^---$/gm;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(normalized)) !== null) {
    starts.push(match.index);
  }
  const chunks: string[] = [];
  for (let i = 0; i < starts.length; i += 2) {
    const start = starts[i]!;
    const end = starts[i + 2] ?? normalized.length;
    chunks.push(normalized.slice(start, end));
  }
  return chunks;
}
