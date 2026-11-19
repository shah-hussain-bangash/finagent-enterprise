import "./globals.css";

import Link from "next/link";
import type { ReactNode } from "react";

import { signOut } from "@/app/actions";
import { MainNavigation } from "@/components/MainNavigation";
import { getSession } from "@/lib/session";
import {
  sessionIdentity,
  type SessionIdentity,
} from "@/lib/session-identity";

export const metadata = {
  title: "FinAgent Enterprise",
  description:
    "Human-reviewed AML investigation copilot",
};

interface NavItem {
  href: string;
  label: string;
  anyPermission?: readonly string[];
}

const NAV: readonly NavItem[] = [
  {
    href: "/",
    label: "Overview",
    anyPermission: [
      "CASE_READ",
      "PLATFORM_OPERATE",
    ],
  },
  {
    href: "/cases",
    label: "Case queue",
    anyPermission: ["CASE_READ"],
  },
  {
    href: "/approvals",
    label: "Approvals",
    anyPermission: ["APPROVAL_DECIDE"],
  },
  {
    href: "/escalations",
    label: "Escalations",
    anyPermission: ["CASE_READ"],
  },
  {
    href: "/policies",
    label: "Policy search",
    anyPermission: [
      "KNOWLEDGE_SEARCH",
      "POLICY_PUBLISH",
      "TENANT_ADMIN",
    ],
  },
  {
    href: "/operations",
    label: "Operations",
    anyPermission: [
      "PLATFORM_OPERATE",
      "CASE_READ",
    ],
  },
  { href: "/workspace", label: "Workspace preview", anyPermission: ["CASE_READ", "PLATFORM_OPERATE", "APPROVAL_DECIDE"] },
  {
    href: "/audit",
    label: "Audit",
    anyPermission: ["AUDIT_READ"],
  },
  {
    href: "/evaluations",
    label: "Evaluations",
    anyPermission: ["EVALUATION_RUN"],
  },
  {
    href: "/admin",
    label: "Administration",
    anyPermission: [
      "TENANT_ADMIN",
      "PLATFORM_OPERATE",
    ],
  },
];

function canSee(
  item: NavItem,
  identity: SessionIdentity | null,
): boolean {
  if (!identity) return false;

  if (
    !item.anyPermission ||
    item.anyPermission.length === 0
  ) {
    return true;
  }

  const granted = new Set(
    identity.permissions,
  );

  return item.anyPermission.some(
    (permission) =>
      granted.has(permission),
  );
}

export default async function RootLayout({
  children,
}: {
  children: ReactNode;
}) {
  const session = await getSession();

  const identity = session
    ? await sessionIdentity(session)
    : null;

  const visibleNav = session
    ? NAV.filter((item) =>
        canSee(item, identity),
      )
    : [];

  const displayName =
    identity?.display_name.trim() ||
    session?.subject ||
    "";

  return (
    <html lang="en">
      <body>
        <a
          href="#main"
          className="skip-link"
        >
          Skip to main content
        </a>

        <header className="top">
          <span className="brand">
            FinAgent Enterprise
          </span>

          {session ? (
            <MainNavigation items={visibleNav.map(({ href, label }) => ({ href, label }))} />
          ) : null}

          {session ? (
            <span className="who">
              {session.token.startsWith("fa_pwd_") ? <Link href="/security">Authenticator</Link> : null}{" "}
              Signed in as{" "}
              <strong>
                {displayName}
              </strong>{" "}
              <form action={signOut}>
                <button type="submit">
                  Sign out
                </button>
              </form>
            </span>
          ) : null}
        </header>

        <p className="synthetic">
          Human-reviewed AML investigation
          copilot. SYNTHETIC data only. AI
          output is a draft for analyst and
          reviewer judgement; the platform
          never moves money, freezes accounts,
          files reports or decides guilt. Times
          are shown in UTC.
        </p>

        <main id="main" tabIndex={-1}>
          {children}
        </main>
      </body>
    </html>
  );
}

