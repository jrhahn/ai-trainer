"""Property-based prohibitions for the output audit (ai-trainer-ops#28).

The examples in ``test_output_audit`` pin what the checks find. These pin what
they must never do, which for a heuristic over prose is the more useful half:
the way this check dies is not by missing one invention, it is by reporting so
much noise that someone turns it off.

So the properties here are almost all one-sided. More context may only make the
audit quieter; a number the coach copied out of its context is never a finding;
a referral to a doctor never creates one. The single two-sided property is the
one the whole module exists for — **a number that is nowhere in the context is
always reported** — and it is stated over a reply holding exactly one number,
because that is the case where no composition rule can excuse it.

Deliberately no property about *which* numbers are allowlisted or *which* terms
are forbidden. Those lists are expected to move as the reports come in
(ai-trainer-ops#28 says report first, gate later), and a suite that pins them
would have to be edited every time the thing it guards legitimately improves.
What must not move is the shape of the answer.
"""

from __future__ import annotations

import re

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from services.output_audit import (
    CHECK_CLAIMS,
    CHECK_NUMBERS,
    SMALL_INTEGER_MAX,
    audit,
    context_values,
    forbidden_claims,
    unexplained_numbers,
)

SETTINGS = settings(
    max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow]
)

# Numbers the context will hold, and numbers it never will. Kept in disjoint
# ranges so "this number is absent" is a fact about the generator rather than a
# coincidence hypothesis has to avoid.
GROUNDED = st.integers(min_value=20, max_value=999)
INVENTED = st.integers(min_value=10_000, max_value=99_999)

# Prose fragments the coach actually writes, used to surround a number so the
# properties run against sentences rather than against bare digits.
FRAME = st.sampled_from(
    [
        "Your FTP is {} W.",
        "Hold {} W on the climbs.",
        "That was {} TSS.",
        "Ride {} minutes easy.",
        "Deine Schwelle liegt bei {} W.",
        "{} is where I would put Tuesday.",
    ]
)


def _details(findings, check: str) -> list[str]:
    return [finding.detail for finding in findings if finding.check == check]


# ---------------------------------------------------------------------------
# Number provenance
# ---------------------------------------------------------------------------


@given(value=GROUNDED, frame=FRAME)
@SETTINGS
def test_a_number_the_context_contains_is_never_reported(value, frame):
    context = f"Athlete model: threshold {value} W, computed 2026-10-06."
    assert unexplained_numbers(frame.format(value), context) == []


@given(value=INVENTED, frame=FRAME)
@SETTINGS
def test_a_number_the_context_lacks_is_always_reported(value, frame):
    """The one thing this module exists to catch.

    One number in the reply, so there is no second grounded number for a
    composition rule to build it from — an invention here has no excuse left.

    This property is load-bearing rather than decorative. Widening the
    composition pool from the reply's own grounded numbers to the whole context
    — the obvious-looking simplification — makes this test fail, and with it
    ``test_an_invented_ftp_is_reported``: with a real prompt's few hundred
    numbers, 285 is one multiplication away from something, and the check goes
    silent on the exact case it was built for.
    """
    context = "Athlete model: threshold 278 W, CTL 61.4, ATL 58.0."
    assert _details(unexplained_numbers(frame.format(value), context), CHECK_NUMBERS) == [
        str(value)
    ]


@given(value=st.floats(min_value=20, max_value=5000, allow_nan=False))
@SETTINGS
def test_rounding_a_context_value_is_never_reported(value):
    """The coach writes 278 for a model that computed 278.4.

    The tolerance is at least half a unit precisely so that stating a rounded
    value is reporting rather than inventing.
    """
    context = f"Computed: {value:.4f}"
    assert unexplained_numbers(f"That puts you at {round(value)}.", context) == []


@given(
    value=INVENTED,
    extra=st.lists(st.integers(min_value=1, max_value=99_999), max_size=8),
    frame=FRAME,
)
@SETTINGS
def test_more_context_never_adds_findings(value, extra, frame):
    """Monotone in the context, which is what makes the check safe to extend.

    Passing the coach more of what it was actually given can only move numbers
    from unexplained to explained. If it could ever do the reverse, every caller
    would have to think about *which* context to audit against, and the quiet
    answer would be to audit against none.
    """
    text = frame.format(value)
    base = "Athlete model: threshold 278 W."
    richer = base + " " + " ".join(str(number) for number in extra)

    before = _details(unexplained_numbers(text, base), CHECK_NUMBERS)
    after = _details(unexplained_numbers(text, richer), CHECK_NUMBERS)
    assert len(after) <= len(before)
    assert set(after) <= set(before)


@given(left=INVENTED, right=INVENTED)
@SETTINGS
def test_composition_never_explains_an_invented_number(left, right):
    """Two invented numbers cannot launder a third.

    The composition rule exists so that a coach adding 3×20 up to 60 is not
    accused of invention. It must not become a way to state anything at all: the
    factors have to be grounded before the total they explain is.
    """
    text = f"Do {left} W for {right} minutes, {left * right} in total."
    findings = _details(
        unexplained_numbers(text, "Athlete model: threshold 278 W."), CHECK_NUMBERS
    )
    assert str(left) in findings
    assert str(right) in findings


