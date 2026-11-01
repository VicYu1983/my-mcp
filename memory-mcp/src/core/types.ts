export type Scope = "global" | "project" | "session";

export const SCOPES: readonly Scope[] = ["global", "project", "session"];

export interface MemoryMeta {
  id: string;
  scope: Scope;
  scope_key: string;
  title: string;
  tags: string[];
  source: string | null;
  confidence: number;
  pinned: boolean;
  supersedes: string | null;
  created_at: string;
  updated_at: string;
  content_hash: string;
}

export interface Memory extends MemoryMeta {
  body: string;
  path: string;
}

export interface MemoryInput {
  content: string;
  title?: string | undefined;
  scope?: Scope | undefined;
  tags?: string[] | undefined;
  source?: string | undefined;
  confidence?: number | undefined;
  pinned?: boolean | undefined;
  supersedes?: string | undefined;
  id?: string | undefined;
}

export interface WriteResult {
  id: string;
  path: string;
  action: "created" | "updated" | "deduped";
  superseded?: string[];
}

export interface SearchHit {
  id: string;
  title: string;
  score: number;
  snippet: string;
  scope: Scope;
  scope_key: string;
  tags: string[];
  pinned: boolean;
  updated_at: string;
  path: string;
}

export interface SearchOptions {
  query: string;
  scopes?: Scope[] | undefined;
  tags?: string[] | undefined;
  limit?: number | undefined;
  since?: string | undefined;
}

export interface ListOptions {
  scopes?: Scope[] | undefined;
  tags?: string[] | undefined;
  limit?: number | undefined;
  offset?: number | undefined;
  sort?: "updated" | "created" | "title" | undefined;
}

export interface IndexRow {
  id: string;
  scope: Scope;
  scope_key: string;
  path: string;
  title: string;
  tags: string;
  source: string | null;
  confidence: number;
  pinned: number;
  created_at: string;
  updated_at: string;
  content_hash: string;
  mtime_ms: number;
  size: number;
  deleted: number;
}
