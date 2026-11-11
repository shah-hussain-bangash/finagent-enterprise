import "server-only";

// Emit only fixed labels. Never log raw error objects, messages, keys, or URLs.
const rules = {
  google_flow: {
    marker: "GOOGLE_FLOW_DIAGNOSTIC",
    stages: ["random_values", "pkce_challenge", "authorization_url", "flow_store"],
  },
  auth_store: {
    marker: "AUTH_STORE_DIAGNOSTIC",
    stages: ["record_key", "redis_connect", "deadline_client", "seal_record", "redis_set", "redis_result"],
  },
  redis_connect: { marker: "AUTH_REDIS_DIAGNOSTIC", stages: ["connect"] },
} as const;

const names = [
  "Error", "TypeError", "RangeError", "AbortError", "TimeoutError", "ErrorReply",
  "ConnectionTimeoutError", "SocketTimeoutError", "SocketClosedUnexpectedlyError",
  "ClientClosedError", "ClientOfflineError", "OperationProcessingError", "UnsupportedOperationError",
] as const;
const codes = [
  "ECONNREFUSED", "ECONNRESET", "ENOTFOUND", "EAI_AGAIN", "ETIMEDOUT", "EPIPE",
  "ENETUNREACH", "EHOSTUNREACH", "ERR_INVALID_ARG_TYPE", "ERR_INVALID_ARG_VALUE",
  "ERR_INVALID_URL", "ERR_CRYPTO_INVALID_KEYLEN", "ERR_CRYPTO_INVALID_IV",
  "ERR_CRYPTO_OPERATION_FAILED", "ERR_TLS_CERT_ALTNAME_INVALID", "CERT_HAS_EXPIRED",
  "DEPTH_ZERO_SELF_SIGNED_CERT", "SELF_SIGNED_CERT_IN_CHAIN", "UNABLE_TO_VERIFY_LEAF_SIGNATURE",
] as const;
const messages = [
  ["The client is closed", "CLIENT_CLOSED"],
  ["The client is offline", "CLIENT_OFFLINE"],
  ["Socket timeout", "SOCKET_TIMEOUT"],
  ["Connection timeout", "CONNECT_TIMEOUT"],
  ["Socket closed unexpectedly", "SOCKET_CLOSED"],
  ["Authentication record collision", "RECORD_COLLISION"],
  ["Authentication store unavailable", "STORE_UNAVAILABLE"],
  ["Invalid authentication lifetime", "INVALID_LIFETIME"],
  ["Invalid session handle", "INVALID_HANDLE"],
  ["Web session encryption is not configured", "ENCRYPTION_CONFIG"],
] as const;
const redisReplies = ["NOAUTH", "WRONGPASS", "NOPERM", "READONLY", "OOM", "MISCONF", "LOADING", "BUSY", "ERR"] as const;

export function logAuthDiagnostic(scope: keyof typeof rules, stage: string, error: unknown): void {
  try {
    if (!Object.prototype.hasOwnProperty.call(rules, scope)) return;
    const rule = rules[scope];
    const safeStage = rule.stages.find((candidate) => candidate === stage);
    if (!safeStage) return;
    const value = error && typeof error === "object"
      ? error as { name?: unknown; code?: unknown; message?: unknown } : {};
    const name = names.find((candidate) => candidate === value.name) ?? "OTHER";
    const code = codes.find((candidate) => candidate === value.code) ?? "NONE";
    const message = typeof value.message === "string" ? value.message : "";
    const reason = messages.find(([expected]) => expected === message)?.[1] ?? "NONE";
    const reply = redisReplies.find((candidate) => message === candidate
      || message.startsWith(candidate + " ")) ?? "NONE";
    console.error(`${rule.marker}: stage=${safeStage} class=${name} code=${code} reason=${reason} redis=${reply}`);
  } catch {
    // Diagnostics must never replace the original authentication failure.
  }
}
