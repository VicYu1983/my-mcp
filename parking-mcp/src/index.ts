#!/usr/bin/env node
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { loadConfig } from "./config.js";
import { NewTaipeiParkingAdapter } from "./providers/new-taipei.js";
import { TaipeiParkingAdapter } from "./providers/taipei.js";
import { ParkingService } from "./service.js";
import { createServer } from "./server.js";

async function main(): Promise<void> {
  const config = loadConfig();
  const service = new ParkingService([
    new TaipeiParkingAdapter({ staleAfterSeconds: config.taipeiStaleAfterSeconds }),
    new NewTaipeiParkingAdapter(),
  ]);
  const server = createServer(service);
  const transport = new StdioServerTransport();

  const shutdown = async (): Promise<void> => {
    await server.close();
    process.exit(0);
  };
  process.on("SIGINT", () => void shutdown());
  process.on("SIGTERM", () => void shutdown());

  await server.connect(transport);
  process.stderr.write("parking-mcp ready: cities=taipei,new_taipei\n");
}

main().catch((error: unknown) => {
  process.stderr.write(`parking-mcp failed to start: ${String(error)}\n`);
  process.exit(1);
});
