"""The consent version moves with the consent text (ai-trainer-ops#136).

An athlete consents to the text they read: the checkbox sentence in Settings
(English and German) and the section on /privacy. ``VERSION`` in
``services.coach_review_consent`` names that text, and a consent to an older
version is not valid. A text change without a new ``VERSION`` would keep old
consents valid for a text nobody agreed to, so the text is pinned here per
version.

When this fails because you changed the text: set ``VERSION`` to today's date
and put the new fingerprint under it.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from services import coach_review_consent

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"

FINGERPRINTS = {
    "2026-10-20": "19aae3713cea5e93",
}


def _between(text: str, start: str, end: str) -> str:
    begin = text.index(start)
    return text[begin : text.index(end, begin)]


def consent_text() -> str:
    """Every word the athlete reads before ticking, whitespace collapsed."""
    card = (FRONTEND / "components" / "CoachReviewConsentCard.tsx").read_text(encoding="utf-8")
    sentence = re.search(r"t\(\s*'(I agree [^']*)'", card)
    assert sentence, "the consent sentence is no longer a t('I agree …') call"
    german = re.search(
        re.escape(f"'{sentence.group(1)}':") + r"\s*'([^']*)'",
        (FRONTEND / "i18n" / "de.ts").read_text(encoding="utf-8"),
    )
    assert german, "the consent sentence has no German translation in de.ts"
    privacy = (FRONTEND / "pages" / "PrivacyPage.tsx").read_text(encoding="utf-8")
    parts = [
        sentence.group(1),
        german.group(1),
        _between(privacy, "function CoachReviewGerman", "function CoachReviewEnglish"),
        _between(privacy, "function CoachReviewEnglish", "\nfunction "),
    ]
    return " ".join(" ".join(part.split()) for part in parts)


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def test_the_consent_text_is_the_one_its_version_names() -> None:
    assert coach_review_consent.VERSION in FINGERPRINTS, (
        f"VERSION {coach_review_consent.VERSION} has no fingerprint here"
    )
    assert fingerprint(consent_text()) == FINGERPRINTS[coach_review_consent.VERSION], (
        "The consent text changed: set VERSION in services/coach_review_consent.py "
        f"to today's date and pin {fingerprint(consent_text())!r} under it."
    )


def test_the_fingerprint_covers_both_languages_and_the_privacy_section() -> None:
    text = consent_text()
    for phrase in (
        "I agree that my chat messages",
        "Ich willige ein",
        "Anthropic als Auftragsverarbeiter",
        "Anthropic as processor",
        "Art. 9(2)(a) GDPR",
    ):
        assert phrase in text, phrase
    # The sections end where they end: nothing of the rest of the page.
    assert "Datenschutzerklärung" not in text and text.count("function ") == 2
