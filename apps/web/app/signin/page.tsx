import { mobileEntryEnabled } from "@/lib/auth-config";
import { passwordLoginAvailable } from "@/lib/password-auth";
﻿import { signIn } from "@/app/actions";
import { safeCaseReturn } from "@/lib/case-return";
import { SubmitButton } from "@/components/SubmitButton";
import { Flash } from "@/components/ui";
import { BASE } from "@/lib/api";
import { authMode, externalSettings } from "@/lib/auth-config";

interface Identity {
  subject: string;
  display_name: string;
  roles: string[];
}

async function identities(): Promise<Identity[] | null> {
  try {
    const res = await fetch(`${BASE}/api/v1/dev/identities`, {
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(10000),
    });

    if (!res.ok) return null;

    return ((await res.json()) as { items: Identity[] }).items;
  } catch {
    return null;
  }
}

function ShieldIcon({
  className = "",
}: {
  className?: string;
}) {
  return (
    <svg
      className={`fin-shield ${className}`}
      viewBox="0 0 64 64"
      aria-hidden="true"
    >
      <path
        d="M32 5 53 13v16c0 14-8 24-21 30C19 53 11 43 11 29V13L32 5Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="4"
      />
      <path
        d="m22 32 7 7 14-17"
        fill="none"
        stroke="currentColor"
        strokeWidth="5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function GoogleIcon() {
  return (
    <svg
      className="fin-google"
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <path
        fill="#4285F4"
        d="M21.6 12.2c0-.7-.1-1.5-.2-2.2H12v4.3h5.4a4.6 4.6 0 0 1-2 3v2.8h3.3c1.9-1.8 2.9-4.5 2.9-7.9Z"
      />
      <path
        fill="#34A853"
        d="M12 22c2.7 0 5-.9 6.7-2.4l-3.3-2.8c-.9.6-2.1 1-3.4 1-2.6 0-4.8-1.8-5.6-4.1H3v2.9A10 10 0 0 0 12 22Z"
      />
      <path
        fill="#FBBC05"
        d="M6.4 13.7A6 6 0 0 1 6.1 12c0-.6.1-1.2.3-1.7V7.4H3A10 10 0 0 0 2 12c0 1.7.4 3.3 1 4.6l3.4-2.9Z"
      />
      <path
        fill="#EA4335"
        d="M12 6.2c1.5 0 2.8.5 3.9 1.5l2.9-2.9A9.8 9.8 0 0 0 3 7.4l3.4 2.9c.8-2.3 3-4.1 5.6-4.1Z"
      />
    </svg>
  );
}

function SearchIcon() {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true">
      <circle
        cx="14"
        cy="14"
        r="8"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.5"
      />
      <path
        d="m20 20 7 7"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

function DocumentIcon() {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true">
      <path
        d="M8 4h11l5 5v19H8Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
      />
      <path
        d="M19 4v6h6M12 15h8M12 20h8M12 24h6"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
      />
    </svg>
  );
}

function ChartIcon() {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true">
      <path
        d="M6 26V17M13 26V11M20 26V15M27 26V6"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
      />
    </svg>
  );
}

function UsersIcon() {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true">
      <circle
        cx="12"
        cy="11"
        r="4"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
      />
      <circle
        cx="22"
        cy="12"
        r="3"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
      />
      <path
        d="M4 27c0-5 3-8 8-8s8 3 8 8M20 20c5 0 8 2 8 7"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
      />
    </svg>
  );
}

function LockIcon() {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true">
      <rect
        x="7"
        y="14"
        width="18"
        height="14"
        rx="3"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
      />
      <path
        d="M11 14V9a5 5 0 0 1 10 0v5"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
      />
    </svg>
  );
}

function BankIcon() {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true">
      <path
        d="M4 12 16 5l12 7M6 14h20M8 15v9M14 15v9M20 15v9M26 15v9M4 26h24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
      />
    </svg>
  );
}

function GlobeIcon() {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true">
      <circle
        cx="16"
        cy="16"
        r="12"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
      />
      <path
        d="M4 16h24M16 4c4 4 6 8 6 12s-2 8-6 12M16 4c-4 4-6 8-6 12s2 8 6 12"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
      />
    </svg>
  );
}

function Feature({
  icon,
  title,
  text,
}: {
  icon: React.ReactNode;
  title: string;
  text: string;
}) {
  return (
    <div className="fin-feature">
      <div className="fin-feature-icon">{icon}</div>
      <div>
        <strong>{title}</strong>
        <p>{text}</p>
      </div>
    </div>
  );
}

function TrustBadge({
  icon,
  title,
  text,
}: {
  icon: React.ReactNode;
  title: string;
  text: string;
}) {
  return (
    <div className="fin-trust-badge">
      <span>{icon}</span>
      <div>
        <strong>{title}</strong>
        <small>{text}</small>
      </div>
    </div>
  );
}

