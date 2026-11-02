import type { MapsClient } from "../maps-client.js";

const SEARCH_TEXT_URL = "https://places.googleapis.com/v1/places:searchText";
const PLACE_DETAILS_URL = "https://places.googleapis.com/v1/places";

const SEARCH_FIELD_MASK = [
  "places.id",
  "places.displayName",
  "places.formattedAddress",
  "places.location",
  "places.rating",
  "places.userRatingCount",
  "places.businessStatus",
  "places.googleMapsUri",
  "places.types",
].join(",");

const DETAILS_FIELD_MASK = [
  "id",
  "displayName",
  "formattedAddress",
  "location",
  "nationalPhoneNumber",
  "internationalPhoneNumber",
  "websiteUri",
  "regularOpeningHours.weekdayDescriptions",
  "rating",
  "userRatingCount",
  "businessStatus",
  "googleMapsUri",
  "types",
].join(",");

// Google place IDs are URL-safe tokens; reject anything else before it reaches a URL path.
const PLACE_ID_PATTERN = /^[A-Za-z0-9_-]+$/;

export interface Coordinates {
  latitude: number;
  longitude: number;
}

export interface PlaceSummary {
  placeId: string;
  name?: string;
  address?: string;
  location?: Coordinates;
  rating?: number;
  userRatingCount?: number;
  businessStatus?: string;
  googleMapsUri?: string;
  types?: string[];
}

export interface PlaceDetails extends PlaceSummary {
  phone?: string;
  internationalPhone?: string;
  website?: string;
  openingHours?: string[];
}

interface RawPlace {
  id?: string;
  displayName?: { text?: string };
  formattedAddress?: string;
  location?: Coordinates;
  rating?: number;
  userRatingCount?: number;
  businessStatus?: string;
  googleMapsUri?: string;
  types?: string[];
  nationalPhoneNumber?: string;
  internationalPhoneNumber?: string;
  websiteUri?: string;
  regularOpeningHours?: { weekdayDescriptions?: string[] };
}

export interface SearchPlacesInput {
  query: string;
  maxResults: number;
  languageCode: string;
  regionCode?: string;
}

export async function searchPlaces(
  client: MapsClient,
  input: SearchPlacesInput,
): Promise<PlaceSummary[]> {
  const body = {
    textQuery: input.query,
    maxResultCount: input.maxResults,
    languageCode: input.languageCode,
    ...(input.regionCode ? { regionCode: input.regionCode } : {}),
  };
  const data = await client.request<{ places?: RawPlace[] }>(SEARCH_TEXT_URL, {
    method: "POST",
    fieldMask: SEARCH_FIELD_MASK,
    body,
  });
  return (data.places ?? []).flatMap((place) => {
    const summary = toSummary(place);
    return summary ? [summary] : [];
  });
}

export async function getPlaceDetails(
  client: MapsClient,
  placeId: string,
  languageCode: string,
): Promise<PlaceDetails> {
  if (!PLACE_ID_PATTERN.test(placeId)) {
    throw new Error("placeId 格式不正確");
  }
  const url = `${PLACE_DETAILS_URL}/${placeId}?languageCode=${encodeURIComponent(languageCode)}`;
  const place = await client.request<RawPlace>(url, {
    method: "GET",
    fieldMask: DETAILS_FIELD_MASK,
  });
  const summary = toSummary(place);
  if (!summary) {
    throw new Error("Google Maps 未回傳此地點的 ID");
  }
  return {
    ...summary,
    ...optional("phone", place.nationalPhoneNumber),
    ...optional("internationalPhone", place.internationalPhoneNumber),
    ...optional("website", place.websiteUri),
    ...optional("openingHours", place.regularOpeningHours?.weekdayDescriptions),
  };
}

function toSummary(place: RawPlace): PlaceSummary | undefined {
  if (!place.id) {
    return undefined;
  }
  return {
    placeId: place.id,
    ...optional("name", place.displayName?.text),
    ...optional("address", place.formattedAddress),
    ...optional("location", place.location),
    ...optional("rating", place.rating),
    ...optional("userRatingCount", place.userRatingCount),
    ...optional("businessStatus", place.businessStatus),
    ...optional("googleMapsUri", place.googleMapsUri),
    ...optional("types", place.types),
  };
}

// Missing upstream fields stay absent rather than being filled with zero or empty strings.
function optional<K extends string, V>(key: K, value: V | undefined): Partial<Record<K, V>> {
  return value === undefined ? {} : ({ [key]: value } as Record<K, V>);
}
