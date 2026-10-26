"""The rules of the coach's chat prompt, and the order that keeps its prefix cacheable.

Split out of ``test_ai_service_unit.py`` (ai-trainer-ops#259); its history is there.
"""

from __future__ import annotations


from domain.untrusted_text import CLOSE, OPEN


def test_plan_updates_rule_defers_on_provisional_classification():
    """The coach must treat unknown/low-confidence ride classifications as provisional
    and defer to the athlete's firsthand account rather than asserting the type (#409)."""
    from services.prompts import ask_trainer_plan_updates_rule

    rule = ask_trainer_plan_updates_rule(None)
    lowered = rule.lower()
    assert "provisional" in lowered
    # Must instruct deferring to the athlete's stated session over the auto-guess.
    assert "firsthand account" in lowered
    # Must forbid re-labelling a reported session as something else (the observed bug).
    assert "unknown" in lowered and "confidence" in lowered


# ---------------------------------------------------------------------------
# Outlook feature (Task 7)
# ---------------------------------------------------------------------------


def test_ask_trainer_system_includes_outlook_rules():
    """The ask_trainer system prompt must contain outlook handling instructions."""
    from services.prompts import ask_trainer_system, ask_trainer_plan_updates_rule

    prompt = ask_trainer_system(
        profile={"name": "Alice"},
        today="2026-05-01",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
    )

    # The prompt must mention outlook-related guidance
    assert "outlook" in prompt.lower()
    # Must reference next 3-5 sessions
    assert "3-5" in prompt
    # Must state that planUpdates should be omitted for a plain outlook
    assert "planUpdates" in prompt or "plan_updates" in prompt.lower()


def test_athlete_context_section_omits_empty_defaults():
    from services.prompts import athlete_context_section

    assert athlete_context_section(None) == ""
    assert (
        athlete_context_section(
            {
                "trainingTendency": "unknown",
                "coachingRisks": [],
                "notes": "",
            }
        )
        == ""
    )


def test_ask_trainer_system_includes_structured_athlete_context():
    from services.prompts import ask_trainer_plan_updates_rule, ask_trainer_system

    prompt = ask_trainer_system(
        profile={"name": "Alice"},
        today="2026-05-01",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
        athlete_context={
            "trainingTendency": "overtrains",
            "restResponse": "restless",
            "adherencePattern": "adds_extra",
            "coachingRisks": ["doing too much when fresh"],
            "notes": "Needs explicit permission to rest.",
        },
    )

    assert "Structured athlete context (durable coaching model)" in prompt
    # Marked: the athlete context is derived from the athlete's own prose and
    # from Strava text, so it reaches the model as data (ai-trainer-ops#33).
    assert f'"trainingTendency": "{OPEN}overtrains{CLOSE}"' in prompt
    assert f'"restResponse": "{OPEN}restless{CLOSE}"' in prompt
    assert "doing too much when fresh" in prompt
    assert "stable knowledge" in prompt


def test_athlete_memory_facts_section_filters_untrusted_facts():
    from services.prompts import athlete_memory_facts_section

    section = athlete_memory_facts_section(
        [
            {
                "fact": "Does too much when fresh",
                "category": "coaching_risk",
                "sourceSnippet": "Added extra intervals after rest.",
                "confidence": 0.75,
                "status": "active",
                "lastConfirmedAt": "2026-06-18T08:00:00Z",
                "observationCount": 3,
            },
            {
                "fact": "Maybe dislikes gym work",
                "category": "preference",
                "confidence": 0.2,
                "status": "active",
            },
            {
                "fact": "Only trains indoors",
                "category": "preference",
                "confidence": 0.9,
                "status": "rejected",
            },
            {
                "fact": "Uses MTB races as motivation",
                "category": "motivation",
                "confidence": 0.1,
                "status": "user_confirmed",
            },
        ]
    )

    assert "Evidence-backed athlete memory" in section
    assert "Does too much when fresh" in section
    assert "Added extra intervals after rest." in section
    assert "Uses MTB races as motivation" in section
    assert "Maybe dislikes gym work" not in section
    assert "Only trains indoors" not in section


