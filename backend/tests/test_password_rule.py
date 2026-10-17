"""The password rule, on the cases the client is tested on too (ai-trainer-ops#114)."""

import json
from pathlib import Path

import pytest

import schemas

CASES = json.loads(
    (Path(__file__).resolve().parents[2] / "frontend" / "src" / "utils" / "passwordCases.json").read_text(
        encoding="utf-8"
    )
)
MESSAGES = {
    "length": "Password must be at least 10 characters.",
    "variety": "Password must not repeat a few characters.",
    "common": "Password must avoid common or app-related words.",
}


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c['password']}|{c['name']}|{c['email']}")
def test_the_server_agrees_with_the_client_on_every_shared_case(case):
    if case["problem"] == "personal":
        expected = f"Password must not include “{case['part']}” from your name or email."
    else:
        expected = MESSAGES.get(case["problem"])

    assert schemas.password_problem(case["password"], case["email"], case["name"]) == expected


def test_the_mail_domain_is_no_personal_part():
    """web.de, GMX and T-Online put "web" and "online" in half the German addresses."""
    assert "web" not in schemas._password_personal_fragments("anna@web.de", "")
    assert "online" not in schemas._password_personal_fragments("jens@t-online.de", "")
