export type CityId = "taipei" | "new_taipei";

export type AvailabilityStatus =
  | "available"
  | "not_provided"
  | "not_reported"
  | "stale"
  | "unverified";

export interface ParkingAvailability {
  status: AvailabilityStatus;
  availableCars?: number;
  lastReportedCars?: number;
  sourceUpdatedAt?: string;
  freshnessNote?: string;
}

export interface ParkingLot {
  city: CityId;
  cityName: string;
  id: string;
  name: string;
  district?: string;
  address?: string;
  latitude?: number;
  longitude?: number;
  coordinateSystem?: "WGS84";
  feeDescription?: string;
  serviceTime?: string;
  totalCars?: number;
  availability: ParkingAvailability;
  sourceUrl: string;
}

export interface ParkingAdapter {
  readonly city: CityId;
  readonly cityName: string;
  fetchLots(): Promise<ParkingLot[]>;
}

export interface SourceError {
  city: CityId;
  cityName: string;
  message: string;
}

export interface ParkingSearchResult {
  query: string;
  checkedAt: string;
  status: "complete" | "partial";
  count: number;
  totalMatches: number;
  truncated: boolean;
  results: ParkingLot[];
  sourceErrors: SourceError[];
}