def test_athlete_memory_facts_section_groups_facts_and_observations():
    """Stable facts and inferred observations land under distinct labels (#386)."""
    from services.prompts import athlete_memory_facts_section

    section = athlete_memory_facts_section(
        [
            {
                "fact": "FTP is about 250 W",
                "kind": "fact",
                "category": "general",
                "confidence": 0.85,
                "status": "active",
            },
            {
                "fact": "Fades in the final interval of VO2 sessions",
                "kind": "observation",
                "category": "recurring_issues",
                "confidence": 0.7,
                "status": "active",
            },
        ]
    )

    fact_label_at = section.index("Stable athlete facts")
    obs_label_at = section.index("Behavioural observations")
    # Both groups render, each under its own heading, facts first.
    assert fact_label_at < obs_label_at
    assert "FTP is about 250 W" in section[fact_label_at:obs_label_at]
    assert "Fades in the final interval" in section[obs_label_at:]


def test_athlete_memory_facts_section_defaults_missing_kind_to_observation():
    """Legacy rows without a kind are treated as observations, not facts (#386)."""
    from services.prompts import athlete_memory_facts_section

    section = athlete_memory_facts_section(
        [
            {
                "fact": "Prefers MTB",
                "category": "general",
                "confidence": 0.8,
                "status": "active",
            }
        ]
    )

    assert "Behavioural observations" in section
    assert "Stable athlete facts" not in section


def test_ask_trainer_system_includes_athlete_memory_facts():
    from services.prompts import ask_trainer_plan_updates_rule, ask_trainer_system

    prompt = ask_trainer_system(
        profile={"name": "Alice"},
        today="2026-06-18",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
        athlete_memory_facts=[
            {
                "fact": "Does too much when fresh",
                "category": "coaching_risk",
                "sourceSnippet": "Repeatedly added extra work after rest.",
                "confidence": 0.8,
                "status": "active",
            }
        ],
    )

    assert "Evidence-backed athlete memory" in prompt
    assert "Does too much when fresh" in prompt
    assert "Repeatedly added extra work after rest." in prompt
    assert "confidence" in prompt


def test_ask_trainer_system_includes_two_layer_recommendation_rules():
    from services.prompts import ask_trainer_plan_updates_rule, ask_trainer_system

    prompt = ask_trainer_system(
        profile={"name": "Alice"},
        today="2026-06-18",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
    )

    assert "Recommendation reasoning layers" in prompt
    assert "Physiology layer" in prompt
    assert "Athlete-context layer" in prompt
    assert "If two options are physiologically similar" in prompt
    assert "keep the final response concise and natural" in prompt


def test_ask_trainer_system_includes_divergence_rationale_pattern():
    """Coach must surface a personal-context call when the layers diverge."""
    from services.prompts import ask_trainer_plan_updates_rule, ask_trainer_system

    prompt = ask_trainer_system(
        profile={"name": "Alice"},
        today="2026-06-18",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
    )

    # The "numbers say X, but knowing you I would do Y" divergence pattern.
    assert "DIVERGE" in prompt
    assert "knowing how you tend to turn easy" in prompt
    # Separate structured rationale fields are part of the response contract.
    assert '"physiologyRationale"' in prompt
    assert '"contextRationale"' in prompt


def test_ask_trainer_system_allows_targeted_questions_for_ambiguous_recommendations():
    from services.prompts import ask_trainer_plan_updates_rule, ask_trainer_system

    prompt = ask_trainer_system(
        profile={"name": "Alice"},
        today="2026-06-18",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
    )

    assert "ask exactly one short targeted learning question" in prompt
    assert "materially change the recommendation" in prompt
    assert "Are you restless because you feel fresh" in prompt
    assert "Would a social ride help you more" in prompt
    assert "Do not over-ask" in prompt
    assert "make the recommendation clear" in prompt


def test_next_ride_recommendation_system_preserves_hard_constraints():
    from services.prompts import next_ride_recommendation_system

    prompt = next_ride_recommendation_system().lower()

    assert "hard athlete availability constraints" in prompt
    assert "never schedule or recommend training on a constrained" in prompt
    assert "physiologically optimal" in prompt
    assert "ask one concise clarifying question" in prompt


def test_next_ride_recommendation_system_includes_hard_session_spacing_rules():
    from services.prompts import next_ride_recommendation_system

    prompt = next_ride_recommendation_system().lower()

    assert "hard-session spacing rules" in prompt
    assert "actual activity history is authoritative" in prompt
    assert "inspect the recent activity history first" in prompt
    assert "do not keep, recommend, or create another vo2max" in prompt
    assert "within roughly 48 hours" in prompt
    assert "move the intensity to a later feasible day" in prompt


