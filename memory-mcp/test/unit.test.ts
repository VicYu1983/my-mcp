import { describe, expect, it } from "vitest";
import { normalizeClientPath } from "../src/config.js";
import { contentHash, parseMemory, serializeMemory } from "../src/core/frontmatter.js";
import { buildMatchExpression } from "../src/core/search.js";
import { newId, slugify } from "../src/core/paths.js";
import { splitMarkdownMemories } from "../src/core/service.js";
import type { MemoryMeta } from "../src/core/types.js";

const meta: MemoryMeta = {
  id: "mem_01JBX0000000000000000000AB",
  scope: "project",
  scope_key: "demo-1234abcd",
  title: "Round trip: 中文標題",
  tags: ["a", "b"],
  source: "session/s1",
  confidence: 0.9,
  pinned: true,
  supersedes: null,
  created_at: "2026-10-31T12:00:00.000Z",
  updated_at: "2026-10-31T12:00:00.000Z",
  content_hash: contentHash("body text"),
};

describe("frontmatter", () => {
  it("round-trips metadata and body", () => {
    const raw = serializeMemory(meta, "body text\n");
    const parsed = parseMemory(raw);
    expect(parsed).not.toBeNull();
    expect(parsed!.meta).toEqual(meta);
    expect(parsed!.body).toBe("body text");
  });

  it("returns null for files without frontmatter", () => {
    expect(parseMemory("just text")).toBeNull();
  });

  it("normalizes CRLF and trailing whitespace in the hash", () => {
    expect(contentHash("a\r\nb  \n")).toBe(contentHash("a\nb"));
  });
});

describe("ids and slugs", () => {
  it("generates sortable ids", () => {
    const early = newId(1_000_000_000_000);
    const late = newId(2_000_000_000_000);
    expect(early < late).toBe(true);
    expect(early).toMatch(/^mem_[0-9A-HJKMNP-TV-Z]{26}$/);
  });

  it("keeps unicode letters in slugs", () => {
    expect(slugify("使用 pnpm 而非 npm")).toBe("使用-pnpm-而非-npm");
    expect(slugify("!!!")).toBe("memory");
  });
});

describe("match expression", () => {
  it("prefix-matches latin terms and quotes CJK terms", () => {
    expect(buildMatchExpression("deploy pipeline")).toBe('"deploy"* OR "pipeline"*');
    expect(buildMatchExpression("記憶")).toBe('"記憶"');
  });

  it("strips FTS operators from user input", () => {
    expect(buildMatchExpression('foo" OR *')).toBe('"foo"* OR "OR"*');
  });
});

describe("markdown splitting", () => {
  it("splits concatenated exports back into memories", () => {
    const raw = [serializeMemory(meta, "one"), serializeMemory(meta, "two")].join("\n");
    const chunks = splitMarkdownMemories(raw);
    expect(chunks).toHaveLength(2);
    expect(parseMemory(chunks[1]!)?.body).toBe("two");
  });
});

describe("client path normalization", () => {
  it("strips the POSIX drive prefix that clients emit for ${userHome}", () => {
    expect(normalizeClientPath("/c:/Users/me/.agent-memory")).toBe("c:/Users/me/.agent-memory");
    expect(normalizeClientPath("C:\\Users\\me\\.agent-memory")).toBe("C:\\Users\\me\\.agent-memory");
  });
});
