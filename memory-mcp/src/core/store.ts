import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { datePrefix, scopeDirName, slugify } from "./paths.js";
import { parseMemory, serializeMemory, type ParsedMemory } from "./frontmatter.js";
import type { MemoryMeta, Scope } from "./types.js";

export function memoryDir(home: string, scope: Scope, scopeKey: string): string {
  return path.join(home, scopeDirName(scope, scopeKey), "memories");
}

export function trashDir(home: string): string {
  return path.join(home, ".trash");
}

export async function ensureDir(dir: string): Promise<void> {
  await fsp.mkdir(dir, { recursive: true });
}

async function uniquePath(dir: string, base: string): Promise<string> {
  let candidate = path.join(dir, `${base}.md`);
  let counter = 2;
  while (fs.existsSync(candidate)) {
    candidate = path.join(dir, `${base}-${counter}.md`);
    counter += 1;
  }
  return candidate;
}

/** Writes atomically (temp file + rename) so a crash never leaves a half-written memory. */
export async function writeMemoryFile(
  dir: string,
  meta: MemoryMeta,
  body: string,
  existingPath?: string,
): Promise<string> {
  await ensureDir(dir);
  const target =
    existingPath ?? (await uniquePath(dir, `${datePrefix(meta.created_at)}-${slugify(meta.title)}`));
  const tmp = `${target}.${process.pid}.${Date.now()}.tmp`;
  await fsp.writeFile(tmp, serializeMemory(meta, body), "utf8");
  await fsp.rename(tmp, target);
  return target;
}

export async function readMemoryFile(filePath: string): Promise<ParsedMemory | null> {
  try {
    const raw = await fsp.readFile(filePath, "utf8");
    return parseMemory(raw);
  } catch {
    return null;
  }
}

export async function listMemoryFiles(dir: string): Promise<string[]> {
  try {
    const entries = await fsp.readdir(dir, { withFileTypes: true });
    return entries
      .filter((entry) => entry.isFile() && entry.name.endsWith(".md"))
      .map((entry) => path.join(dir, entry.name));
  } catch {
    return [];
  }
}

export async function statFile(filePath: string): Promise<{ mtimeMs: number; size: number } | null> {
  try {
    const stat = await fsp.stat(filePath);
    return { mtimeMs: Math.round(stat.mtimeMs), size: stat.size };
  } catch {
    return null;
  }
}

/** Soft delete: moves the file into `.trash/` so it stays recoverable and out of git history noise. */
export async function moveToTrash(home: string, filePath: string): Promise<void> {
  const dir = trashDir(home);
  await ensureDir(dir);
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const target = path.join(dir, `${stamp}-${path.basename(filePath)}`);
  await fsp.rename(filePath, target);
}

export async function removeFile(filePath: string): Promise<void> {
  await fsp.rm(filePath, { force: true });
}

/** Walks every scope bucket under the memory home and yields memory file paths. */
export async function listAllMemoryFiles(home: string): Promise<string[]> {
  const roots = [
    path.join(home, "global", "memories"),
    path.join(home, "projects"),
    path.join(home, "sessions"),
  ];
  const files: string[] = [];
  for (const root of roots) {
    if (root.endsWith("memories")) {
      files.push(...(await listMemoryFiles(root)));
      continue;
    }
    let buckets: string[];
    try {
      buckets = (await fsp.readdir(root, { withFileTypes: true }))
        .filter((entry) => entry.isDirectory())
        .map((entry) => path.join(root, entry.name, "memories"));
    } catch {
      continue;
    }
    for (const bucket of buckets) {
      files.push(...(await listMemoryFiles(bucket)));
    }
  }
  return files;
}
