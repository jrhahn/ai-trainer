/**
 * The app's data as the server sends it and the app holds it: the plan, the
 * profile, the rides, the races (ai-trainer-ops#275). Types only, imported by
 * the store, the services and the utils alike, so none of them imports another
 * for its types; the store re-exports them for the components.
 */

/** A session the athlete reports in the chat, offered for entry under the
 *  reply (ai-trainer-ops#166); `utils/reportedSession` reads it off their words. */
export interface ReportedSession {
  date: string
  sport: 'cycling' | 'running' | 'strength'
  durationMinutes?: number
  distanceKm?: number
  normalizedPower?: number
  averageHeartRate?: number
}

export type AiProvider = 'openai' | 'gemini'

export type Sport = 'cycling' | 'running' | 'strength'
export type Weekday = 'monday' | 'tuesday' | 'wednesday' | 'thursday' | 'friday' | 'saturday' | 'sunday'

/** A rebuild of the plan the app made by itself (ai-trainer-ops#206). */
export interface PlanRebuild {
  /** ISO time of the rebuild. */
  at: string
  /** Codes from ``plan_inputs.REASONS``. */
  reasons: string[]
}

/** A weekday the athlete prefers over another (ai-trainer-ops#204). */
export interface DayPreference {
  prefer: Weekday
  over: Weekday
}

export interface UserProfile {
  name: string
  email: string
  bikeType: 'road' | 'mtb' | 'gravel' | 'other'
  trainingGoal: 'race' | 'general_fitness'
  raceDate?: string
  raceDescription?: string
  weeklyHours?: number
  /** Asked at onboarding (ai-trainer-ops#43). Undefined means not stated. */
  sports?: Sport[]
  trainingDays?: Weekday[]
  followsTrainingPlan: boolean
  maxHeartRate?: number
  restingHeartRate?: number
  currentFTP?: number
  /** Running threshold pace in seconds per kilometre — what FTP is to the bike
   * (#716). Undefined means the athlete has not set one, and their runs are
   * priced against the Critical Speed fit over their history instead. */
  thresholdPaceSecondsPerKm?: number
  /** Body weight in kilograms, optional (ai-trainer-ops#188): what a climb
   * target in W/kg needs. Undefined means the athlete has not entered one. */
  weightKg?: number
  /** Weekdays preferred over others, said in chat (ai-trainer-ops#204). */
  dayPreferences?: DayPreference[]
  consumedTokens?: number
  /** None until the athlete picks one (ai-trainer-ops#214). */
  fitnessLevel: 'beginner' | 'intermediate' | 'advanced' | null
}

export interface WorkoutFeedback {
  actualDurationMinutes: number
  averagePower?: number
  averageHeartRate?: number
  peakPower?: number
  /** 0 when nobody reported one: a file the import brought in (ai-trainer-ops#311). */
  perceivedEffort: 0 | 1 | 2 | 3 | 4 | 5
  notes: string
  completedAt: string
  /** The sport the session was logged in; the server always says (#714). */
  sport?: string
  /** How far it went, when the athlete said (ai-trainer-ops#47). `null` is
   *  "they did not say", which is not zero kilometres. */
  distanceKm?: number | null
  /** The head unit's normalized power, when the athlete said (ai-trainer-ops#169). */
  normalizedPower?: number | null
}

