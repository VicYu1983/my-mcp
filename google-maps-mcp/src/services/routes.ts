import type { MapsClient } from "../maps-client.js";
import type { Coordinates } from "./places.js";

const COMPUTE_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes";

const ROUTE_FIELD_MASK = [
  "routes.distanceMeters",
  "routes.duration",
  "routes.staticDuration",
  "routes.localizedValues",
].join(",");

export type TravelMode = "DRIVE" | "WALK" | "BICYCLE" | "TRANSIT";

// A place is either a free-form address or an exact coordinate pair.
export type Waypoint = string | Coordinates;

export interface ComputeRouteInput {
  origin: Waypoint;
  destination: Waypoint;
  travelMode: TravelMode;
  languageCode: string;
}

export interface RouteResult {
  distanceMeters: number;
  durationSeconds: number;
  staticDurationSeconds?: number;
  distanceText?: string;
  durationText?: string;
}

interface RawRoute {
  distanceMeters?: number;
  duration?: string;
  staticDuration?: string;
  localizedValues?: {
    distance?: { text?: string };
    duration?: { text?: string };
  };
}

export async function computeRoute(
  client: MapsClient,
  input: ComputeRouteInput,
): Promise<RouteResult> {
  const body = {
    origin: toWaypoint(input.origin),
    destination: toWaypoint(input.destination),
    travelMode: input.travelMode,
    languageCode: input.languageCode,
    units: "METRIC",
  };
  const data = await client.request<{ routes?: RawRoute[] }>(COMPUTE_ROUTES_URL, {
    method: "POST",
    fieldMask: ROUTE_FIELD_MASK,
    body,
  });
  const route = data.routes?.[0];
  if (!route || route.distanceMeters === undefined || route.duration === undefined) {
    throw new Error("找不到可行的路線");
  }
  return {
    distanceMeters: route.distanceMeters,
    durationSeconds: parseDurationSeconds(route.duration),
    ...(route.staticDuration === undefined
      ? {}
      : { staticDurationSeconds: parseDurationSeconds(route.staticDuration) }),
    ...(route.localizedValues?.distance?.text === undefined
      ? {}
      : { distanceText: route.localizedValues.distance.text }),
    ...(route.localizedValues?.duration?.text === undefined
      ? {}
      : { durationText: route.localizedValues.duration.text }),
  };
}

function toWaypoint(waypoint: Waypoint): Record<string, unknown> {
  if (typeof waypoint === "string") {
    return { address: waypoint };
  }
  return {
    location: {
      latLng: { latitude: waypoint.latitude, longitude: waypoint.longitude },
    },
  };
}

// Routes API encodes durations as protobuf Duration strings, e.g. "1234.5s".
function parseDurationSeconds(value: string): number {
  const seconds = Number.parseFloat(value);
  if (!value.endsWith("s") || Number.isNaN(seconds)) {
    throw new Error(`無法解析路線時間：${value}`);
  }
  return seconds;
}
