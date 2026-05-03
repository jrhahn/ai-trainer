# AI Coach Improvements

Goal: make the in-app trainer feel more like an adaptive coach conversation and less like a static ride classifier. Work through these tasks in order. Each task should leave the app in a working state and include focused tests.

## Task 1: Stop Classifying Short Or Uncertain Rides As Endurance

Problem: `classify_ride_purpose()` currently returns `endurance` when watts/FTP are missing and also labels very short steady rides as endurance if average power falls in Z2. This makes short spins, commutes, warmups, aborted rides, and data-poor rides look like meaningful aerobic endurance sessions.

Implementation steps:

- Update `backend/services/analysis.py`.
- Add minimum-duration handling before average-power classification.
- Suggested categories:
  - `unknown` when there is not enough stream data or FTP is missing.
  - `short_easy_spin` for short low-intensity rides.
  - `short_hard_effort` for short high-intensity rides.
  - keep `recovery`, `endurance`, `tempo`, and interval categories for sufficiently long rides.
- Suggested thresholds:
  - under 10 minutes: `unknown` unless there are clear high-power efforts.
  - 10-25 minutes with low/medium intensity: `short_easy_spin`.
  - 10-25 minutes with high NP/avg power or intervals: `short_hard_effort`.
  - only allow `endurance` when duration is long enough to plausibly build aerobic base, e.g. at least 30 minutes.
- Include duration in `build_ride_analysis()` so the LLM sees why a ride is short or uncertain.
- Update `build_rule_based_summary()` labels for new categories.

Acceptance criteria:

- A 5-minute ride with missing watts returns `unknown`, not `endurance`.
- A 15-minute easy ride returns `short_easy_spin`, not `endurance`.
- A 20-minute hard ride returns `short_hard_effort` or an interval category when intervals are clearly detected.
- A 60-minute Z2 ride still returns `endurance`.
- Existing interval classification behavior remains intact.

Tests:

- Add/adjust unit tests in `backend/tests/test_ai_service_unit.py`.
- Run `uv run pytest backend/tests/test_ai_service_unit.py`.

## Task 2: Store Ride Classification Confidence And Reason

Problem: the app stores a single ride purpose string, but the coach needs to know whether the classification is reliable. A vague or short ride should be described with uncertainty instead of being treated as a full training session.

Implementation steps:

- Extend ride analysis output to include:
  - `ride_category`
  - `classification_confidence`: `high`, `medium`, or `low`
  - `classification_reason`: one short machine-readable sentence
  - `duration_seconds`
- Decide whether this should be stored directly on `ride_metrics` or derived in the prompt context. Prefer storing if it improves reuse across chat, dashboard, and recalculation.
- If storing:
  - add columns to `backend/models.py`.
  - add an Alembic migration.
  - update CRUD upsert paths.
  - update schemas if the frontend needs the values.
- Include confidence/reason in `ride_metrics_context_section()`.

Acceptance criteria:

- Recent ride history shown to the LLM includes whether a ride classification is reliable.
- Short/data-poor rides are not silently presented as normal endurance work.
- No existing ride-metric endpoints break.

Tests:

- Add migration/model tests if columns are added.
- Add prompt-context tests for `ride_metrics_context_section()`.
- Run relevant backend tests.

## Task 3: Review All Newly Added Rides Together

Problem: the current coaching flow is biased toward reviewing the latest ride. That fails when the athlete does not open or use the coach after every ride. If three rides were added since the last coach interaction, the coach should review the batch as a small training block, not pretend only the newest ride matters.

Implementation steps:

- Identify where newly imported Strava rides are known in `backend/routers/ai.py` and the ride-metrics pipeline.
- Track which rides have already been included in a coach review. Possible approaches:
  - add `coach_reviewed_at` or `review_batch_id` to `ride_metrics`;
  - track the user's last coach-reviewed Strava activity/date;
  - use the import job result if it reliably lists newly added rides.
- Add a batch review service that accepts all newly added ride metrics since the last review.
- The batch review should summarize:
  - what changed across the new rides;
  - whether each ride matched the plan;
  - whether any ride was short, ambiguous, skipped, over-paced, or especially strong;
  - combined fatigue/load impact from the batch;
  - what the next planned session should be.
- For ambiguous rides inside the batch, ask for concise subjective feedback rather than over-interpreting them.
- Persist the review result in a way the dashboard/chat can show it once, then mark those rides as reviewed.
- Keep single-ride behavior as a special case of batch review with one newly added ride.

Acceptance criteria:

