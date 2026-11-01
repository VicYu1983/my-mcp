import { describe, expect, it } from "vitest";
import { ParkingService } from "../src/service.js";
import type { ParkingAdapter, ParkingLot } from "../src/types.js";

function lot(city: "taipei" | "new_taipei", id: string, name: string): ParkingLot {
  return {
    city,
    cityName: city === "taipei" ? "臺北市" : "新北市",
    id,
    name,
    availability: { status: "not_reported" },
    sourceUrl: "https://example.test",
  };
}

function adapter(
  city: "taipei" | "new_taipei",
  fetchLots: () => Promise<ParkingLot[]>,
): ParkingAdapter {
  return { city, cityName: city === "taipei" ? "臺北市" : "新北市", fetchLots };
}

describe("ParkingService", () => {
  it("searches all enabled cities and sorts exact matches first", async () => {
    const service = new ParkingService(
      [
        adapter("taipei", async () => [lot("taipei", "1", "嘟嘟房世貿一館")]),
        adapter("new_taipei", async () => [
          lot("new_taipei", "2", "世貿一館停車場"),
          lot("new_taipei", "3", "世貿一館"),
        ]),
      ],
      () => new Date("2026-11-01T03:00:00.000Z"),
    );

    const result = await service.search({ query: "世貿一館" });
    expect(result.status).toBe("complete");
    expect(result.totalMatches).toBe(3);
    expect(result.results[0]?.id).toBe("3");
    expect(result.checkedAt).toBe("2026-11-01T03:00:00.000Z");
  });

  it("supports a city filter and surfaces source errors without hiding successful results", async () => {
    const service = new ParkingService([
      adapter("taipei", async () => [lot("taipei", "1", "中央停車場")]),
      adapter("new_taipei", async () => {
        throw new Error("官方 API unavailable");
      }),
    ]);
    const partial = await service.search({ query: "中央停車場" });
    expect(partial.status).toBe("partial");
    expect(partial.results).toHaveLength(1);
    expect(partial.sourceErrors[0]?.message).toContain("unavailable");

    const filtered = await service.search({ query: "中央停車場", city: "taipei" });
    expect(filtered.status).toBe("complete");
    expect(filtered.sourceErrors).toHaveLength(0);
  });

  it("rejects empty names and invalid result limits", async () => {
    const service = new ParkingService([adapter("taipei", async () => [])]);
    await expect(service.search({ query: "  " })).rejects.toThrow("非空白");
    await expect(service.search({ query: "停車場", limit: 51 })).rejects.toThrow("1 到 50");
  });
});
