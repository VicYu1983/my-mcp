import { XMLParser } from "fast-xml-parser";
import type { ParkingAdapter, ParkingLot } from "../types.js";
import { parseTwd97Coordinates } from "./coordinates.js";
import {
  fetchText,
  fieldText,
  finiteNumber,
  makeAvailability,
  parseTaipeiTimestamp,
  record,
  type FetchFunction,
} from "./shared.js";

const STATIC_URL = "https://tcgbusfs.blob.core.windows.net/blobtcmsv/TCMSV_alldesc.xml";
const LIVE_URL = "https://tcgbusfs.blob.core.windows.net/blobtcmsv/TCMSV_allavailable.xml";
const DATASET_URL = "https://data.taipei/dataset/detail?id=d5c0656b-5250-4179-a491-c94daa56ef2c";
const xmlParser = new XMLParser({ ignoreAttributes: true, parseTagValue: false, trimValues: true });

interface TaipeiFeed {
  updateTime?: string;
  parks: Record<string, unknown>[];
}

function parseFeed(xml: string): TaipeiFeed {
  const root = record(xmlParser.parse(xml));
  const data = record(root?.DATA);
  if (!data) throw new Error("臺北官方 XML 格式錯誤：找不到 DATA 節點。");
  const parksNode = data.PARK;
  const parks = (Array.isArray(parksNode) ? parksNode : [parksNode])
    .map(record)
    .filter((park): park is Record<string, unknown> => park !== undefined);
  return { updateTime: fieldText(data, "UPDATETIME"), parks };
}

function parseEntranceCoordinates(
  value: unknown,
): { latitude: number; longitude: number } | undefined {
  const entranceRoot = record(value);
  const entrance = record(entranceRoot?.EntrancecoordInfo);
  if (!entrance) return undefined;
  const latitude = finiteNumber(fieldText(entrance, "Xcod"));
  const longitude = finiteNumber(fieldText(entrance, "Ycod"));
  if (
    latitude === undefined ||
    longitude === undefined ||
    latitude < 20 ||
    latitude > 27 ||
    longitude < 118 ||
    longitude > 123
  ) {
    return undefined;
  }
  return { latitude, longitude };
}

function indexAvailability(parks: Record<string, unknown>[]): Map<string, number> {
  const indexed = new Map<string, number>();
  for (const park of parks) {
    const id = fieldText(park, "ID");
    const rawCars = finiteNumber(fieldText(park, "AVAILABLECAR"));
    if (id && rawCars !== undefined) indexed.set(id, rawCars);
  }
  return indexed;
}

export class TaipeiParkingAdapter implements ParkingAdapter {
  readonly city = "taipei" as const;
  readonly cityName = "臺北市";
  private readonly fetcher: FetchFunction;
  private readonly now: () => Date;
  private readonly staleAfterSeconds: number;

  constructor(options: {
    fetcher?: FetchFunction;
    now?: () => Date;
    staleAfterSeconds?: number;
  } = {}) {
    this.fetcher = options.fetcher ?? fetch;
    this.now = options.now ?? (() => new Date());
    this.staleAfterSeconds = options.staleAfterSeconds ?? 600;
  }

  async fetchLots(): Promise<ParkingLot[]> {
    const [staticResponse, liveResponse] = await Promise.all([
      fetchText(this.fetcher, STATIC_URL, "application/xml"),
      fetchText(this.fetcher, LIVE_URL, "application/xml"),
    ]);
    const staticFeed = parseFeed(staticResponse.body);
    const liveFeed = parseFeed(liveResponse.body);
    const sourceUpdatedAt =
      parseTaipeiTimestamp(liveFeed.updateTime) ??
      parseTaipeiTimestamp(liveResponse.lastModified);
    if (!sourceUpdatedAt) {
      throw new Error("臺北官方即時資料缺少有效的 UPDATETIME/Last-Modified。");
    }

    const availability = indexAvailability(liveFeed.parks);
    const checkedAt = this.now();
    return staticFeed.parks.flatMap((park) => {
      const id = fieldText(park, "ID");
      const name = fieldText(park, "NAME");
      if (!id || !name) return [];
      const coordinates =
        parseEntranceCoordinates(park.EntranceCoord) ??
        parseTwd97Coordinates(fieldText(park, "TW97X"), fieldText(park, "TW97Y"));
      return [
        {
          city: this.city,
          cityName: this.cityName,
          id,
          name,
          district: fieldText(park, "AREA"),
          address: fieldText(park, "ADDRESS"),
          ...(coordinates
            ? {
                latitude: coordinates.latitude,
                longitude: coordinates.longitude,
                coordinateSystem: "WGS84" as const,
              }
            : {}),
          feeDescription: fieldText(park, "PAYEX"),
          serviceTime: fieldText(park, "SERVICETIME"),
          totalCars: finiteNumber(fieldText(park, "TOTALCAR")),
          availability: makeAvailability(
            this.city,
            availability.get(id),
            sourceUpdatedAt,
            checkedAt,
            this.staleAfterSeconds,
          ),
          sourceUrl: DATASET_URL,
        },
      ];
    });
  }
}
