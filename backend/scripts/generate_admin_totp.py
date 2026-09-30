"""Generate an ADMIN_TOTP_SECRET and the QR to enrol it (#688).

    uv run python -m scripts.generate_admin_totp

The admin panel has no user row, so its secret is an environment variable and
there is no enrollment endpoint — one less unauthenticated surface in front of
the account that can read every athlete's email address and delete any of them.
That trade means enrolling is this manual step.

Prints the secret once, and does not store it. Set it as `ADMIN_TOTP_SECRET` on
the `production` environment of the deploying repository — `admin_totp_secret`
is already in `deploy/forwarded-vars.yml`, so nothing else needs wiring (#700).
Do not keep the output.

Enrol in the authenticator *before* the next deploy. A non-empty secret makes
the panel demand a code, and an unscanned one is a code nobody can produce; the
way back is to clear the variable and deploy again.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pyotp  # noqa: E402

from config import settings  # noqa: E402


def main() -> int:
    secret = pyotp.random_base32()
    uri = pyotp.TOTP(secret).provisioning_uri(
        name="admin", issuer_name=f"{settings.totp_issuer} (admin)"
    )

    print("ADMIN_TOTP_SECRET:")
    print(f"  {secret}")
    print()
    print("Add to the authenticator app with this URI:")
    print(f"  {uri}")
    print()
    print("Then set it as the ADMIN_TOTP_SECRET secret on the production")
    print("environment of the deploying repository. It is already listed in")
    print("deploy/forwarded-vars.yml, so nothing else needs wiring (#700).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
