import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { loadConfig } from "../src/config.js";
import { MemoryService } from "../src/core/service.js";

let home: string;
let service: MemoryService;

async function makeService(overrides: { sessionId?: string; projectRoot?: string } = {}) {
  const config = loadConfig({
    home,
    projectRoot: overrides.projectRoot ?? path.join(home, "repo"),
    sessionId: overrides.sessionId ?? "s1",
  });
  return MemoryService.create({ config });
}

beforeEach(async () => {
  home = await fs.mkdtemp(path.join(os.tmpdir(), "agent-memory-test-"));
  service = await makeService();
});

afterEach(async () => {
  service.close();
  await fs.rm(home, { recursive: true, force: true });
});

describe("write and read", () => {
  it("creates a markdown file with frontmatter and reads it back", async () => {
    const result = await service.write({
      content: "Use node:sqlite instead of better-sqlite3 to avoid native builds.",
      title: "SQLite driver choice",
      tags: ["decision", "storage"],
    });

    expect(result.action).toBe("created");
    const raw = await fs.readFile(result.path, "utf8");
    expect(raw.startsWith("---\n")).toBe(true);
    expect(raw).toContain("title: SQLite driver choice");
    expect(raw).toContain("Use node:sqlite");

    const memory = await service.get(result.id);
    expect(memory?.title).toBe("SQLite driver choice");
    expect(memory?.tags).toEqual(["decision", "storage"]);
    expect(memory?.scope).toBe("project");
  });

  it("derives a title from the first line when none is given", async () => {
    const result = await service.write({ content: "# Build quirk\n\nRun pnpm build twice." });
    const memory = await service.get(result.id);
    expect(memory?.title).toBe("Build quirk");
  });

  it("updates in place when an id is supplied", async () => {
    const created = await service.write({ content: "first version", title: "Note" });
    const updated = await service.write({ id: created.id, content: "second version" });

    expect(updated.action).toBe("updated");
    expect(updated.path).toBe(created.path);
    const memory = await service.get(created.id);
    expect(memory?.body).toBe("second version");
    expect(memory?.title).toBe("Note");
  });
});

describe("dedupe", () => {
  it("does not create a second file for identical content in the same scope", async () => {
    const first = await service.write({ content: "Same fact.", title: "A" });
    const second = await service.write({ content: "Same fact.", title: "B" });

    expect(second.action).toBe("deduped");
    expect(second.id).toBe(first.id);
    expect(await service.stats().then((s) => s.total)).toBe(1);
  });

  it("allows the same content in a different scope", async () => {
    await service.write({ content: "Same fact.", scope: "project" });
    const global = await service.write({ content: "Same fact.", scope: "global" });
    expect(global.action).toBe("created");
  });
});

describe("scope isolation", () => {
  it("keeps project and global memories in separate directories", async () => {
    const project = await service.write({ content: "project only", scope: "project" });
    const global = await service.write({ content: "global only", scope: "global" });

    expect(project.path).toContain(path.join("projects"));
    expect(global.path).toContain(path.join("global", "memories"));
  });

  it("does not leak another project's memories into search", async () => {
    await service.write({ content: "alpha project secret", scope: "project" });
    service.close();

    service = await makeService({ projectRoot: path.join(home, "other-repo") });
    const hits = await service.search({ query: "alpha" });
    expect(hits).toHaveLength(0);
  });
});

describe("search", () => {
  it("finds memories by keyword and returns a snippet", async () => {
    await service.write({
      content: "The deploy pipeline requires NODE_OPTIONS=--max-old-space-size=4096.",
      title: "Deploy memory limit",
      tags: ["ci"],
    });
    const hits = await service.search({ query: "NODE_OPTIONS" });
    expect(hits.length).toBeGreaterThan(0);
    expect(hits[0]?.title).toBe("Deploy memory limit");
    expect(hits[0]?.snippet).toContain("NODE_OPTIONS");
  });

  it("finds CJK content through the LIKE fallback", async () => {
    await service.write({ content: "這個專案使用 pnpm，不要用 npm install。", title: "套件管理器" });
    const hits = await service.search({ query: "套件管理器" });
    expect(hits.length).toBeGreaterThan(0);
    expect(hits[0]?.title).toBe("套件管理器");
  });

  it("filters by tag", async () => {
    await service.write({ content: "tagged fact one", tags: ["alpha"] });
    await service.write({ content: "tagged fact two", tags: ["beta"] });
    const hits = await service.search({ query: "tagged", tags: ["alpha"] });
    expect(hits).toHaveLength(1);
    expect(hits[0]?.tags).toEqual(["alpha"]);
  });

  it("ranks pinned memories above unpinned ones", async () => {
    await service.write({ content: "ranking probe unpinned", title: "unpinned" });
    await service.write({ content: "ranking probe pinned", title: "pinned", pinned: true });
    const hits = await service.search({ query: "ranking probe" });
    expect(hits[0]?.title).toBe("pinned");
  });

  it("returns nothing for an empty result set", async () => {
    await service.write({ content: "something unrelated" });
    expect(await service.search({ query: "zzzznotfound" })).toHaveLength(0);
  });
});