def test_ask_trainer_system_includes_response_quality_rules():
    """ask_trainer_system must include response quality rules for the response field."""
    from services.prompts import ask_trainer_system, ask_trainer_plan_updates_rule

    prompt = ask_trainer_system(
        profile={"name": "Alice"},
        today="2026-05-01",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
    ).lower()

    # Must acknowledge uncertainty when data is weak
    assert "uncertain" in prompt or "sparse" in prompt or "weak" in prompt
    # Must require reference to a concrete ride/athlete detail
    assert "concrete" in prompt or "specific" in prompt
    # Must require a clear next action
    assert (
        "next action" in prompt or "next step" in prompt or "recommendation" in prompt
    )
    # Must cap follow-up questions at one
    assert (
        "at most one" in prompt or "one follow-up" in prompt or "one concise" in prompt
    )


def test_ask_trainer_system_includes_attentive_coach_rules():
    """ask_trainer_system must tell the coach to notice important athlete details."""
    from services.prompts import ask_trainer_system, ask_trainer_plan_updates_rule

    prompt = ask_trainer_system(
        profile={"name": "Alice"},
        today="2026-05-01",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
    ).lower()

    assert "attentive coach" in prompt
    assert "high-signal" in prompt
    assert "running out of drink" in prompt
    assert "hot" in prompt
    assert "wie viel und was hast du" in prompt
    assert "same language" in prompt


def test_ask_trainer_plan_updates_rule_preserves_availability_constraints():
    from services.prompts import ask_trainer_plan_updates_rule

    rule = ask_trainer_plan_updates_rule(None).lower()

    assert "hard athlete constraints" in rule
    assert "no training on friday" in rule
    assert "do not schedule workouts on constrained" in rule
    assert "physiologically optimal" in rule


def test_a_wish_for_a_given_day_counts_as_asking_for_a_plan_change():
    # "I'd like to ride tomorrow" about a rest day got agreement twice and no
    # change, until the athlete asked for the update in so many words.
    from services.prompts import ask_trainer_plan_updates_rule

    for context_workout in (None, {"date": "2026-10-16", "title": "Rest Day"}):
        rule = ask_trainer_plan_updates_rule(context_workout)
        assert "A plain wish for a given day counts as asking" in rule
        assert "A maybe" in rule


def test_a_protective_change_needs_no_request_and_a_plain_question_none():
    """The owner's (a) to ai-trainer-ops#103 Q30. Asked or not, the coach fixes a
    plan the athlete's own news makes unsafe, every affected day, and says how to
    undo it; other departures it only offers to change."""
    from services.plan_priors import RACE_EVE_MAX_MINUTES, RACE_WEEK_MAX_MINUTES
    from services.prompts import ask_trainer_plan_updates_rule

    for context_workout in (None, {"date": "2026-10-16", "title": "Threshold 4x8"}):
        rule = ask_trainer_plan_updates_rule(context_workout)
        assert "without being asked for a protective change" in rule
        assert "illness or the return from it" in rule
        assert "more hours than the athlete says they have" in rule
        # The race-eve prior's own number, so the two cannot drift apart (#84).
        assert (
            f"anything over {RACE_EVE_MAX_MINUTES} minutes on the two days before it"
            in rule
        )
        # And the race-week prior's (ai-trainer-ops#103 Q35).
        assert (
            f"anything over {RACE_WEEK_MAX_MINUTES // 60} hours in the race week"
            in rule
        )
        assert "Change every affected day, not only today" in rule
        # Run 1475 still asked "Soll ich den Plan anpassen?" and kept the race-eve
        # ride for a reason of its own.
        assert "make it in this reply" in rule
        assert "never for one you supply" in rule
        assert "get the old session back" in rule
        assert "offer the change instead of making it" in rule
        assert "no reason to change a day that is fine" in rule
    assert "Only include this" not in ask_trainer_plan_updates_rule(None)
    viewing = ask_trainer_plan_updates_rule(
        {"date": "2026-10-16", "title": "Threshold 4x8"}
    )
    assert "in three cases" in viewing and "(3) Also include it" in viewing


def test_ask_trainer_system_checks_constraints_before_plan_updates():
    from services.prompts import ask_trainer_system, ask_trainer_plan_updates_rule

    prompt = ask_trainer_system(
        profile={"name": "Alice"},
        today="2026-06-18",
        last_7_days=[],
        next_n_days=[
            {"date": "2026-06-18", "weekday": "Thursday", "workoutType": "intervals"},
            {"date": "2026-06-19", "weekday": "Friday", "workoutType": "rest"},
        ],
        assessment_section="",
        memory_section="Coach notes about this athlete: Friday is unavailable for training.",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
    ).lower()

    assert "hard constraint rules" in prompt
    assert "availability constraints" in prompt
    assert "do not move workouts onto constrained" in prompt
    assert "friday is unavailable for training" in prompt


