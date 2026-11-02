export class MapsApiError extends Error {
  constructor(
    message: string,
    readonly httpStatus: number,
  ) {
    super(message);
    this.name = "MapsApiError";
  }
}

export interface MapsClientOptions {
  apiKey: string;
  fetch?: typeof fetch;
  timeoutMs?: number;
}

export interface RequestOptions {
  method: "GET" | "POST";
  fieldMask: string;
  body?: unknown;
}

const DEFAULT_TIMEOUT_MS = 10_000;

export class MapsClient {
  private readonly apiKey: string;
  private readonly fetchImpl: typeof fetch;
  private readonly timeoutMs: number;

  constructor(options: MapsClientOptions) {
    this.apiKey = options.apiKey;
    this.fetchImpl = options.fetch ?? fetch;
    this.timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  }

  // The key travels in a header, never in the URL, so it cannot leak through logged URLs.
  async request<T>(url: string, options: RequestOptions): Promise<T> {
    const headers: Record<string, string> = {
      "X-Goog-Api-Key": this.apiKey,
      "X-Goog-FieldMask": options.fieldMask,
    };
    const init: RequestInit = {
      method: options.method,
      headers,
      signal: AbortSignal.timeout(this.timeoutMs),
    };
    if (options.body !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.body);
    }

    let response: Response;
    try {
      response = await this.fetchImpl(url, init);
    } catch (error) {
      throw new MapsApiError(`無法連線至 Google Maps 服務：${this.sanitize(errorMessage(error))}`, 0);
    }

    const text = await response.text();
    if (!response.ok) {
      throw new MapsApiError(
        `Google Maps API 回應 ${response.status}：${this.sanitize(extractErrorMessage(text))}`,
        response.status,
      );
    }
    try {
      return JSON.parse(text) as T;
    } catch {
      throw new MapsApiError("Google Maps API 回傳非 JSON 內容", response.status);
    }
  }

  private sanitize(message: string): string {
    return message.split(this.apiKey).join("[REDACTED]");
  }
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function extractErrorMessage(text: string): string {
  try {
    const parsed = JSON.parse(text) as { error?: { message?: unknown } };
    if (typeof parsed.error?.message === "string") {
      return parsed.error.message;
    }
  } catch {
    // Not JSON; fall through to the raw text.
  }
  return text.slice(0, 300) || "(empty response)";
}
