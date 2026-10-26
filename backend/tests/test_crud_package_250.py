"""crud.py as a package, one module per concern (ai-trainer-ops#250).

crud.py held 216 public functions and constants in 5,615 lines. Each concern has
a module of its own now, and the package re-exports every public name, so no
caller had to change. These tests hold that: every name crud.py had is still on
the package, and every public name a module defines is exported, as that very
object and from that one module.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

import crud

# The public names of crud.py before the split (develop 620ea7b7).
OLD_PUBLIC = """
ATHLETE_HYPOTHESIS_CONFIRM_CONFIDENCE ATHLETE_HYPOTHESIS_DECAY_STEP
ATHLETE_HYPOTHESIS_PROMPT_LIMIT ATHLETE_HYPOTHESIS_PROMPT_MIN_CONFIDENCE
ATHLETE_HYPOTHESIS_PROMPT_STALE_AFTER_DAYS ATHLETE_HYPOTHESIS_RETIRE_CONFIDENCE
ATHLETE_INQUIRY_ANSWER_CONFIDENCE ATHLETE_INQUIRY_MAX_ASKS
ATHLETE_INQUIRY_MAX_PENDING ATHLETE_MEMORY_ARCHIVE_AFTER_DAYS
ATHLETE_MEMORY_CONFIDENCE_DECAY_PER_DAY ATHLETE_MEMORY_CONFIDENCE_STEP
ATHLETE_MEMORY_CONTRADICTION_PENALTY ATHLETE_MEMORY_DECAY_GRACE_DAYS
ATHLETE_MEMORY_DEFAULT_CONFIDENCE ATHLETE_MEMORY_INITIAL_CONFIDENCE_CAP
ATHLETE_MEMORY_MIN_EVIDENCE ATHLETE_MEMORY_PROMPT_LIMIT
ATHLETE_MEMORY_PROMPT_MIN_CONFIDENCE ATHLETE_MEMORY_STALE_AFTER_DAYS
ATHLETE_MEMORY_STALE_CONFIDENCE ATHLETE_OPEN_QUESTION_AUTO_CLOSE_EVIDENCE
ATHLETE_OPEN_QUESTION_PROMPT_LIMIT ATHLETE_OPEN_QUESTION_PROMPT_STALE_AFTER_DAYS
ATHLETE_PREDICTION_CONFIDENCE_STEP ATHLETE_PREDICTION_DEFAULT_CONFIDENCE
EXACT_IDENTITY_SOURCES MEMORY_FACT_SAME_WITHOUT_FIGURES
MEMORY_FACT_SAME_WITH_FIGURES add_trusted_device answer_ride_purpose_question
apply_athlete_memory_confidence_decay athlete_memory_observation_counts
athlete_utility_weights claim_password_reset_token clear_athlete_memory
consume_recovery_code count_open_athlete_experiments
count_open_athlete_open_questions count_open_hypotheses
count_pending_athlete_inquiries count_trusted_devices count_wrong_flags_since
create_athlete_metric_snapshot create_athlete_performance_snapshot
create_chat_message create_password_reset_token create_plan_change_summary
create_plan_commitment create_race_event create_user create_wrong_flag
deactivate_availability_constraint deactivate_availability_constraints_for_dates
deactivate_expired_availability_constraints deactivate_expired_plan_commitments
decay_unsupported_model_hypotheses delete_all_ride_metrics delete_athlete_experiment
delete_athlete_hypothesis delete_athlete_inquiry delete_athlete_memory_fact
delete_athlete_metric_snapshots delete_athlete_open_question
delete_athlete_prediction delete_chat_messages delete_intervals_token
delete_race_event delete_state delete_strava_token delete_workout_log
dismiss_athlete_inquiry end_illness evaluate_athlete_prediction
expire_stale_uncertainties get_all_ride_metrics_ordered get_athlete_context
get_athlete_experiment get_athlete_home_location get_athlete_hypothesis
get_athlete_inquiry get_athlete_memory_fact get_athlete_messages_since
get_athlete_metric_history get_athlete_model get_athlete_motivation_model
get_athlete_open_question get_athlete_performance_model
get_athlete_performance_snapshots get_athlete_prediction
get_athlete_prediction_accuracy get_chat_messages get_coach_memory
get_intervals_token get_last_flagged_constraint_dates get_latest_map_5min
get_latest_ride_metric get_latest_ride_metric_with_location get_logged_exercises
get_motivation_weight_history get_narrated_batch_ids get_near_duplicate_ride_metric
get_password_reset_token get_plan_text_translations get_prompt_athlete_hypotheses
get_prompt_athlete_memory_facts get_prompt_athlete_open_questions get_race_event
get_race_events get_recent_athlete_messages get_recent_ride_start_locations
get_recorded_activity_dates get_ride_metric_by_date get_ride_metric_by_external_id
get_ride_metric_by_identity get_ride_metric_by_source get_ride_metric_by_strava_id
get_ride_metrics_by_activity_ids get_ride_metrics_by_date get_ride_metrics_history
get_ride_metrics_missing_weather get_rider_assessment get_rides_with_weather
get_strava_token get_strength_sets get_strength_sets_for_exercise get_training_plan
get_training_plan_for_update get_trusted_device
get_unclassified_intervals_ride_metrics get_unreviewed_ride_metrics
get_user_by_email get_user_by_email_simple get_user_by_id
get_users_with_activity_sources get_users_with_training_plans get_week_goals
get_workout_log_by_date get_workout_logs hand_off_athlete_inquiry
increment_user_consumed_tokens invalidate_login_summary invalidate_training_status
list_active_availability_constraints list_active_goals list_active_plan_commitments
list_athlete_experiments list_athlete_hypotheses list_athlete_inquiries
list_athlete_memory_facts list_athlete_open_questions list_athlete_predictions
list_plan_day_history list_recovery_codes list_relevant_states
list_uncertainty_events list_wrong_flags locked_training_plan_select
mark_ride_metric_unfetchable mark_rides_as_reviewed motivation_model_as_dict
observe_athlete_memory_fact plan_day_history_stats propose_athlete_hypothesis
reask_athlete_inquiry record_athlete_inquiry
record_athlete_memory_fact_contradiction record_athlete_open_question
record_athlete_prediction record_llm_calls record_plan_day_changes record_state
record_uncertainty_event record_value_decision remove_goal replace_recovery_codes
replace_strength_sets resolve_athlete_inquiry retract_illness revoke_trusted_devices
revoke_user_tokens save_plan_text_translations save_week_goal set_plan_day_reasons
set_ride_feel_legs set_training_status set_wrong_flag_handled
suggest_athlete_experiment sum_admin_key_tokens_since sum_user_tokens_since
update_athlete_experiment update_athlete_hypothesis update_athlete_memory_fact
update_athlete_open_question update_athlete_prediction
update_coach_memory_if_unchanged update_race_event update_ride_match
update_ride_metric_classification update_ride_metric_notes upsert_active_goal
upsert_athlete_context upsert_athlete_home_location upsert_athlete_model
upsert_athlete_motivation_model upsert_athlete_performance_model
upsert_availability_constraint upsert_coach_memory upsert_intervals_token
upsert_ride_metric upsert_rider_assessment upsert_strava_token upsert_training_plan
upsert_workout_log visible_athlete_memory_fact_texts wrong_flag_weekly_counts
""".split()


def _modules():
    return [
        importlib.import_module(f"crud.{info.name}")
        for info in pkgutil.iter_modules(crud.__path__)
    ]


def _defined(module) -> set[str]:
    """The public names a module defines itself, not the ones it imports."""
    names = set()
    for node in ast.parse(Path(module.__file__).read_text()).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return {name for name in names if not name.startswith("_")}


def test_every_name_crud_py_had_is_still_there():
    assert len(OLD_PUBLIC) == 216
    assert sorted(set(OLD_PUBLIC) - set(dir(crud))) == []


def test_the_package_exports_what_its_modules_define():
    """A function added to a module but not to the package would be missing
    from ``crud.<name>``, which is how every caller reaches it."""
    modules = _modules()
    defined = [(module, name) for module in modules for name in _defined(module)]

    assert len(modules) > 10
    assert sorted(name for _, name in defined) == sorted(crud.__all__)
    assert [
        f"{module.__name__}.{name}"
        for module, name in defined
        if getattr(crud, name) is not getattr(module, name)
    ] == []