def test_ask_trainer_system_includes_rest_recommendation_rules():
    from services.prompts import ask_trainer_system, ask_trainer_plan_updates_rule

    prompt = ask_trainer_system(
        profile={"name": "Alice"},
        today="2026-06-19",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
    ).lower()

    assert "rest/recovery recommendation rules" in prompt
    assert "ctl, atl, tsb" in prompt
    assert "recent tss" in prompt
    assert "rpe" in prompt
    assert "subjective leg feel" in prompt
    assert "one easy/recovery day" in prompt
    assert "unavailable day with no training counts" in prompt
    assert "do not claim two complete rest days are mandatory" in prompt
    assert "neutral or positive tsb" in prompt
    assert "bounded easy z2/recovery ride" in prompt


def test_ask_trainer_system_rest_rules_require_numeric_explanation():
    from services.prompts import ask_trainer_system, ask_trainer_plan_updates_rule

    prompt = ask_trainer_system(
        profile={"name": "Alice"},
        today="2026-06-19",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
    ).lower()

    assert "corrected the chronology or availability" in prompt
    assert "re-evaluate from that corrected sequence" in prompt
    assert "show the numbers" in prompt
    assert "what each number supports" in prompt
    assert "what it does not support" in prompt
    assert "generic supercompensation or overtraining language" in prompt


def test_ask_trainer_system_includes_hard_session_spacing_rules():
    from services.prompts import ask_trainer_system, ask_trainer_plan_updates_rule

    prompt = ask_trainer_system(
        profile={"name": "Alice"},
        today="2026-06-19",
        last_7_days=[
            {
                "date": "2026-06-18",
                "workoutType": "intervals",
                "title": "VO2max intervals",
            },
        ],
        next_n_days=[
            {
                "date": "2026-06-20",
                "workoutType": "intervals",
                "title": "VO2max intervals",
            },
        ],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
        metrics_history_section=(
            "Recent activity history (newest first):\n"
            "  2026-06-18 | sport:cycling | interval_vo2max | TSS 135 | TSB +2.1"
        ),
    ).lower()

    assert "hard-session spacing rules" in prompt
    assert "actual activity history is authoritative" in prompt
    assert "do not keep, recommend, or create another vo2max" in prompt
    assert "within roughly 48 hours" in prompt
    assert "upcoming plan violates this spacing" in prompt
    assert "positive tsb" in prompt
    assert "2026-06-18 | sport:cycling | interval_vo2max" in prompt
    assert '"date": "2026-06-20"' in prompt
    assert f'"title": "{OPEN}VO2max intervals{CLOSE}"'.lower() in prompt


def test_extracts_one_off_friday_availability_constraint():
    from datetime import date

    from domain.availability import extract_availability_constraints

    constraints = extract_availability_constraints(
        "Freitag habe ich keine Zeit für Training.",
        today=date(2026, 6, 18),
    )

    assert constraints == [
        {
            "constraint_type": "no_training",
            "constraint_date": "2026-06-19",
            "weekday": "friday",
            "reason": "Athlete said they are unavailable for training.",
            "source": "Freitag habe ich keine Zeit für Training.",
            "expires_on": "2026-06-19",
        }
    ]


def test_availability_constraints_block_training_plan_updates():
    from routers.ai import _filter_plan_updates_for_availability_constraints

    updates = [
        {
            "date": "2026-06-19",
            "workoutType": "intervals",
            "title": "VO2 Intervals",
            "durationMinutes": 60,
        },
        {
            "date": "2026-06-20",
            "workoutType": "endurance",
            "title": "Endurance",
            "durationMinutes": 90,
        },
    ]
    constraints = [
        {
            "constraintType": "no_training",
            "constraintDate": "2026-06-19",
            "weekday": "friday",
            "expiresOn": "2026-06-19",
        }
    ]

    filtered = _filter_plan_updates_for_availability_constraints(updates, constraints)

    assert [update["date"] for update in filtered] == ["2026-06-20"]


def test_ask_trainer_system_includes_outlook_response_example():
    """ask_trainer_system must include an example response for an outlook request."""
    from services.prompts import ask_trainer_system, ask_trainer_plan_updates_rule

    prompt = ask_trainer_system(
        profile={"name": "Alice"},
        today="2026-05-01",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
    ).lower()

    # The prompt should contain an inline example for outlook responses
    assert "example" in prompt
    # The example should reference sessions and fatigue/tsb in context
    assert "recovery" in prompt or "endurance" in prompt
    assert "tsb" in prompt.lower() or "fatigue" in prompt.lower()