function DashboardArtwork() {
  return (
    <div className="fin-dashboard-art" aria-hidden="true">
      <div className="fin-laptop">
        <div className="fin-laptop-screen">
          <div className="fin-screen-top">
            <span />
            <span />
            <span />
          </div>

          <div className="fin-screen-layout">
            <div className="fin-screen-nav">
              <i />
              <i />
              <i />
              <i />
              <i />
            </div>

            <div className="fin-screen-main">
              <div className="fin-map-card">
                <div className="fin-map-grid" />

                <span className="fin-node n1" />
                <span className="fin-node n2" />
                <span className="fin-node n3" />
                <span className="fin-node n4" />
                <span className="fin-node n5" />

                <span className="fin-map-line l1" />
                <span className="fin-map-line l2" />
                <span className="fin-map-line l3" />
              </div>

              <div className="fin-small-panels">
                <div>
                  <i style={{ width: "78%" }} />
                  <i style={{ width: "58%" }} />
                  <i style={{ width: "88%" }} />
                  <i style={{ width: "65%" }} />
                </div>

                <div className="fin-donut">
                  <span />
                </div>
              </div>

              <div className="fin-bars">
                <i style={{ height: "35%" }} />
                <i style={{ height: "58%" }} />
                <i style={{ height: "43%" }} />
                <i style={{ height: "82%" }} />
                <i style={{ height: "68%" }} />
                <i style={{ height: "91%" }} />
                <i style={{ height: "61%" }} />
              </div>
            </div>
          </div>
        </div>

        <div className="fin-laptop-base" />
      </div>

      <div className="fin-kpi-stack">
        <div className="fin-kpi-card">
          <span className="fin-kpi-icon">↗</span>
          <div>
            <strong>Sample</strong>
            <small>Transaction review</small>
          </div>
        </div>

        <div className="fin-kpi-card">
          <span className="fin-kpi-icon warning">△</span>
          <div>
            <strong>Sample</strong>
            <small>Risk indicators</small>
          </div>
        </div>

        <div className="fin-kpi-card">
          <span className="fin-kpi-ring" />
          <div>
            <strong>Sample</strong>
            <small>Investigation overview</small>
          </div>
        </div>
      </div>
    </div>
  );
}

