import { parsePositiveInteger } from "./providers/shared.js";

export interface ParkingConfig {
  taipeiStaleAfterSeconds: number;
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): ParkingConfig {
  return {
    taipeiStaleAfterSeconds: parsePositiveInteger(env.PARKING_TAIPEI_STALE_AFTER_SECONDS, 600),
  };
}
