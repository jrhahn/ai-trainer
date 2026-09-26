"""Generate an ADMIN_TOTP_SECRET and the QR to enrol it (#688).

    uv run python -m scripts.generate_admin_totp

The admin panel has no user row, so its secret is an environment variable and
there is no enrollment endpoint — one less unauthenticated surface in front of
the account that can read every athlete's email address and delete any of them.
That trade means enrolling is this manual step.

Prints the secret once. Put it in the deploy secrets (all four places — see
tests/test_deploy_wiring.py for why that is not a joke) and do not keep the
output.
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
    print("Then set it as the ADMIN_TOTP_SECRET secret in the production")
    print("environment, and forward it in .github/workflows/deploy.yml — both")
    print("the env: block and extra_vars, or it renders empty (#694).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