export default async function SignIn({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const sp = await searchParams;
  const caseReturn = safeCaseReturn(sp.returnTo);
  let passwordEnabled = false;

  let mode: "local" | "entra" | "google" | null = null;

  try {
    mode = authMode();

    if (mode !== "local") {
      externalSettings();
    }

    const res = await fetch(`${BASE}/api/v1/auth/config`, {
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(10000),
    });

    if (!res.ok) {
      mode = null;
    } else {
      const config = await res.json() as { mode?: string };
      if (config.mode !== mode) mode = null;
      passwordEnabled = mode !== null && await passwordLoginAvailable();
    }
  } catch {
    mode = null;
  }

  const ids =
    mode === "local"
      ? await identities()
      : null;

  return (
    <section
      className="fin-login"
      aria-labelledby="signin-heading"
    >
      <div className="fin-business-side">
        <div className="fin-business-overlay" />

        <div className="fin-top-row">
          <div className="fin-main-brand">
            <ShieldIcon />

            <div>
              <div className="fin-brand-name">
                <strong>FinAgent</strong>
                <span>Enterprise</span>
              </div>

              <p>
                AI for Safer Financial Decisions
              </p>
            </div>
          </div>

          <div className="fin-top-badges">
            <TrustBadge
              icon={<LockIcon />}
              title="ACCESS"
              text="Role-based controls"
            />

            <TrustBadge
              icon={<ShieldIcon />}
              title="REVIEW"
              text="AML / KYC workflows"
            />

            <TrustBadge
              icon={<ChartIcon />}
              title="EVIDENCE"
              text="Recorded provenance"
            />
          </div>
        </div>

        <div className="fin-business-copy">
          <h2>
            Intelligent AML Investigation
            <br />
            for a{" "}
            <span>
              Safer Financial Ecosystem
            </span>
          </h2>

          <p>
            Human-reviewed investigation support.
            <br />
            Designed for financial institutions and enterprise teams.
          </p>
        </div>

        <div className="fin-feature-row">
          <Feature
            icon={<SearchIcon />}
            title="Investigate"
            text="Analyse transactions and behaviour patterns"
          />

          <Feature
            icon={<DocumentIcon />}
            title="Explain"
            text="Transparent, evidence-based reasoning"
          />

          <Feature
            icon={<ShieldIcon />}
            title="Comply"
            text="Aligned with AML/KYC best practices"
          />

          <Feature
            icon={<ChartIcon />}
            title="Protect"
            text="Help prevent financial crime and strengthen trust"
          />
        </div>

        <DashboardArtwork />

        <div className="fin-bottom-trust">
          <div>
            <BankIcon />
            <p>
              <strong>Designed for</strong>
              Financial institutions
            </p>
          </div>

          <div>
            <GlobeIcon />
            <p>
              <strong>Investigation</strong>
              AML / KYC workflows
            </p>
          </div>

          <div>
            <ShieldIcon />
            <p>
              <strong>Access controls</strong>
              Authorised users
            </p>
          </div>

          <div>
            <UsersIcon />
            <p>
              <strong>Built for Analysts,</strong>
              Reviewers and Compliance Teams
            </p>
          </div>
        </div>
      </div>

      <div className="fin-auth-side">
        <div className="fin-auth-card">
          <div className="fin-welcome">
            <span>Welcome to</span>

            <h1 id="signin-heading">
              FinAgent{" "}
              <em>Enterprise</em>
            </h1>
          </div>

          <p className="fin-auth-subtitle">
            Sign in with your professional account
          </p>

          {sp.expired ? (
            <p
              role="status"
              className="warn fin-login-message"
            >
              Your session has expired or is missing.
              Please sign in again. {caseReturn ? "After sign-in, we will reopen the requested case page if you have access." : "After sign-in, you will return to the overview; reopen your case to continue."}
            </p>
          ) : null}

          <Flash error={sp.error} />

          {passwordEnabled ? (
            <form method="post" action="/auth/password" className="stack panel" aria-label="Email and password sign-in">
              {caseReturn && <input type="hidden" name="returnTo" value={caseReturn} />}
              <label htmlFor="email">Email address</label>
              <input id="email" name="email" type="email" autoComplete="username" required maxLength={254} />
              <label htmlFor="password">Password</label>
              <input id="password" name="password" type="password" autoComplete="current-password" required maxLength={128} />
              <button type="submit" className="fin-google-button">Sign in with email</button>
              <p className="muted">Use the FinAgent password provided for your account. Contact your administrator for access or password reset.</p>
            </form>
          ) : null}

          {mobileEntryEnabled() ? (<p className="muted">Private mobile access. Use your existing FinAgent email and password.</p>) : mode === "google" ? (
            <>
              <form
                method="post"
                action={caseReturn ? `/auth/google/start?returnTo=${encodeURIComponent(caseReturn)}` : "/auth/google/start"}
                className="fin-provider-form"
              >
                <button
                  type="submit"
                  className="fin-google-button"
                >
                  <GoogleIcon />
                  <span>Sign in with Google</span>
                </button>
              </form>

              {passwordEnabled && <div className="fin-or">
                <span>or</span>
              </div>}

              <div className="fin-authorisation">
                <ShieldIcon />

                <p>
                  Access requires an existing authorisation
                  <br />
                  and FinAgent Enterprise role.
                </p>
              </div>

              <div className="fin-security-list">
                <div>
                  <span className="fin-security-icon">
                    <UsersIcon />
                  </span>

                  <p>
                    <strong>Role-based access</strong>
                    <small>
                      Secure access for authorised professionals
                    </small>
                  </p>
                </div>

                <div>
                  <span className="fin-security-icon">
                    <LockIcon />
                  </span>

                  <p>
                    <strong>Enterprise security</strong>
                    <small>
                      Protected by your organisation&apos;s identity provider
                    </small>
                  </p>
                </div>

                <div>
                  <span className="fin-security-icon">
                    <DocumentIcon />
                  </span>

                  <p>
                    <strong>Recorded activity</strong>
                    <small>
                      Review available audit history
                    </small>
                  </p>
                </div>
              </div>
            </>
          ) : mode === "entra" ? (
            <form
              method="post"
              action={caseReturn ? `/auth/entra/start?returnTo=${encodeURIComponent(caseReturn)}` : "/auth/entra/start"}
              className="fin-provider-form"
            >
              <button
                type="submit"
                className="fin-google-button"
              >
                Sign in with Microsoft
              </button>
            </form>
          ) : ids === null ? (
            <div className="panel">
              <p>
                Sign-in is unavailable. Ask your administrator
                to check the API and authentication configuration.
              </p>
            </div>
          ) : ids.length === 0 ? (
            <div className="panel" role="status"><p>No synthetic identities are available. Ask your local administrator to check the test setup.</p></div>
          ) : (
            <form
              action={signIn}
              className="stack panel"
            >
              {caseReturn && <input type="hidden" name="returnTo" value={caseReturn} />}
              <p className="warn">
                LOCAL DEVELOPMENT sign-in with SYNTHETIC identities.
                Not available in deployed environments.
              </p>

              <label htmlFor="subject">
                Synthetic identity
              </label>

              <select
                id="subject"
                name="subject"
                required
              >
                {ids.map((i) => (
                  <option
                    key={i.subject}
                    value={i.subject}
                  >
                    {i.subject} - {i.roles.join(", ")}
                  </option>
                ))}
              </select>

              <label htmlFor="mfa">
                Multi-factor authentication satisfied
              </label>

              <select
                id="mfa"
                name="mfa"
                defaultValue="yes"
              >
                <option value="yes">
                  Yes (required for privileged actions)
                </option>

                <option value="no">
                  No (test step-up enforcement)
                </option>
              </select>

              <SubmitButton>
                Sign in
              </SubmitButton>
            </form>
          )}
        </div>

        <p className="fin-auth-disclaimer">
          SYNTHETIC data only. AI output remains a draft for
          analyst and reviewer judgement.
        </p>
      </div>
    </section>
  );
}
