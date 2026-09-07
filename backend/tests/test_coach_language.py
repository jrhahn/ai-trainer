"""The coach must write in the athlete's language, on every athlete-facing path (#658).

Production evidence that motivated this: of 44 nightly plan-change narrations sent
to a German-speaking athlete, 44 were in English — not drift but a structural gap,
because that message is unprompted and the only language rule in the codebase was
"reply in the same language the athlete used". There is no athlete turn to mirror.
Chat replies leaked the same way, 187 of 251, with the rule stated once in the
cached prefix and ~20 English data sections between it and the output contract.
"""

import crud
import models
from auth import hash_password
from services.prompts import (
    ATHLETE_LANGUAGE_RULE,
    ask_trainer_system,
    ask_trainer_system_sections,
    athlete_language_section,
    batch_review_system,
    coach_static_prefix,
    generate_inquiries_system,
    next_ride_recommendation_system,
    plan_change_summary_system,
    plan_change_summary_user,
    process_pending_feedbacks_system,
    rate_workout_system,
)
from tests.conftest import TestSessionLocal

TODAY = "2026-09-07"


async def _create_user(email: str) -> str:
    async with TestSessionLocal() as db:
        user = models.User(
            email=email,
            name="Test Rider",
            hashed_password=hash_password("Str0ng!Pass"),
            is_onboarded=True,
        )
        db.add(user)
        await db.flush()
        await db.commit()
        return user.id


async def _write_messages(user_id: str, messages: list[tuple[str, str]]) -> None:
    async with TestSessionLocal() as db:
        for index, (role, content) in enumerate(messages):
            await crud.create_chat_message(
                db,
                user_id,
                role=role,
                content=content,
                timestamp=f"2026-09-07T12:{index:02d}:00",
            )
        await db.commit()


def _sections(**overrides):
    kwargs = {
        "profile": {"name": "Test"},
        "today": TODAY,
        "last_7_days": [],
        "next_n_days": [],
        "assessment_section": "",
        "memory_section": "",
        "workout_section": "",
        "plan_updates_rule": "",
    }
    kwargs.update(overrides)
    return ask_trainer_system_sections(**kwargs)


# --- the narration, which had no anchor at all ---------------------------------


def test_the_narration_states_the_language_rule():
    assert ATHLETE_LANGUAGE_RULE in plan_change_summary_system()


def test_the_narration_rule_covers_the_per_day_reasons_too():
    # Both the summary and each day's reason are shown to the athlete; the reasons
    # are a separate JSON field and were the half most likely to be missed.
    prompt = plan_change_summary_system()
    assert "per-day reason" in prompt


def test_the_narration_carries_the_athletes_own_words():
    user_msg = plan_change_summary_user(
        [{"date": TODAY, "old_day": {}, "new_day": {}}],
        {"name": "Test"},
        run_context="your daily overnight routine check of the plan",
        language_samples=["Ich hab ja gestern nichts gemacht"],
    )
    assert "Ich hab ja gestern nichts gemacht" in user_msg
    assert ATHLETE_LANGUAGE_RULE in user_msg


def test_the_narration_without_samples_is_unchanged():
    # A brand-new athlete has never written anything. The section must vanish
    # rather than emit an empty quote block that reads as "they said nothing".
    kwargs = dict(
        run_context="an automatic plan adjustment",
    )
    changes = [{"date": TODAY, "old_day": {}, "new_day": {}}]
    without = plan_change_summary_user(changes, {"name": "Test"}, **kwargs)
    empty = plan_change_summary_user(
        changes, {"name": "Test"}, language_samples=[], **kwargs
    )
    assert without == empty
    assert "quoted only so you can tell" not in without


def test_language_samples_are_capped_and_trimmed():
    section = athlete_language_section(
        ["  erste  ", "", "   ", "zweite", "dritte", "vierte"]
    )
    # Only the three most recent, and the blank entries dropped entirely.
    assert "erste" not in section
    assert "vierte" in section and "zweite" in section and "dritte" in section


