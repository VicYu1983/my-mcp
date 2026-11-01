import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { MemoryService } from "../core/service.js";
import { guard, jsonResult } from "./helpers.js";

const scopeSchema = z.enum(["global", "project", "session"]);

export function registerPortabilityTools(server: McpServer, service: MemoryService): void {
  server.registerTool(
    "memory_export",
    {
      title: "Export memories",
      description:
        "Export memories to a single file. `json` is lossless and re-importable; `markdown` concatenates " +
        "the original frontmatter files for human reading and git commits.",
      inputSchema: {
        format: z.enum(["json", "markdown"]).describe("Output format."),
        scope: z.array(scopeSchema).optional().describe("Scopes to export; defaults to all three."),
        outPath: z.string().optional().describe("Absolute output path; defaults under $MEMORY_HOME/exports."),
      },
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: false },
    },
    async (args) =>
      guard(async () => {
        const result = await service.export({
          format: args.format,
          scopes: args.scope,
          outPath: args.outPath,
        });
        return jsonResult(result);
      }),
  );

  server.registerTool(
    "memory_import",
    {
      title: "Import memories",
      description:
        "Import memories from a JSON or Markdown export. Existing ids and duplicate content are skipped " +
        "and reported as conflicts. Use dryRun=true to preview counts without writing.",
      inputSchema: {
        format: z.enum(["json", "markdown"]).describe("Format of the input."),
        path: z.string().optional().describe("File to read the import from."),
        data: z.string().optional().describe("Inline import payload (alternative to `path`)."),
        scope: scopeSchema.optional().describe("Force every imported memory into this scope."),
        dryRun: z.boolean().optional().describe("Report what would happen without writing."),
      },
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true },
    },
    async (args) =>
      guard(async () => {
        const result = await service.import({
          format: args.format,
          filePath: args.path,
          data: args.data,
          scope: args.scope,
          dryRun: args.dryRun,
        });
        return jsonResult(result);
      }),
  );

  server.registerTool(
    "memory_reindex",
    {
      title: "Rebuild the index",
      description:
        "Reconcile the SQLite index with the Markdown files. Run this after editing memory files by hand " +
        "or after pulling changes with git. The index is disposable: Markdown is the source of truth.",
      inputSchema: {},
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true },
    },
    async () =>
      guard(async () => {
        const report = await service.reindex();
        return jsonResult(report);
      }),
  );

  server.registerTool(
    "memory_stats",
    {
      title: "Memory statistics",
      description: "Show how many memories exist per scope, plus index size and resolved paths.",
      inputSchema: {},
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true },
    },
    async () =>
      guard(async () => {
        const stats = await service.stats();
        return jsonResult(stats);
      }),
  );
}
