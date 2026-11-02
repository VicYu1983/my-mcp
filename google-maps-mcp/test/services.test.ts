import { describe, expect, it, vi } from "vitest";
import { MapsApiError, MapsClient } from "../src/maps-client.js";
import { getPlaceDetails, searchPlaces } from "../src/services/places.js";
import { computeRoute } from "../src/services/routes.js";

const KEY = "test-key-123";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockFetch(response: Response | (() => Promise<Response>)) {
  return vi.fn(async (_url: string | URL | Request, _init?: RequestInit) =>
    typeof response === "function" ? response() : response.clone(),
  );
}

function clientWith(fetchMock: ReturnType<typeof mockFetch>): MapsClient {
  return new MapsClient({ apiKey: KEY, fetch: fetchMock as unknown as typeof fetch });
}

describe("MapsClient", () => {
  it("sends the key in a header and never in the URL", async () => {
    const fetchMock = mockFetch(jsonResponse({ places: [] }));
    await searchPlaces(clientWith(fetchMock), {
      query: "台北 101",
      maxResults: 5,
      languageCode: "zh-TW",
    });

    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(String(url)).not.toContain(KEY);
    const headers = init?.headers as Record<string, string>;
    expect(headers["X-Goog-Api-Key"]).toBe(KEY);
  });

  it("returns an API error with the upstream message and HTTP status", async () => {
    const fetchMock = mockFetch(
      jsonResponse({ error: { code: 403, message: "API key not valid", status: "PERMISSION_DENIED" } }, 403),
    );
    const error = await searchPlaces(clientWith(fetchMock), {
      query: "x",
      maxResults: 1,
      languageCode: "zh-TW",
    }).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(MapsApiError);
    expect((error as MapsApiError).httpStatus).toBe(403);
    expect((error as Error).message).toContain("API key not valid");
  });

  it("redacts the key if it ever appears in an error message", async () => {
    const fetchMock = mockFetch(
      jsonResponse({ error: { message: `bad key ${KEY}` } }, 400),
    );
    const error = (await searchPlaces(clientWith(fetchMock), {
      query: "x",
      maxResults: 1,
      languageCode: "zh-TW",
    }).catch((e: unknown) => e)) as Error;

    expect(error.message).not.toContain(KEY);
    expect(error.message).toContain("[REDACTED]");
  });

  it("wraps network failures without leaking the key", async () => {
    const fetchMock = mockFetch(async () => {
      throw new Error(`socket hang up ${KEY}`);
    });
    const error = (await searchPlaces(clientWith(fetchMock), {
      query: "x",
      maxResults: 1,
      languageCode: "zh-TW",
    }).catch((e: unknown) => e)) as MapsApiError;

    expect(error).toBeInstanceOf(MapsApiError);
    expect(error.httpStatus).toBe(0);
    expect(error.message).not.toContain(KEY);
  });
});

describe("searchPlaces", () => {
  it("posts the query with the field mask and normalizes results", async () => {
    const fetchMock = mockFetch(
      jsonResponse({
        places: [
          {
            id: "ChIJ-abc_123",
            displayName: { text: "臺北 101", languageCode: "zh-TW" },
            formattedAddress: "110台北市信義區信義路五段7號",
            location: { latitude: 25.033, longitude: 121.5654 },
            rating: 4.6,
            userRatingCount: 1000,
            businessStatus: "OPERATIONAL",
            googleMapsUri: "https://maps.google.com/?cid=1",
            types: ["tourist_attraction"],
          },
          { displayName: { text: "沒有 ID 的地點" } },
        ],
      }),
    );
    const results = await searchPlaces(clientWith(fetchMock), {
      query: "台北 101",
      maxResults: 3,
      languageCode: "zh-TW",
      regionCode: "TW",
    });

    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(url).toBe("https://places.googleapis.com/v1/places:searchText");
    expect(init?.method).toBe("POST");
    const headers = init?.headers as Record<string, string>;
    expect(headers["X-Goog-FieldMask"]).toContain("places.displayName");
    expect(JSON.parse(String(init?.body))).toEqual({
      textQuery: "台北 101",
      maxResultCount: 3,
      languageCode: "zh-TW",
      regionCode: "TW",
    });

    expect(results).toHaveLength(1);
    expect(results[0]).toEqual({
      placeId: "ChIJ-abc_123",
      name: "臺北 101",
      address: "110台北市信義區信義路五段7號",
      location: { latitude: 25.033, longitude: 121.5654 },
      rating: 4.6,
      userRatingCount: 1000,
      businessStatus: "OPERATIONAL",
      googleMapsUri: "https://maps.google.com/?cid=1",
      types: ["tourist_attraction"],
    });
  });

  it("returns an empty list when the API omits places", async () => {
    const fetchMock = mockFetch(jsonResponse({}));
    const results = await searchPlaces(clientWith(fetchMock), {
      query: "nothing",
      maxResults: 5,
      languageCode: "zh-TW",
    });
    expect(results).toEqual([]);
  });

  it("does not invent values for missing upstream fields", async () => {
    const fetchMock = mockFetch(jsonResponse({ places: [{ id: "abc" }] }));
    const [place] = await searchPlaces(clientWith(fetchMock), {
      query: "x",
      maxResults: 1,
      languageCode: "zh-TW",
    });
    expect(place).toEqual({ placeId: "abc" });
    expect(place).not.toHaveProperty("rating");
    expect(place).not.toHaveProperty("userRatingCount");
  });
});

