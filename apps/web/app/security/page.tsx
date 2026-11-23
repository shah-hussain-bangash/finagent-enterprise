import { mobileEntryEnabled } from "@/lib/auth-config";
import Link from "next/link";
import { load } from "@/lib/api";
import { verifyAuthenticator } from "@/app/actions";

export const dynamic = "force-dynamic";

export default async function SecurityPage({ searchParams }: {
  searchParams: Promise<{ ok?: string; error?: string }>
}) {
  const query = await searchParams;
  const uncertainAction = typeof query.error === "string" && query.error.startsWith("CONNECTION_ERROR:");
  const result = await load<{ enrolled: boolean; mfa_satisfied: boolean }>("/api/v1/auth/totp/status");
  return <>
    <h1>Security verification</h1>
    {mobileEntryEnabled() ? <p><Link href="/security/passkeys">Register or verify a phone passkey</Link></p> : null}
    <p><Link href="/security/email">Verify your email address</Link></p>
    <p>Local synthetic environment. Verify a code to authorize protected actions for up to five minutes within this sign-in session.</p>
    {query.ok ? <p role="status">Authenticator verified. Return to your pending review while verification is current.</p> : null}
    {uncertainAction ? <p role="alert">The verification outcome is unknown. Check the current status before trying again. <a href="/security">Reload security verification</a></p>
      : query.error ? <p role="alert">Verification failed. Check your current code and account enrollment. Repeated failures may temporarily lock verification.</p> : null}
    {result.error ? <p role="alert">Authenticator verification is unavailable for this sign-in method or environment.</p> :
      !result.data.enrolled ? <p>Ask your local credential administrator to enroll your authenticator through the private setup console. A password alone does not satisfy MFA.</p> : <>
        <p>Current verification: <strong>{result.data.mfa_satisfied ? "Verified" : "Required"}</strong></p>
        {!uncertainAction && <form action={verifyAuthenticator} className="panel">
          <label htmlFor="authenticator-code">Six-digit authenticator code</label>
          <input id="authenticator-code" name="code" type="password" inputMode="numeric"
            autoComplete="one-time-code" pattern="[0-9]{6}" minLength={6} maxLength={6} required />
          <button type="submit">Verify authenticator</button>
        </form>}
      </>}
    <p><Link href="/approvals">Return to approvals</Link></p>
  </>;
}
