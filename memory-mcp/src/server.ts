import { McpServer, ResourceTemplate } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { MemoryService } from "./core/service.js";
import { registerPortabilityTools } from "./tools/portability.js";
import { registerDeleteTool, registerGetTool, registerListTool, registerSearchTool } from "./tools/query.js";
import { registerWriteTool } from "./tools/write.js";

export const SERVER_INSTRUCTIONS = `This server gives you durable long-term memory across conversations.

WHEN TO WRITE (memory_write):
- A decision was made and the reasoning is not obvious from the code (architecture, library choice, trade-off).
- A project convention or gotcha was discovered (build quirks, required env vars, ordering constraints).
- The user stated a preference that should apply to future work.
- Something was tried and failed, and the failure is worth not repeating.
Use scope=project for repository knowledge, scope=global for cross-project preferences,
scope=session for notes that only matter for the current task.
Write one focused memory per fact, with a short title and 1-3 tags. Prefer updating an existing
memory (pass its id) over creating a near-duplicate.

WHEN TO READ (memory_search / memory_get):
- At the start of a task, search for the project name, the subsystem, and the user's goal.
- Before asking the user to repeat context, search first.
- When a command or convention surprises you, search before guessing.

DO NOT STORE:
- Secrets, tokens, passwords, or personal data.
- Facts that are trivially re-derivable from the repository (file lists, function signatures).
- Transient state such as "currently running tests" or "user is on line 42".

Memories are plain Markdown files on disk and are safe to edit by hand; call memory_reindex
after manual edits or a git pull.`;

export function createServer(service: MemoryService): McpServer {
  const server = new McpServer(
    { name: "agent-memory", version: "0.1.0" },
    { instructions: SERVER_INSTRUCTIONS },
  );

  registerWriteTool(server, service);
  registerSearchTool(server, service);
  registerGetTool(server, service);
  registerListTool(server, service);
  registerDeleteTool(server, service);
  registerPortabilityTools(server, service);

  server.registerResource(
    "memory",
    new ResourceTemplate("memory://{scope}/{id}", { list: undefined }),
    {
      title: "Stored memory",
      description: "Read a single memory by scope and id.",
      mimeType: "text/markdown",
    },
    async (uri, variables) => {
      const id = String(variables["id"] ?? "");
      const memory = await service.get(id);
      if (!memory) {
        return {
          contents: [{ uri: uri.href, mimeType: "text/plain", text: `Memory not found: ${id}` }],
        };
      }
      return {
        contents: [
          {
            uri: uri.href,
            mimeType: "text/markdown",
            text: `# ${memory.title}\n\n${memory.body}\n`,
          },
        ],
      };
    },
  );

  return server;
}
