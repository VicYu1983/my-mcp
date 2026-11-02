#!/usr/bin/env node
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { loadConfig } from "./config.js";
import { MapsClient } from "./maps-client.js";
import { createServer } from "./server.js";

async function main(): Promise<void> {
  const config = loadConfig();
  const server = createServer(new MapsClient({ apiKey: config.apiKey }));
  const transport = new StdioServerTransport();

  const shutdown = async (): Promise<void> => {
    await server.close();
    process.exit(0);
  };
  process.on("SIGINT", () => void shutdown());
  process.on("SIGTERM", () => void shutdown());

  await server.connect(transport);
  process.stderr.write("google-maps-mcp ready: services=places,routes\n");
}

main().catch((error: unknown) => {
  process.stderr.write(`google-maps-mcp failed to start: ${String(error)}\n`);
  process.exit(1);
});
