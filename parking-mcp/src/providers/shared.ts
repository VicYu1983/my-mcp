import type { CityId, ParkingAvailability } from "../types.js";

export type FetchFunction = typeof fetch;

export const SOURCE_TIMEOUT_MS = 15_000;
export const NEW_TAIPEI_UPDATE_NOTE =
  "新北市政府資料開放平台宣告每 3 分鐘更新；來源未提供資料產生時間，無法驗證此數值的實際新鮮度。";

export function record(value: unknown): Record<string, unknown> | undefined {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return undefined;
  return value as Record<string, unknown>;
}

export function fieldText(source: Record<string, unknown>, key: string): string | undefined {
  const value = source[key];
  if (typeof value !== "string" && typeof value !== "number") return undefined;
  const text = String(value).trim();
  return text.length > 0 ? text : undefined;
}

export function finiteNumber(value: string | undefined): number | undefined {
  if (value === undefined) return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

export async function fetchText(
  fetcher: FetchFunction,
  url: string,
  accept: string,
): Promise<{ body: string; lastModified?: string }> {
  const response = await fetcher(url, {
    headers: { accept },
    signal: AbortSignal.timeout(SOURCE_TIMEOUT_MS),
  });
  if (!response.ok) {
    throw new Error(`官方資料來源回應 HTTP ${response.status} (${url})`);
  }
  return {
    body: await response.text(),
    lastModified: response.headers.get("last-modified") ?? undefined,
  };
}

export function parseTaipeiTimestamp(value: string | undefined): string | undefined {
  if (!value) return undefined;
  const timestamp = value.match(
    /^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) ([A-Z][a-z]{2}) (\d{1,2}) (\d{2}:\d{2}:\d{2}) CST (\d{4})$/,
  );
  const date = timestamp
    ? new Date(`${timestamp[1]} ${timestamp[2]}, ${timestamp[4]} ${timestamp[3]} GMT+0800`)
    : new Date(value);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

export function makeAvailability(
  city: CityId,
  rawCars: number | undefined,
  sourceUpdatedAt: string | undefined,
  checkedAt: Date,
  taipeiStaleAfterSeconds: number,
): ParkingAvailability {
  if (rawCars === undefined) {
    return {
      status: "not_reported",
      freshnessNote: "官方即時資料沒有此停車場的汽車空位紀錄。",
    };
  }
  if (rawCars < 0) {
    return {
      status: "not_provided",
      freshnessNote: "官方資料標示未提供汽車即時空位；負值不代表空位數為 0。",
      ...(sourceUpdatedAt ? { sourceUpdatedAt } : {}),
    };
  }
  if (city === "new_taipei") {
    return {
      status: "unverified",
      availableCars: rawCars,
      freshnessNote: `${NEW_TAIPEI_UPDATE_NOTE} 以下為本次查詢取得的官方數值，非資料產生時間。`,
    };
  }
  if (!sourceUpdatedAt) {
    return {
      status: "not_reported",
      freshnessNote: "官方即時資料未提供可解析的更新時間，因此不顯示空位數。",
    };
  }

  const ageSeconds = (checkedAt.getTime() - Date.parse(sourceUpdatedAt)) / 1000;
  if (ageSeconds < -60 || ageSeconds > taipeiStaleAfterSeconds) {
    return {
      status: "stale",
      lastReportedCars: rawCars,
      sourceUpdatedAt,
      freshnessNote:
        ageSeconds < -60
          ? "官方更新時間晚於本次查詢時間，無法確認資料順序。"
          : `官方資料已超過設定的新鮮度門檻 ${taipeiStaleAfterSeconds} 秒；僅提供最後回報值，不視為目前空位。`,
    };
  }
  return { status: "available", availableCars: rawCars, sourceUpdatedAt };
}

export function normalizeSearchText(value: string): string {
  return value.normalize("NFKC").toLocaleLowerCase().replace(/[\s\p{P}\p{S}]+/gu, "");
}

export function parsePositiveInteger(value: string | undefined, fallback: number): number {
  if (value === undefined || value.trim() === "") return fallback;
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < 1) {
    throw new Error("PARKING_TAIPEI_STALE_AFTER_SECONDS 必須是正整數。");
  }
  return parsed;
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
