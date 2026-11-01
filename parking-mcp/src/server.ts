import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import type { ParkingService } from "./service.js";

export const SERVER_INSTRUCTIONS = `查詢臺北市與新北市官方停車場資料。

使用者提供停車場名稱時呼叫 parking_search，預設跨所有已支援縣市搜尋；結果有多筆時列出候選並請使用者確認城市/地址，不要自行選定。
只查路外停車場汽車資料，不查路邊車格或機車。
務必保留 availability.status、freshnessNote 及查詢時間，不可把 not_provided/not_reported 當成 0 個空位，也不可把新北未驗證的值稱為已驗證即時。
`;

const citySchema = z.enum(["taipei", "new_taipei"]);

export function createServer(service: ParkingService): McpServer {
  const server = new McpServer(
    { name: "parking-mcp", version: "0.1.0" },
    { instructions: SERVER_INSTRUCTIONS },
  );

  server.registerTool(
    "parking_search",
    {
      title: "查詢停車場",
      description:
        "依停車場名稱搜尋臺北市及新北市官方路外停車場資料，回傳地址、座標、費率及汽車剩餘車位。預設跨縣市搜尋；若同名/相似結果多筆，請使用者依城市或地址確認。結果會說明資料未提供、缺列或新鮮度未驗證等狀態。",
      inputSchema: {
        name: z.string().trim().min(1).max(100).describe("停車場名稱或名稱片段"),
        city: citySchema
          .optional()
          .describe("可選：只查 taipei（臺北市）或 new_taipei（新北市）"),
        limit: z.number().int().min(1).max(50).optional().describe("最多回傳筆數，預設 10"),
      },
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true },
    },
    async ({ name, city, limit }) => {
      try {
        const result = await service.search({
          query: name,
          ...(city ? { city } : {}),
          ...(limit === undefined ? {} : { limit }),
        });
        return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        return {
          isError: true,
          content: [{ type: "text" as const, text: `停車場查詢失敗：${message}` }],
        };
      }
    },
  );

  return server;
}
