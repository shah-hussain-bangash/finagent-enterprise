"""Issue a LOCAL-ONLY development bearer token for a seeded synthetic principal (ADR-0008).

    python scripts/dev_token.py a.analyst1@synthetic

Refuses to run unless Settings validates with auth_mode=local (i.e. environment local/test).
The token carries only the subject; tenant and roles come from the database.
"""

from __future__ import annotations

import argparse

from finagent.core.config import AuthMode, get_settings
from finagent.security.authentication.tokens import LocalDevTokenValidator


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("subject", help="e.g. a.analyst1@synthetic, b.tm-connector@synthetic")
    parser.add_argument("--ttl", type=int, default=3600)
    parser.add_argument("--no-mfa", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    if settings.auth_mode is not AuthMode.LOCAL or settings.dev_jwt_secret is None:
        raise SystemExit("dev tokens are only available in local auth mode")
    issuer = LocalDevTokenValidator(settings.dev_jwt_secret.get_secret_value(), settings.api_audience)
    print(issuer.issue(args.subject, args.ttl, mfa=not args.no_mfa))


if __name__ == "__main__":
    main()
