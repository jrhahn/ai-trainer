"""Email addresses are case-insensitive end to end (ops#109).

EmailStr lowercases only the domain, so Foo@Example.de was stored and looked up
with its local part's case intact: sign-in with another spelling failed, and a
second spelling made a second account. The request schemas normalise the whole
address now, and the crud lookups are case-insensitive.
"""

import pytest

_REGISTER = "/api/v1/auth/register"
_LOGIN = "/api/v1/auth/login"
_PASSWORD = "Str0ng-passphrase!"


@pytest.mark.asyncio
async def test_sign_in_works_with_any_capitalisation(client):
    reg = await client.post(
        _REGISTER,
        json={"name": "Rider", "email": "Foo@Example.de", "password": _PASSWORD},
    )
    assert reg.status_code == 200, reg.text

    for spelling in (
        "foo@example.de",
        "FOO@EXAMPLE.DE",
        "Foo@Example.DE",
        "  foo@example.de  ",
    ):
        resp = await client.post(
            _LOGIN, json={"email": spelling, "password": _PASSWORD}
        )
        assert resp.status_code == 200, (spelling, resp.text)


@pytest.mark.asyncio
async def test_a_second_account_for_the_same_mailbox_is_refused(client):
    first = await client.post(
        _REGISTER,
        json={"name": "Rider", "email": "Case@Example.de", "password": _PASSWORD},
    )
    assert first.status_code == 200, first.text

    second = await client.post(
        _REGISTER,
        json={"name": "Rider", "email": "case@example.de", "password": _PASSWORD},
    )
    assert second.status_code == 409, second.text
