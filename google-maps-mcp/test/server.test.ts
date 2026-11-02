import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { describe, expect, it, vi } from "vitest";
import { MapsClient } from "../src/maps-client.js";
import { createServer } from "../src/server.js";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function connect(fetchImpl: typeof fetch) {
  const client = new MapsClient({ apiKey: "k", fetch: fetchImpl });
  const server = createServer(client);
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  await server.connect(serverTransport);
  const mcpClient = new Client({ name: "test", version: "0.0.0" });
  await mcpClient.connect(clientTransport);
  return { mcpClient, close: async () => {
    await mcpClient.close();
    await server.close();
  } };
}

function textOf(result: unknown): string {
  const content = (result as { content: { type: string; text: string }[] }).content;
  return content[0]?.text ?? "";
}

describe("google-maps-mcp server", () => {
  it("registers the three Maps tools with read-only annotations", async () => {
    const { mcpClient, close } = await connect(vi.fn() as unknown as typeof fetch);
    try {
      const { tools } = await mcpClient.listTools();
      expect(tools.map((t) => t.name).sort()).toEqual([
        "maps_compute_route",
        "maps_get_place_details",
        "maps_search_places",
      ]);
      for (const tool of tools) {
        expect(tool.annotations?.readOnlyHint).toBe(true);
      }
    } finally {
      await close();
    }
  });

  it("maps_search_places returns normalized JSON", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({ places: [{ id: "p1" }] }));
    const { mcpClient, close } = await connect(fetchImpl as unknown as typeof fetch);
    try {
      const result = await mcpClient.callTool({
        name: "maps_search_places",
        arguments: { query: "咖啡", maxResults: 2 },
      });
      expect(result.isError).toBeFalsy();
      expect(JSON.parse(textOf(result))).toEqual({
        count: 1,
        places: [{ placeId: "p1" }],
      });
    } finally {
      await close();
    }
  });

  it("maps_search_places surfaces upstream failures as tool errors", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse({ error: { message: "quota exceeded" } }, 429),
    );
    const { mcpClient, close } = await connect(fetchImpl as unknown as typeof fetch);
    try {
      const result = await mcpClient.callTool({
        name: "maps_search_places",
        arguments: { query: "咖啡" },
      });
      expect(result.isError).toBe(true);
      expect(textOf(result)).toContain("quota exceeded");
    } finally {
      await close();
    }
  });

  it("rejects invalid input before calling the API", async () => {
    const fetchImpl = vi.fn();
    const { mcpClient, close } = await connect(fetchImpl as unknown as typeof fetch);
    try {
      const badRoute = await mcpClient.callTool({
        name: "maps_compute_route",
        arguments: {
          origin: { latitude: 200, longitude: 0 },
          destination: "台北車站",
        },
      });
      const badSearch = await mcpClient.callTool({
        name: "maps_search_places",
        arguments: { query: "x", maxResults: 99 },
      });
      expect(badRoute.isError).toBe(true);
      expect(badSearch.isError).toBe(true);
      expect(fetchImpl).not.toHaveBeenCalled();
    } finally {
      await close();
    }
  });
});
