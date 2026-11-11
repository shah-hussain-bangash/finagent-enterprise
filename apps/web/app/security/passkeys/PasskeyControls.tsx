"use client";
import { useState } from "react";

function decode(value: string): ArrayBuffer {
  const text = atob(value.replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(text, (char) => char.charCodeAt(0)).buffer;
}
function encode(value: ArrayBuffer): string {
  return btoa(String.fromCharCode(...new Uint8Array(value))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
function credentialJSON(value: PublicKeyCredential): object {
  const response = value.response;
  const common = { id: value.id, rawId: encode(value.rawId), type: value.type, clientExtensionResults: {} };
  if (response instanceof AuthenticatorAttestationResponse) {
    return { ...common, response: { clientDataJSON: encode(response.clientDataJSON), attestationObject: encode(response.attestationObject) } };
  }
  if (response instanceof AuthenticatorAssertionResponse) {
    return { ...common, response: { clientDataJSON: encode(response.clientDataJSON),
      authenticatorData: encode(response.authenticatorData), signature: encode(response.signature),
      userHandle: response.userHandle ? encode(response.userHandle) : null } };
  }
  throw new Error("Unsupported authenticator response");
}

interface Descriptor { id: string; type: "public-key" }
interface Creation extends Omit<PublicKeyCredentialCreationOptions, "challenge" | "user" | "excludeCredentials"> {
  challenge: string; user: Omit<PublicKeyCredentialUserEntity, "id"> & { id: string }; excludeCredentials?: Descriptor[];
}
interface RequestOptions extends Omit<PublicKeyCredentialRequestOptions, "challenge" | "allowCredentials"> {
  challenge: string; allowCredentials?: Descriptor[];
}

export default function PasskeyControls({ enrolled, emailVerified, enrollmentAllowed }: {
  enrolled: boolean; emailVerified: boolean; enrollmentAllowed: boolean;
}) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  async function call(operation: string, payload: object = {}) {
    const response = await fetch("/security/passkeys/api", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ operation, payload }), credentials: "same-origin", cache: "no-store",
    });
    if (response.status === 401) { window.location.assign("/signin?expired=1"); throw new Error("Sign in again"); }
    if (!response.ok) throw new Error("Request not accepted. Check recent email verification, password, session and account enrollment. Try again after one minute if rate limited.");
    return await response.json() as { challenge_id: string; publicKey: Creation | RequestOptions };
  }
  async function perform(register: boolean) {
    setBusy(true); setMessage("");
    try {
      if (!window.isSecureContext || !window.PublicKeyCredential) throw new Error("Open the private HTTPS website in a browser that supports passkeys.");
      const supplied = password;
      setPassword("");
      const start = await call(register ? "register-options" : "verify-options", register ? { password: supplied } : {});
      let credential: Credential | null;
      if (register) {
        const options = start.publicKey as Creation;
        credential = await navigator.credentials.create({ publicKey: { ...options, challenge: decode(options.challenge),
          user: { ...options.user, id: decode(options.user.id) },
          excludeCredentials: options.excludeCredentials?.map((item) => ({ ...item, id: decode(item.id) })) } });
      } else {
        const options = start.publicKey as RequestOptions;
        credential = await navigator.credentials.get({ publicKey: { ...options, challenge: decode(options.challenge),
          allowCredentials: options.allowCredentials?.map((item) => ({ ...item, id: decode(item.id) })) } });
      }
      if (!(credential instanceof PublicKeyCredential)) throw new Error("No passkey response received. Start again.");
      await call(register ? "register-finish" : "verify-finish", { challenge_id: start.challenge_id, credential: credentialJSON(credential) });
      window.location.assign("/security/passkeys?ok=" + (register ? "registered" : "verified"));
    } catch (error) {
      setMessage(error instanceof DOMException ? "Phone verification was cancelled or unavailable. Start a fresh request and select the correct saved account." : error instanceof Error ? error.message : "Request failed. Start again.");
    } finally { setBusy(false); }
  }
  async function revoke() {
    if (!window.confirm("Revoke this FinAgent passkey? All its verification grants will stop working. Registering a replacement requires your credential administrator.")) return;
    setBusy(true);
    try { await call("revoke"); window.location.assign("/security/passkeys?ok=revoked"); }
    catch { setMessage("Verify this passkey first, then retry revocation."); }
    finally { setBusy(false); }
  }
  return <div className="panel stack">
    {message ? <p role="alert">{message}</p> : null}
    {enrolled ? <>
      <button type="button" disabled={busy} onClick={() => void perform(false)}>Verify with passkey</button>
      <button type="button" disabled={busy} onClick={() => void revoke()}>Revoke this passkey</button>
    </> : enrollmentAllowed ? <>
      <p>First verify your email, then confirm your current FinAgent password to register. Registration does not grant MFA.</p>
      <label htmlFor="passkey-password">Current FinAgent password</label>
      <input id="passkey-password" type="password" value={password} maxLength={128} autoComplete="current-password" onChange={(event) => setPassword(event.target.value)} />
      <button type="button" disabled={busy || !emailVerified || !password} onClick={() => void perform(true)}>Create FinAgent passkey</button>
    </> : <p>Enrollment is unavailable. Ask your credential administrator about authorization or recovery.</p>}
  </div>;
}
