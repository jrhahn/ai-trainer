/**
 * The react-query keys, in one place (ai-trainer-ops#274). A component that
 * changes a list refreshes it by its key, so the keys are shared: the coach's
 * memory lists, the athlete model, the settings cards.
 */

// The coach's memory (Settings → the coach)
export const ATHLETE_TRAITS_QUERY_KEY = 'athlete-memory-facts'
export const ATHLETE_HYPOTHESES_QUERY_KEY = 'athlete-hypotheses'
export const ATHLETE_OPEN_QUESTIONS_QUERY_KEY = 'athlete-open-questions'
export const VALIDATION_EXPERIMENTS_QUERY_KEY = 'validation-experiments'
export const ATHLETE_PREDICTIONS_QUERY_KEY = 'athlete-predictions'
export const MEMORY_PRIVACY_QUERY_KEY = 'memory-privacy-settings'

// The athlete model and what the coach weighs
export const ATHLETE_MODEL_QUERY_KEY = 'athlete-model'
export const PERFORMANCE_MODEL_QUERY_KEY = 'athlete-performance-model'
export const PERFORMANCE_HYPOTHESES_QUERY_KEY = 'athlete-performance-hypotheses'
export const MOTIVATION_MODEL_QUERY_KEY = 'motivation-model'
export const WEIGHT_HISTORY_QUERY_KEY = 'motivation-weight-history'

// Settings cards
export const HOME_LOCATION_QUERY_KEY = 'athlete-home-location'
export const COACH_REVIEW_CONSENT_QUERY_KEY = 'coach-review-consent'
export const AI_KEY_QUERY_KEY = 'ai-key-status'
export const TOTP_QUERY_KEY = 'totp-status'

// What the model writes for the dashboard when it is missing (ai-trainer-ops#277)
export const LOGIN_SUMMARY_QUERY_KEY = 'login-summary-refresh'
export const TRAINING_STATUS_QUERY_KEY = 'training-status-refresh'

// The coach chat's timeline: plan history, recommendations, pinned questions (ai-trainer-ops#277)
export const COACH_TIMELINE_QUERY_KEY = 'coach-timeline'

// Server state moved out of the store (ai-trainer-ops#273)
export const PLAN_QUERY_KEY = 'training-plan'
export const WORKOUT_LOGS_QUERY_KEY = 'workout-logs'
export const PROFILE_QUERY_KEY = 'user-profile'
