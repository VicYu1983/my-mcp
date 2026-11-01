import proj4 from "proj4";

proj4.defs(
  "EPSG:3826",
  "+proj=tmerc +lat_0=0 +lon_0=121 +k=0.9999 +x_0=250000 +y_0=0 +ellps=GRS80 +units=m +no_defs +type=crs",
);

export function parseTwd97Coordinates(
  xValue: string | undefined,
  yValue: string | undefined,
): { latitude: number; longitude: number } | undefined {
  if (xValue === undefined || yValue === undefined) return undefined;
  const x = Number(xValue);
  const y = Number(yValue);
  if (!Number.isFinite(x) || !Number.isFinite(y) || (x === 0 && y === 0)) return undefined;
  const [longitude, latitude] = proj4("EPSG:3826", "EPSG:4326", [x, y]);
  if (
    !Number.isFinite(latitude) ||
    !Number.isFinite(longitude) ||
    latitude < 20 ||
    latitude > 27 ||
    longitude < 118 ||
    longitude > 123
  ) {
    return undefined;
  }
  return { latitude, longitude };
}
