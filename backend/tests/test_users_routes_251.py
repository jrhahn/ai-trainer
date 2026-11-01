"""routers/users/ as a package, one module per concern (ai-trainer-ops#251, part 2).

routers/users.py held 81 routes of a dozen concerns in 2,800 lines. They now
live in one module per concern, each on a router of its own with the old prefix
and tag, and the package's router includes them all. These tests hold what the
split promised: the same routes under the same names, no route that could
answer for another, and no module above 600 lines.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from pathlib import Path

import routers.users as users_package
from main import app

PACKAGE = Path(users_package.__file__).parent

# Every operation under /users/me as routers/users.py served it before the
# split: method, path and operation id, read from the OpenAPI the frontend's
# API types are generated from.
ROUTES = (
    "DELETE /api/v1/users/me delete_me_api_v1_users_me_delete",
    "DELETE /api/v1/users/me/activities/{date}/{slot} delete_manual_activity_api_v1_users_me_activities__date___slot__delete",
    "DELETE /api/v1/users/me/ai-key delete_ai_key_api_v1_users_me_ai_key_delete",
    "DELETE /api/v1/users/me/athlete-hypotheses/{hypothesis_id} delete_athlete_hypothesis_api_v1_users_me_athlete_hypotheses__hypothesis_id__delete",
    "DELETE /api/v1/users/me/athlete-memory-facts/{fact_id} delete_athlete_memory_fact_api_v1_users_me_athlete_memory_facts__fact_id__delete",
    "DELETE /api/v1/users/me/availability-constraints/{constraint_id} remove_availability_constraint_api_v1_users_me_availability_constraints__constraint_id__delete",
    "DELETE /api/v1/users/me/chat clear_chat_api_v1_users_me_chat_delete",
    "DELETE /api/v1/users/me/day-preferences/{prefer}/{over} remove_day_preference_api_v1_users_me_day_preferences__prefer___over__delete",
    "DELETE /api/v1/users/me/goals/{goal_id} remove_goal_api_v1_users_me_goals__goal_id__delete",
    "DELETE /api/v1/users/me/inquiries/{inquiry_id} delete_athlete_inquiry_api_v1_users_me_inquiries__inquiry_id__delete",
    "DELETE /api/v1/users/me/memory clear_all_memory_api_v1_users_me_memory_delete",
    "DELETE /api/v1/users/me/open-questions/{question_id} delete_athlete_open_question_api_v1_users_me_open_questions__question_id__delete",
    "DELETE /api/v1/users/me/predictions/{prediction_id} delete_athlete_prediction_api_v1_users_me_predictions__prediction_id__delete",
    "DELETE /api/v1/users/me/race-events/{event_id} delete_race_event_api_v1_users_me_race_events__event_id__delete",
    "DELETE /api/v1/users/me/states/{state_id} remove_state_api_v1_users_me_states__state_id__delete",
    "DELETE /api/v1/users/me/validation-experiments/{experiment_id} delete_validation_experiment_api_v1_users_me_validation_experiments__experiment_id__delete",
    "GET /api/v1/users/me get_me_api_v1_users_me_get",
    "GET /api/v1/users/me/ai-key/status get_ai_key_status_api_v1_users_me_ai_key_status_get",
    "GET /api/v1/users/me/athlete-context get_athlete_context_api_v1_users_me_athlete_context_get",
    "GET /api/v1/users/me/athlete-hypotheses list_athlete_hypotheses_api_v1_users_me_athlete_hypotheses_get",
    "GET /api/v1/users/me/athlete-memory-facts list_athlete_memory_facts_api_v1_users_me_athlete_memory_facts_get",
    "GET /api/v1/users/me/athlete-model get_athlete_model_api_v1_users_me_athlete_model_get",
    "GET /api/v1/users/me/chat get_chat_api_v1_users_me_chat_get",
    "GET /api/v1/users/me/coach-memory get_coach_memory_api_v1_users_me_coach_memory_get",
    "GET /api/v1/users/me/coach-review-consent get_coach_review_consent_api_v1_users_me_coach_review_consent_get",
    "GET /api/v1/users/me/coach-timeline get_coach_timeline_api_v1_users_me_coach_timeline_get",
    "GET /api/v1/users/me/day-board get_day_board_api_v1_users_me_day_board_get",
    "GET /api/v1/users/me/export export_me_api_v1_users_me_export_get",
    "GET /api/v1/users/me/home-location get_home_location_api_v1_users_me_home_location_get",
    "GET /api/v1/users/me/inquiries list_athlete_inquiries_api_v1_users_me_inquiries_get",
    "GET /api/v1/users/me/memory-export export_memory_api_v1_users_me_memory_export_get",
    "GET /api/v1/users/me/memory-privacy get_memory_privacy_settings_api_v1_users_me_memory_privacy_get",
    "GET /api/v1/users/me/metrics-history get_metrics_history_api_v1_users_me_metrics_history_get",
    "GET /api/v1/users/me/motivation-model get_motivation_model_api_v1_users_me_motivation_model_get",
    "GET /api/v1/users/me/motivation-model/weight-history get_motivation_weight_history_api_v1_users_me_motivation_model_weight_history_get",
    "GET /api/v1/users/me/open-questions list_athlete_open_questions_api_v1_users_me_open_questions_get",
    "GET /api/v1/users/me/plan get_plan_api_v1_users_me_plan_get",
    "GET /api/v1/users/me/plan-history get_plan_history_api_v1_users_me_plan_history_get",
    "GET /api/v1/users/me/plan-history/stats get_plan_history_stats_api_v1_users_me_plan_history_stats_get",
    "GET /api/v1/users/me/predictions list_athlete_predictions_api_v1_users_me_predictions_get",
    "GET /api/v1/users/me/race-events get_race_events_api_v1_users_me_race_events_get",
    "GET /api/v1/users/me/ride-metrics-history get_ride_metrics_history_api_v1_users_me_ride_metrics_history_get",
    "GET /api/v1/users/me/strength/e1rm/{exercise} get_e1rm_trend_api_v1_users_me_strength_e1rm__exercise__get",
    "GET /api/v1/users/me/strength/exercises get_logged_exercises_api_v1_users_me_strength_exercises_get",
    "GET /api/v1/users/me/validation-experiments list_validation_experiments_api_v1_users_me_validation_experiments_get",
    "GET /api/v1/users/me/weather-forecast get_weather_forecast_api_v1_users_me_weather_forecast_get",
    "GET /api/v1/users/me/workouts get_workouts_api_v1_users_me_workouts_get",
    "GET /api/v1/users/me/workouts/{date}/strength get_strength_session_api_v1_users_me_workouts__date__strength_get",
    "PATCH /api/v1/users/me/athlete-hypotheses/{hypothesis_id} update_athlete_hypothesis_api_v1_users_me_athlete_hypotheses__hypothesis_id__patch",
    "PATCH /api/v1/users/me/athlete-memory-facts/{fact_id} correct_athlete_memory_fact_api_v1_users_me_athlete_memory_facts__fact_id__patch",
    "PATCH /api/v1/users/me/open-questions/{question_id} update_athlete_open_question_api_v1_users_me_open_questions__question_id__patch",
    "PATCH /api/v1/users/me/predictions/{prediction_id} update_athlete_prediction_api_v1_users_me_predictions__prediction_id__patch",
    "PATCH /api/v1/users/me/ride-feedback/{strava_activity_id} save_ride_feedback_api_v1_users_me_ride_feedback__strava_activity_id__patch",
    "PATCH /api/v1/users/me/ride-purpose/{strava_activity_id} answer_ride_purpose_api_v1_users_me_ride_purpose__strava_activity_id__patch",
    "PATCH /api/v1/users/me/validation-experiments/{experiment_id} update_validation_experiment_api_v1_users_me_validation_experiments__experiment_id__patch",
    "POST /api/v1/users/me/activities add_manual_activity_api_v1_users_me_activities_post",
    "POST /api/v1/users/me/ai-key/test test_ai_key_api_v1_users_me_ai_key_test_post",
    "POST /api/v1/users/me/athlete-memory-facts observe_athlete_memory_fact_api_v1_users_me_athlete_memory_facts_post",
    "POST /api/v1/users/me/chat add_chat_message_api_v1_users_me_chat_post",
    # Added after the split: an answer to an offer under a reply (ai-trainer-ops#338 T1).
    "POST /api/v1/users/me/chat/{message_id}/offers/{kind}/resolve resolve_chat_offer_api_v1_users_me_chat__message_id__offers__kind__resolve_post",
    "POST /api/v1/users/me/estimate-ftp estimate_ftp_api_v1_users_me_estimate_ftp_post",
    "POST /api/v1/users/me/inquiries/{inquiry_id}/answer answer_athlete_inquiry_api_v1_users_me_inquiries__inquiry_id__answer_post",
    "POST /api/v1/users/me/inquiries/{inquiry_id}/dismiss dismiss_athlete_inquiry_api_v1_users_me_inquiries__inquiry_id__dismiss_post",
    "POST /api/v1/users/me/race-events create_race_event_api_v1_users_me_race_events_post",
    "POST /api/v1/users/me/race/clear clear_profile_race_api_v1_users_me_race_clear_post",
    "POST /api/v1/users/me/recalculate-metrics recalculate_metrics_api_v1_users_me_recalculate_metrics_post",
    "POST /api/v1/users/me/upload-fit upload_fit_file_api_v1_users_me_upload_fit_post",
    "POST /api/v1/users/me/upload-fit/bulk upload_fit_files_bulk_api_v1_users_me_upload_fit_bulk_post",
    "POST /api/v1/users/me/workouts/{date} save_workout_api_v1_users_me_workouts__date__post",
    "POST /api/v1/users/me/wrong-flags flag_wrong_api_v1_users_me_wrong_flags_post",
    "PUT /api/v1/users/me update_me_api_v1_users_me_put",
    "PUT /api/v1/users/me/activities/{date}/{slot} update_manual_activity_api_v1_users_me_activities__date___slot__put",
    "PUT /api/v1/users/me/ai-key save_ai_key_api_v1_users_me_ai_key_put",
    "PUT /api/v1/users/me/athlete-context save_athlete_context_api_v1_users_me_athlete_context_put",
    "PUT /api/v1/users/me/athlete-model save_athlete_model_api_v1_users_me_athlete_model_put",
    "PUT /api/v1/users/me/coach-memory save_coach_memory_api_v1_users_me_coach_memory_put",
    "PUT /api/v1/users/me/coach-review-consent update_coach_review_consent_api_v1_users_me_coach_review_consent_put",
    "PUT /api/v1/users/me/home-location save_home_location_api_v1_users_me_home_location_put",
    "PUT /api/v1/users/me/memory-privacy update_memory_privacy_settings_api_v1_users_me_memory_privacy_put",
    "PUT /api/v1/users/me/motivation-model save_motivation_model_api_v1_users_me_motivation_model_put",
    "PUT /api/v1/users/me/plan save_plan_api_v1_users_me_plan_put",
    "PUT /api/v1/users/me/race-events/{event_id} update_race_event_api_v1_users_me_race_events__event_id__put",
)


def _routes():
    return [
        route
        for info in pkgutil.iter_modules([str(PACKAGE)])
        for route in importlib.import_module(f"routers.users.{info.name}").router.routes
    ]


def test_the_split_keeps_every_route_and_its_name():
    served = sorted(
        f"{method.upper()} {path} {operation['operationId']}"
        for path, item in app.openapi()["paths"].items()
        if path == "/api/v1/users/me" or path.startswith("/api/v1/users/me/")
        for method, operation in item.items()
    )

    assert served == sorted(ROUTES)


def test_no_route_can_answer_for_another():
    """A request goes to the first route that matches it, and the package
    includes its modules in an order of its own. That is safe while no route
    matches another one's path: a parameter where another route has a fixed
    segment would take that route's requests, depending on the order."""
    routes = _routes()
    assert len(routes) == len(ROUTES)

    clashes = []
    for route in routes:
        sample = route.path
        for name in route.param_convertors:
            sample = re.sub(r"\{" + name + r"(:[^}]*)?\}", "1", sample)
        clashes += [
            f"{other.path} matches {sample}"
            for other in routes
            if other is not route
            and other.methods & route.methods
            and other.path_regex.match(sample)
        ]

    assert clashes == []


def test_no_module_is_above_600_lines():
    sizes = {
        path.name: len(path.read_text().splitlines()) for path in PACKAGE.glob("*.py")
    }

    assert len(sizes) > 2
    assert {name: size for name, size in sizes.items() if size > 600} == {}
