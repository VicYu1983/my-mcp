import { createHash } from "node:crypto";
import { parse as parseYaml, stringify as stringifyYaml } from "yaml";
import type { MemoryMeta, Scope } from "./types.js";

const FRONTMATTER = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/;

export interface ParsedMemory {
  meta: MemoryMeta;
  body: string;
}

export function normalizeContent(content: string): string {
  return content.replace(/\r\n/g, "\n").replace(/[ \t]+$/gm, "").trim();
}

export function contentHash(content: string): string {
  return `sha256:${createHash("sha256").update(normalizeContent(content)).digest("hex")}`;
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function asStringArray(value: unknown): string[] {
  if (Array.isArray(value)) return value.filter((v): v is string => typeof v === "string");
  if (typeof value === "string" && value.trim()) return [value.trim()];
  return [];
}

function asScope(value: unknown): Scope {
  return value === "global" || value === "session" ? value : "project";
}

export function serializeMemory(meta: MemoryMeta, body: string): string {
  const frontmatter = {
    id: meta.id,
    scope: meta.scope,
    scope_key: meta.scope_key,
    title: meta.title,
    tags: meta.tags,
    source: meta.source,
    confidence: meta.confidence,
    pinned: meta.pinned,
    supersedes: meta.supersedes,
    created_at: meta.created_at,
    updated_at: meta.updated_at,
    content_hash: meta.content_hash,
  };
  const yaml = stringifyYaml(frontmatter, { lineWidth: 0 }).trimEnd();
  return `---\n${yaml}\n---\n\n${normalizeContent(body)}\n`;
}

export function parseMemory(raw: string): ParsedMemory | null {
  const match = FRONTMATTER.exec(raw);
  if (!match) return null;
  let data: unknown;
  try {
    data = parseYaml(match[1] ?? "");
  } catch {
    return null;
  }
  if (!data || typeof data !== "object") return null;
  const record = data as Record<string, unknown>;
  const id = asString(record["id"]);
  if (!id) return null;

  const body = raw.slice(match[0].length);
  const meta: MemoryMeta = {
    id,
    scope: asScope(record["scope"]),
    scope_key: asString(record["scope_key"], "global"),
    title: asString(record["title"], id),
    tags: asStringArray(record["tags"]),
    source: typeof record["source"] === "string" ? record["source"] : null,
    confidence: typeof record["confidence"] === "number" ? record["confidence"] : 0.8,
    pinned: record["pinned"] === true,
    supersedes: typeof record["supersedes"] === "string" ? record["supersedes"] : null,
    created_at: asString(record["created_at"], new Date(0).toISOString()),
    updated_at: asString(record["updated_at"], new Date(0).toISOString()),
    content_hash: asString(record["content_hash"], contentHash(body)),
  };
  return { meta, body: normalizeContent(body) };
}