- If one new ride is imported, behavior remains equivalent to post-ride review.
- If multiple rides are imported since the last coach review, the coach reviews all of them together.
- The coach does not ignore older newly added rides just because a newer ride exists.
- The review explains the combined implication for the next session.
- Reviewed rides are not repeatedly presented as newly added on every login.

Tests:

- Add backend tests for one new ride, multiple new rides, and no unreviewed rides.
- Add a router/service test ensuring all newly added ride metrics are included in the LLM prompt.
- Add tests for marking rides as reviewed after the coach review is generated.

## Task 4: Make Post-Ride Feedback A Dialogue, Not A Final Verdict

Problem: after a new ride, the app often produces a one-shot coach note. ChatGPT felt better because the user described how the ride felt, then the coach adapted the next ride.

Implementation steps:

- Update the auto feedback prompt in `backend/services/prompts.py`.
- For short, low-confidence, or ambiguous rides, instruct the coach to ask one concise follow-up question instead of over-interpreting the ride.
- Add a structured response field for follow-up intent, for example:
  - `needs_athlete_feedback: boolean`
  - `follow_up_question: string | null`
  - `suggested_feedback_tags: string[]`
- Update `rate_completed_workout()` response handling in `backend/services/ai_service.py`.
- Update API schemas if this data will be returned to the frontend.
- Keep existing `feedback` text for backward compatibility.

Acceptance criteria:

- If a ride is short/ambiguous, the coach says something like: "This looks like a short easy spin rather than a full endurance session. Was it intentional recovery, a commute, or did you cut it short?"
- The app does not confidently prescribe the next hard workout from weak data.
- Normal high-confidence workouts still get direct feedback.

Tests:

- Add unit tests that assert the prompt includes follow-up rules.
- Add service tests for parsing optional follow-up fields.

## Task 5: Add A Lightweight Subjective Ride Feedback Loop

Problem: objective Strava streams do not tell the coach how the athlete felt. The direct ChatGPT workflow worked because the user always gave subjective context.

Implementation steps:

- Add a frontend post-ride feedback UI for recent rides missing `user_note`.
- Keep it lightweight:
  - perceived effort/RPE
  - legs: fresh/normal/heavy
  - intent: planned workout/recovery/commute/free ride/aborted
  - optional note
- Add or reuse a backend endpoint to update the `RideMetric.user_note`.
- Consider storing structured feedback separately if plain notes become too limiting.
- After saving subjective feedback, refresh coach memory or trigger a coach response for the next session.

Acceptance criteria:

- A user can quickly explain what a new ride was.
- The note appears in `ride_metrics_context_section()` as `Athlete: "..."`
- Future `ask-trainer` responses use the subjective feedback naturally.

Tests:

- Add backend CRUD/API tests for saving ride feedback.
- Add frontend component tests for the feedback form.

## Task 6: Generate A Concrete Next-Ride Recommendation After Feedback

Problem: the app can adapt plans, but it does not consistently behave like "I did this; what should I do next?"

Implementation steps:

- Add a service flow that takes:
  - newly reviewed ride batch, or the latest ride metric when there is only one new ride
  - subjective feedback
  - current training plan
  - coach memory
  - rider assessment
  - current CTL/ATL/TSB
- Ask the LLM for:
  - `response`: natural coach message
  - `next_session_recommendation`: short explanation
  - `planUpdates`: only when the next planned session should actually change
- Reuse existing `ask_trainer` plan update application logic where possible.
- Make the coach distinguish between:
  - "keep the next ride as planned"
  - "do the same ride but easier"
  - "replace with recovery/rest"
  - "move intensity later"

Acceptance criteria:

- After a user gives ride feedback for one ride or a batch of new rides, the app can immediately say what to do next.
- If the plan changes, the plan card updates.
- If no change is needed, the coach still explains why.

Tests:

- Add backend service tests with mocked LLM output.
- Add router tests that plan updates are persisted.

## Task 7: Add Optional Outlook For The Next Few Sessions

Problem: the user liked asking ChatGPT for an outlook on upcoming units. The app should support this explicitly without always overloading every response.

Implementation steps:

- Extend the coach prompt so that when the user asks for an outlook, the coach summarizes the next 3-5 sessions.
- Include current fatigue/readiness and recent ride feedback in the reasoning.
- Do not rewrite the plan unless the user asks or the coach detects a clear recovery issue.
- Add a small frontend quick action, for example "Show outlook", that sends a normal `ask-trainer` message.

Acceptance criteria:

