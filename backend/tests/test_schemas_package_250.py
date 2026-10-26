"""schemas.py as a package, one module per concern (ai-trainer-ops#250).

schemas.py held 231 public schemas, helpers and constants in 3,528 lines. Each
concern has a module of its own now, and the package re-exports every public
name, so no caller had to change. These tests hold that: every name schemas.py
had is still on the package, and every public name a module defines is
exported, as that very object and from that one module.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

import schemas

# The public names of schemas.py before the split (develop 620ea7b7). Its logger
# is not one: each module that logs has its own now.
OLD_PUBLIC = """
AIKeySaveRequest AIKeyStatusSchema AccountDeleteRequest AdherencePattern
AnalyseActivitiesRequest AnalyseActivitiesResponse AskTrainerRequest
AskTrainerResponse AthleteAvailabilityConstraintSchema AthleteContextRequest
AthleteContextSchema AthleteExperimentSchema AthleteExperimentStatus
AthleteExperimentUpdateRequest AthleteExperimentsResponse AthleteFactCandidateSchema
AthleteHomeLocationResponse AthleteHomeLocationSchema AthleteHomeLocationUpdate
AthleteHypothesesResponse AthleteHypothesisSchema AthleteHypothesisStatus
AthleteHypothesisUpdateRequest AthleteInquiriesResponse AthleteInquiryAnswerRequest
AthleteInquiryAnswerResponse AthleteInquirySchema AthleteInquiryStatus
AthleteMemoryFactKind AthleteMemoryFactObservationRequest AthleteMemoryFactSchema
AthleteMemoryFactStatus AthleteMemoryFactUpdateRequest AthleteMemoryFactsResponse
AthleteMetricSnapshotSchema AthleteModelRequest AthleteModelSchema
AthleteMotivationModelRequest AthleteMotivationModelSchema AthleteOpenQuestionSchema
AthleteOpenQuestionStatus AthleteOpenQuestionUpdateRequest
AthleteOpenQuestionsResponse AthletePerformanceAttributeSchema
AthletePerformanceLimiterSchema AthletePerformanceModelSchema
AthletePredictionAccuracy AthletePredictionSchema AthletePredictionStatus
AthletePredictionUpdateRequest AthletePredictionsResponse BODY_WEIGHT_MAX_KG
BODY_WEIGHT_MIN_KG BatchReviewRidesResponse CONTEXT_WORKOUT_MAX_CHARS CamelModel
CaptchaChallengeResponse CaptchaSolution CapturedConstraintSchema CapturedGoalSchema
CapturedStateSchema ChatHistoryResponse ChatMessageRequest ChatMessageSchema
CoachMemoryRequest CoachMemoryResponse CoachReviewConsentRequest
CoachReviewConsentSchema CoachSawSchema CodeSourceSchema CriticalPowerSchema
DATE_PATTERN DEFAULT_PLAN_SPORT DailyForecastSchema DayBoardOptionSchema
DayBoardResponse DayPreferenceSchema E1rmPointSchema E1rmTrendResponse
EmailVerificationConfirm EmailVerificationResend EstimateFTPRequest
EstimateFTPResponse ExtractAthleteFactsRequest ExtractAthleteFactsResponse
FTP_MAX_WATTS FTP_MIN_WATTS FitBulkUploadResponse FitUploadFileResult
FitUploadResponse GeneratePlanRequest HeartRateRange ImportFailedActivitySchema
ImportHistoryResponse ImportProgressResponse IntervalsConnectionSchema
LOGGED_ACTIVITY_SOURCE LegalContactResponse LoggedExercisesResponse
LoginChallengeResponse LoginRequest MAX_HR_MAX_BPM MAX_HR_MIN_BPM
ManualActivityRequest ManualActivityResponse ManualActivityUpdate MemoryExportSchema
MemoryPrivacySettingsRequest MemoryPrivacySettingsSchema MetricsHistoryResponse
MotivationComponent MotivationEntrySchema MotivationEntryStatus MotivationSource
MotivationWeightEventSchema NormalizedEmail OnboardingSport PERSONAL_PART_MIN_CHARS
PLAN_DAY_TEXT_MAX_CHARS PLAN_DAY_TITLE_MAX_CHARS PLAN_REQUEST_MAX_DAYS
PasswordChangeRequest PasswordResetConfirm PasswordResetRequest PlanDay
PlanDayUpdateSchema PlanInputsChangedResponse PlanInterval PlanRebuildResponse
PlanRebuildSchema PlanRequest PlanResponse PlanTranslationsResponse
PowerCurveBandSchema PowerCurveResponse PowerDurationPointSchema PowerRange
ProcessPendingFeedbacksRequest ProcessPendingFeedbacksResponse ProfileProposalSchema
RESTING_HR_MAX_BPM RESTING_HR_MIN_BPM RaceEventFeedbackRequest
RaceEventFeedbackResponse RaceEventRequest RaceEventResponse RaceEventsResponse
RaceMovedFromSchema RaceOfferSchema RaceProposalSchema RateWorkoutRequest
RateWorkoutResponse ReadinessRecommendation ReadinessScoreResponse ReasoningItem
RecalculateMetricsRequest RecalculateMetricsResponse RefreshKnowledgeResponse
RefreshLoginSummaryResponse RegisterRequest ReplyNoteSchema ResolveRideMatchRequest
ResolveRideMatchResponse RestResponse RideFeedbackRequest RideFeedbackResponse
RideIdentityRequest RideLabelUpdateSchema RideMetricHistoryResponse RideMetricSchema
RidePurposeAnswerRequest RidePurposeAnswerResponse RiderAssessmentSchema
SeasonPlanResponse SeasonWeekOut SessionResponse SessionRevokeRequest
SessionRevokeResponse SignupConfigResponse StravaActivitySchema
StravaConnectionSchema StrengthPrescription StrengthSessionResponse
StrengthSetResultSchema StrengthSetSchema THRESHOLD_PACE_MAX_SECONDS_PER_KM
THRESHOLD_PACE_MIN_SECONDS_PER_KM TokenResponse TotpConfirmRequest
TotpDisableRequest TotpEnrollResponse TotpLoginRequest TotpRecoveryCodesResponse
TotpStatusResponse TrainingDaySchema TrainingRoiEmphasisSchema
TrainingRoiRecommendationSchema TrainingRoiSystemGainSchema TrainingStatusResponse
TrainingTendency TrainingUtilityOptionSchema TrainingUtilitySchema TrainingWeekday
UpdateProfileRequest UserProfileSchema UserResponse WEAK_PASSWORD_TERMS WEEK_ORDER
WRONG_FLAG_EXCERPT_MAX WRONG_FLAG_HANDLED_NOTE_MAX WRONG_FLAG_NOTE_MAX
WeatherForecastResponse WorkoutFeedbackRequest WorkoutFeedbackSchema
WorkoutLogRequest WrongFlagCreate WrongFlagHandledRequest WrongFlagOut
WrongFlagsResponse canonical_plan_day day_field day_slot day_sport
drop_stale_snake_twins merge_update normalise_plan_sport normalize_slot
password_problem session_key week_ordered without_issue_reference
""".split()


def _modules():
    return [
        importlib.import_module(f"schemas.{info.name}")
        for info in pkgutil.iter_modules(schemas.__path__)
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
    return {name for name in names if not name.startswith("_") and name != "logger"}


def test_every_name_schemas_py_had_is_still_there():
    assert len(OLD_PUBLIC) == 231
    assert sorted(set(OLD_PUBLIC) - set(dir(schemas))) == []


def test_the_package_exports_what_its_modules_define():
    """A schema added to a module but not to the package would be missing
    from ``schemas.<name>``, which is how every caller reaches it."""
    modules = _modules()
    defined = [(module, name) for module in modules for name in _defined(module)]

    assert len(modules) > 10
    assert sorted(name for _, name in defined) == sorted(schemas.__all__)
    assert [
        f"{module.__name__}.{name}"
        for module, name in defined
        if getattr(schemas, name) is not getattr(module, name)
    ] == []
