"""Bearer-token validation (FR-001, SPR-004, prompt §16; ADR-0008).

A validated token yields only (issuer, subject, mfa). Tenant and roles are resolved from our
database by `resolver.resolve_principal` - never from token custom claims or request fields.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

import jwt

from finagent.core.config import LOCAL_ISSUER, AuthMode, Settings
from finagent.core.errors import AppError, ErrorCode

_MFA_METHODS = frozenset({"mfa", "otp", "hwk", "fido", "rsa", "swk"})


@dataclass(frozen=True)
class VerifiedIdentity:
    issuer: str
    subject: str
    mfa: bool
    scopes: frozenset[str] = frozenset()


class TokenValidator(Protocol):
    def validate(self, token: str) -> VerifiedIdentity: ...


def _unauthenticated() -> AppError:
    return AppError(ErrorCode.UNAUTHENTICATED)


def _identity(claims: dict[str, Any], subject_claim: str) -> VerifiedIdentity:
    subject = claims.get(subject_claim)
    if not isinstance(subject, str) or not subject:
        raise _unauthenticated()
    amr = claims.get("amr") or []
    mfa = isinstance(amr, list) and bool(_MFA_METHODS.intersection(map(str, amr)))
    return VerifiedIdentity(issuer=str(claims["iss"]), subject=subject, mfa=mfa)


class LocalDevTokenValidator:
    """HS256 development issuer. Refuses to exist outside local/test (enforced by Settings)."""

    def __init__(self, secret: str, audience: str) -> None:
        self._secret, self._audience = secret, audience

    def validate(self, token: str) -> VerifiedIdentity:
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                audience=self._audience,
                issuer=LOCAL_ISSUER,
                options={"require": ["exp", "nbf", "iat", "iss", "aud", "sub"]},
                leeway=30,
            )
        except jwt.PyJWTError as exc:
            raise _unauthenticated() from exc
        return _identity(claims, "sub")

    def issue(self, subject: str, ttl_seconds: int = 3600, mfa: bool = True) -> str:
        now = int(time.time())
        claims = {
            "iss": LOCAL_ISSUER,
            "aud": self._audience,
            "sub": subject,
            "iat": now,
            "nbf": now,
            "exp": now + ttl_seconds,
            "amr": ["pwd", "mfa"] if mfa else ["pwd"],
        }
        return jwt.encode(claims, self._secret, algorithm="HS256")


class EntraTokenValidator:
    """Microsoft Entra ID access tokens: signature via JWKS, issuer, audience, exp, nbf.

    Principal key is (iss, oid); `oid` is stable per user per tenant, unlike `sub` (pairwise).
    """

    def __init__(self, issuer: str, audience: str, jwks_url: str) -> None:
        self._issuer, self._audience = issuer, audience
        self._jwks = jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=3600, timeout=5)

    def validate(self, token: str) -> VerifiedIdentity:
        try:
            key = self._jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "nbf", "iss", "aud", "oid"]},
                leeway=60,
            )
        except jwt.PyJWTError as exc:
            raise _unauthenticated() from exc
        identity = _identity(claims, "oid")
        amr = claims.get("amr")
        scopes = claims.get("scp")
        # A single authentication method is not evidence of MFA. Missing claims fail closed.
        return VerifiedIdentity(
            issuer=identity.issuer,
            subject=identity.subject,
            mfa=isinstance(amr, list) and "mfa" in amr,
            scopes=frozenset(scopes.split()) if isinstance(scopes, str) else frozenset(),
        )


class GoogleTokenValidator:
    """Verify first-party Google ID credentials, never Google API access tokens.

    Restricted to the explicitly configured web client audience. Tenant and roles remain
    database-owned; email, hosted domain and token role/MFA claims grant nothing.
    """

    def __init__(self, client_id: str) -> None:
        self._audience = client_id
        self._jwks = jwt.PyJWKClient(
            "https://www.googleapis.com/oauth2/v3/certs",
            cache_keys=True,
            lifespan=3600,
            timeout=5,
        )

    def validate(self, token: str) -> VerifiedIdentity:
        if not token or len(token) > 24576:
            raise _unauthenticated()
        try:
            key = self._jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer="https://accounts.google.com",
                options={
                    "require": ["exp", "iat", "iss", "aud", "sub"],
                    "strict_aud": True,
                },
                leeway=30,
            )
        except jwt.PyJWTError as exc:
            raise _unauthenticated() from exc
        subject, issued, expires = (
            claims.get("sub"),
            claims.get("iat"),
            claims.get("exp"),
        )
        if (
            not isinstance(subject, str)
            or not 1 <= len(subject) <= 255
            or type(issued) is not int
            or type(expires) is not int
            or not 0 < expires - issued <= 3660
            or claims.get("azp", self._audience) != self._audience
        ):
            raise _unauthenticated()
        # No approved Google step-up contract exists. Never infer MFA from account settings.
        return VerifiedIdentity(issuer="https://accounts.google.com", subject=subject, mfa=False)


def build_validator(settings: Settings) -> TokenValidator:
    if settings.auth_mode is AuthMode.LOCAL:
        if settings.dev_jwt_secret is None:  # also enforced by Settings; never rely on assert
            raise RuntimeError("local auth mode without dev_jwt_secret")
        return LocalDevTokenValidator(settings.dev_jwt_secret.get_secret_value(), settings.api_audience)
    if settings.auth_mode is AuthMode.GOOGLE:
        if not settings.google_web_client_id:
            raise RuntimeError("google auth mode without web client configuration")
        return GoogleTokenValidator(settings.google_web_client_id)
    if not (settings.entra_issuer and settings.entra_jwks_url):
        raise RuntimeError("entra auth mode without issuer/jwks configuration")
    return EntraTokenValidator(settings.entra_issuer, settings.api_audience, settings.entra_jwks_url)