def test_a_very_long_message_does_not_blow_up_the_prompt():
    section = athlete_language_section(["x" * 5000])
    assert len(section) < 1000


def test_no_samples_means_no_section():
    assert athlete_language_section(None) == ""
    assert athlete_language_section([]) == ""
    assert athlete_language_section(["", "   "]) == ""


# --- the chat, where the rule existed but sat 20 sections too early ------------


def test_the_output_contract_restates_the_language_rule():
    # Recency is the stated reason the output contract is last (see the comment in
    # ask_trainer_system_sections). Language is an output property like any other.
    assert ATHLETE_LANGUAGE_RULE in _sections()["closing"]


def test_the_language_rule_is_near_the_end_of_the_coach_prompt():
    prompt = ask_trainer_system(
        {"name": "Test"}, TODAY, [], [], "", "", "", "",
    )
    tail = prompt[-4000:]
    assert ATHLETE_LANGUAGE_RULE in tail


def test_the_original_prefix_rule_is_still_there():
    # Reinforced, not moved: the prefix copy is cached and therefore free.
    assert "same language the athlete used" in coach_static_prefix()


def test_the_cached_prefix_did_not_change_shape():
    # The fix must not land in the static prefix — a per-athlete variation there
    # would break the cache for the whole request (#514/#538).
    assert ATHLETE_LANGUAGE_RULE not in coach_static_prefix()


def test_every_section_but_the_closing_is_untouched_by_the_fix():
    sections = _sections()
    assert not any(
        ATHLETE_LANGUAGE_RULE in value
        for key, value in sections.items()
        if key != "closing"
    )


# --- the remaining athlete-facing prose paths ---------------------------------


def test_all_athlete_facing_prompts_state_the_language_rule():
    # Every one of these produces text the athlete reads in the app: ride reviews,
    # workout verdicts, the dashboard login brief, the next-ride recommendation,
    # and the inquiry questions put to them directly (#506).
    for prompt in (
        batch_review_system(),
        rate_workout_system(),
        process_pending_feedbacks_system(),
        next_ride_recommendation_system(),
        generate_inquiries_system(3),
    ):
        assert ATHLETE_LANGUAGE_RULE in prompt


def test_the_rule_names_plan_data_as_the_wrong_thing_to_copy():
    # The concrete failure mode: workout titles are English for every athlete
    # ("Active Recovery Flush Spin"), and the model followed them.
    assert "workout titles" in ATHLETE_LANGUAGE_RULE


# --- the language samples come from the athlete, not from the coach -----------


async def test_only_the_athletes_own_messages_are_sampled():
    user_id = await _create_user("lang-roles@example.com")
    await _write_messages(
        user_id,
        [
            ("user", "Ich hab ja gestern nichts gemacht"),
            ("assistant", "Coming off your rest day yesterday"),
            ("user", "Bin nur etwas müde"),
        ],
    )

    async with TestSessionLocal() as db:
        samples = await crud.get_recent_athlete_messages(db, user_id)

    # Feeding the coach's own English back in would entrench exactly the bug.
    assert all("Coming off" not in s for s in samples)
    assert "Ich hab ja gestern nichts gemacht" in samples


async def test_the_sample_limit_is_honoured():
    user_id = await _create_user("lang-limit@example.com")
    await _write_messages(user_id, [("user", f"Nachricht {i}") for i in range(6)])

    async with TestSessionLocal() as db:
        samples = await crud.get_recent_athlete_messages(db, user_id, limit=2)

    assert len(samples) == 2
    # Most recent, and oldest-first so the prompt reads chronologically.
    assert samples == ["Nachricht 4", "Nachricht 5"]


async def test_an_athlete_who_never_wrote_anything_yields_no_samples():
    user_id = await _create_user("lang-silent@example.com")

    async with TestSessionLocal() as db:
        assert await crud.get_recent_athlete_messages(db, user_id) == []
