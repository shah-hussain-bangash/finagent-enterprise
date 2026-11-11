import Link from "next/link";
import { load } from "@/lib/api";
import { mobileEntryEnabled } from "@/lib/auth-config";
import PasskeyControls from "./PasskeyControls";

export const dynamic = "force-dynamic";

export default async function PasskeysPage({ searchParams }: { searchParams: Promise<{ ok?: string }> }) {
  const query = await searchParams;
  if (!mobileEntryEnabled()) return <><h1>Phone passkeys</h1><p>Open FinAgent directly on the configured private phone HTTPS website.</p></>;
  const status = await load<{ enrolled: boolean; enrollment_allowed: boolean; email_verified: boolean; mfa_satisfied: boolean }>("/api/v1/auth/passkeys/status");
  return <>
    <h1>Phone passkeys</h1>
    <p>Local synthetic environment. Android chooses the available fingerprint, face or screen-lock method. FinAgent receives a signed response, not biometric images.</p>
    {status.error ? <p role="alert">Passkeys are unavailable for this session or environment. Ask your credential administrator.</p> : <>
      <p>Passkey: <strong>{status.data.enrolled ? "Registered" : "Not active"}</strong>. Current MFA: <strong>{status.data.mfa_satisfied ? "Verified" : "Required"}</strong>.</p>
      {query.ok === "registered" && status.data.enrolled ? <p role="status">Passkey registered. Select Verify with passkey before protected actions.</p> : null}
      {query.ok === "verified" && status.data.mfa_satisfied ? <p role="status">Verified. Protected actions are available for up to five minutes within this phone session, subject to your permissions.</p> : null}
      <PasskeyControls enrolled={status.data.enrolled} enrollmentAllowed={status.data.enrollment_allowed} emailVerified={status.data.email_verified} />
    </>}
    <p><Link href="/security/email">Verify your email address</Link></p>
    <p><Link href="/approvals">Return to approvals</Link></p>
    <p>One local test passkey per approved account. Revocation is available after verification; lost-device recovery and replacement require the credential administrator. No review decision is made here.</p>
  </>;
}
