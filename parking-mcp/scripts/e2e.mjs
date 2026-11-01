import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const serverPath = fileURLToPath(new URL("../dist/index.js", import.meta.url));
const transport = new StdioClientTransport({
  command: process.execPath,
  args: [serverPath],
});
const client = new Client({ name: "parking-mcp-e2e", version: "0.1.0" });

try {
  await client.connect(transport);
  const { tools } = await client.listTools();
  assert(tools.some((tool) => tool.name === "parking_search"), "parking_search tool is registered");

  const result = await client.callTool({
    name: "parking_search",
    arguments: { name: "民生立體停車場", city: "taipei" },
  });
  assert(!result.isError, JSON.stringify(result));
  const content = result.content.find((item) => item.type === "text");
  assert(content && "text" in content, "tool returns text content");
  const payload = JSON.parse(content.text);
  assert.equal(payload.status, "complete");
  assert(payload.results.some((lot) => lot.name.includes("民生立體停車場")));

  const newTaipeiResult = await client.callTool({
    name: "parking_search",
    arguments: { name: "遠東百貨停車場", city: "new_taipei" },
  });
  assert(!newTaipeiResult.isError, JSON.stringify(newTaipeiResult));
  const newTaipeiContent = newTaipeiResult.content.find((item) => item.type === "text");
  assert(newTaipeiContent && "text" in newTaipeiContent, "New Taipei query returns text content");
  const newTaipeiPayload = JSON.parse(newTaipeiContent.text);
  assert.equal(newTaipeiPayload.status, "complete");
  assert(newTaipeiPayload.results.some((lot) => lot.city === "new_taipei"));
  assert(
    newTaipeiPayload.results.every(
      (lot) => lot.availability.status !== "available",
    ),
    "New Taipei values are not represented as verified current data",
  );
  console.log("parking MCP stdio e2e passed");
} finally {
  await client.close();
}