export interface TrainingDay {
  date: string
  /**
   * Ordered position of this session within its date (#496). A date may hold
   * more than one session (AM yoga + PM endurance), so `(date, slot)` — not the
   * date alone — identifies a session. Absent on legacy single-session days and
   * read as 0; use utils/planSessions helpers rather than reading it directly.
   */
  slot?: number
  /** Free-text when-in-the-day hint ("am", "pm", "18:30"); display only. */
  timeOfDay?: string
  /**
   * Which sport the session prescribes (#710). `workoutType` says what the
   * session is *for*; this says what the athlete actually does. Absent on every
   * day written before the field existed, and on every cycling day — the
   * backend omits the default so storage stays byte-stable. Read it through
   * utils/planSport, never directly, so a missing value reads as cycling.
   */
  sport?: string
  workoutType: 'rest' | 'endurance' | 'intervals' | 'tempo' | 'race' | 'recovery' | 'strength'
  title: string
  description: string
  durationMinutes: number
  /**
   * Optional planned-duration window (#368). A single value is the degenerate
   * window durationMinMinutes === durationMaxMinutes; when both are set,
   * durationMinutes is the midpoint. Use utils/planDuration helpers to read them.
   */
  durationMinMinutes?: number
  durationMaxMinutes?: number
  targetPower?: { low: number; high: number }
  targetHeartRate?: { low: number; high: number }
  intervals?: Array<{ duration: number; power?: number; heartRate?: number; pace?: string; rest: number }>
  /**
   * What to lift on a strength session (#714). The gym's counterpart to
   * `intervals`, and deliberately a separate field: an interval is a duration at
   * a power, a set is reps at a load.
   *
   * Intensity is `rir` (reps in reserve) by preference — the plan cannot know
   * what a given weight will feel like on the day, so RIR is the prescription
   * the athlete can execute honestly whether they are fresh or tired.
   * `percentE1rm` is the alternative for a percentage-based block. The backend's
   * persist gate keeps at most one of the two, so both are never set together.
   * Absent on every day that is not a prescribed gym session.
   */
  strengthExercises?: Array<{
    exercise: string
    sets: number
    reps: number
    rir?: number
    percentE1rm?: number
  }>
  completed?: boolean
  feedback?: WorkoutFeedback
  coachFeedback?: string
  workoutPurpose?: string
  keyFocusPoints?: string[]
  /** Hard planning priors this day still breaks (ai-trainer-ops#87), as keys. */
  priorNotes?: string[]
  /**
   * Server-set marker of which trigger last set this day. "user" means a manual
   * edit / coach-chat change, which pins the day against automated overwrites
   * (see backend services/plan_pipeline.py). Read-only from the client.
   */
  source?: string
}

export interface RaceEvent {
  id: string
  date: string
  startTime?: string | null
  distanceKm: number
  elevationM: number
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  timestamp: string
  planUpdateCount?: number
  sources?: Array<{ title: string; doi?: string; url?: string; sourceType?: string }>
  failedUserMessage?: string
  physiologyRationale?: string
  contextRationale?: string
  objectiveRationale?: string
  // Live only, never stored with the chat: the confirm step for what this
  // reply's turn captured or proposes (ai-trainer-ops#65, #71).
  capturedGoals?: Array<{ id: string; label: string }>
  capturedStates?: Array<{ id: string; label: string; removable: boolean }>
  capturedConstraints?: Array<{ id: string; label: string; removable: boolean }>
  // Live only: a day preference the chat kept (ai-trainer-ops#204).
  capturedDayPreferences?: DayPreference[]
  raceProposal?: { raceDate?: string | null; raceDescription?: string | null; calendarRaces?: number } | null
  // A race the athlete said is coming, offered for the calendar (ai-trainer-ops#175),
  // and the race it replaces when they said it moves.
  raceOffer?: {
    date: string
    replaces?: { date: string; kind: 'profile' | 'calendar'; eventId?: string | null } | null
  } | null
  profileProposal?: {
    weeklyHours?: number | null
    trainingDays?: Weekday[] | null
    sports?: Sport[] | null
    ftp?: number | null
    // ai-trainer-ops#202
    fitnessLevel?: UserProfile['fitnessLevel'] | null
    maxHeartRate?: number | null
    // ai-trainer-ops#204: the days swap in a day generally preferred.
    standingDaySwap?: boolean
  } | null
  // Live only as well: a session the athlete reported in this turn, offered
  // for entry under the reply (ai-trainer-ops#166).
  reportedSession?: ReportedSession
  // Live only as well: the deterministic blocks the coach was given (ops#17).
  coachSaw?: Array<{
    key: string
    title: string
    text: string
    source: { path: string; line: number; symbol: string; ref: string }
  }>
  // The app's own notes under a coach reply, beside its text and stored with it
  // (ai-trainer-ops#262).
  notes?: Array<{ kind: string; text: string }>
}

export interface StravaConnection {
  athleteId: number
  athleteName: string
}

export interface IntervalsConnection {
  athleteId: string
  athleteName?: string | null
  /** The key's first and last four characters, never more (ai-trainer-ops#307). */
  keyHint?: string | null
}

export interface StravaActivity {
  id: number
  // Raw provider id (e.g. intervals.icu `i166933341`) as a string. The numeric
  // `id` above is a 19-digit hash for intervals activities and loses precision
  // as a JS Number; `external_id` is round-trip-safe and is what the backend
  // persists as the activity's external key (#429 Bug B).
  external_id?: string
  name: string
  type: string
  sport_type?: string
  distance: number
  moving_time: number
  elapsed_time: number
  total_elevation_gain: number
  start_date: string
  start_date_local?: string
  start_latlng?: [number, number] | number[]
  average_watts?: number
  weighted_average_watts?: number
  max_watts?: number
  average_heartrate?: number
  max_heartrate?: number
}

export interface HrZone {
  low: number
  high: number
}

