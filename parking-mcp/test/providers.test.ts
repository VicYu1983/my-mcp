import { describe, expect, it } from "vitest";
import { NewTaipeiParkingAdapter } from "../src/providers/new-taipei.js";
import { TaipeiParkingAdapter } from "../src/providers/taipei.js";

function response(body: string, lastModified?: string): Response {
  return new Response(body, {
    status: 200,
    headers: lastModified ? { "last-modified": lastModified } : {},
  });
}

describe("TaipeiParkingAdapter", () => {
  it("joins static and live data, converts coordinates and applies the source timestamp", async () => {
    const fetcher: typeof fetch = async (input) => {
      const url = String(input);
      if (url.endsWith("TCMSV_alldesc.xml")) {
        return response(
          `<DATA><PARK><ID>TPE1</ID><NAME>民生立體停車場</NAME><AREA>松山區</AREA><ADDRESS>民生東路</ADDRESS><PAYEX>小型車30元/時</PAYEX><TOTALCAR>100</TOTALCAR><TW97X>306457.778</TW97X><TW97Y>2772361.517</TW97Y></PARK></DATA>`,
        );
      }
      return response(
        `<DATA><UPDATETIME>Sun Nov 01 11:00:00 CST 2026</UPDATETIME><PARK><ID>TPE1</ID><AVAILABLECAR>13</AVAILABLECAR></PARK></DATA>`,
      );
    };
    const adapter = new TaipeiParkingAdapter({
      fetcher,
      now: () => new Date("2026-11-01T03:01:00.000Z"),
    });

    await expect(adapter.fetchLots()).resolves.toMatchObject([
      {
        city: "taipei",
        id: "TPE1",
        name: "民生立體停車場",
        feeDescription: "小型車30元/時",
        totalCars: 100,
        latitude: expect.closeTo(25.05, 0.02),
        longitude: expect.closeTo(121.56, 0.02),
        availability: {
          status: "available",
          availableCars: 13,
          sourceUpdatedAt: "2026-11-01T03:00:00.000Z",
        },
      },
    ]);
  });

  it("does not expose a live count when the official timestamp is stale", async () => {
    const fetcher: typeof fetch = async (input) => {
      if (String(input).endsWith("TCMSV_alldesc.xml")) {
        return response("<DATA><PARK><ID>TPE1</ID><NAME>停車場</NAME></PARK></DATA>");
      }
      return response(
        "<DATA><UPDATETIME>Sun Nov 01 10:00:00 CST 2026</UPDATETIME><PARK><ID>TPE1</ID><AVAILABLECAR>5</AVAILABLECAR></PARK></DATA>",
      );
    };
    const adapter = new TaipeiParkingAdapter({
      fetcher,
      now: () => new Date("2026-11-01T03:01:00.000Z"),
    });
    const lots = await adapter.fetchLots();
    expect(lots[0]?.availability).toMatchObject({ status: "stale", lastReportedCars: 5 });
    expect(lots[0]?.availability).not.toHaveProperty("availableCars");
  });
});

describe("NewTaipeiParkingAdapter", () => {
  it("joins paginated official records and keeps freshness explicitly unverified", async () => {
    const fetcher: typeof fetch = async (input) => {
      const url = new URL(String(input));
      const isStatic = url.pathname.includes("b1464ef0");
      const page = Number(url.searchParams.get("page"));
      if (page > 0) return response("[]");
      return isStatic
        ? response(
            JSON.stringify([
              {
                ID: "010001",
                NAME: "府中停車場",
                AREA: "板橋區",
                ADDRESS: "府中路",
                PAYEX: "小型車計時 30 元",
                SERVICETIME: "0~24時",
                TOTALCAR: "50",
                TW97X: "296882",
                TW97Y: "2767068",
              },
            ]),
          )
        : response(JSON.stringify([{ ID: "010001", AVAILABLECAR: "0" }]));
    };
    const adapter = new NewTaipeiParkingAdapter({
      fetcher,
      now: () => new Date("2026-11-01T03:01:00.000Z"),
    });
    const lots = await adapter.fetchLots();

    expect(lots).toMatchObject([
      {
        city: "new_taipei",
        id: "010001",
        name: "府中停車場",
        feeDescription: "小型車計時 30 元",
        totalCars: 50,
        availability: {
          status: "unverified",
          availableCars: 0,
          freshnessNote: expect.stringContaining("無法驗證"),
        },
      },
    ]);
    expect(lots[0]?.latitude).toBeCloseTo(25.0, 0);
    expect(lots[0]?.longitude).toBeCloseTo(121.46, 1);
  });

  it("preserves the official no-availability sentinel as not provided", async () => {
    const fetcher: typeof fetch = async (input) => {
      const url = new URL(String(input));
      if (Number(url.searchParams.get("page")) > 0) return response("[]");
      return url.pathname.includes("b1464ef0")
        ? response(JSON.stringify([{ ID: "1", NAME: "無空位資料場" }]))
        : response(JSON.stringify([{ ID: "1", AVAILABLECAR: "-9" }]));
    };
    const adapter = new NewTaipeiParkingAdapter({ fetcher });
    const lots = await adapter.fetchLots();
    expect(lots[0]?.availability).toMatchObject({ status: "not_provided" });
  });
});
