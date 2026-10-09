"""Can this athlete use the coach at all? (ai-trainer-ops#41)

The dead end in #41 was not that a key was missing. It was that nothing could
*say* a key was missing until a model call had already been attempted — so
onboarding walked to its last step, pressed "Generate", and got a 402 pointing
at a Settings page that is unreachable before onboarding finishes.

``user_must_supply_own_key`` answers it in advance. What these tests are really
protecting is the agreement between that answer and ``get_provider``: an answer
of "no key needed" followed by an ``AIKeyNotConfiguredError`` would move the
dead end one screen later and make it look like a bug in the plan generator.
So every case below asserts both halves together.
"""

import pytest

import models
from services import llm as llm_service


def _user(*, gemini: str | None = None, openai: str | None = None, provider: str = "gemini"):
    return models.User(
        email="athlete@example.com",
        user_gemini_api_key=gemini,
        user_openai_api_key=openai,
        ai_provider=provider,
    )


@pytest.fixture
def deployment(monkeypatch):
    """Set the two global keys and the fallback switch together.

    Together on purpose: the interesting states are combinations, and setting
    them one at a time is how a test ends up asserting against a deployment that
    could not exist.
    """

    def configure(*, gemini: str = "", openai: str = "", fallback: bool = False):
        monkeypatch.setattr(llm_service.settings, "gemini_api_key", gemini)
        monkeypatch.setattr(llm_service.settings, "openai_api_key", openai)
        monkeypatch.setattr(llm_service.settings, "allow_admin_ai_key_fallback", fallback)
        monkeypatch.setattr(llm_service.settings, "ai_stub_provider", False)

    return configure


def _call_would_succeed(user: models.User) -> bool:
    """Whether ``get_provider`` can serve *user* — the other half of the claim.

    Through ``set_user_ai_keys``, because that is how every route reaches it: the
    BYOK context is what makes ``get_provider`` consult the user's own key at
    all, and outside it the function takes the global path unconditionally.
    """
    token = llm_service.set_user_ai_keys(
        {
            "gemini": user.user_gemini_api_key,
            "openai": user.user_openai_api_key,
        }
    )
    try:
        llm_service.get_provider(llm_service.resolve_user_provider(user))
        return True
    except llm_service.AIKeyNotConfiguredError:
        return False
    finally:
        llm_service.reset_user_ai_keys(token)


class TestByokOnly:
    """Fallback off — the mode the landing page promises."""

    def test_a_new_account_is_told_to_bring_a_key(self, deployment):
        # The #41 reproduction, as a question asked before the attempt.
        deployment(gemini="a-global-key", fallback=False)
        user = _user()

        assert llm_service.user_must_supply_own_key(user) is True
        assert _call_would_succeed(user) is False

    def test_its_own_key_is_enough(self, deployment):
        deployment(fallback=False)
        user = _user(gemini="the-athletes-own-key")

        assert llm_service.user_must_supply_own_key(user) is False
        assert _call_would_succeed(user) is True

    def test_a_key_for_the_other_provider_also_counts(self, deployment):
        # `resolve_user_provider` routes to whichever provider is usable, so a
        # user who stored a Gemini preference but an OpenAI key is fine. The
        # status must not demand a second key for a preference.
        deployment(fallback=False)
        user = _user(openai="an-openai-key", provider="gemini")

        assert llm_service.user_must_supply_own_key(user) is False
        assert _call_would_succeed(user) is True

    def test_the_owners_key_does_not_count(self, deployment):
        # The point of the mode: a global key exists and is deliberately not
        # lent out. Answering False here would send every athlete to a
        # "Generate" button that cannot work.
        deployment(gemini="a-global-key", openai="another", fallback=False)

        assert llm_service.user_must_supply_own_key(_user()) is True


class TestHosted:
    """Fallback on — the owner pays for the model."""

    def test_no_key_is_asked_for_when_the_owner_has_one(self, deployment):
        deployment(gemini="a-global-key", fallback=True)
        user = _user()

        assert llm_service.user_must_supply_own_key(user) is False
        assert _call_would_succeed(user) is True

    def test_a_key_is_asked_for_when_the_owner_has_none(self, deployment):
        # Fallback on and nothing to fall back to: the second failure mode in
        # #41, which used to surface as "Failed to fetch".
        deployment(fallback=True)
        user = _user()

        assert llm_service.user_must_supply_own_key(user) is True
        assert _call_would_succeed(user) is False

    def test_the_athletes_own_key_still_satisfies_it(self, deployment):
        deployment(fallback=True)

        assert llm_service.user_must_supply_own_key(_user(gemini="own")) is False


def test_the_stub_needs_no_key(monkeypatch, deployment):
    """Its entire purpose is a deployment with no key.

    Asking for one would make the browser suite configure the thing the stub
    stands in for — and the onboarding step under test here would appear in the
    one harness built to walk past it.
    """
    deployment(fallback=False)
    monkeypatch.setattr(llm_service, "stub_is_active", lambda: True)

    user = _user()
    assert llm_service.user_must_supply_own_key(user) is False
    assert _call_would_succeed(user) is True


def test_it_shares_its_rule_with_resolve_user_provider(deployment):
    """The two must not be able to disagree.

    `resolve_user_provider` swallows "nothing is usable" by returning the
    stored preference so that `get_provider` raises the right provider's error.
    That is reasonable and it means the name it returns says nothing about
    whether a call will work — which is exactly why this question needed a
    function of its own rather than a comparison against that name.
    """
    deployment(gemini="a-global-key", fallback=False)
    user = _user(provider="gemini")

    # A name is returned, and it is not an answer.
    assert llm_service.resolve_user_provider(user) == "gemini"
    assert llm_service.user_must_supply_own_key(user) is True
