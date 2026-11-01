import { createHash, randomBytes } from "node:crypto";
import path from "node:path";
import type { Scope } from "./types.js";

const CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";

/** ULID-style identifier: 10 chars of time + 16 chars of randomness, lexicographically sortable. */
export function newId(now: number = Date.now()): string {
  let time = "";
  let t = now;
  for (let i = 0; i < 10; i++) {
    time = CROCKFORD[t % 32] + time;
    t = Math.floor(t / 32);
  }
  const bytes = randomBytes(16);
  let rand = "";
  for (let i = 0; i < 16; i++) {
    rand += CROCKFORD[bytes[i]! % 32];
  }
  return `mem_${time}${rand}`;
}

export function isMemoryId(value: string): boolean {
  return /^mem_[0-9A-HJKMNP-TV-Z]{26}$/.test(value);
}

/** Filesystem-safe slug that keeps unicode letters (so CJK titles stay readable). */export function slugify(input: string, maxLength = 48): string {
  const slug = input
    .normalize("NFKC")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, "-")
    .replace(/^-+|-+$/g, "");
  const trimmed = slug.slice(0, maxLength).replace(/-+$/g, "");
  return trimmed.length > 0 ? trimmed : "memory";
}

export function shortHash(value: string, length = 8): string {
  return createHash("sha256").update(value).digest("hex").slice(0, length);
}

export function scopeDirName(scope: Scope, scopeKey: string): string {
  if (scope === "global") return "global";
  if (scope === "project") return path.join("projects", scopeKey);
  return path.join("sessions", scopeKey);
}

export function datePrefix(iso: string): string {
  return iso.slice(0, 10);
}
