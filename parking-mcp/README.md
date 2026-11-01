# Parking MCP

Standalone TypeScript MCP server for searching official off-street car parking data by name in Taipei and New Taipei.

## Requirements

- Node.js >= 24
- Network access to the city open-data endpoints

## Build and run

```powershell
npm install
npm run build
npm start
```

The server uses stdio. Keep stdout reserved for MCP JSON-RPC; startup diagnostics are written to stderr.

## Configure VS Code

Add the server to `.vscode/mcp.json`:

```json
{
  "servers": {
    "parking": {
      "type": "stdio",
      "command": "node",
      "args": ["C:/path/to/my-mcp/parking-mcp/dist/index.js"]
    }
  }
}
```

Use an absolute path on Windows. The source must be built before launching the server.

## Tool

### `parking_search`

- `name` (required): parking-lot name or a fragment.
- `city` (optional): `taipei` or `new_taipei`; omitted searches both supported cities.
- `limit` (optional): 1-50 results, default 10.

Search is Unicode-normalized and punctuation/whitespace-insensitive substring matching. Exact name matches are listed first. Multiple hits are returned as candidates with city and address so the caller can ask the user to confirm.

Each result includes official source, location, original fee description, service hours, total car spaces, and an availability status:

- `available`: Taipei count within the configured freshness window.
- `unverified`: New Taipei supplied a count, but its API does not include the upstream data timestamp. The response includes the query time and states the city's published three-minute refresh interval; it does not claim that the count is verified current.
- `not_provided`: the source uses a negative sentinel (including New Taipei `-9`) to mean the vacancy count is unavailable. It is not zero.
- `not_reported`: there is no live record for the lot.
- `stale`: Taipei's source timestamp exceeded the freshness threshold. The last reported count is returned separately and must not be interpreted as current vacancies.

If one city feed fails, results from the other are preserved and `status` is `partial` with a `sourceErrors` entry. If neither source can be read, the errors are returned rather than a success-shaped empty result.

## Configuration

| Variable | Default | Description |
|---|---:|---|
| `PARKING_TAIPEI_STALE_AFTER_SECONDS` | `600` | Maximum age for Taipei's source timestamp before vacancy is reported as stale. Must be a positive integer. |

Taipei coordinates are converted from TWD97 to WGS84 (the official feed's entrance WGS84 coordinate is preferred when present). New Taipei coordinates are converted from TWD97. Fee descriptions remain in the official source wording rather than being reinterpreted.

## Official sources

- [Taipei parking dataset](https://data.taipei/dataset/detail?id=d5c0656b-5250-4179-a491-c94daa56ef2c), [static lots](https://tcgbusfs.blob.core.windows.net/blobtcmsv/TCMSV_alldesc.xml), [live availability](https://tcgbusfs.blob.core.windows.net/blobtcmsv/TCMSV_allavailable.xml).
- New Taipei datasets: [static lot data](https://data.ntpc.gov.tw/api/datasets/b1464ef0-9c7c-4a6f-abf7-6bdf32847e68/json?page=0&size=5), [live vacancy](https://data.ntpc.gov.tw/api/datasets/e09b35a5-a738-48cc-b0f5-570b67ad9c78/json?page=0&size=5), [API guide and published update frequency](https://data.ntpc.gov.tw/applications).

## Development

```powershell
npm run typecheck
npm test
npm run build
```

Each city has its own adapter implementing the shared `ParkingAdapter` contract. To add a city, first verify official dataset semantics and freshness, then implement an adapter and its fixture-based tests. Do not treat missing fields as zero or merge datasets without a stable official lot ID.
