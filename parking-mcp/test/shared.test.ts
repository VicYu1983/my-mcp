import { describe, expect, it } from "vitest";
import {
  makeAvailability,
  normalizeSearchText,
  parseTaipeiTimestamp,
} from "../src/providers/shared.js";

describe("official availability normalization", () => {
  const checkedAt = new Date("2026-11-01T03:01:00.000Z");

  it("does not turn unsupported sentinel values into zero", () => {
    expect(makeAvailability("new_taipei", -9, undefined, checkedAt, 600)).toMatchObject({
      status: "not_provided",
      freshnessNote: expect.stringContaining("不代表空位數為 0"),
    });
  });

  it("keeps New Taipei's value but declares freshness unverified", () => {
    expect(makeAvailability("new_taipei", 0, undefined, checkedAt, 600)).toMatchObject({
      status: "unverified",
      availableCars: 0,
      freshnessNote: expect.stringContaining("每 3 分鐘更新"),
    });
  });

  it("marks Taipei data stale and does not expose it as current availability", () => {
    expect(
      makeAvailability("taipei", 17, "2026-11-01T02:40:00.000Z", checkedAt, 600),
    ).toMatchObject({
      status: "stale",
      lastReportedCars: 17,
    });
    expect(
      makeAvailability("taipei", 17, "2026-11-01T02:40:00.000Z", checkedAt, 600),
    ).not.toHaveProperty("availableCars");
  });

  it("marks a missing live record distinctly", () => {
    expect(makeAvailability("taipei", undefined, undefined, checkedAt, 600)).toMatchObject({
      status: "not_reported",
    });
  });
});

describe("Taipei timestamp and query normalization", () => {
  it("parses the official CST timestamp as Taiwan time", () => {
    expect(parseTaipeiTimestamp("Sun Nov 01 11:00:00 CST 2026")).toBe(
      "2026-11-01T03:00:00.000Z",
    );
  });

  it("normalizes whitespace and punctuation in CJK names", () => {
    expect(normalizeSearchText("  臺北．車站  停車場 ")).toBe("臺北車站停車場");
  });
});
