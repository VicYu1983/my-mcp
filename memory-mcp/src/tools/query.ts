import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { MemoryService } from "../core/service.js";
import { guard, jsonResult } from "./helpers.js";

const scopeSchema = z.enum(["global", "project", "session"]);

export function registerSearchTool(server: McpServer, service: MemoryService): void {
  server.registerTool(
    "memory_search",
    {
      title: "Search memories",
      description:
        "Search stored memories with full-text ranking (BM25 + recency + pinned boost). " +
        "Call this BEFORE asking the user to repeat context, and at the start of a task to recall " +
        "project conventions and past decisions. Searches session, project, and global scopes by default.",
      inputSchema: {
        query: z.string().min(1).describe("Keywords or a phrase. Multiple words are OR-ed."),
        scope: z
          .array(scopeSchema)
          .optional()
          .describe("Restrict to specific scopes; defaults to session + project + global."),
        tags: z.array(z.string()).optional().describe("Only return memories carrying all these tags."),
        limit: z.number().int().min(1).max(100).optional().describe("Max results, default 10."),
        since: z.string().optional().describe("ISO date; ignore memories older than this."),
      },
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true },
    },
    async (args) =>
      guard(async () => {
        const results = await service.search({
          query: args.query,
          scopes: args.scope,
          tags: args.tags,
          limit: args.limit,
          since: args.since,
        });
        return jsonResult({ count: results.length, results });
      }),
  );
}

export function registerGetTool(server: McpServer, service: MemoryService): void {
  server.registerTool(
    "memory_get",
    {
      title: "Get a memory",
      description: "Fetch one memory in full (frontmatter + body) by id.",
      inputSchema: {
        id: z.string().min(1).describe("Memory id, e.g. mem_01JBX..."),
      },
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true },
    },
    async (args) =>
      guard(async () => {
        const memory = await service.get(args.id);
        if (!memory) throw new Error(`Memory not found: ${args.id}`);
        return jsonResult(memory);
      }),
  );
}

export function registerListTool(server: McpServer, service: MemoryService): void {
  server.registerTool(
    "memory_list",
    {
      title: "List memories",
      description:
        "Browse memories without a search query, newest first. Useful for reviewing what is stored.",
      inputSchema: {
        scope: z.array(scopeSchema).optional().describe("Restrict to specific scopes."),
        tags: z.array(z.string()).optional().describe("Only memories carrying all these tags."),
        limit: z.number().int().min(1).max(200).optional().describe("Page size, default 20."),
        offset: z.number().int().min(0).optional().describe("Pagination offset."),
        sort: z.enum(["updated", "created", "title"]).optional().describe("Sort order, default updated."),
      },
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true },
    },
    async (args) =>
      guard(async () => {
        const result = await service.list({
          scopes: args.scope,
          tags: args.tags,
          limit: args.limit,
          offset: args.offset,
          sort: args.sort,
        });
        return jsonResult(result);
      }),
  );
}

export function registerDeleteTool(server: McpServer, service: MemoryService): void {
  server.registerTool(
    "memory_delete",
    {
      title: "Delete a memory",
      description:
        "Delete a memory. By default it is moved to `.trash/` (recoverable); pass hard=true to erase the file.",
      inputSchema: {
        id: z.string().min(1).describe("Memory id to delete."),
        hard: z.boolean().optional().describe("Permanently remove instead of moving to trash."),
      },
      annotations: { readOnlyHint: false, destructiveHint: true, idempotentHint: true },
    },
    async (args) =>
      guard(async () => {
        const deleted = await service.delete(args.id, args.hard === true);
        if (!deleted) throw new Error(`Memory not found: ${args.id}`);
        return jsonResult({ id: args.id, deleted: true, hard: args.hard === true });
      }),
  );
}
