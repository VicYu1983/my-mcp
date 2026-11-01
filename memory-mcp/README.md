# agent-memory-mcp

A **Markdown-first long-term memory MCP server** for AI agents. It lets an agent remember
decisions, conventions, gotchas, and user preferences across conversations — and recall them
cheaply when they matter.

- **Markdown is the source of truth.** Every memory is a plain `.md` file with YAML frontmatter.
  Readable, hand-editable, and diff-friendly in git.
- **SQLite is a disposable index.** Full-text search runs on FTS5 (BM25). Delete `index.db` any
  time; `memory_reindex` rebuilds it from the files.
- **Zero external services.** No embedding API, no vector database, no native compilation —
  it uses Node's built-in `node:sqlite`.

## Requirements

Node.js **>= 24** (for `node:sqlite` with FTS5).

## Install

```bash
npm install
npm run build
```

## Configure your MCP client

### VS Code / Copilot — global (all workspaces)

Install once, use from every project. Write `~/.copilot/mcp-config.json`
(`$COPILOT_HOME/mcp-config.json` when `COPILOT_HOME` is set):

```json
{
  "mcpServers": {
    "agent-memory": {
      "type": "stdio",
      "command": "node",
      "args": ["C:/path/to/memory-mcp/dist/index.js"],
      "env": { "MEMORY_HOME": "C:/Users/you/.agent-memory" }
    }
  }
}
```

Use absolute paths: the server's working directory is the current workspace, and
`${userHome}` may expand to a POSIX-style `/c:/...` path that breaks on Windows.

Then:

1. Run `npm install && npm run build` (the server runs `dist/index.js`, not the TypeScript sources).
2. Run **MCP: List Servers** → select `agent-memory` → **Start**.
3. In Copilot Chat, open **Configure Tools** (or **Chat: Open Customizations** → **Tools**) and make sure the `memory_*` tools are enabled.
4. Verify with a prompt like: *"Use the agent-memory MCP server to write a memory that this project uses pnpm, then search for it."*

Memories are bucketed per project automatically, so one global server instance serves every
repository without mixing them.

### VS Code — workspace only

Use `.vscode/mcp.json` (VS Code `servers` format) instead when you want the server shared with
the repository rather than with your whole machine:

```json
{
  "servers": {
    "agent-memory": {
      "type": "stdio",
      "command": "node",
      "args": ["${workspaceFolder}/dist/index.js"],
      "env": { "MEMORY_HOME": "${env:USERPROFILE}/.agent-memory" }
    }
  }
}
```

Then:

1. Run `npm install && npm run build` (the server runs `dist/index.js`, not the TypeScript sources).
2. Open the Command Palette → **MCP: List Servers** → select `agent-memory` → **Start**.
3. In Copilot Chat, open **Configure Tools** (or **Chat: Open Customizations** → **Tools**) and make sure the `memory_*` tools are enabled.
4. Verify with a prompt like: *"Use the agent-memory MCP server to write a memory that this project uses pnpm, then search for it."*

To add it to a different workspace instead, run **MCP: Add Server** → **Command (stdio)** and enter
`node <absolute-path>/dist/index.js`.

Do not configure the same server name in both the global and workspace files: VS Code loads both,
so two processes would share one `index.db`.

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "agent-memory": {
      "command": "node",
      "args": ["C:/path/to/memory-mcp/dist/index.js"],
      "env": { "MEMORY_HOME": "C:/Users/you/.agent-memory" }
    }
  }
}
```

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `MEMORY_HOME` | `~/.agent-memory` | Root directory for all memories and the index |
| `MEMORY_PROJECT_ROOT` | git toplevel of cwd, else cwd | Which project bucket to use |
| `MEMORY_SESSION_ID` | `default` | Session bucket name |

## Tools

| Tool | Purpose |
|---|---|
| `memory_write` | Store a memory. Deduplicates identical content per scope. Pass `id` to update, `supersedes` to replace. |
| `memory_search` | Ranked full-text search (BM25 × recency × pinned × scope boost). |
| `memory_get` | Fetch one memory in full by id. |
| `memory_list` | Browse memories, newest first, with tag filters and pagination. |
| `memory_delete` | Soft delete to `.trash/`, or `hard: true` to erase. |
| `memory_export` | Export to JSON (lossless) or Markdown (human/git friendly). |
| `memory_import` | Import from JSON or Markdown; skips duplicates and reports conflicts. |
| `memory_reindex` | Reconcile the index with the files after manual edits or a `git pull`. |
| `memory_stats` | Counts per scope, index size, resolved paths. |

Resource: `memory://{scope}/{id}` returns a single memory as Markdown.

## Scopes

| Scope | Directory | Use for |
|---|---|---|
| `global` | `global/memories/` | Cross-project preferences ("always use pnpm") |
| `project` | `projects/<name>-<hash8>/memories/` | Repository knowledge, decisions, gotchas |
| `session` | `sessions/<id>/memories/` | Scratch notes for the current task |

Search defaults to all three, with a small ranking boost toward the more specific scopes.

## On-disk layout

```
~/.agent-memory/
├── global/memories/2026-10-31-use-pnpm.md
├── projects/memory-mcp-256db399/memories/2026-10-31-sqlite-driver.md
├── sessions/default/memories/
├── .trash/                 # soft-deleted memories
├── exports/                # default export destination
└── index.db                # derived FTS5 index (safe to delete)
```

A memory file looks like this:

```markdown
---
id: mem_01M68ZZT3E0JGH1WHVZTHZ66XH
scope: project
scope_key: memory-mcp-256db399
title: 套件管理器
tags:
  - convention
source: null
confidence: 0.8
pinned: false
supersedes: null
created_at: 2026-10-31T12:47:09.933Z
updated_at: 2026-10-31T12:47:09.933Z
content_hash: sha256:...
---

這個專案用 pnpm，不要用 npm install。
```

## How ranking works

$$\text{score} = \text{bm25}_{\text{normalized}} \times e^{-\text{ageDays}/90} \times (1 + 0.5\cdot\text{pinned}) \times \text{scopeBoost}$$

`scopeBoost` is 1.15 for session, 1.10 for project, 1.0 for global. BM25 is normalized against
the best hit in each query so the recency and pinned factors actually matter.

FTS5's `unicode61` tokenizer splits CJK poorly, so when a query returns no FTS hits the server
falls back to a `LIKE` scan over titles, tags, and bodies.

## Design notes

- **Atomic writes.** Files are written to a temp path and renamed, so a crash never leaves a
  half-written memory.
- **Incremental sync.** On startup the server scans all memory files; during a session it
  re-scans at most every 2 seconds, and only re-reads files whose mtime or size changed.
- **Dedupe.** Content is normalized (CRLF, trailing whitespace) and hashed. Writing identical
  content into the same scope returns `action: "deduped"` instead of creating a second file.
- **Secret warning.** `memory_write` detects common credential patterns and returns a warning.
  It never blocks the write — that is your call.

## Security

**Do not store secrets, tokens, passwords, or personal data in memories.** They are plain text
on disk and will be committed if you version `MEMORY_HOME` with git. The built-in detection is a
convenience, not a guarantee.

## Development

```bash
npm run typecheck   # tsc --noEmit
npm test            # vitest
npm run build       # emit dist/
node scripts/e2e.mjs  # end-to-end stdio smoke test against dist/
```

## Not included (yet)

Vector/semantic retrieval, automatic conversation summarization, memory decay/forgetting, and an
entity relation graph. The architecture leaves room for them: `search.ts` can take a reranker,
and the `memories` table can gain an `embedding` column without touching the file format.
