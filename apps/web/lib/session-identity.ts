import "server-only";

import type { Session } from "./session";

const BASE =
  process.env.FINAGENT_API_URL ??
  "http://127.0.0.1:8001";

export interface SessionIdentity {
  issuer: string;
  subject: string;
  display_name: string;
  roles: string[];
  permissions: string[];
  mfa_satisfied: boolean;
}

function strings(value: unknown): string[] {
  if (!Array.isArray(value)) return [];

  return value.filter(
    (item): item is string =>
      typeof item === "string",
  );
}

export async function sessionIdentity(
  session: Session,
): Promise<SessionIdentity | null> {
  try {
    const response = await fetch(
      `${BASE}/api/v1/auth/session`,
      {
        headers: {
          Authorization: `Bearer ${session.token}`,
        },
        cache: "no-store",
        redirect: "error",
        signal: AbortSignal.timeout(10000),
      },
    );

    if (!response.ok) return null;

    const raw = (await response.json()) as
      Record<string, unknown>;

    if (
      typeof raw.issuer !== "string" ||
      typeof raw.subject !== "string" ||
      typeof raw.display_name !== "string" ||
      typeof raw.mfa_satisfied !== "boolean"
    ) {
      return null;
    }

    return {
      issuer: raw.issuer,
      subject: raw.subject,
      display_name: raw.display_name,
      roles: strings(raw.roles),
      permissions: strings(raw.permissions),
      mfa_satisfied: raw.mfa_satisfied,
    };
  } catch {
    // Navigation hints fail closed.
    // Backend RBAC remains authoritative.
    return null;
  }
}
