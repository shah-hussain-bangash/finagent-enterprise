# ADR 0034: Planned private mobile HTTPS route for direct phone passkeys

Date: 2026-11-07. Proposed; not implemented, installed, exposed or accepted.

The user has an Android HONOR X9d, no PC Bluetooth, no Windows platform
authenticator and no domain. The platform-availability HTML check only tested
Windows Hello, not the phone. Cross-device QR authentication requiring Bluetooth
on the PC is unsuitable. The proposed route is to open FinAgent directly in the
phone browser on a trusted private HTTPS hostname, then use Android WebAuthn.
Android determines the permitted fingerprint/face/screen-lock method. FinAgent
must verify the signed assertion with user verification required; it must never
receive face photographs or a manually asserted biometric-success flag.

Tailscale Serve is a candidate private-access option: an owned tailnet can supply
an HTTPS ts.net name and proxy a loopback web service without buying a domain.
This requires the user to install/configure Tailscale on their PC and phone,
review device membership/access policy, enable HTTPS issuance and approve the
specific serving configuration. No Funnel or public exposure is authorized or
configured. Hostnames used for certificate issuance may be visible in public
certificate-transparency logs; select an appropriate machine name first.

A separate mobile BFF is proposed so the current desktop origin and Google OAuth
callback can stay fixed. Its exact HTTPS origin must be configured and validated,
with secure host cookies, same-origin mutations and email sign-in. Primary-provider
start/callback routes on that mobile entry must be unavailable unless the matching
Google OAuth redirect is independently configured. The backend still resolves
identities and roles from the same database and checks assignment and separation
of duties. The WebAuthn RP ID must be the configured domain, never an IP literal
or a value taken from an untrusted Host/forwarded header.

Passkey implementation, library/dependency review, enrollment policy, challenge
binding, verification, revocation, browser/device tests and runtime activation
remain outstanding. Email possession proof alone does not authorize a decision.
The current TOTP enforcement remains intact while this alternative is built.

References:
- https://tailscale.com/docs/features/tailscale-serve
- https://tailscale.com/docs/how-to/set-up-https-certificates
- https://www.w3.org/TR/webauthn-3/
