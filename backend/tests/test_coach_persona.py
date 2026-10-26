"""The coach personas: their voice, their warmth, and the medical line.

Split out of ``test_ai_service_unit.py`` (ai-trainer-ops#259); its history is there.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# COACH_PERSONA / RUNNING_COACH_PERSONA — balanced coach voice
# ---------------------------------------------------------------------------


def test_both_personas_send_a_pain_that_does_not_settle_to_a_physio_or_doctor():
    """knee-pain passed in run 1459 and gave no referral in run 1475 (ops#86)."""
    from services.prompts import COACH_PERSONA, RUNNING_COACH_PERSONA

    for persona in (COACH_PERSONA, RUNNING_COACH_PERSONA):
        assert "a physio or doctor should look at it if it does not settle" in persona


def test_both_personas_send_a_medication_question_to_a_doctor_or_pharmacist():
    """The painkiller scenario declined without naming either on 2026-10-16 (ops#86, ops#5)."""
    from services.prompts import COACH_PERSONA, RUNNING_COACH_PERSONA

    for persona in (COACH_PERSONA, RUNNING_COACH_PERSONA):
        assert "a question for their doctor or pharmacist" in persona


def test_coach_persona_is_warm_but_not_overfamiliar():
    """COACH_PERSONA must stay warm without buddy-style overfamiliarity."""
    from services.prompts import COACH_PERSONA

    text = COACH_PERSONA.lower()
    assert (
        "warm" in text
        and "personal" in text
        and "respectful" in text
        and "direct" in text
    )
    assert "cycling" in COACH_PERSONA.lower(), "COACH_PERSONA must mention cycling"
    assert "great friend" not in text
    assert "my friend" not in text
    assert "supportive buddy" not in text


def test_running_coach_persona_is_warm_but_not_overfamiliar():
    """RUNNING_COACH_PERSONA must use the same balanced voice as COACH_PERSONA."""
    from services.prompts import RUNNING_COACH_PERSONA

    text = RUNNING_COACH_PERSONA.lower()
    assert (
        "warm" in text
        and "personal" in text
        and "respectful" in text
        and "direct" in text
    )
    assert "running" in text
    assert "great friend" not in text
    assert "my friend" not in text
    assert "supportive buddy" not in text


def test_every_coach_persona_draws_the_medical_line():
    """No diagnosis or treatment, and a doctor for red-flag symptoms (ai-trainer-ops#5).

    Every coach prompt starts from one of these personas, so the boundary is
    pinned here rather than per surface.
    """
    from services.prompts import (
        COACH_PERSONA,
        GENERAL_ENDURANCE_COACH_PERSONA,
        RUNNING_COACH_PERSONA,
    )

    for persona in (
        COACH_PERSONA,
        RUNNING_COACH_PERSONA,
        GENERAL_ENDURANCE_COACH_PERSONA,
    ):
        text = persona.lower()
        assert "not a medical professional" in text
        assert "never diagnose" in text
        assert "chest pain" in text and "see a doctor" in text
        assert "pre-existing condition" in text


def test_coach_persona_handles_jokes_and_sarcasm():
    """Both personas must understand jokes and sarcasm without losing the coaching thread."""
    from services.prompts import COACH_PERSONA, RUNNING_COACH_PERSONA

    for persona in (COACH_PERSONA, RUNNING_COACH_PERSONA):
        text = persona.lower()
        assert "take the athlete seriously" in text
        assert "joking" in text
        assert "sarcastic" in text
        assert "useful coaching" in text


def test_coach_persona_encourages_concise_answers():
    """Both personas must keep the default answer length focused."""
    from services.prompts import COACH_PERSONA, RUNNING_COACH_PERSONA

    for persona in (COACH_PERSONA, RUNNING_COACH_PERSONA):
        text = persona.lower()
        assert "concise" in text
        assert "2-4 focused sentences" in text


def test_coach_persona_keeps_replies_personal():
    """Both personas must make replies feel specific to the athlete."""
    from services.prompts import COACH_PERSONA, RUNNING_COACH_PERSONA

    for persona in (COACH_PERSONA, RUNNING_COACH_PERSONA):
        text = persona.lower()
        assert "name" in text
        assert "specific to their goals" in text
        assert "recent training" in text
        assert "personal detail" in text
        assert "coach memory" in text
        assert "feel seen" in text


def test_coach_persona_has_balanced_conversational_tone():
    """Both personas must steer toward natural warmth without performative friendliness."""
    from services.prompts import COACH_PERSONA, RUNNING_COACH_PERSONA

    for persona in (COACH_PERSONA, RUNNING_COACH_PERSONA):
        text = persona.lower()
        assert "balanced conversational tone" in text
        assert "attentive" in text
        assert "natural" in text
        assert "calmly confident" in text
        assert "neither robotic nor performatively friendly" in text


def test_personas_are_sport_distinct():
    """The two personas must be distinct and each reference their own sport."""
    from services.prompts import COACH_PERSONA, RUNNING_COACH_PERSONA

    assert COACH_PERSONA != RUNNING_COACH_PERSONA
    assert "cycling" in COACH_PERSONA and "cycling" not in RUNNING_COACH_PERSONA
    assert "running" in RUNNING_COACH_PERSONA and "running" not in COACH_PERSONA


def test_coach_persona_empathy_traits():
    """COACH_PERSONA must instruct the AI to show empathy and avoid judgement."""
    from services.prompts import COACH_PERSONA

    text = COACH_PERSONA.lower()
    assert "empathy" in text or "empathetic" in text or "compassion" in text
    assert "judged" in text or "judgment" in text or "judge" in text


def test_coach_persona_encourages_direct_address():
    """Both personas must instruct the AI to address the athlete directly with 'you'."""
    from services.prompts import COACH_PERSONA, RUNNING_COACH_PERSONA

    for persona in (COACH_PERSONA, RUNNING_COACH_PERSONA):
        assert "address the athlete directly" in persona.lower() or "'you'" in persona


def test_coach_persona_retains_long_term_philosophy():
    """Both personas must still communicate the long-term development philosophy."""
    from services.prompts import COACH_PERSONA, RUNNING_COACH_PERSONA

    for persona in (COACH_PERSONA, RUNNING_COACH_PERSONA):
        assert "long-term" in persona.lower()
        assert "recovery" in persona.lower()


def test_analyse_activities_system_uses_correct_persona():
    """analyse_activities_system must embed the sport-appropriate persona."""
    from services.prompts import (
        analyse_activities_system,
        COACH_PERSONA,
        RUNNING_COACH_PERSONA,
    )

    cycling_system = analyse_activities_system("cycling")
    running_system = analyse_activities_system("running")

    # Each system prompt must start with the matching persona
    assert cycling_system.startswith(COACH_PERSONA)
    assert running_system.startswith(RUNNING_COACH_PERSONA)
    # And must not bleed into the wrong sport
    assert "You are a knowledgeable cycling coach" not in running_system
    assert "You are a knowledgeable running coach" not in cycling_system


def test_generate_plan_system_uses_coach_persona():
    """generate_plan_system must embed COACH_PERSONA (cycling plans only)."""
    from services.prompts import generate_plan_system, COACH_PERSONA

    system = generate_plan_system()
    assert system.startswith(COACH_PERSONA)


def test_rate_workout_system_uses_coach_persona():
    """rate_workout_system must embed COACH_PERSONA."""
    from services.prompts import rate_workout_system, COACH_PERSONA

    system = rate_workout_system()
    assert system.startswith(COACH_PERSONA)


def test_coach_voice_traits_template_interpolation():
    """_COACH_VOICE_TRAITS must resolve without placeholders for known sport names."""
    from services.prompts import _COACH_VOICE_TRAITS

    for sport in ("cycling", "running"):
        resolved = _COACH_VOICE_TRAITS.format(sport=sport)
        assert sport in resolved
        assert "{sport}" not in resolved
