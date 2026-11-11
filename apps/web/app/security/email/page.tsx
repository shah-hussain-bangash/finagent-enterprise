import Link from "next/link";
import { load } from "@/lib/api";
import { sendEmailVerification, confirmEmailVerification } from "@/app/actions";

export const dynamic = "force-dynamic";

export default async function EmailVerificationPage({searchParams}:{searchParams:Promise<{ok?:string;error?:string}>}) {
  const query=await searchParams;
  const result=await load<{email_verified:boolean;challenge_id:string|null;grants_mfa:false}>("/api/v1/auth/email/status");
  return <>
    <h1>Verify your email address</h1>
    <p>Codes are sent to the email registered for your current account. Verification confirms email access; protected reviewer decisions still require MFA.</p>
    {query.ok ? <p role="status">Request completed. Check the status below and your registered email inbox.</p>:null}
    {query.error ? <p role="alert">The request could not be completed. Check your latest code and expiry, or try sending again after one minute.</p>:null}
    {result.error ? <p role="alert">Email verification is unavailable for this account or environment.</p>:<>
      <p>Email verification: <strong>{result.data.email_verified?"Verified for this session":"Not verified"}</strong></p>
      <form action={sendEmailVerification} className="panel"><button type="submit">Send verification email</button></form>
      {result.data.challenge_id ? <form action={confirmEmailVerification} className="panel">
        <input type="hidden" name="challenge_id" value={result.data.challenge_id}/>
        <label htmlFor="email-code">Six-digit code from your latest FinAgent email</label>
        <input id="email-code" name="code" type="password" inputMode="numeric" autoComplete="one-time-code"
          pattern="[0-9]{6}" minLength={6} maxLength={6} required/>
        <button type="submit">Verify email</button>
      </form>:null}
    </>}
    <p><Link href="/security">Return to security</Link></p>
  </>;
}
