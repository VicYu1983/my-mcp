export interface MapsConfig {
  apiKey: string;
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): MapsConfig {
  const apiKey = env.GOOGLE_MAPS_API_KEY?.trim();
  if (!apiKey) {
    throw new Error("GOOGLE_MAPS_API_KEY is required");
  }
  return { apiKey };
}
