"""The prompts the snapshot and budget tests render (ai-trainer-ops#259).

Each builder renders one prompt from fixed inputs, so the text depends on the
prompt code alone. The system prompts carry the fixed text of every model
call; the coach prompt is rendered twice, once with no athlete data (its fixed
text) and once with some of each kind.
"""

from __future__ import annotations

from collections.abc import Callable

from services import prompts as p

# A Monday, so the weekday names in a prompt do not move with the calendar.
TODAY = "2026-05-04"


def _ask_trainer_minimal() -> str:
    return p.ask_trainer_system(
        profile={"name": "Alice"},
        today=TODAY,
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=p.ask_trainer_plan_updates_rule(None),
    )


def _ask_trainer_rich() -> str:
    session = {
        "date": "2026-05-05",
        "workoutType": "intervals",
        "title": "Sweet spot 3x12",
        "description": "3x12 min at 88-92 % FTP, 5 min easy between.",
        "durationMinutes": 75,
        "targetPower": {"low": 220, "high": 235},
    }
    return p.ask_trainer_system(
        profile={"name": "Alice", "currentFTP": 250, "weight": 62, "weeklyHours": 8, "trainingGoal": "race"},
        today=TODAY,
        last_7_days=[{"date": "2026-05-02", "workoutType": "endurance", "title": "Long ride", "durationMinutes": 180}],
        next_n_days=[session, {"date": "2026-05-07", "workoutType": "rest", "title": "Rest day", "durationMinutes": 0}],
        assessment_section="",
        memory_section="\n\nCoach memory: prefers early rides; knee niggle in March, gone since.\n",
        workout_section=p.ask_trainer_workout_section(session),
        plan_updates_rule=p.ask_trainer_plan_updates_rule(session),
        open_questions=[{"question": "Is the FTP underestimated?", "evidence": "Two rides above it last week."}],
        pending_inquiries=[{"question": "What gets in the way on Tuesdays?", "status": "pending"}],
    )


BUILDERS: dict[str, Callable[[], str]] = {
    "ask_trainer_minimal": _ask_trainer_minimal,
    "ask_trainer_rich": _ask_trainer_rich,
    "analyse_activities_cycling": lambda: p.analyse_activities_system("cycling", 250),
    "analyse_activities_running": lambda: p.analyse_activities_system("running", None),
    "generate_plan": p.generate_plan_system,
    "adapt_plan": p.adapt_plan_system,
    "week_goals": p.week_goals_system,
    "plan_text_translation_de": lambda: p.plan_text_translation_system("de"),
    "opening_message_en_cycling": lambda: p.opening_message_system("en", "cycling"),
    "opening_message_de_running": lambda: p.opening_message_system("de", "running"),
    "plan_change_summary": p.plan_change_summary_system,
    "update_memory": p.update_memory_system,
    "extract_athlete_facts": p.extract_athlete_facts_system,
    "generate_athlete_insights": p.generate_athlete_insights_system,
    "derive_athlete_model": p.derive_athlete_model_system,
    "generate_athlete_hypotheses": p.generate_athlete_hypotheses_system,
    "generate_open_questions": p.generate_open_questions_system,
    "generate_inquiries": lambda: p.generate_inquiries_system(3),
    "evaluate_inquiry_answer": lambda: p.evaluate_inquiry_answer_system(False),
    "evaluate_inquiry_answer_final_de": lambda: p.evaluate_inquiry_answer_system(True, "de"),
    "generate_validation_experiments": p.generate_validation_experiments_system,
    "generate_athlete_predictions": p.generate_athlete_predictions_system,
    "evaluate_athlete_predictions": p.evaluate_athlete_predictions_system,
    "detect_contradictions": p.detect_contradictions_system,
    "match_observations": p.match_observations_system,
    "rate_workout": p.rate_workout_system,
    "ask_trainer_classify": p.ask_trainer_classify_system,
    "refresh_login_summary": p.refresh_login_summary_system,
    "refresh_login_summary_de": lambda: p.refresh_login_summary_system("de"),
    "training_status": p.training_status_system,
    "batch_review": p.batch_review_system,
    "next_ride_recommendation": p.next_ride_recommendation_system,
    "process_pending_feedbacks": p.process_pending_feedbacks_system,
    "process_pending_feedbacks_de": lambda: p.process_pending_feedbacks_system("de"),
}

# The fixed text of each prompt: every builder but the coach prompt with
# athlete data, which grows with the athlete, not with the code.
FIXED_TEXT = sorted(name for name in BUILDERS if name != "ask_trainer_rich")