@given(text=st.text(alphabet=st.characters(blacklist_categories=("Nd", "No")), max_size=200))
@SETTINGS
def test_text_without_digits_is_never_a_number_finding(text):
    assert unexplained_numbers(text, "threshold 278 W") == []


@given(text=st.text(max_size=300), context=st.text(max_size=300))
@SETTINGS
def test_every_reported_number_is_actually_in_the_text(text, context):
    """The report must not name a token the coach never wrote.

    A finding is something a human then goes looking for. One that cannot be
    found in the reply is worse than no finding, because it costs the search.
    """
    for detail in _details(unexplained_numbers(text, context), CHECK_NUMBERS):
        assert detail in text


# ---------------------------------------------------------------------------
# Forbidden claims
# ---------------------------------------------------------------------------

CLAIM_TEXT = st.sampled_from(
    [
        "You have anemia and should rest.",
        "If you have anemia, ask your doctor.",
        "This is not medical advice.",
        "Nice work on Sunday, keep Wednesday easy.",
        "Your prognosis is good.",
        "Treat yourself to a rest day.",
        "This might be an iron deficiency.",
        "Iron deficiency is common in endurance athletes.",
    ]
)

REFERRAL = st.sampled_from(
    [
        " This is not medical advice.",
        " Please see a doctor about it.",
        " Consult your physician first.",
        " I am not a doctor.",
    ]
)


@given(text=CLAIM_TEXT, referral=REFERRAL)
@SETTINGS
def test_adding_a_referral_never_adds_a_finding(text, referral):
    """The safe sentence must never be the thing that trips the check.

    If it could, the product would be punished for saying the one thing it
    should always say, and the obvious fix would be to stop saying it.
    """
    assert len(forbidden_claims(text + referral)) <= len(forbidden_claims(text))


@given(text=CLAIM_TEXT)
@SETTINGS
def test_capitalisation_does_not_change_the_verdict(text):
    expected = len(forbidden_claims(text))
    assert len(forbidden_claims(text.upper())) == expected
    assert len(forbidden_claims(text.lower())) == expected


@given(text=st.text(max_size=300))
@SETTINGS
def test_every_reported_claim_is_actually_in_the_text(text):
    for detail in _details(forbidden_claims(text), CHECK_CLAIMS):
        assert detail in text


# ---------------------------------------------------------------------------
# Totality
# ---------------------------------------------------------------------------


@given(
    text=st.text(max_size=400),
    context=st.one_of(
        st.none(),
        st.text(max_size=400),
        st.dictionaries(st.text(max_size=8), st.integers() | st.floats() | st.text(max_size=8), max_size=6),
        st.lists(st.integers() | st.none() | st.booleans(), max_size=10),
    ),
)
@SETTINGS
def test_the_audit_is_total(text, context):
    """No input makes the audit raise.

    ``report`` catches anyway, because an exception there would cost the athlete
    their answer for the sake of a counter. That ``except`` is only honest if
    nothing is expected to reach it.
    """
    audit(text, context)


@given(context=st.recursive(
    st.integers() | st.floats(allow_nan=True, allow_infinity=True) | st.text(max_size=8) | st.none() | st.booleans(),
    lambda children: st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=4), children, max_size=4),
    max_leaves=12,
))
@SETTINGS
def test_context_values_are_sorted_non_negative_and_finite_free_of_bools(context):
    """What ``context_values`` promises its callers.

    Sorted, because membership is a bisect; absolute, because the sign lives in
    the prose; and no booleans, because ``True`` is an ``int`` in Python and a 1
    the model never stated would otherwise explain a 1 the coach invented.
    """
    values = context_values(context)
    assert values == sorted(values)
    assert all(value >= 0 for value in values if value == value)  # NaN excluded
    assert all(not isinstance(value, bool) for value in values)


@given(text=st.text(max_size=200))
@SETTINGS
def test_a_finding_always_carries_a_known_check_and_a_reason(text):
    for finding in audit(text, "threshold 278 W"):
        assert finding.check in {CHECK_NUMBERS, CHECK_CLAIMS}
        assert finding.reason
        assert not re.fullmatch(r"\s*", finding.detail)


@given(value=st.integers(min_value=0, max_value=SMALL_INTEGER_MAX))
@SETTINGS
def test_whole_numbers_in_the_enumeration_range_are_never_reported(value):
    """Rep counts and day counts, which appear in nearly every reply.

    Pinned as a property rather than left to the examples because the bound is
    expected to move once the reports show what actually turns up there — and
    whatever it moves to, every integer below it must stay quiet.
    """
    assert unexplained_numbers(f"Do {value} efforts.", "nothing numeric here") == []
