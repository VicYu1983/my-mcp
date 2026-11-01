import { execFileSync } from "node:child_process";
import os from "node:os";
import path from "node:path";
import { shortHash, slugify } from "./core/paths.js";
import type { Scope } from "./core/types.js";

export interface Config {
  home: string;
  projectKey: string;
  projectRoot: string;
  sessionId: string;
  defaultScope: Scope;
}

export interface ConfigOverrides {
  home?: string | undefined;
  projectRoot?: string | undefined;
  sessionId?: string | undefined;
  defaultScope?: Scope | undefined;
}

/**
 * Some MCP clients expand `${userHome}` to a POSIX-style path such as `/c:/Users/me`.
 * On Windows `path.resolve` would turn that into `C:\c:\Users\me`, so strip the leading slash.
 */
export function normalizeClientPath(value: string): string {
  if (process.platform === "win32" && /^\/[A-Za-z]:[\\/]/.test(value)) {
    return value.slice(1);
  }
  return value;
}

function resolveProjectRoot(explicit?: string): string {
  if (explicit) return path.resolve(normalizeClientPath(explicit));
  try {
    const out = execFileSync("git", ["rev-parse", "--show-toplevel"], {
      cwd: process.cwd(),
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
      timeout: 3000,
    }).trim();
    if (out) return path.resolve(out);
  } catch {
    // not a git repository, or git is unavailable
  }
  return process.cwd();
}

export function projectKeyFor(root: string): string {
  const normalized = path.resolve(root);
  return `${slugify(path.basename(normalized), 32)}-${shortHash(normalized)}`;
}

export function loadConfig(overrides: ConfigOverrides = {}): Config {
  const home = path.resolve(
    normalizeClientPath(
      overrides.home ?? process.env["MEMORY_HOME"] ?? path.join(os.homedir(), ".agent-memory"),
    ),
  );
  const projectRoot = resolveProjectRoot(overrides.projectRoot ?? process.env["MEMORY_PROJECT_ROOT"]);
  const sessionId = slugify(
    overrides.sessionId ?? process.env["MEMORY_SESSION_ID"] ?? "default",
    64,
  );
  const defaultScope = overrides.defaultScope ?? "project";
  return {
    home,
    projectRoot,
    projectKey: projectKeyFor(projectRoot),
    sessionId,
    defaultScope,
  };
}

/** The directory key that identifies a scope bucket on disk. */
export function scopeKeyFor(config: Config, scope: Scope): string {
  if (scope === "global") return "global";
  if (scope === "project") return config.projectKey;
  return config.sessionId;
}

/** Scopes searched by default, ordered from most to least specific. */
export function defaultSearchScopes(config: Config): Scope[] {
  return config.defaultScope === "global" ? ["global"] : ["session", "project", "global"];
}