- "Show outlook" returns a natural explanation of upcoming sessions and why they are ordered that way.
- No plan updates are applied unless explicitly returned.

Tests:

- Add chat prompt tests for outlook behavior.
- Add frontend test for the quick action if implemented.

## Task 8: Improve Coach Memory For Training Preferences And Patterns

Problem: memory is currently generic. To feel like ChatGPT, the coach should remember recurring training context: preferred ride days, time constraints, how the athlete responds to intensity, and common failure modes.

Implementation steps:

- Update `update_memory_system()` in `backend/services/prompts.py`.
- Add explicit memory categories:
  - schedule constraints
  - subjective fatigue patterns
  - preferred workout types
  - recurring issues such as over-pacing endurance rides
  - recent FTP/current target context
  - race/event priorities
- Keep memory concise and current.
- Avoid storing transient details unless they affect future coaching.

Acceptance criteria:

- If the athlete repeatedly says weekday time is limited, future plans and chat reflect that.
- If the athlete often over-rides endurance days, the coach mentions pacing restraint naturally.

Tests:

- Add/update memory prompt tests.

## Task 9: Revisit Model And Provider Defaults

Problem: direct ChatGPT may be using a stronger conversational model than the app. The app currently defaults to Gemini when a Gemini key is configured and OpenAI uses `gpt-4o-mini`.

Implementation steps:

- Make the coach model configurable via environment variables.
- Separate model defaults for:
  - classification/routing
  - JSON-only plan generation
  - conversational coach chat
  - post-ride feedback
- Prefer a stronger model for `ask-trainer` and post-ride coaching than for cheap classification.
- Keep tests provider-agnostic.

Acceptance criteria:

- The selected model can be changed without code edits.
- Cheap tasks can still use a fast model.
- Coach chat can use a stronger model when configured.

Tests:

- Add tests for config defaults and provider model selection.

## Task 10: Tighten Prompts Around Natural Coaching Behavior

Problem: the prompts already include a warm persona, but they still push concise JSON outputs and can sound like a generated card.

Implementation steps:

- Keep structured JSON for machine handling, but improve `response` rules:
  - acknowledge uncertainty when data is weak.
  - reference one concrete user/ride detail.
  - give one clear next action.
  - ask at most one follow-up question when needed.
  - avoid pretending a short ride created meaningful endurance adaptation.
- Add examples for:
  - short recovery spin
  - over-paced endurance ride
  - missed/aborted workout
  - successful interval day
  - athlete asking for outlook

Acceptance criteria:

- Responses sound like a coach continuing a conversation, not a static analysis card.
- Weak data leads to humility and a question.
- Strong data leads to specific advice.

Tests:

- Add prompt snapshot/string tests for the new behavioral rules.

## Task 11: End-To-End Verification Scenario

Problem: the improvements should work together, not just pass isolated unit tests.

Implementation steps:

- Create or document an end-to-end test scenario:
  1. Athlete has a current plan with tomorrow's endurance ride.
  2. Athlete does not use the coach for several days.
  3. Three new rides sync from Strava at once: one normal endurance ride, one over-paced tempo-like ride, and one 15-minute easy ride.
  4. The short ride is classified as `short_easy_spin` with low/medium confidence.
  5. Coach reviews all three newly added rides together and explains the combined training impact.
  6. Coach asks whether the short ride was recovery, commute, or an aborted workout.
  7. Athlete says: "I cut it short, legs felt heavy."
  8. App stores the subjective note.
  9. Coach recommends reducing or replacing the next ride.
  10. Plan update is applied only if warranted.
  11. Athlete asks for outlook.
  12. Coach explains the next 3-5 sessions naturally.

Acceptance criteria:

- This scenario can be run manually or automated.
- The final behavior matches the desired ChatGPT-like loop: observe, ask, learn, adapt, explain.

Tests:

- Add integration tests where practical.
- Add manual QA notes if full automation is too expensive.

## Implementation Order

1. Task 1: classification fixes.
2. Task 2: confidence/reason context.
3. Task 3: batch review of newly added rides.
4. Task 4: post-ride follow-up fields.
5. Task 5: subjective feedback UI/API.
6. Task 6: next-ride recommendation flow.
7. Task 7: outlook quick action.
8. Task 8: memory improvements.
9. Task 9: model/provider configuration.
10. Task 10: prompt tone refinements.
11. Task 11: end-to-end verification.

Do not skip Task 1. If ride classification remains wrong, the coach will keep building natural-sounding feedback on bad assumptions.