describe("getPlaceDetails", () => {
  it("requests details with the place details field mask", async () => {
    const fetchMock = mockFetch(
      jsonResponse({
        id: "abc",
        displayName: { text: "店家" },
        nationalPhoneNumber: "02 1234 5678",
        websiteUri: "https://example.com",
        regularOpeningHours: { weekdayDescriptions: ["星期一: 10:00–22:00"] },
        businessStatus: "OPERATIONAL",
      }),
    );
    const details = await getPlaceDetails(clientWith(fetchMock), "abc", "zh-TW");

    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(url).toBe("https://places.googleapis.com/v1/places/abc?languageCode=zh-TW");
    expect(init?.method).toBe("GET");
    expect((init?.headers as Record<string, string>)["X-Goog-FieldMask"]).toContain(
      "regularOpeningHours.weekdayDescriptions",
    );
    expect(details).toMatchObject({
      placeId: "abc",
      name: "店家",
      phone: "02 1234 5678",
      website: "https://example.com",
      openingHours: ["星期一: 10:00–22:00"],
    });
  });

  it("rejects placeIds that could alter the request path", async () => {
    const fetchMock = mockFetch(jsonResponse({}));
    await expect(
      getPlaceDetails(clientWith(fetchMock), "../other?x=1", "zh-TW"),
    ).rejects.toThrow("placeId 格式不正確");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("computeRoute", () => {
  it("sends address and coordinate waypoints and parses durations", async () => {
    const fetchMock = mockFetch(
      jsonResponse({
        routes: [
          {
            distanceMeters: 12345,
            duration: "1234.5s",
            staticDuration: "1100s",
            localizedValues: {
              distance: { text: "12.3 公里" },
              duration: { text: "21 分鐘" },
            },
          },
        ],
      }),
    );
    const route = await computeRoute(clientWith(fetchMock), {
      origin: "台北車站",
      destination: { latitude: 25.033, longitude: 121.5654 },
      travelMode: "DRIVE",
      languageCode: "zh-TW",
    });

    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(url).toBe("https://routes.googleapis.com/directions/v2:computeRoutes");
    expect(JSON.parse(String(init?.body))).toEqual({
      origin: { address: "台北車站" },
      destination: { location: { latLng: { latitude: 25.033, longitude: 121.5654 } } },
      travelMode: "DRIVE",
      languageCode: "zh-TW",
      units: "METRIC",
    });
    expect(route).toEqual({
      distanceMeters: 12345,
      durationSeconds: 1234.5,
      staticDurationSeconds: 1100,
      distanceText: "12.3 公里",
      durationText: "21 分鐘",
    });
  });

  it("reports an error when no route is returned", async () => {
    const fetchMock = mockFetch(jsonResponse({}));
    await expect(
      computeRoute(clientWith(fetchMock), {
        origin: "a",
        destination: "b",
        travelMode: "WALK",
        languageCode: "zh-TW",
      }),
    ).rejects.toThrow("找不到可行的路線");
  });
});
