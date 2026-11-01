import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const home = await fs.mkdtemp(path.join(os.tmpdir(), "agent-memory-e2e-"));
const transport = new StdioClientTransport({
  command: process.execPath,
  args: [path.resolve("dist/index.js")],
  env: { ...process.env, MEMORY_HOME: home, MEMORY_SESSION_ID: "e2e" },
  stderr: "pipe",
});

const client = new Client({ name: "e2e", version: "1.0.0" });
await client.connect(transport);

const { tools } = await client.listTools();
console.log("TOOLS:", tools.map((t) => t.name).join(", "));
console.log("INSTRUCTIONS_LEN:", client.getInstructions()?.length ?? 0);

const write = await client.callTool({
  name: "memory_write",
  arguments: {
    content: "這個專案用 pnpm，不要用 npm install。",
    title: "套件管理器",
    tags: ["convention"],
  },
});
console.log("WRITE:", write.content[0].text);

const search = await client.callTool({ name: "memory_search", arguments: { query: "pnpm" } });
console.log("SEARCH:", search.content[0].text);

const stats = await client.callTool({ name: "memory_stats", arguments: {} });
console.log("STATS:", stats.content[0].text);

const templates = await client.listResourceTemplates();
console.log("RESOURCE_TEMPLATES:", templates.resourceTemplates.map((r) => r.uriTemplate).join(", "));

const id = JSON.parse(write.content[0].text).id;
const read = await client.readResource({ uri: `memory://project/${id}` });
console.log("RESOURCE_READ:", read.contents[0].text.slice(0, 60).replace(/\n/g, " | "));

const bad = await client.callTool({
  name: "memory_get",
  arguments: { id: "mem_00000000000000000000000000" },
});
console.log("ERROR_HANDLING isError:", bad.isError, "|", bad.content[0].text);

await client.close();
await fs.rm(home, { recursive: true, force: true });
console.log("E2E OK");