export interface HrZones {
  zone1: HrZone
  zone2: HrZone
  zone3: HrZone
  zone4: HrZone
  zone5: HrZone
}

export interface AthleteMetricSnapshot {
  recordedAt: string
  ftp?: number
  thresholdHR?: number
  ctl?: number
  atl?: number
  tsb?: number
  /** The main sport's CTL, which `tsb` is the form of. `ctl` is the rung of
   *  whatever was logged last, about 1 after a rider's gym session. */
  formCtl?: number | null
  source: string
}

export interface RideMetricPoint {
  stravaActivityId: number
  externalActivityId?: string | null
  /** Where the row came from; `logged` is a session the athlete entered (#47). */
  activitySource?: string | null
  activityName?: string | null
  activityStartDatetime?: string | null
  activityDate: string
  sportType: string
  tss?: number
  // Where `tss` came from: 'provider' | 'power' | 'heart_rate' | 'duration'.
  // Sessions without a power meter carry an estimated load rather than a zero,
  // and an estimate shown as a measured TSS is the same mistake one layer up (#579).
  tssSource?: string | null
  ctlAfter?: number
  atlAfter?: number
  tsbAfter?: number
  durationSeconds?: number
  startLat?: number | null
  startLng?: number | null
  weatherTemperatureC?: number | null
  weatherApparentTemperatureC?: number | null
  weatherCondition?: string | null
  weatherCode?: number | null
  weatherWindSpeedKph?: number | null
  weatherPrecipitationMm?: number | null
  weatherSource?: string | null
  avgPowerW?: number
  /** The session's average heart rate; scores heart-rate intervals (ai-trainer-ops#133). */
  avgHeartRateBpm?: number | null
  /** How far it went; with the duration it gives the pace a run is scored on (#133). */
  distanceKm?: number | null
  normalizedPowerW?: number
  coachNote?: string | null
  userNote?: string | null
  feelLegs?: 'fresh' | 'normal' | 'heavy' | null
  labelOverride?: string | null
  ridePurpose?: string | null
  classificationConfidence?: string | null
  /**
   * The "what was this session?" question the coach puts on the activity (#580).
   * `purposeQuestionOpen` is decided on the backend so the rule for what counts
   * as unresolved lives in one place; `purposeQuestionStatus` records how it was
   * closed once it has been.
   */
  purposeQuestionOpen?: boolean
  purposeQuestionStatus?: 'answered' | 'skipped' | null
  /**
   * The compliance badge scored on the backend against the matched plan day.
   * Authoritative when present — the same value the coach sees, so the card and
   * the chat can no longer disagree (#551). Absent for unmatched rides, where
   * the local computation below still applies.
   */
  matchScore?: number | null
  matchLabel?: string | null
  planMatchStatus?: 'unmatched' | 'auto_matched' | 'ambiguous' | 'manual_matched'
  matchedPlanDate?: string | null
  matchedPlanSnapshot?: Partial<TrainingDay> | null
  matchedAt?: string | null
}

/**
 * One day of the upcoming outlook near the athlete's training location (#495).
 * Keyed by ISO date so planned days can look their own forecast up; days beyond
 * the ~16-day horizon are simply absent.
 */
export interface DailyForecast {
  date: string
  condition?: string | null
  weatherCode?: number | null
  temperatureMaxC?: number | null
  temperatureMinC?: number | null
  precipitationMm?: number | null
  windSpeedKph?: number | null
  /** Coaching-relevant summary: very_hot | hot | cold | freezing | rain | … */
  loadFlag?: string | null
}

export interface AthleteHomeLocation {
  latitude: number
  longitude: number
  label: string
  /** user_set beats inferred; latest_ride means nothing is persisted yet. */
  source: string
  confidence: number
  rideCount?: number
  updatedAt?: string | null
}

export interface RiderAssessment {
  riderType: 'timetrial' | 'sprinter' | 'climber' | 'allrounder' | 'endurance'
  notes: string
  hrZones?: HrZones
  rideInsights?: string
  lastRideFeedback?: string
  loginSummary?: string
  /** Coach-authored dashboard status badge (#499) — written by the backend
   * status pipeline, never computed here, so the coach can explain it. */
  trainingStatusLabel?: string
  /** Mirrors `TRAINING_STATUS_TONES` in backend/services/prompts.py. The
   *  dashboard coloured the training summary from it (#623) until the owner's
   *  review of 2026-10-24; it now shows the label in words and ignores this. */
  trainingStatusTone?: 'positive' | 'steady' | 'caution' | 'alert'
  trainingStatusRationale?: string
}
