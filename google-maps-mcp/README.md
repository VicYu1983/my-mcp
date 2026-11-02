# Google Maps MCP

Standalone TypeScript MCP server for Google Maps Platform. It exposes three read-only tools backed by the Places API (New) and the Routes API.

## Requirements

- Node.js >= 24
- A Google Maps Platform API key with these APIs enabled in the same Google Cloud project:
  - **Places API (New)**
  - **Routes API**

## Build and run

```powershell
npm install
npm run build
npm start
```

The server uses stdio. Keep stdout reserved for MCP JSON-RPC; startup diagnostics are written to stderr. The process exits at startup if `GOOGLE_MAPS_API_KEY` is missing.

## Configure VS Code

Add the server to `.vscode/mcp.json`. The key is requested through a VS Code input prompt so it is never written into the repository:

```json
{
  "inputs": [
    {
      "type": "promptString",
      "id": "google-maps-api-key",
      "description": "Google Maps Platform API key",
      "password": true
    }
  ],
  "servers": {
    "google-maps": {
      "type": "stdio",
      "command": "node",
      "args": ["C:/path/to/my-mcp/google-maps-mcp/dist/index.js"],
      "env": {
        "GOOGLE_MAPS_API_KEY": "${input:google-maps-api-key}"
      }
    }
  }
}
```

Use an absolute path on Windows. Build the server before launching it.

## Tools

### `maps_search_places`

Text search for places.

- `query` (required): place name, address, or keyword.
- `maxResults` (optional): 1-20, default 5.
- `languageCode` (optional): BCP-47 code such as `zh-TW` (default) or `en`.
- `regionCode` (optional): two-letter ISO 3166-1 code, e.g. `TW`, used as a bias.

Returns `placeId`, `name`, `address`, `location`, `rating`, `userRatingCount`, `businessStatus`, `googleMapsUri`, and `types`. Fields Google does not return are omitted rather than filled in.

### `maps_get_place_details`

- `placeId` (required): an ID from `maps_search_places`.
- `languageCode` (optional).

Returns the summary fields plus `phone`, `internationalPhone`, `website`, and `openingHours` (weekday descriptions).

### `maps_compute_route`

Distance and estimated travel time between two points.

- `origin`, `destination` (required): an address string, or `{ "latitude": number, "longitude": number }`.
- `travelMode` (optional): `DRIVE` (default), `WALK`, `BICYCLE`, or `TRANSIT`.
- `languageCode` (optional).

Returns `distanceMeters`, `durationSeconds`, `staticDurationSeconds` (no traffic), and localized text when available. Durations are estimates; they are not guaranteed live traffic conditions.

## Security notes

- The key is sent in the `X-Goog-Api-Key` header, never in the URL, and any echo of it in an error message is redacted.
- Place IDs are validated before they are placed in a request path.
- All tools are annotated read-only. Input is validated with zod before any network call.
- Restrict the key in Google Cloud Console to the two APIs above, and set a budget alert and quota limits; Places and Routes are billed per request.

## Development

```powershell
npm run typecheck
npm test
npm run build
```

Tests use a mocked `fetch` and never call Google. To add a service, create a module under `src/services/` that takes a `MapsClient`, add its field mask and normalization, register the tool in `src/server.ts`, and cover it in `test/`.