describe("list and delete", () => {
  it("lists newest first and paginates", async () => {
    await service.write({ content: "one", title: "one" });
    await service.write({ content: "two", title: "two" });
    const page = await service.list({ limit: 1, offset: 0 });
    expect(page.total).toBe(2);
    expect(page.items).toHaveLength(1);
  });

  it("soft deletes into .trash and hides the memory", async () => {
    const created = await service.write({ content: "to be removed" });
    expect(await service.delete(created.id)).toBe(true);
    expect(await service.get(created.id)).toBeNull();

    const trash = await fs.readdir(path.join(home, ".trash"));
    expect(trash.length).toBe(1);
  });

  it("hard deletes the file", async () => {
    const created = await service.write({ content: "to be erased" });
    await service.delete(created.id, true);
    await expect(fs.access(created.path)).rejects.toThrow();
  });
});

describe("export and import", () => {
  it("round-trips through JSON", async () => {
    await service.write({ content: "exported fact", title: "Exported", tags: ["x"] });
    const exported = await service.export({ format: "json" });
    expect(exported.count).toBe(1);

    const otherHome = await fs.mkdtemp(path.join(os.tmpdir(), "agent-memory-import-"));
    const other = await MemoryService.create({
      config: loadConfig({ home: otherHome, projectRoot: path.join(home, "repo"), sessionId: "s1" }),
    });
    try {
      const result = await other.import({ format: "json", filePath: exported.path });
      expect(result.imported).toBe(1);
      const hits = await other.search({ query: "exported fact" });
      expect(hits).toHaveLength(1);
    } finally {
      other.close();
      await fs.rm(otherHome, { recursive: true, force: true });
    }
  });

  it("round-trips through markdown", async () => {
    await service.write({ content: "markdown fact", title: "Md" });
    const exported = await service.export({ format: "markdown" });
    const raw = await fs.readFile(exported.path, "utf8");
    expect(raw).toContain("markdown fact");

    const preview = await service.import({ format: "markdown", data: raw, dryRun: true });
    expect(preview.imported).toBe(0);
    expect(preview.skipped).toBe(1);
  });
});

describe("index reconciliation", () => {
  it("picks up files edited outside the server", async () => {
    const created = await service.write({ content: "original body", title: "Editable" });
    const raw = await fs.readFile(created.path, "utf8");
    await fs.writeFile(created.path, raw.replace("original body", "hand edited body"), "utf8");

    const report = await service.reindex();
    expect(report.indexed).toBe(1);

    const memory = await service.get(created.id);
    expect(memory?.body).toBe("hand edited body");
  });

  it("drops index rows whose files disappeared", async () => {
    const created = await service.write({ content: "will vanish" });
    await fs.rm(created.path);
    const report = await service.reindex();
    expect(report.removed).toBe(1);
    expect(await service.get(created.id)).toBeNull();
  });

  it("rebuilds an identical index from scratch", async () => {
    await service.write({ content: "rebuild me", title: "Rebuild" });
    const before = await service.stats();
    service.close();

    for (const suffix of ["", "-wal", "-shm"]) {
      await fs.rm(path.join(home, `index.db${suffix}`), { force: true });
    }

    service = await makeService();
    const after = await service.stats();
    expect(after.total).toBe(before.total);
  });
});

describe("secret detection", () => {
  it("warns without blocking the write", async () => {
    const result = await service.write({
      content: "deploy key is ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    });
    expect(result.action).toBe("created");
    expect(result.warnings.length).toBeGreaterThan(0);
  });
});

describe("first run", () => {
  it("creates MEMORY_HOME when it does not exist yet", async () => {
    const freshHome = path.join(home, "nested", "does-not-exist");
    const fresh = await MemoryService.create({
      config: loadConfig({ home: freshHome, projectRoot: path.join(home, "repo"), sessionId: "s1" }),
    });
    try {
      const created = await fresh.write({ content: "first ever memory" });
      expect(created.action).toBe("created");
      await expect(fs.access(path.join(freshHome, "index.db"))).resolves.toBeUndefined();
    } finally {
      fresh.close();
    }
  });
});
