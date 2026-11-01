import type { ParkingAdapter, ParkingLot } from "../types.js";
import { parseTwd97Coordinates } from "./coordinates.js";
import {
  fetchText,
  fieldText,
  finiteNumber,
  makeAvailability,
  record,
  type FetchFunction,
} from "./shared.js";

const API_BASE = "https://data.ntpc.gov.tw/api/datasets";
const STATIC_DATASET_ID = "b1464ef0-9c7c-4a6f-abf7-6bdf32847e68";
const LIVE_DATASET_ID = "e09b35a5-a738-48cc-b0f5-570b67ad9c78";
const DATASET_URL = `https://data.ntpc.gov.tw/datasets/${STATIC_DATASET_ID}`;
const PAGE_SIZE = 1000;
const MAX_PAGES = 100;

function parseRows(body: string, url: string): Record<string, unknown>[] {
  let parsed: unknown;
  try {
    parsed = JSON.parse(body);
  } catch (error) {
    throw new Error(`新北官方資料不是有效 JSON (${url}): ${String(error)}`);
  }
  if (!Array.isArray(parsed)) {
    throw new Error(`新北官方資料格式錯誤：預期 JSON 陣列 (${url})。`);
  }
  return parsed
    .map(record)
    .filter((row): row is Record<string, unknown> => row !== undefined);
}

async function fetchAllRows(
  fetcher: FetchFunction,
  datasetId: string,
): Promise<Record<string, unknown>[]> {
  const result: Record<string, unknown>[] = [];
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const url = new URL(`${API_BASE}/${datasetId}/json`);
    url.searchParams.set("page", String(page));
    url.searchParams.set("size", String(PAGE_SIZE));
    const response = await fetchText(fetcher, url.href, "application/json");
    const rows = parseRows(response.body, url.href);
    result.push(...rows);
    if (rows.length < PAGE_SIZE) return result;
  }
  throw new Error(`新北官方資料超過安全分頁上限 (${MAX_PAGES} 頁)。`);
}

export class NewTaipeiParkingAdapter implements ParkingAdapter {
  readonly city = "new_taipei" as const;
  readonly cityName = "新北市";
  private readonly fetcher: FetchFunction;
  private readonly now: () => Date;

  constructor(options: { fetcher?: FetchFunction; now?: () => Date } = {}) {
    this.fetcher = options.fetcher ?? fetch;
    this.now = options.now ?? (() => new Date());
  }

  async fetchLots(): Promise<ParkingLot[]> {
    const [staticRows, liveRows] = await Promise.all([
      fetchAllRows(this.fetcher, STATIC_DATASET_ID),
      fetchAllRows(this.fetcher, LIVE_DATASET_ID),
    ]);
    const liveById = new Map<string, number>();
    for (const row of liveRows) {
      const id = fieldText(row, "ID");
      const availableCars = finiteNumber(fieldText(row, "AVAILABLECAR"));
      if (id && availableCars !== undefined) liveById.set(id, availableCars);
    }
    const checkedAt = this.now();

    return staticRows.flatMap((row) => {
      const id = fieldText(row, "ID");
      const name = fieldText(row, "NAME");
      if (!id || !name) return [];
      const coordinates = parseTwd97Coordinates(
        fieldText(row, "TW97X"),
        fieldText(row, "TW97Y"),
      );
      return [
        {
          city: this.city,
          cityName: this.cityName,
          id,
          name,
          district: fieldText(row, "AREA"),
          address: fieldText(row, "ADDRESS"),
          ...(coordinates
            ? {
                latitude: coordinates.latitude,
                longitude: coordinates.longitude,
                coordinateSystem: "WGS84" as const,
              }
            : {}),
          feeDescription: fieldText(row, "PAYEX"),
          serviceTime: fieldText(row, "SERVICETIME"),
          totalCars: finiteNumber(fieldText(row, "TOTALCAR")),
          availability: makeAvailability(
            this.city,
            liveById.get(id),
            undefined,
            checkedAt,
            0,
          ),
          sourceUrl: DATASET_URL,
        },
      ];
    });
  }
}