# ---------------------------------------------------------------------------
# Communication style — opener variation rules
# ---------------------------------------------------------------------------


def test_coach_voice_traits_allows_occasional_fillers_but_requires_varied_openers():
    """_COACH_VOICE_TRAITS should allow occasional fillers but prevent default/repetitive openers."""
    from services.prompts import _COACH_VOICE_TRAITS

    trait_text = _COACH_VOICE_TRAITS.lower()
    assert "great question" in trait_text
    assert "occasionally" in trait_text
    assert "do not use them as a default opener" in trait_text
    assert "avoid repetitive" in trait_text
    assert "you did a tough ride yesterday" in trait_text


def test_ask_trainer_system_response_rules_allow_occasionally_and_avoid_repetition():
    """ask_trainer_system should allow occasional fillers while prohibiting repetitive openers.

    The persona says it, once: the response-quality rules repeated it until #257.
    """
    from services.prompts import ask_trainer_system

    prompt = ask_trainer_system(
        profile={"name": "Test"},
        today="2026-05-15",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule="",
    )
    prompt_lower = prompt.lower()
    assert "short fillers are allowed occasionally" in prompt_lower
    assert "do not use them as a default opener" in prompt_lower
    assert "template-like starts" in prompt_lower
    assert "you did a tough ride yesterday" in prompt_lower


def _cache_order_prompt(**overrides) -> str:
    from services.prompts import ask_trainer_system

    kwargs = dict(
        profile={"ftp": 250},
        today="2026-05-26",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule="",
        date_context="Current local date context (Europe/Berlin):\n- Today is Tuesday.",
    )
    kwargs.update(overrides)
    return ask_trainer_system(**kwargs)


def test_static_rules_precede_all_volatile_data_for_gemini_caching():
    """Gemini's implicit cache only matches a prefix, so the rules must come first.

    Every volatile item after the rules is fine; a single volatile item *before*
    them moves the cache boundary to zero and silently costs 10x on input (#514).
    """
    prompt = _cache_order_prompt(
        metrics_history_section="Recent activity history (newest first):\n  2026-05-25 | TSS 80",
    )

    last_static = max(
        prompt.index(marker)
        for marker in (
            "Activity feedback rules:",
            "Hard constraint rules:",
            "Outlook rules:",
            "Date awareness rules:",
            "Response quality rules",
        )
    )
    # Markers have to be strings that appear *only* in the data — several rule
    # blocks name the sections they talk about ("Upcoming plan", "Current local
    # date context"), so those match inside the static text and prove nothing.
    first_volatile = min(
        prompt.index(marker)
        for marker in (
            "Athlete data for this conversation",
            "Today's date: 2026-05-26",
            'Athlete profile: {"ftp": 250}',
            "2026-05-25 | TSS 80",
        )
    )
    assert last_static < first_volatile, (
        "volatile athlete data appears before the static rule block — "
        "this breaks the cacheable prefix"
    )


def test_the_cacheable_prefix_is_identical_across_different_athletes_and_days():
    """The whole point: two unrelated requests must share a long literal prefix."""
    import os

    monday = _cache_order_prompt(
        profile={"ftp": 250},
        today="2026-05-26",
        date_context="Current local date context: Tuesday",
        metrics_history_section="Recent activity history (newest first):\n  a",
    )
    other_athlete_other_day = _cache_order_prompt(
        profile={"ftp": 310, "name": "Someone Else"},
        today="2027-11-02",
        date_context="Current local date context: Thursday",
        metrics_history_section="Recent activity history (newest first):\n  b",
    )

    shared = len(os.path.commonprefix([monday, other_athlete_other_day]))
    # The static block is thousands of characters; anything in the hundreds would
    # mean something volatile crept back toward the front.
    assert shared > 4000, f"shared prefix collapsed to {shared} characters"
    assert monday[:shared].startswith("You are a knowledgeable cycling coach")


def test_output_contract_stays_last_where_the_model_reads_it_most_reliably():
    """Format compliance is worth more than the ~200 tokens it would add to the prefix."""
    prompt = _cache_order_prompt(plan_updates_rule="- planUpdates: an array")

    assert prompt.rstrip().endswith("- planUpdates: an array")
    assert prompt.index("ALWAYS respond with a valid JSON object") > prompt.index(
        "Athlete profile:"
    )
