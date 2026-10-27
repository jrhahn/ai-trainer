"""The cache probe measures the prompt the app sends (ai-trainer-ops#257, #538).

``scripts/probe_implicit_cache.py`` asks the live API whether the coach prompt's
fixed prefix is served from Google's cache. It is only worth its few cents if
the prefix it sends is the app's own, so this holds the two together without a
network call.
"""

from __future__ import annotations

from scripts import probe_implicit_cache
from services import prompts


def test_the_probe_sends_the_apps_own_fixed_prefix():
    prefix, how = probe_implicit_cache.coach_prefix()

    assert prefix == prompts.coach_static_prefix()
    assert how == "prompts.coach_static_prefix()"
