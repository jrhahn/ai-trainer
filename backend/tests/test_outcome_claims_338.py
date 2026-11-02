"""A promised outcome for the body is a forbidden claim (review of 2026-11-01,
training A6, ai-trainer-ops#338).

Werner's coach wrote strength work "um das Kniegelenk muskulär optimal zu
schützen und die Belastung auf dem Rad schmerzfrei abzufangen": a treatment and
outcome claim, and the audit counted nothing.
"""

from __future__ import annotations

import pytest

from services import output_audit


def _promises(text: str) -> list[str]:
    return [
        finding.detail
        for finding in output_audit.forbidden_claims(text)
        if finding.reason == "promises a protective or healing outcome for the body"
    ]


@pytest.mark.parametrize(
    "text",
    [
        "Zwei Krafteinheiten pro Woche, um das Kniegelenk muskulär optimal zu "
        "schützen und die Belastung auf dem Rad schmerzfrei abzufangen.",
        "Diese Übungen schützen dein Knie.",
        "This routine will heal your knee.",
        "Strong glutes protect your knees on long climbs.",
        "Das macht deinen Rücken schmerzfrei.",
        "Core work keeps your lower back pain-free.",
        "Damit heilt die Sehne schneller.",
        "Damit bleibst du auf dem Rad schmerzfrei.",
        "Strength work makes you pain-free on long climbs.",
    ],
)
def test_a_promised_outcome_is_a_finding(text):
    assert _promises(text)


@pytest.mark.parametrize(
    "text",
    [
        "Fahr nur, solange es schmerzfrei geht.",
        "Only ride if it is pain-free.",
        "Strength work for the knee: pain at most 3/10, settled by the next morning.",
        "Lass das Knie von einer Physiotherapeutin anschauen.",
        "Back to back long rides this weekend.",
        "Come back to me after the ride.",
        "Hold 90 rpm on the climb.",
        # Pain rules, rest advice, weather and look-alike words (review of
        # #1289).
        "Keep the ride pain-free.",
        "Keep it pain-free.",
        "Ride only if it will be pain-free.",
        "Wenn du schmerzfrei bleiben willst, reduziere die Last.",
        "Ob es schmerzfrei wird, entscheidet die Schmerzskala.",
        "Rückenwind auf dem Heimweg.",
        "Knielinge als Schutz vor Kälte.",
        "Knielinge schützen deine Knie vor Kälte.",
        "Gib dem Knie Zeit zu heilen.",
        "Lass die Sehne ausheilen.",
        "Kniebeugen mit leichtem Gewicht.",
        "Rest lets the tendon heal.",
    ],
)
def test_a_pain_rule_or_a_referral_is_no_promise(text):
    assert _promises(text) == []
