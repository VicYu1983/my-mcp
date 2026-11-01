import type {
  CityId,
  ParkingAdapter,
  ParkingLot,
  ParkingSearchResult,
  SourceError,
} from "./types.js";
import { errorMessage, normalizeSearchText } from "./providers/shared.js";

const DEFAULT_LIMIT = 10;
const MAX_LIMIT = 50;

export class ParkingService {
  constructor(
    private readonly adapters: ParkingAdapter[],
    private readonly now: () => Date = () => new Date(),
  ) {}

  async search(options: {
    query: string;
    city?: CityId;
    limit?: number;
  }): Promise<ParkingSearchResult> {
    const query = options.query.trim();
    const normalizedQuery = normalizeSearchText(query);
    if (!normalizedQuery) throw new Error("請提供非空白的停車場名稱。");

    const limit = options.limit ?? DEFAULT_LIMIT;
    if (!Number.isInteger(limit) || limit < 1 || limit > MAX_LIMIT) {
      throw new Error(`limit 必須介於 1 到 ${MAX_LIMIT}。`);
    }
    const adapters = this.adapters.filter(
      (adapter) => options.city === undefined || adapter.city === options.city,
    );
    if (adapters.length === 0) {
      throw new Error(`沒有設定所選縣市的官方資料來源：${options.city ?? "unknown"}。`);
    }

    const settled = await Promise.all(
      adapters.map(async (adapter) => {
        try {
          return { lots: await adapter.fetchLots() };
        } catch (error) {
          return {
            error: {
              city: adapter.city,
              cityName: adapter.cityName,
              message: errorMessage(error),
            } satisfies SourceError,
          };
        }
      }),
    );
    const sourceErrors: SourceError[] = [];
    const lots: ParkingLot[] = [];
    for (const result of settled) {
      if (result.error !== undefined) sourceErrors.push(result.error);
      else lots.push(...result.lots);
    }
    const matches = this.matchLots(lots, normalizedQuery);
    const results = matches.slice(0, limit);

    return {
      query,
      checkedAt: this.now().toISOString(),
      status: sourceErrors.length > 0 ? "partial" : "complete",
      count: results.length,
      totalMatches: matches.length,
      truncated: matches.length > results.length,
      results,
      sourceErrors,
    };
  }

  private matchLots(lots: ParkingLot[], query: string): ParkingLot[] {
    return lots
      .filter((lot) => normalizeSearchText(lot.name).includes(query))
      .sort((left, right) => {
        const leftName = normalizeSearchText(left.name);
        const rightName = normalizeSearchText(right.name);
        const leftExact = leftName === query ? 0 : 1;
        const rightExact = rightName === query ? 0 : 1;
        if (leftExact !== rightExact) return leftExact - rightExact;
        const leftPrefix = leftName.startsWith(query) ? 0 : 1;
        const rightPrefix = rightName.startsWith(query) ? 0 : 1;
        if (leftPrefix !== rightPrefix) return leftPrefix - rightPrefix;
        return left.cityName.localeCompare(right.cityName, "zh-Hant");
      });
  }
}
