import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { MemoryService } from "../core/service.js";
import { guard, jsonResult } from "./helpers.js";

const scopeSchema = z.enum(["global", "project", "session"]);

export function registerWriteTool(server: McpServer, service: MemoryService): void {
  server.registerTool(
    "memory_write",
    {
      title: "Write a memory",
      description:
        "Persist a durable fact, decision, convention, or user preference so it survives this conversation. " +
        "Use scope=project for repository knowledge, scope=global for cross-project preferences, " +
        "scope=session for scratch notes tied to the current task. " +
        "Identical content in the same scope is deduplicated automatically. " +
        "Pass `id` to update an existing memory, or `supersedes` to mark an older memory as replaced.",
      inputSchema: {
        content: z.string().min(1).describe("Markdown body of the memory."),
        title: z.string().optional().describe("Short title; derived from the first line when omitted."),
        scope: scopeSchema.optional().describe("Defaults to the server's default scope (project)."),
        tags: z.array(z.string()).optional().describe("Free-form tags for filtering."),
        source: z.string().optional().describe("Where this came from, e.g. 'session/abc123' or a file path."),
        confidence: z.number().min(0).max(1).optional().describe("0..1 confidence, default 0.8."),
        pinned: z.boolean().optional().describe("Pinned memories get a ranking boost."),
        supersedes: z.string().optional().describe("Id of a memory this one replaces."),
        id: z.string().optional().describe("Existing memory id to update in place."),
      },
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false },
    },
    async (args) =>
      guard(async () => {
        const result = await service.write({
          content: args.content,
          title: args.title,
          scope: args.scope,
          tags: args.tags,
          source: args.source,
          confidence: args.confidence,
          pinned: args.pinned,
          supersedes: args.supersedes,
          id: args.id,
        });
        return jsonResult(result);
      }),
  );
}
