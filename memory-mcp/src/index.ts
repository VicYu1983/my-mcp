#!/usr/bin/env node
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { loadConfig } from "./config.js";
import { MemoryService } from "./core/service.js";
import { createServer } from "./server.js";

async function main(): Promise<void> {
  const config = loadConfig();
  const service = await MemoryService.create({ config });
  const server = createServer(service);
  const transport = new StdioServerTransport();

  const shutdown = async (): Promise<void> => {
    try {
      await server.close();
    } finally {
      service.close();
    }
    process.exit(0);
  };

  process.on("SIGINT", () => void shutdown());
  process.on("SIGTERM", () => void shutdown());

  await server.connect(transport);
  // stdout is reserved for the JSON-RPC stream, so diagnostics go to stderr.
  process.stderr.write(
    `agent-memory ready: home=${config.home} project=${config.projectKey} session=${config.sessionId}\n`,
  );
}

main().catch((error: unknown) => {
  process.stderr.write(`agent-memory failed to start: ${String(error)}\n`);
  process.exit(1);
});
