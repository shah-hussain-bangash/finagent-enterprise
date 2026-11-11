import "server-only";
import { logAuthDiagnostic } from "./auth-diagnostics";
import { createCipheriv, createDecipheriv, createHash, randomBytes } from "node:crypto";
import { createClient } from "@redis/client";
import { externalSettings } from "./auth-config";

export interface StoredSession { token: string; subject: string; exp: number }
export interface LoginFlow { state: string; nonce: string; verifier: string; exp: number }
export interface AuthStore {
  put(kind: "flow" | "session", id: string, value: LoginFlow | StoredSession, ttl: number): Promise<void>;
  read(kind: "flow" | "session", id: string, consume?: boolean): Promise<unknown>;
  remove(kind: "flow" | "session", id: string): Promise<void>;
}
export const FLOW_COOKIE = "fa_oidc_flow";
export const SESSION_COOKIE = "fa_entra_session";
export const flowCookieName = (secure: boolean, provider = "entra"): string => {
  const name = provider === "google" ? "fa_google_flow" : FLOW_COOKIE;
  return secure ? `__Host-${name}` : name;
};
export const sessionCookieName = (secure: boolean, provider = "entra"): string => {
  const name = provider === "google" ? "fa_google_session" : SESSION_COOKIE;
  return secure ? `__Host-${name}` : name;
};
export const validHandle = (id: string): boolean => /^[a-f0-9]{64}$/.test(id);
export const newHandle = (): string => randomBytes(32).toString("hex");

function encryptionKey(): Buffer {
  const secret = process.env.FINAGENT_WEB_SESSION_SECRET ?? "";
  if (secret.length < 32) throw new Error("Web session encryption is not configured");
  return createHash("sha256").update(`finagent-entra-v1:${secret}`).digest();
}

export function sealRecord(value: unknown, recordKey: string): string {
  const iv = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", encryptionKey(), iv);
  cipher.setAAD(Buffer.from(recordKey));
  const data = Buffer.concat([cipher.update(JSON.stringify(value), "utf8"), cipher.final()]);
  return Buffer.concat([iv, cipher.getAuthTag(), data]).toString("base64url");
}

export function openRecord(value: string, recordKey: string): unknown {
  if (value.length > 65536) throw new Error("Invalid session record");
  const bytes = Buffer.from(value, "base64url");
  const decipher = createDecipheriv("aes-256-gcm", encryptionKey(), bytes.subarray(0, 12));
  decipher.setAAD(Buffer.from(recordKey));
  decipher.setAuthTag(bytes.subarray(12, 28));
  return JSON.parse(Buffer.concat([decipher.update(bytes.subarray(28)), decipher.final()]).toString("utf8"));
}

function makeRedisClient() {
  return createClient({
      url: externalSettings().redisUrl,
      socket: { connectTimeout: 3000, socketTimeout: 5000, reconnectStrategy: false },
      disableOfflineQueue: true,
      commandsQueueMaxLength: 100,
  });
}
type Client = ReturnType<typeof makeRedisClient>;
let connection: Promise<Client> | undefined;
async function redisClient(): Promise<Client> {
  const cached = connection;
  if (cached) {
    const existing = await cached;
    if (existing.isOpen && existing.isReady) return existing;
    if (connection === cached) connection = undefined;
    try { existing.destroy(); } catch { /* Dispose a terminal client without masking recovery. */ }
  }
  if (!connection) {
    const client = makeRedisClient();
    // Log only fixed classifications; Redis messages may contain connection details.
    client.on("error", () => {});
    const release = () => {
      if (connection === pending) connection = undefined;
    };
    client.on("end", release);
    client.on("terminated", () => {
      release();
      try { client.destroy(); } catch { /* The closed client still needs best-effort cleanup. */ }
    });
    // Assign the cached promise before connect can emit any lifecycle events.
    const pending: Promise<Client> = Promise.resolve()
      .then(() => client.connect())
      .then(() => {
        if (!client.isOpen || !client.isReady) throw new Error("Authentication store unavailable");
        return client;
      })
      .catch((error: unknown) => {
        logAuthDiagnostic("redis_connect", "connect", error);
        release();
        try { client.destroy(); } catch { /* Preserve the original redacted connection failure. */ }
        throw new Error("Authentication store unavailable");
      });
    connection = pending;
  }
  return connection;
}

export async function closeAuthStore(): Promise<void> {
  const pending = connection;
  connection = undefined;
  if (pending) {
    const client = await pending;
    if (client.isOpen) client.destroy();
  }
}

function key(kind: "flow" | "session", id: string): string {
  if (!validHandle(id)) throw new Error("Invalid session handle");
  const cfg = externalSettings();
  return `finagent:web-auth:v1:${cfg.tenantId}:${cfg.clientId}:${kind}:${id}`;
}

export const authStore: AuthStore = {
  async put(kind, id, value, ttl) {
    let stage = "record_key";
    try {
      if (!Number.isInteger(ttl) || ttl < 1 || ttl > 3600) throw new Error("Invalid authentication lifetime");
      const recordKey = key(kind, id);
      stage = "redis_connect";
      const connected = await redisClient();
      stage = "deadline_client";
      const client = connected.withAbortSignal(AbortSignal.timeout(3000));
      stage = "seal_record";
      const sealed = sealRecord(value, recordKey);
      stage = "redis_set";
      const result = await client.set(recordKey, sealed, { EX: ttl, NX: true });
      stage = "redis_result";
      if (result !== "OK") throw new Error("Authentication record collision");
    } catch (error: unknown) {
      logAuthDiagnostic("auth_store", stage, error);
      throw error;
    }
  },
  async read(kind, id, consume = false) {
    if (!validHandle(id)) return null;
    const recordKey = key(kind, id);
    const client = (await redisClient()).withAbortSignal(AbortSignal.timeout(3000));
    // GETDEL atomically consumes a login transaction across all web replicas.
    const value = consume ? await client.getDel(recordKey) : await client.get(recordKey);
    if (value === null) return null;
    return openRecord(value, recordKey);
  },
  async remove(kind, id) {
    if (!validHandle(id)) return;
    const client = (await redisClient()).withAbortSignal(AbortSignal.timeout(3000));
    await client.del(key(kind, id));
  },
};

export function isSession(value: unknown): value is StoredSession {
  if (!value || typeof value !== "object") return false;
  const s = value as Partial<StoredSession>;
  return typeof s.token === "string" && s.token.length > 0 && s.token.length <= 24576
    && typeof s.subject === "string" && s.subject.length > 0 && s.subject.length <= 256
    && typeof s.exp === "number" && Number.isInteger(s.exp) && s.exp > Date.now() / 1000;
}
