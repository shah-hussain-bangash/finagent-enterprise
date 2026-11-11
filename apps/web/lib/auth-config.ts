import "server-only";
// Server authentication settings. Never import this module into a client component.
export interface EntraSettings {
  tenantId: string;
  clientId: string;
  clientSecret: string;
  scope: string;
  origin: string;
  issuer: string;
  redirectUri: string;
  redisUrl: string;
  secure: boolean;
}

const GUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export function authMode(env: NodeJS.ProcessEnv = process.env): "local" | "entra" | "google" {
  const mode = env.FINAGENT_WEB_AUTH_MODE ?? "local";
  if (mode !== "local" && mode !== "entra" && mode !== "google") throw new Error("Invalid web authentication mode");
  if (mode === "local" && env.FINAGENT_ENVIRONMENT && !["local", "test"].includes(env.FINAGENT_ENVIRONMENT)) {
    throw new Error("Local web authentication is forbidden in this environment");
  }
  return mode;
}

export function entraSettings(env: NodeJS.ProcessEnv = process.env): EntraSettings {
  if (authMode(env) !== "entra") throw new Error("Entra authentication is disabled");
  const tenantId = (env.FINAGENT_ENTRA_TENANT_ID ?? "").toLowerCase();
  const clientId = (env.FINAGENT_ENTRA_WEB_CLIENT_ID ?? "").toLowerCase();
  const clientSecret = env.FINAGENT_ENTRA_WEB_CLIENT_SECRET ?? "";
  const scope = env.FINAGENT_ENTRA_API_SCOPE ?? "";
  if (!GUID.test(tenantId) || !GUID.test(clientId) || !clientSecret || clientSecret.length > 4096) {
    throw new Error("Entra application settings are incomplete");
  }
  // Exactly one delegated FinAgent scope; never request Graph or offline_access.
  if (!/^(?:api:\/\/[0-9a-f-]{36}|https:\/\/[a-z0-9.-]+(?:\/[a-z0-9._-]+)*)\/[a-z0-9._-]+$/i.test(scope)
      || scope.endsWith("/.default")) throw new Error("A delegated API scope is required");
  const { origin, redisUrl, secure } = transportSettings(env);
  return {
    tenantId, clientId, clientSecret, scope, origin,
    issuer: `https://login.microsoftonline.com/${tenantId}/v2.0`,
    redirectUri: `${origin}/auth/entra/callback`,
    redisUrl, secure,
  };
}

// A provider discriminator separates store namespaces and cookies without changing Entra keys.
export interface GoogleSettings extends EntraSettings { tenantId: "google" }
export function googleSettings(env: NodeJS.ProcessEnv = process.env): GoogleSettings {
  if (authMode(env) !== "google") throw new Error("Google authentication is disabled");
  const clientId = env.FINAGENT_GOOGLE_WEB_CLIENT_ID ?? "";
  const clientSecret = env.FINAGENT_GOOGLE_WEB_CLIENT_SECRET ?? "";
  if (!/^[A-Za-z0-9_-]{1,200}\.apps\.googleusercontent\.com$/.test(clientId)
      || !clientSecret || clientSecret.length > 4096) throw new Error("Google settings are incomplete");
  const common = transportSettings(env);
  return { ...common, tenantId: "google", clientId, clientSecret, scope: "openid email profile",
    issuer: "https://accounts.google.com", redirectUri: `${common.origin}/auth/google/callback` };
}

export function externalSettings(): EntraSettings {
  return authMode() === "google" ? googleSettings() : entraSettings();
}

function transportSettings(env: NodeJS.ProcessEnv): { origin: string; redisUrl: string; secure: boolean } {
  const environment = env.FINAGENT_ENVIRONMENT;
  if (!["local", "test", "dev", "staging", "production"].includes(environment ?? "")) {
    throw new Error("An explicit environment is required for external authentication");
  }
  const originUrl = new URL(env.FINAGENT_WEB_ORIGIN ?? "");
  const local = environment === "local" || environment === "test";
  const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(originUrl.hostname);
  if (originUrl.username || originUrl.password || originUrl.pathname !== "/" || originUrl.search || originUrl.hash
      || (originUrl.protocol !== "https:" && !(local && loopback && originUrl.protocol === "http:"))) {
    throw new Error("The web origin must be HTTPS, or local loopback HTTP for local/test");
  }
  const redis = new URL(env.FINAGENT_WEB_AUTH_REDIS_URL ?? "");
  if (!["redis:", "rediss:"].includes(redis.protocol) || redis.hash || (!local && redis.protocol !== "rediss:")) {
    throw new Error("External authentication requires a private Redis session store with TLS outside local/test");
  }
  const api = new URL(env.FINAGENT_API_URL ?? "http://127.0.0.1:8001");
  if (api.username || api.password || api.search || api.hash || api.pathname !== "/"
      || !["http:", "https:"].includes(api.protocol) || (!local && api.protocol !== "https:")) {
    throw new Error("The API requires HTTPS outside local/test");
  }
  const origin = originUrl.origin;
  return { origin, redisUrl: redis.href, secure: originUrl.protocol === "https:" };
}

// Private mobile entry: primary-provider callbacks remain on the desktop origin.
export function mobileEntryEnabled(env: NodeJS.ProcessEnv = process.env): boolean {
  const flag = env.FINAGENT_WEB_MOBILE_ENTRY;
  if (flag === undefined || flag === "false") return false;
  if (flag !== "true" || !["local", "test"].includes(env.FINAGENT_ENVIRONMENT ?? "")) {
    throw new Error("Invalid private mobile entry configuration");
  }
  const origin = new URL(env.FINAGENT_WEB_ORIGIN ?? "");
  if (origin.protocol !== "https:" || !origin.hostname.endsWith(".ts.net") || origin.port
      || origin.username || origin.password || origin.pathname !== "/" || origin.search || origin.hash
      || env.FINAGENT_WEB_PASSWORD_LOGIN_ENABLED !== "true" || authMode(env) === "local") {
    throw new Error("Private mobile entry requires HTTPS and existing password authentication");
  }
  return true;
}
