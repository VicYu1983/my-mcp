import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import type { MapsClient } from "./maps-client.js";
import { getPlaceDetails, searchPlaces } from "./services/places.js";
import { computeRoute } from "./services/routes.js";

export const SERVER_INSTRUCTIONS = `Google Maps Platform 查詢工具（Places 與 Routes API）。

- 使用者給地點名稱或關鍵字時，先呼叫 maps_search_places；結果有多筆時請使用者確認，不要自行選定。
- 需要電話、網站、營業時間時，才呼叫 maps_get_place_details，並使用搜尋結果中的 placeId。
- 規劃路線時呼叫 maps_compute_route；起訖點可為地址字串或經緯度。路線時間為依交通模式估算，不保證即時路況。
- 回覆中保留 businessStatus、營業時間與查詢結果來源，不要把缺少的欄位補成 0 或推測值。
`;

const coordinatesSchema = z.object({
  latitude: z.number().min(-90).max(90),
  longitude: z.number().min(-180).max(180),
});

const waypointSchema = z.union([z.string().trim().min(1).max(500), coordinatesSchema]);

const languageSchema = z
  .string()
  .trim()
  .regex(/^[a-zA-Z]{2,3}(-[a-zA-Z0-9]{2,8})*$/, "語言代碼格式應如 zh-TW、en")
  .optional();

const jsonResult = (value: unknown) => ({
  content: [{ type: "text" as const, text: JSON.stringify(value, null, 2) }],
});

const errorResult = (prefix: string, error: unknown) => ({
  isError: true,
  content: [
    {
      type: "text" as const,
      text: `${prefix}：${error instanceof Error ? error.message : String(error)}`,
    },
  ],
});

export function createServer(client: MapsClient): McpServer {
  const server = new McpServer(
    { name: "google-maps-mcp", version: "0.1.0" },
    { instructions: SERVER_INSTRUCTIONS },
  );

  server.registerTool(
    "maps_search_places",
    {
      title: "搜尋地點",
      description:
        "依文字關鍵字搜尋 Google 地圖上的地點，回傳名稱、地址、座標、評分與 placeId。結果有多筆時請使用者確認。",
      inputSchema: {
        query: z.string().trim().min(1).max(200).describe("地點名稱、地址或關鍵字，例如「台北 101」"),
        maxResults: z.number().int().min(1).max(20).optional().describe("最多回傳筆數，預設 5"),
        languageCode: languageSchema.describe("回應語言，預設 zh-TW"),
        regionCode: z
          .string()
          .trim()
          .regex(/^[A-Za-z]{2}$/, "地區代碼應為兩碼 ISO 3166-1，如 TW")
          .optional()
          .describe("偏好地區（ISO 3166-1 兩碼），例如 TW"),
      },
      annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: true },
    },
    async ({ query, maxResults, languageCode, regionCode }) => {
      try {
        const places = await searchPlaces(client, {
          query,
          maxResults: maxResults ?? 5,
          languageCode: languageCode ?? "zh-TW",
          ...(regionCode ? { regionCode: regionCode.toUpperCase() } : {}),
        });
        return jsonResult({ count: places.length, places });
      } catch (error) {
        return errorResult("地點搜尋失敗", error);
      }
    },
  );

  server.registerTool(
    "maps_get_place_details",
    {
      title: "地點詳細資訊",
      description: "依 placeId 取得地點的電話、網站、營業時間與營運狀態。placeId 來自 maps_search_places。",
      inputSchema: {
        placeId: z.string().trim().min(1).max(200).describe("maps_search_places 回傳的 placeId"),
        languageCode: languageSchema.describe("回應語言，預設 zh-TW"),
      },
      annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: true },
    },
    async ({ placeId, languageCode }) => {
      try {
        const place = await getPlaceDetails(client, placeId, languageCode ?? "zh-TW");
        return jsonResult(place);
      } catch (error) {
        return errorResult("地點詳細資訊查詢失敗", error);
      }
    },
  );

  server.registerTool(
    "maps_compute_route",
    {
      title: "規劃路線",
      description:
        "計算起點到終點的距離與預估時間。起訖點可為地址字串或經緯度；交通模式可選 DRIVE、WALK、BICYCLE、TRANSIT。",
      inputSchema: {
        origin: waypointSchema.describe("起點：地址字串或 {latitude, longitude}"),
        destination: waypointSchema.describe("終點：地址字串或 {latitude, longitude}"),
        travelMode: z
          .enum(["DRIVE", "WALK", "BICYCLE", "TRANSIT"])
          .optional()
          .describe("交通模式，預設 DRIVE"),
        languageCode: languageSchema.describe("回應語言，預設 zh-TW"),
      },
      annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: true },
    },
    async ({ origin, destination, travelMode, languageCode }) => {
      try {
        const route = await computeRoute(client, {
          origin,
          destination,
          travelMode: travelMode ?? "DRIVE",
          languageCode: languageCode ?? "zh-TW",
        });
        return jsonResult(route);
      } catch (error) {
        return errorResult("路線規劃失敗", error);
      }
    },
  );

  return server;
}
