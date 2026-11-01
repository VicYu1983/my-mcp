import type { CallToolResult } from "@modelcontextprotocol/sdk/types.js";

export function jsonResult(payload: unknown): CallToolResult {
  return {
    content: [{ type: "text", text: JSON.stringify(payload, null, 2) }],
  };
}

export function errorResult(error: unknown): CallToolResult {
  const message = error instanceof Error ? error.message : String(error);
  return {
    isError: true,
    content: [{ type: "text", text: `Error: ${message}` }],
  };
}

/** Wraps a tool body so failures surface as tool errors instead of crashing the server. */
export async function guard(fn: () => Promise<CallToolResult>): Promise<CallToolResult> {
  try {
    return await fn();
  } catch (error) {
    return errorResult(error);
  }
}
