# Changelog

All notable changes to the backend will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **The planner plans for the athlete the conversation already learned**
  (`services/freshness_allocation.py`, `services/prompts.py`,
  `services/ai_service.py`, `routers/ai.py`, `services/plan_maintenance.py`) —
  the coach and the planner had stopped agreeing. Since #562/#565/#597 the
  conversation layer knows what kind of rider it is talking to; the planner knew
  none of it and got the whole athlete-model stack passed to it nowhere. So it
  asked the only question it could — *can this athlete physiologically handle
  another threshold session?* — was right about the answer, and put one in the
  week 48 h after the last one, in 34 °C, two days before the long off-road day
  the athlete actually trains for. The coach then talked them out of it in chat.
  Both halves were internally consistent; only one was working for the athlete.

  The missing term is not physiological. `recovery_value()` asks what freshness
  is *worth* to this athlete per thing it can be spent on — the long off-road
  days, the next key session, an event — read off the motivation model's own
  weights and the race calendar. It is derived on every call and never stored:
  the moment it becomes a column it is a third source of truth for what the
  athlete wants, drifting against the motivation and performance models. Race
  freshness is worth zero with an empty calendar, the same rule #564 already
  applies to race specificity.

  `allocate_day()` then ranks five kinds of day on the axes the athlete's weight
  vector already spans (no new axis — the weights are learned at one gate in
  #566 or nowhere), minus two named deductions: heat, and the freshness a
  higher-valued demand wanted. A hard session is charged for the weekend it
  costs and never for the freshness it *is* the point of. The adaptation credit
  comes from the existing ROI chain (#478) rather than a constant of its own, so
  there stays one answer to "what pays off". Both deductions survive into the
  output, for the reason #564 keeps its two sub-scores: a day that lost to the
  weather and a day that lost to the weekend lost for different reasons, and a
  coach that cannot say which is not explaining anything.

  The option that makes this issue real is the strength day. It was on no board
  the planner could see, which meant the only way to not schedule intervals was
  to schedule nothing — and a gym hour is real training that costs almost
  nothing in *riding* freshness. On a hot day, 48 h after a hard session, ahead
  of a long weekend ride, it now wins on the athlete's own numbers instead of
  having to be argued for in chat afterwards.

  Two guards decide whether this shipped as an improvement or as a regression
  for everyone. A default athlete's board still ranks the key session first —
  nobody's week is quietly reordered on deploy. And a learned heat tolerance is
  evidence and acts like it: an athlete who demonstrably rides fine in the heat
  keeps their hot-day riding, rather than being nagged off it a second time on
  top of the deduction already applied. Heat only enters the board at all when
  the forecast horizon actually contains a hot day.

  Both plan triggers call one helper, so the "generate a plan" button and the
  nightly regen cannot plan for different people — the same reason plan *writes*
  all go through one pipeline. The block is gated on the memory switch like the
  rest of the durable profile, and is best-effort throughout: a failure to read
  the objective costs the context and degrades to yesterday's physiology-first
  plan, never to no plan. A new athlete gets today's prompt unchanged.

  The planner is also told how to say it. `plan_allocation_rule()` bans "avoid
  unnecessary fatigue", "keep systemic load low" and "protect the adaptation" in
  `workoutPurpose` — true sentences about a stranger — and asks for what the day
  buys *them*, which is the #565 chain applied to the plan rather than to the
  chat. It says in as many words that this is not an instruction to train less:
  the load is not being reduced, it is being spent somewhere else. (#602)

- **Recovery decisions are explained through the rider, not through what to
  avoid** (`services/rider_identity.py`, `services/prompts.py`,
  `routers/ai.py`) — the coach made the right call and gave the wrong reason.
  Every clause after a downgrade was risk management ("keep your systemic
  fatigue completely flat"), so the easier choice read as the absence of
  training — the worst framing for an athlete whose goal is trail quality rather
  than weekly load. Worse, "protecting an easy day from your competitive streak"
  turned a behavioural pattern into a character flaw the athlete was being
  helped to suppress.

  #565 already says explain in the athlete's objective. It lost here, because
  the rules it competes with (`rest_recommendation_rules`) are physiology end to
  end and there was nothing in the prompt to explain *this* athlete's easy day
  with. `PATTERN_RULES` is that missing piece: what each observed pattern means
  on a session meant to be easy. "Rides harder with a target ahead" is a fact
  about the athlete; "the easy ride is not at risk in the first twenty minutes,
  it is at risk the moment someone appears up the road" is the sentence that
  makes the recommendation theirs. The patterns come from the `rider_identity`
  observations #593 accumulates, at the confidence where the coach is allowed to
  rely on them — a second, independent telling, never one remark.

  The riding-style read is deliberately one-directional. Durability is measured
  against something a ride file contains, so "rides best under sustained
  pressure" is supportable; the opposite is not, because
  `_infer_anaerobic_capacity` caps its own confidence at 0.3 and says why —
  nothing confirms a one-minute effort was maximal. Calling someone a puncher on
  that would be the coach inventing an identity, which is the failure #565
  guards against for objectives, so it is never asserted.

  On the banned vocabulary: the issue lists bare words ("don't", "avoid",
  "protect"). Banning those as words would mangle ordinary English and be
  rightly ignored, so the rule names the *phrasing* in full — "protect your
  recovery", "keep systemic load flat", "skipping training" — and gives the
  replacement for each. Same reason #593 spelled out its three questions instead
  of saying "avoid generic ones". `rest_recommendation_rules` now says
  explicitly that it decides whether to rest, not how to say it. (#597)

- **A check that the numbers this codebase decides with are load-bearing**
  (`scripts/check_policy_guards.py`, `tests/policy_guards.py`,
  `tests/policy_guard_plugin.py`, `.github/workflows/ci.yml`) — "rules as data"
  is the house style, and the constants beside those tables are the other half
  of the policy: `CHANNEL_COST` decides which uncertainties are ever put to the
  athlete, `DEFAULT_LOAD_PER_HOUR` decides whether an hour in the gym raises
  TSB. Nothing checked that any of them mattered. A passing suite does not
  answer that question — a test can pass because the behaviour is right, or
  because it asserts on something that would hold whatever the number said.

  For each of the 17 registered constants: move it a long way, run the tests
  that own it, require them to fail. The registry is the reviewable artifact and
  says what each number decides. Perturbation happens on the imported module,
  never on disk, so a killed run cannot leave the tree modified. Its own CI job,
  about two minutes.

  It found real gaps on its first run. `test_an_unknown_sport_still_gets_a_load`
  asserted `load > 0`, which still held with the duration fallback moved to
  1 load/hour — under which an unrecognised sport would be the cheapest thing an
  athlete can do, so importing one would make load quietly disappear (#579's bug
  by a different route). The assertion now pins the fallback between the
  cheapest and dearest known sport. Three further "gaps" were wrong guesses in
  the registry about which tests own a constant, which is the other thing this
  writes down: that ownership was nowhere on record before.

  Not general mutation testing. `mutmut` 3.7 was tried and does not fit: it runs
  from a copied tree, so the whole backend has to be enumerated in `also_copy`
  (fifteen entries, each found by a separate failure); stats collection alone is
  a full suite run, and one 417-line module had not finished after ten minutes.
  Decisively, it cannot tell policy from prose — the modules worth checking are
  mostly `ValueRule(...)` and `SignalRule(...)` declarations, so its survivors
  would be regex and English. (#600)

### Changed

- **A memory observation crosses the trust threshold on its second sighting,
  not its third** — no code change; three docstrings and a test comment said
  three. The accrual is 0.35 on a first sighting and +0.2 after, and
  `ATHLETE_MEMORY_MIN_EVIDENCE` is 2, so the correct number was always two.

## [0.56.0] - 2026-09-08

### Added

- **The plan knows what was agreed, not just what was edited**
  (`models.PlanCommitment`, `services/plan_commitments.py`,
  `services/plan_pipeline.py`, `services/prompts.py`,
  `services/coach_schema.py`, migration `20260820_000001`) — a pin protects the
  day the coach wrote; it does not protect the day that day was written *for*.
  On 2026-09-08 the coach set Wednesday to strength and recorded why in the day
  itself — *"keeping lower body load light ahead of Thursday's interval
  session"* — and hours later an automated write turned Thursday into a second
  strength day. Nothing was violated: Wednesday was pinned, and Thursday had
  never been claimed by anyone.

  The coach can now attach the window its reasoning spans to a plan change. An
  active commitment is injected into every plan-writing prompt — chat, nightly,
  auto-adapt — and enforced at the pipeline gate: an automated write may not
  change a day inside the window, while `coach_chat` and `user_edit` still can,
  because the athlete asking for something different is exactly how an
  arrangement ends. A new commitment supersedes any active one it overlaps,
  since agreeing something new for the same days is changing your mind, not
  adding a second contradictory instruction.

  Shaped after `AthleteAvailabilityConstraint` deliberately — same athlete-scoped
  row with a window, a reason, a source and an `active` flag, enforced in the
  same place. The difference is what it encodes: a constraint says what the
  athlete *can* do, a commitment says what they *decided*, and no structural
  check can infer the second. "Friday is rest because you race Sunday" is
  indistinguishable from any other rest day.

  Fail-safe throughout: an unparseable window or a missing sentence is dropped
  rather than stored, a window longer than 21 days is clamped so one bad
  response cannot freeze a season, a commitment read failure lets the plan write
  proceed, and a storage failure never costs the plan change it accompanied. A
  hard availability constraint still outranks a commitment. (#667)

## [0.55.0] - 2026-09-08

### Changed

- **The nightly job is a coach turn now, not a second planner**
  (`services/plan_maintenance.py`, `services/plan_context.py`,
  `services/ai_service.py`, `services/prompts.py`, `routers/ai.py`) — it asked
  the model for the whole remaining plan back, so every night about eleven days
  returned with freshly generated prose whether or not anything about them had
  changed: **309 applied day changes across 30 batches in 30 days of
  production**, one batch every single night, against the coach's 45. The
  athlete experienced that as their week moving under them, and when they asked
  the coach about it the honest answer was *"the system updated the plan
  automatically in the background"*.

  Four changes. `adapt_training_plan` now returns only the days it is actually
  changing or adding, and the caller folds them into the stored plan, so a day
  the run did not name comes back byte-identical; an empty list is a valid and
  often correct answer. The run receives the same context the chat coach has had
  since #652/#659 — the plan change log and the coherence warnings — built by
  the new shared `plan_context.plan_writer_context` so chat, nightly and
  auto-adapt cannot drift apart. It can extend the rolling window by appending
  days, which nothing else does now that #664 took the regeneration off the sync
  path. And it only runs when there is a reason to: a session missed in the last
  three days, or a window that has grown short. The old condition was true for
  the rest of the plan's life once any single day had been missed.

  Two reasons were deliberately left out. A new availability constraint does not
  need one — constraints are enforced at the pipeline gate on every write, so
  they take effect without an LLM run. Weather does not get one either:
  recognising a *relevant* change needs a stored forecast baseline that does not
  exist, and without it "reacting to weather" would be the old unconditional
  rewrite wearing a reason. (#666)

## [0.54.0] - 2026-09-08

### Added

- **Every plan writer is now checked for coherence, not just the coach chat**
  (`services/plan_coherence.py`, `services/plan_pipeline.py`) — #659 taught the
  coach to notice that two adjacent days hold the same session and #660 that
  strength is a loading day, but both lived in the chat prompt, and
  `find_repeated_sessions` was called from exactly one place. The two triggers
  responsible for 92 % of production plan changes saw neither: we had hardened
  the path that was not causing the problem. On 2026-09-08 the coach pinned
  Wednesday as strength, saying in the day itself that it was protecting
  Thursday's intervals, and three hours later an automated write turned Thursday
  into a second gym day — legal under every rule the pipeline had, since the pin
  covered Wednesday and nobody had claimed Thursday. The check now runs in
  `_enforce_and_persist`, the one gate all writers pass, and an automated write
  that *creates* a collision has the later of the two days handed back to its
  previous content, recorded as a blocked attempt in `plan_day_history`. The
  detector also gained `find_stacked_strength`, because the two colliding
  sessions had different titles and the repeat detector could not see them.

  Three deliberate limits. The gate never invents a replacement — what Thursday
  should be instead is a coaching decision, and inventing one is the #651
  mistake. It never blocks `coach_chat` or `user_edit`, where stacking is a
  choice the athlete made. And it enforces only what is decidable from the plan
  — an identical session twice, or strength twice; whether strength may sit next
  to a threshold session depends on which muscles it loads, so that stays in the
  prompt where the coach can weigh it. (#665)

## [0.53.2] - 2026-09-08

### Fixed

- **Two browser tabs can no longer generate two different training weeks**
  (`services/single_flight.py`, `routers/ai.py`) — `useStravaSync`'s in-flight
  guard is a ref scoped to one hook instance, so a second tab has no idea the
  first is already syncing. On 2026-09-08 that produced two `generate` batches
  one second apart, each a full non-deterministic LLM rewrite of the same
  eleven days, the second undoing the first; the athlete was left with strength
  on the day after the strength day the coach had agreed with them hours
  earlier. `POST /ai/generate-plan` now coalesces concurrent calls per athlete:
  the first runs, the rest wait for its result. Coalescing rather than
  rejecting, because the caller wants the plan — a 409 during onboarding would
  leave a new athlete with an empty week. Failures reach every joined caller, so
  nobody gets a stale success in place of an error, and a caller giving up never
  cancels the write the others depend on (#664).

## [0.53.1] - 2026-09-07

### Fixed

- **Strength now counts as load the plan has to space out** (`services/prompts.py`)
  — `hard_session_spacing_rules()` was the only rule in the whole coach prompt
  that reasoned about how sessions follow one another, and every clause in it was
  scoped to VO2max/HIIT/threshold/sprint. Strength appeared exactly once, and only
  to deny it counts as a rest day: the rule knew strength adds load while never
  saying where that load may not sit, so two strength days in a row passed every
  clause it had. Strength is now treated as a loading day — not two days running,
  and not immediately before or after a hard interval session, since heavy
  lower-body work and hard intervals compete for the same legs. Upper-body/core
  work is called out as the milder case so sessions are not moved for no
  physiological reason, and the whole thing is a strong default rather than a
  block: an arrangement the athlete asked for is kept, with the stacking said out
  loud. The same two clauses were added to `TRAINING_PLAN_PRINCIPLES`, so the
  nightly planner stops writing the collisions the coach would then have to
  unpick (#660).

## [0.53.0] - 2026-09-07

### Added

- **The plan is checked as a week, not one day at a time**
  (`services/plan_coherence.py`, `services/prompts.py`, `services/ai_service.py`,
  `routers/ai.py`) — every guard in the plan pipeline asks whether *this* day may
  be written; none ever asked what the write did to the days either side. So when
  the athlete said they were tired and thinking about the gym, the coach moved
  today onto a 45-minute "Core and Upper Body Strength" session while Tuesday
  already held that identical session — same type, same title, same duration,
  two days running. The write was correct in every respect (`source=coach_chat`,
  `applied=t`); it was the only day anyone looked at. Forty-four seconds later
  the athlete asked the coach to review the coming days, and it read the
  duplicate, described it accurately and endorsed it — reading a plan and
  auditing one are different tasks, and only one of them happens by accident.
  Consecutive days holding the same session are now found deterministically and
  handed to the coach as a stated fact, and the planUpdates rule requires the day
  before and after every edited date to be checked, with the knock-on fix in the
  same array rather than merely mentioned in prose. Detection only: which of the
  two days should change is a coaching decision, and a guard that invented one
  would repeat the #651 mistake (#659).

## [0.52.1] - 2026-09-07

### Fixed

- **The coach writes in the athlete's language again** (`services/prompts.py`,
  `services/ai_service.py`, `services/coach_summary.py`, `crud.py`) — a
  German-speaking athlete was being answered in English: all 44 of the nightly
  plan-change narrations sent since July, and 187 of 251 chat replies. Two
  separate causes. The narration is *unprompted*, so the codebase's only language
  instruction — "reply in the same language the athlete used" — had no athlete
  turn to bind to and defaulted to English every single time; it now receives the
  athlete's own last three messages, quoted solely as a language sample. In chat
  the rule existed but sat in the cached prefix, ~20 English data sections and a
  long English output contract away from where the answer is written; the output
  contract is last precisely because recency decides response format, and
  language is a response-format property like any other, so it is now stated
  there too. The prefix copy stays — it is cached and therefore free. Every other
  athlete-facing prose path was audited and given the same rule: ride batch
  reviews, workout verdicts and their follow-up questions, the dashboard login
  brief, the next-ride recommendation, and the inquiry questions put to the
  athlete directly (#658).

## [0.52.0] - 2026-09-06

### Added

- **The coach can say why the plan changed**
  (`services/prompts.py`, `services/ai_service.py`, `routers/ai.py`) —
  `plan_day_history` has recorded every plan write since #343, complete with the
  trigger and the coach's own stated reason, and the coach chat was never shown
  a line of it. It held the plan's current state and nothing about how it got
  there. So when the athlete asked why today had become a recovery spin — hours
  after the nightly run had shortened it and written down exactly why — the coach
  did the only thing possible with an end state: it invented a plausible reason,
  reaching for the wrong ride to justify a change made overnight by a job it
  could not see.

  `plan_change_history_section` puts that record next to the plan it explains.
  Only applied, athlete-visible changes near today survive the filter: automated
  runs reword description prose nightly, and at a line each those rows would bury
  the one that matters. Blocked proposals are left out on principle — one never
  reached the athlete's plan, so presenting it as a change would be its own lie.
  (#652)

### Fixed

- **Pushback on a checkable fact is no longer conceded**
  (`services/prompts.py`) — `reveal_uncertainty_rule` tells the coach to stop
  defending itself when the athlete pushes back and to let their judgement
  decide. That is right for *how hard should today be?* and wrong for *wasn't
  today a long ride?*, which has an answer in the data. Nothing in the prompt
  separated the two, so the coach applied the deferral rule to a factual dispute:
  it agreed twice, invented a Friday session that had never happened, and
  rewrote the plan on the strength of the disagreement alone.

  `disputed_fact_rule()` draws the line — facts are checked against the record
  and answered from it, including when the record contradicts the athlete;
  judgement calls keep the existing deferral. The athlete was factually right
  that day, which is what hides the defect: the coach agreed *without checking*,
  and would have agreed just as readily had they been wrong. (#652)

## [0.51.2] - 2026-09-06

### Fixed

- **An unattended job can no longer rewrite the day the athlete is about to ride**
  (`services/plan_pipeline.py`) — at 02:00 on a Saturday the nightly maintenance
  run deleted that same Saturday's long ride, eight hours after the coach had
  confirmed it in chat and seven before the athlete got up. The pipeline guards
  days that are user-pinned, completed, or already ridden (#342/#345/#472); at
  02:00 today is none of those by definition, so the one day the athlete had
  already read and shaped their morning around was the most exposed to the
  trigger they can least see coming. `_preserve_today` freezes it for
  `respect_pins` sources only, so the athlete can still change today through the
  coach, activity sync can still mark it completed, and an active hard
  constraint still wins. (#651)

- **A move whose destination is blocked no longer deletes the session**
  (`services/plan_pipeline.py`) — the same run was not deleting that long ride,
  it was moving it to Sunday. Sunday was the athlete's pinned rest day and was
  correctly reverted; Saturday applied anyway, and the ride existed on neither
  day. The guards filter session by session with no notion that two changes in
  one batch belong together, so a correctly-working pin produced the worst
  available outcome. `_revert_orphaned_moves` restores the source when the
  destination was blocked, matching on title, workout type and duration together
  so it fires only for a workout that genuinely relocated. The reverted source is
  then recorded `applied=False`, so the history shows the whole move blocked
  rather than silently half-applied. (#651)

## [0.51.1] - 2026-09-06

### Fixed

- **The coach no longer guesses which day a ride happened on**
  (`services/dates.py`, `services/prompts.py`) — the ride history reached the
  coach as bare ISO dates, so it had to work out for itself how long ago each
  activity was, and for the newest one it reached for the nearest round answer.
  On a Saturday it called the athlete's Thursday ride "yesterday's 118-minute
  effort"; corrected, it fell back on the plan — the only anchored thing in its
  context — and narrated Friday's scheduled-but-unridden recovery spin as a ride
  that had happened.

  Plan days have carried weekday and relative-day anchors since #462/#464, and
  `PLAN_TIMING_GUIDANCE` tells the coach to use them, but that convention only
  ever faced forward. `activity_date_anchor()` is its past-facing twin and always
  states both the weekday and the offset — `2026-09-03 (Thursday, 2 days ago)` —
  because `plan_day_date_labels` labels only ±1 day and "2 days ago" is exactly
  the distinction that was missing. `ACTIVITY_TIMING_RULE` mirrors
  `PLAN_TIMING_GUIDANCE` and adds the part the second wrong sentence needed: a
  past plan day is only what was *scheduled*, never evidence it was ridden.
  (#650)

## [0.51.0] - 2026-08-11

### Added

- **The coach notices the interesting part of a workout message**
  (`services/workout_curiosity.py`, `services/prompts.py`,
  `services/uncertainty_value.py`, `routers/ai.py`, `crud.py`) — after a session
  the coach reliably validated the execution, advised conservatively and asked
  how the legs felt. Nothing in that is wrong and it is worth almost nothing: it
  spends the one follow-up question a reply is allowed on the least informative
  thing available, while the rider the athlete chased down, the week they spent
  ill and the sentence where they said what they love about cycling go
  unremarked.

  `SIGNAL_RULES` reads the four narrative families deterministically — social,
  emotion, behaviour, health — with German patterns alongside English, since a
  capture that only reads English quietly stops working for half the messages.
  Physiology is the fifth and is read *off the numbers* rather than by keyword,
  because "power rose across all three intervals" is a fact about a sequence. The
  rules name a **topic** and never a question: a stored question string would be
  read out verbatim and every athlete would get the same sentence, which is this
  issue's own failure one level up.

  Whether to ask at all goes through the #582 gate on its own `curiosity`
  channel rather than a second bar with its own opinions, and that reuse does
  the central work for free. The gate declines uncertainties the training stream
  settles by itself, so "is their threshold power rising?" scores 0.08 and is
  refused while "is a target up the road what lifts their effort?" scores 1.00
  and is asked. Restraint comes from the same place: a message with numbers and
  no story fires nothing at all.

  Ranking keeps three judgements apart — the gate (worth asking at all), novelty
  (which still has something to reveal, an ordering and never a veto) and
  whether the signal could *explain* what the numbers did, which is what breaks
  the ties the gate genuinely cannot.

  What each signal argues for is written to `athlete_memory_facts` as an
  observation carrying the athlete's own sentence, so the rider-identity picture
  accumulates through the existing confidence-accrual machinery — a second,
  independent sighting to cross the trust threshold — and each telling costs the topic its
  novelty, so the coach moves on to what it does not know yet.

  Two defects in the shared value gate surfaced, both found by a guardrail
  asking whether every rule could ever clear its own gate, and both fixed there
  rather than worked around:

  * **Two of the twelve rules were dead.** A question about pacing *intent* was
    declined for containing the word "pacing", and a fuelling question for
    containing "fitness". A power file records what the athlete did, never
    whether they meant to, and nothing at all about what went into them, so
    `lives_in_the_athlete` gained those patterns.
  * **A negative reducibility rule did not subtract**, although the module has
    always said it does; it only set a flag. When "pacing is recorded" and "why
    they paced that way is not" both fire on one question, letting the first win
    outright made it unaskable. (#593)

- **One gate deciding whether an uncertainty is worth resolving**
  (`services/uncertainty_value.py`, `services/athlete_inquiry.py`,
  `services/hypothesis_generation.py`, `services/open_question_generation.py`,
  `services/experiment_suggestion.py`, `crud.py`) —
  four modules raise uncertainties independently (hypotheses, open questions,
  validation experiments, questions put to the athlete) and each had its own
  entry rule. None of them asked the question that matters: is reducing this
  worth what reducing it costs?

  `value = decision_relevance × (1 − data_share × how much this channel cares)`,
  raised when the value clears the channel's cost. The rules are `VALUE_RULES`,
  declared as data in the idiom of #566, and every verdict is recorded by name —
  including the declines, which had nowhere to be legible before. Relevance is
  scored against the athlete's own utility weights (#564/#566), so "worth
  resolving" means worth resolving *for this athlete*.

  The third part is where a single gate earns its keep: reducibility is not a
  universal penalty. An open question and a hypothesis are *how the coach waits*
  for data, so being answerable by data is why they exist; only channels that
  spend someone's effort pay for it. That distinction was previously English
  inside one prompt, applying to one channel of four. (#582)

- **The uncertainty records have a ceiling and an end**
  (`services/uncertainty_lifecycle.py`, `services/learning_pipeline.py`,
  `crud.py`, `models.py`, migration `20260817_000001`) —
  74 hypotheses had been raised and none had ever been resolved, expired or
  counted. Each channel now has a capacity, a staleness horizon and a written
  expiry reason, and every raise, resolution and expiry is an
  `AthleteUncertaintyEvent`. A record that accumulates without a lifecycle is
  not a memory, it is a leak. (#581)

- **The athlete can answer what an unclassified session was**
  (`services/ride_purpose_question.py`, `schemas.py`, `crud.py`,
  `routers/users.py`) — the coach's question about a session it could not
  classify was prose in a chat message, so the answer went into the reply box
  where nothing was listening. It is now a question with options attached to the
  ride, and the answer writes `classification_confidence = "athlete"` — a
  persistent marker rather than a request-time snapshot, so the next automated
  classification pass cannot quietly overwrite what the athlete said (the #342
  bug class). (#580)

- **Sessions without a power meter stop counting as rest days**
  (`services/training_load.py`, `services/activity_imports.py`,
  `services/analysis.py`, `crud.py`, `models.py`, migration
  `20260815_000001`) — an hour of strength training contributed zero load, so
  TSB *rose* on a day the athlete had trained. Load now resolves down a ladder —
  provider value, power, hrTSS, then duration × an assumed intensity — and
  records which rung answered in `ride_metrics.tss_source`, so a load can always
  say where it came from and a later recalibration knows exactly which rows it
  may touch. (#579)

- **The utility weights are learned by a rule you can read, with a trail you can
  query** (`services/motivation_inference.py`, `models.py`, `crud.py`,
  `routers/users.py`, migration `20260813_000001`) — the weights already moved
  from behaviour; what was missing was the two things that make a learned model
  usable. The rule is `WEIGHT_RULES`, declared as data rather than an if-chain,
  with four properties stated once and holding across all of them: cold start,
  boundedness, normalization at the gate, and pinned components excluded by the
  gate rather than by each rule remembering to check. The trail is
  `AthleteMotivationWeightEvent`, written at the same gate the weights are, so a
  weight cannot move without a row explaining it — including when the athlete
  moves it themselves. Workout ratings and per-ride feel stay unwired on purpose:
  neither has an honest mapping onto a weight yet. (#566)

- **The athlete can answer an ambiguous ride match**
  (`services/ride_matching.py`, `services/plan_pipeline.py`, `routers/ai.py`) — when two
  activities could both be the planned session, the matcher had no way to ask.
  It now surfaces the ambiguity and takes the athlete's answer, rather than
  guessing and being wrong silently. (#574)

### Changed

- **One gate for "which ride does this client mean"** (`crud.py`, `schemas.py`,
  `services/ride_matching.py`, `routers/ai.py`) — the rule that a ride is
  identified by its provider string id, never by the numeric one a browser
  rounds, had been written down since #441 and forgotten at three more
  endpoints. `crud.get_ride_metric_by_identity` is now the only lookup, every
  request model that names a ride inherits `RideIdentityRequest`, and
  `tests/test_ride_identity_gate.py` sweeps both shapes so a new endpoint cannot
  quietly miss it.

  Writing that guardrail found two paths nobody knew were in this class.
  `/ai/next-ride-recommendation` would fall through to "no ride at all" and
  recommend from thin air — silently, which is worse in kind than the 404 the
  athlete at least saw; it is latent, since no UI calls it yet.
  `/ai/rate-workout` passes its id straight to Strava's own endpoint and is
  genuinely exempt, recorded as `identifies_stored_ride = False` with a
  docstring the guardrail requires, because an opt-out should be an argument on
  the record rather than a switch someone flipped. (#592)

- **The split-session window depends on what the session was for**
  (`services/ride_matching.py`) — one global 90-minute gap decided whether two
  recordings were one interrupted session, for every kind of session. They are
  not the same question. For a recovery spin continuity is not the stimulus, so
  two half-hours three hours apart are the session; for an endurance ride
  continuity *is* the stimulus — progressive glycogen depletion, the shift
  toward fat oxidation, durability — so two fresh hours never produce the state
  the adaptation comes from. The endurance window is therefore *tighter* than
  the old global one (45 minutes, the length of a real café stop) and the easy
  window is far looser (8 hours). (#590)

### Fixed

- **The duration-rung assumptions were too timid**
  (`services/training_load.py`, migration `20260818_000001`) — #579 stopped a
  gym session reading as a rest day but only halved the error: an hour of
  strength still lifted TSB by 2.6 points. Strength goes 35 → 55 load/hour,
  cycling 45 → 50, hiking 30 → 35, and the migration reprices the stored rows
  that used the duration rung — only those, which is what `tss_source` was added
  for — then replays CTL/ATL/TSB per athlete. Deliberately not forced to exactly
  zero: a session below the seven-day average genuinely does lower acute
  fatigue, and pinning it flat would be fitting the model to one day. (#591)

- **One yoga class dragged the whole day's rides into ambiguous**
  (`services/ride_matching.py`) — `_is_duration_focused_ride_plan` ended in
  `all(_is_cycling_ride(ride) for ride in rides)` over *every* activity of the
  day, so a yoga session made it false, the duration-matching path was skipped
  entirely, and two road rides plus the yoga all came back ambiguous — including
  asking the yoga which bike ride it had been. The guard that answers "could
  this activity be this session at all?" already existed and was wired only into
  the two-a-day path (#496); the single-session path now asks it too, and an
  activity that was never a candidate carries no verdict about one. (#589)

- **The ride the athlete pointed at could not be found**
  (`services/ride_matching.py`, `schemas.py`) — resolving an ambiguous
  intervals ride answered 404 for two weeks and the card said "Could not save
  that. Try again in a moment", where retrying could never work. #574 added an
  endpoint that identified the ride by the synthesized 63-bit numeric id, which
  a browser rounds; the provider's string id now travels with the request. Third
  recurrence of #441. (#588)

- **Every non-cycling activity classified as unknown/low**
  (`services/activity_identity.py`, `services/analysis.py`,
  `services/summary_pipeline.py`, migration `20260814_000001`) — so the coach asked a
  gym session what its intervals had been. Activities whose sport type already
  says what they are are classified from that with high confidence rather than
  from a power stream they never had. (#578)

- **A failed coach request lost the `llm_calls` rows it had paid for**
  (`services/token_accounting.py`, `database.py`, `services/activity_sync.py`) — `track_llm_usage`
  persisted through the request-scoped session, so an endpoint that raised took
  the usage rows with it on rollback, along with `users.consumed_tokens`. The
  log line and the table disagreed by ~52k input tokens, and the one case a cost
  table most needs to show — a model that has started rejecting every request —
  was the one case invisible in it. Usage from a raising block is now deferred
  onto the session and flushed once the transaction has resolved, on both paths;
  the session dependency is built by a factory the test suite shares, because a
  harness that skips the `finally` cannot see a bug that lives in it. (#560)

### Added

- **The athlete can see and correct what the coach thinks they train for**
  (`frontend/src/components/MotivationModelSettings.tsx`,
  `services/motivation_model.py`) — the last piece of #561 before weight
  learning. The motivation model has been driving planning decisions (#564) and
  the coach's explanations (#565) while being invisible; an inferred objective
  the athlete cannot see or fix is a silent misconfiguration of the entire
  coaching strategy.

  The settings card sits *above* the learned traits on purpose: everything below
  it describes the athlete, this decides what all of it is in service of. It
  shows the primary objective, secondary objectives, constraints, the weight
  balance and the learned modality affinity — each inferred item with its
  confidence and the athlete's own words that produced it, so a guess never
  reads as a fact. Objectives can be rewritten, reordered, added and removed;
  weights can be pinned so learning leaves them alone; a contradicted entry
  surfaces its question rather than being silently dropped.

  Two backend defects surfaced while building against it, both of which broke
  promises this screen makes:

  * **The athlete's ordering was discarded.** `normalize_entries` sorted its
    output by source and confidence — a ranking meant for deciding what the cap
    drops, which also silently undid the sequence the athlete put their
    objectives in. The ranking now decides only what is dropped; survivors come
    back in the order they arrived.
  * **An entry the athlete typed was stored as `inferred`.** A `user_set` write
    kept whatever source each entry carried, defaulting to `inferred` when the
    client sent none — which left the very next inference pass free to overwrite
    what the athlete had just written. Entries in a `user_set` write with no
    stated source are now attributed to the athlete, while an untouched inferred
    entry keeps its provenance so the UI can still show it was a guess.

- **The coach explains its recommendations in the athlete's own objective**
  (`services/prompts.py`, `services/coach_schema.py`, `services/ai_service.py`,
  `frontend/src/components/AIChat.tsx`) — the fourth piece of #561, and the one
  the athlete actually sees.

  The coach already knew *why* a session works; that is what the performance
  model and the ROI recommendation are for. What it lacked was what the session
  is **for**. "Threshold training increases your FTP" is true and, for most
  athletes, beside the point — they do not want a bigger number, they want to
  still enjoy the last descent after four hours. Same prescription, and the
  difference between a coach that sounds like a spreadsheet and one that sounds
  like it knows them.

  `objective_framing_rule()` makes physiology the *mechanism* and the athlete's
  objective the *payoff*, and asks for the chain to end on the payoff. Goals get
  restated the same way: "increase FTP" is a means, and the rule asks what the
  FTP is for. The reply contract gains `objectiveRationale` — one phrase naming
  what the advice buys them — carried through the response schema (with the
  `propertyOrdering` invariant from #558) to a third line in the chat's "Why
  this advice?" disclosure.

  It is deliberately **not** a fourth knowledge-source badge. The three from
  #377 say where a claim came from; this says what it is for, so it gets its own
  treatment and leads the list — it is the part the athlete came for.

  Three rules exist because of specific ways this could go wrong rather than
  fail, and each has a test: it must **never invent an objective** (an athlete
  with none inferred, or one held at low confidence, gets plain physiological
  framing — that is correct behaviour, not a gap); an inferred objective is
  **held loosely** and phrased so the athlete can correct it; and when #564's
  ranking puts a lower-physiology option first, the coach must **say so
  plainly** — dressing a preference-driven pick up as the physiologically
  optimal one would make the whole feature a way of lying more fluently.

  The rule costs ~371 tokens and sits inside the byte-identical cacheable prefix
  (#514/#538), so it is paid for once rather than per turn — a test holds it to
  a budget and holds it in the prefix.

- **The planner scores training options by expected athlete utility, not
  physiological return alone** (`services/training_utility.py`,
  `services/roi_recommendation.py`, `services/prompts.py`, migration
  `20260812_000001`) — the third piece of #561, and the one where the model
  starts changing recommendations.

      utility = Σ wᵢ · scoreᵢ(option)

  The weights are the athlete's own, from #562, learned from what they say and
  do in #563. Nothing here hardcodes what matters; it hardcodes only how to
  *measure* each axis once the athlete has said how much each one counts.

  An option is a **(system, modality)** pair, and that pairing is the point:
  the physiological prescription can be identical while the answer changes —
  `threshold @ road` (physiology 5.0, motivation 3.2) loses to
  `threshold @ mtb` (physiology 4.0, motivation 8.5) for a trail rider. Same
  threshold work, different bike, and the second one is the one that happens.

  Modality is new data. `AthleteMotivationModel` gained `modality_affinity`,
  because the weight vector says *what* an athlete values and cannot say in what
  form — two athletes can both weight enjoyment at 0.45 and mean completely
  different rides by it. Affinities are independent scores in [0, 1], not a
  distribution: liking the MTB does not require disliking the road. They are fed
  by revealed preference in the behavioural pass, which was already counting
  modality swaps and discarding them.

  Three properties the module is built around: **both sub-scores survive into
  the output**, so the coach can say the MTB won on preference rather than
  physiology — presenting a motivation-driven pick as the physiologically
  optimal one would be a lie the numbers contradict, and the prompt says so
  explicitly. **Constraints filter rather than discount** — "avoid unnecessary
  crash risk" is not worth 0.1 utility, it removes options — but a filter that
  empties the board has malfunctioned, so it never leaves nothing to recommend.
  And **a default motivation model changes no recommendation**: an athlete
  nobody has learned anything about yet keeps exactly today's physiology-first
  ordering, and a race-focused athlete is not pushed off the road bike. Both
  halves of that guard are tests, not intentions.

- **The coach learns what the athlete trains for, from what they say and do**
  (`services/motivation_inference.py`, `services/motivation_model.py`,
  `services/learning_pipeline.py`, `routers/ai.py`) — the second piece of #561,
  filling the model #562 created. Two sources, both deterministic:

  *What the athlete says.* "I don't care about races", "I want more trail
  time", "I don't chase FTP" are captured on every coach turn, in the same
  idiom as the weather-preference and home-location captures (#495) and for the
  same reasons: no tokens, testable, bilingual, and every update carries the
  athlete's own sentence as its `source_snippet` — which is what makes an
  inferred objective correctable rather than mysterious (#567).

  *What the athlete does.* A continuous-learning step reads the stored rides:
  riding the MTB when the plan said road, riding off-plan, following the
  structure that was written down, and whether races are on the calendar.

  The hard part is not extraction, it is **restraint** — an objective is what
  every planning decision will be scored against (#564), so a single sentence
  must not be able to redefine it. Three rules enforce that, and all three live
  in the `motivation_model` gate rather than in the inference module, so a
  future third source of evidence inherits them: a statement enters as a
  *secondary* objective capped at `INITIAL_CONFIDENCE_CAP` and only recurrence
  promotes it to primary; behaviour moves a weight by at most
  `MAX_WEIGHT_NUDGE` per run, so it takes weeks of a consistent signal to change
  what the athlete values; and evidence that argues against a stored objective
  marks it `contradicted` for the athlete to settle rather than flipping it —
  the resolution path `AthleteMemoryFact` already uses (#386/#387).

  Underneath all of it, the `user_set` override from #562: none of this can
  overwrite an objective the athlete stated by hand, or move a weight they
  pinned. That is exercised through the real inference path, not just the gate.

- **The athlete's objective is now data** (`models.AthleteMotivationModel`,
  `services/motivation_model.py`, `crud.upsert_athlete_motivation_model`,
  `GET`/`PUT /users/me/motivation-model`, migration
  `20260811_000001`) — the first piece of #561. The coaching system treated
  physiological performance as the optimization objective. For most athletes it
  is only the enabling factor: they train to ride technical descents, to enjoy a
  multi-day adventure, to feel good outdoors. This records what the training is
  *for* — a primary objective, secondary objectives, constraints, and a utility
  weight vector — as a first-class part of the athlete profile.

  The distinction it turns on: **a preference is not an objective**. An
  `AthleteMemoryFact` already said the athlete likes MTB. Nothing said they use
  fitness to maximize enjoyable technical trail riding, and only the second is
  something a planner can score against (#564) or a coach can explain itself
  with (#565).

  `AthleteContext.motivation_drivers` was the closest thing that existed — a
  flat JSON list of free-text drivers with no structure, no provenance, and no
  consumer that treated it as an objective. Its contents migrate into the new
  model as `user_set` secondary objectives and the column is dropped, so
  motivation has exactly one home. Drivers are deliberately *not* promoted to a
  primary objective: "MTB" is a preference, and guessing an objective from one
  is the conflation this exists to end.

  `services/motivation_model.py` is the single normalization gate, in the spirit
  of the `schemas.PlanDay` gate in `plan_pipeline` (#424). Two invariants live
  there because both break silently: the weight vector spans exactly
  `MOTIVATION_COMPONENTS` and sums to 1.0, so a utility score is comparable at
  all; and `user_set` beats `inferred`, so the inference pass in #563 cannot
  overwrite an objective the athlete stated by hand — the stale-snapshot clobber
  class of #342/#345/#346, which `AthleteHomeLocation` guards the same way. A
  third distinction earns its own tests: a field the caller omits means
  "nothing to say", not "delete this", so a partial edit does not blank the
  lists the athlete left alone.

  The model reaches the coach and next-ride prompts through a compact
  `motivation_model_section`, replacing what `motivation_drivers` used to
  contribute rather than dropping it. The wording rules that make the coach
  *explain* its recommendations in the athlete's own terms are #565.

- **The coach's reply is constrained by a schema, and its shape is now a
  metric** (`services/coach_schema.py`, `services/llm.py`,
  `services/metrics.py`, `monitoring/grafana/dashboards/ai-trainer.json`) — the
  root cause behind the prose replies below. `response_mime_type:
  application/json` asks for JSON; a `response_schema` constrains generation to
  it. The coach call now sends one, so a prose reply is prevented rather than
  caught — and prevented on any model, which matters because the same failure
  predates the switch to Flash-Lite (#511): the inquiry path was already
  guarding against it while the coach still ran on Flash.

  A schema is also a new way to lose data, so two things are pinned by tests. A
  field the coach may write today but the schema omits would stop reaching the
  plan tomorrow with no error anywhere — the drift-bug class of #422 and #424 —
  so the `planUpdates` item covers every field of `schemas.PlanDayUpdateSchema`,
  the canonical shape the persist gate accepts, and a test walks that model so a
  field added there fails here instead of quietly going missing. And Gemini
  emits properties in `propertyOrdering` order, so `thinking` is ordered before
  `response`: the prompt asks the model to reason before it answers, and a
  schema that emitted the answer first would delete the chain of thought — a
  quality regression no assertion about JSON shape would catch. Only `thinking`
  and `response` are required; requiring a plan update would push the model into
  inventing edits nobody asked for. The schema is Gemini's dialect, which the
  API only validates at request time, so a test parses it through
  `types.Schema` — building a `GenerateContentConfig` proves nothing, it keeps a
  raw dict as-is. `OpenAIProvider` accepts the argument and ignores it rather
  than failing a call the athlete's own settings routed there.

  `coach_replies_total{contract="json"|"prose"}` counts what actually comes
  back, with a Grafana panel. A prose reply is no longer an error but is still a
  degraded turn — it carries no `planUpdates`, so the coach silently cannot
  change the plan — and without a number, "should the coach run on Flash rather
  than Flash-Lite" stays a matter of opinion. In-process rather than read from a
  table, unlike the cost metrics: the question is a rate over time, which
  `increase()` answers correctly across the restarts a deploy causes. (#558)

- **The coach prompt reports what it is made of** (`services/prompts.py`,
  `services/ai_service.py`, `services/token_accounting.py`) — where the
  prompt's bulk sits has been guessed at twice and gone stale both times: #510
  measured it by hand against a ~21,300-token prompt, then #512 and #513
  changed it, and the prompt is now ~16,200 tokens with roughly 11,900 of that
  athlete data. `ask_trainer_system_sections` returns the prompt as named parts
  in send order (`static`, the athlete sections, `closing`);
  `ask_trainer_system` joins them and produces the byte-identical string it
  always did. Every build emits one line naming each part's size, biggest
  first, so the composition is always current and always about the real
  athlete's real data — a script would have to duplicate the twenty-odd fetches
  the endpoint does and would drift from them. Sizes only, never content: these
  sections are the athlete's health data and it stays behind
  `LOG_LLM_PAYLOADS` (#499). Characters rather than tokens, since counting
  tokens needs the provider's tokeniser; the live API measured 4.8 chars/token
  for this text (#549). The logging lives in `token_accounting` rather than in
  `prompts`, which promises in its own docstring to stay free of I/O. (#556)

### Changed

- **Every usage source is now `<kind>:<kebab-name>`** (`services/token_accounting.py`,
  `routers/ai.py`, `routers/users.py`, the nine step modules,
  `services/activity_sync.py`, `monitoring/grafana/dashboards/ai-trainer.json`) —
  labels had drifted into three shapes: endpoints reported `api:ask_trainer`,
  the sync reported `job:strava-sync`, and nine generator steps reported bare
  names like `coach-narration`. The Grafana panel claimed the prefix told you
  where the spend came from, which held for two thirds of the sources. The kind
  now says what *opened the scope* — `api` an endpoint, `job` a scheduler job,
  `bg` a background task, `step` a reusable unit that can run under any of
  them. That is knowable where the code is written, unlike "what triggered
  this": `coach-narration` is called from two endpoints, a scheduler job and
  the ride-review chain, so a fixed `job:` would have been wrong most of the
  time. Two related fixes fell out: `athlete-inquiry` covered both the nightly
  generation and an athlete answering one, and is now
  `step:athlete-inquiry-generation` / `step:athlete-inquiry-answer`; and the
  `api:` prefix is no longer bolted on inside `_token_usage_scope`, nor the
  sync label built by f-string, so the label in Grafana is a string that exists
  verbatim in the code and grepping for one finds the other. `begin_collection`
  warns on a malformed source, and a test walks every call site with `ast` and
  checks its shape — it catches all fifteen of the old labels when the change
  is reverted. Existing `llm_calls` rows and Prometheus series keep their old
  labels; two days of data was the cheapest this rename will ever be. (#549)

### Fixed

- **A coach reply in prose 500'd the request, and the unit stripper ate words
  out of the coach's German** (`services/ai_service.py`) — two defects in
  `_parse_ai_json`, found from one production incident where three coach
  questions in a row returned "Sorry, something went wrong and I could not
  respond".

  The model was fine every time: all three calls logged `ok=true`, and 692
  characters on 165 output tokens is 4.19 chars/token — the same ratio as the
  replies that worked, so these were *complete* answers, not truncations.
  `repair_json` returns an empty string only for text with no JSON structure at
  all (a truncated object it repairs happily), so what came back was prose.
  `json_mode` is not a guarantee. `ask_trainer` parsed outside any guard, the
  retry loop only ever retried an *already parsed* reply whose `response` field
  was empty, and `routers/ai.py` has no handler for `JSONDecodeError` — so the
  designed 502-with-explanation never fired and the request 500'd with a
  traceback. The athlete lost the turn outright: a failed request never
  persists the question, so it was gone from `chat_messages` too. A prose reply
  is now passed through as the answer, carrying no plan updates because there
  is no structure to read them from. It is deliberately not retried: the same
  question produced prose three times out of three, so another attempt mostly
  buys another 17k input tokens, and the prose already *is* the coach's answer.
  A reply with neither structure nor a word in it still ends as
  `AIResponseFormatError` → 502, the path that already existed. Logged by shape
  only — length and whether it was fenced — because the reply is the athlete's
  health data (#499).

  The second defect was silent and older. The unit stripper added in #516 turns
  `"durationMinutes": 180 minutes` into `180`, but it matched anywhere in the
  document, including inside the coach's own prose: any number followed by a
  word and then a comma lost the word. `"Wir fahren am Samstag 4 Stunden,
  danach Pause"` reached the athlete as `"am Samstag 4, danach Pause"`. Nothing
  caught it — `note_json_repair` compares lengths *after* this substitution, so
  the damage never appeared in a log — and it ran on all ~20 `_parse_ai_json`
  call sites, so plan descriptions, ride notes and login summaries were exposed
  too, not just the chat. The pattern now only matches directly after the colon
  that opens a value, which is the one position #516 was about. (#558)

- **Scheduler metrics were labelled with a name Prometheus owns**
  (`services/metrics.py`, `monitoring/grafana/dashboards/ai-trainer.json`) —
  `scheduler_job_runs_total` and `scheduler_job_duration_seconds` used a `job`
  label. Prometheus writes its own `job` from the scrape config and, with the
  default `honor_labels: false`, renames an exposed one out of the way. In
  production the series arrived as
  `scheduler_job_runs_total{job="ai-trainer-backend", exported_job="activity-sync"}`,
  so the dashboard panels — which grouped by `job` — drew a single line for the
  whole scrape target instead of one per scheduler job. The label is now
  `scheduler_job`, and a test pins it by rendering the exposition and checking
  at the label boundary (`job="` is a substring of `scheduler_job="`, so the
  naive assertion passes either way). Series already stored under
  `exported_job` stay until they age out of the 180-day retention. (#549)

### Added

- **Prometheus metrics and a Grafana dashboard** (`services/metrics.py` (new),
  `main.py`, `services/scheduler.py`, `compose.yml`, `monitoring/`) — the second
  half of #549. `/metrics` exposes two kinds of series from two different
  sources, deliberately. **LLM cost** is read from the `llm_calls` table at
  scrape time: in-process counters would restart at zero on every deploy, which
  is the exact failure the first half fixed, so `llm_calls_total`,
  `llm_tokens_total` (input/output/cached) and `llm_latency_seconds_total`
  are cumulative sums over the rows and survive a restart, and cannot disagree
  with the records they come from. **App health** — `http_requests_total`,
  `http_request_duration_seconds`, `scheduler_job_runs_total`,
  `scheduler_job_duration_seconds`, `db_connection_pool` — is in-process, where
  a reset on restart is what Prometheus expects anyway. Every label is bounded:
  HTTP is labelled by the matched *route template*, so `/api/v1/users/{user_id}`
  is one series and an unmatched path collapses to `route="unmatched"` instead
  of minting one series per thing a scanner probes; `prompt_sha` appears
  nowhere, being effectively unbounded and a SQL question rather than a time
  series. `/metrics` is mounted at the root rather than under `/api` because
  Traefik routes only `PathPrefix(/api)` and `/healthz`, which leaves it
  reachable from the Compose network and from nowhere else. Grafana and
  Prometheus bind to `127.0.0.1` with `traefik.enable=false` and are reached
  through an SSH tunnel (`ssh -L 3000:127.0.0.1:3000`); a publicly reachable
  Grafana is a standing scan target and this deployment has one human. The
  dashboard covers spend by source, calls by task, failures by model, provider
  latency, request rate/errors/p95 by route, scheduler duration and outcomes,
  and the connection pool. Alerting is deliberately not included: useful
  thresholds are hard to guess before there are weeks of data. (#549)

- **Per-call LLM cost records survive the deploy that ends the container**
  (`models.py`, `crud.py`, `services/token_accounting.py`, migration
  `20260809_000001`) — #516 emitted one structured line per provider call, and
  those lines lived in the container's log. Every deploy recreates the
  container, so the record only ever covered "since the last deploy": measured
  right after one, the backend had 32 log lines and zero `LLM call` entries,
  with four deploys in the preceding two days. The durable counters on `users`
  are lifetime totals and cannot say which feature, which model, which prompt or
  when — the questions #510 (did the bill actually come down?) and #538 (what
  would a prompt diet buy?) both need. A row now goes into `llm_calls` for every
  call: task, provider, model, source, the input/output/cached split, latency,
  `json_mode`, ok/error and `prompt_sha`. Records are collected in the
  collection scope rather than written where they happen, because `record_call`
  runs in the synchronous provider layer and holds no session — the scope has to
  be open for the tokens to be billed anyway, so the rows are inserted at the
  same moment and under the same source. Failed calls are stored even though
  they spent nothing: a model that has started rejecting every request is
  exactly what a cost table has to show (#401). Calls made outside every scope
  are still only warned about, since there is no user to attribute them to —
  after #537 there should be none. The log line stays; it is what you read while
  something is going wrong, this is what you query afterwards. No backfill is
  possible: the records this table exists to keep were in logs that are already
  gone. (#549)

### Fixed

- **A manual ride resolve can name which session of the day it was**
  (`schemas.py`, `routers/ai.py`, `services/ride_matching.py`) —
  `resolve_manual_match` has taken a `planned_slot` since two-a-days existed
  (#496), but `ResolveRideMatchRequest` carried no such field, so the router
  could not fill it and `_session_at_slot` always fell back to the date's first
  session. Ambiguity is most likely exactly when a date holds two sessions,
  which was the case the interface could not express. The request now carries an
  optional `plannedSlot`; omitting it keeps the old behaviour, which is what
  every single-session day sends. Adding it exposed a second problem that the
  slot-0-only limitation had been hiding: the resolve unmatched *every* other
  ride of the date, so answering "the evening one was the intervals" would have
  discarded the morning ride's perfectly good match. Only rides still claiming
  the same session are unmatched now — an ambiguous ride of that date, or one
  matched to that same slot. An extra activity keeps its `Additional` /
  `Too much` label, since it was never claiming the session. No UI calls this
  endpoint yet; that half of #547 stays open. (#547)

- **A session recorded in two files is reviewed and counted as one**
  (`services/ride_matching.py`, `services/training_status.py`) — when two
  recordings were accepted as one planned session (#543) both were written as
  matched, but only one was handed on. The coach therefore rated a 60-minute
  half against the 120-minute plan and told the athlete they had done half of
  what they did, and the training status let only the first recording claim the
  session: the other became an "extra activity" the plan never asked for, or —
  worse — was consumed as evidence that a *different* session of that date had
  been ridden. `review_matched_ride_and_adapt` now recovers the whole group from
  what the match already wrote to the rows (same `matched_plan_date` and slot,
  looked up by the ride's own `activity_date` so a manual resolve onto another
  date cannot drag in strangers) and describes the session by its total:
  durations summed, average power weighted by duration rather than averaged,
  notes and perceived effort taken from whichever half the athlete commented on.
  The stream analysis stays scoped to the recording it was built from, which is
  what its streams actually describe. The matcher still returns one
  representative ride per session on purpose — returning both would buy two
  coach notes and two LLM calls for one session — and now says so where the next
  reader would otherwise "fix" it. `mark_matched_days_completed` was never
  affected: it keys on `(date, slot)`, which both halves share. (#545)

- **Two same-day activities only count as one session when they were one**
  (`services/activity_identity.py`, `services/ride_matching.py`) — with several
  activities on a day that has a single planned session, the matcher added their
  durations up and, if the sum fit, marked *both* as the completed session. The
  gate for that only asked whether every activity was cycling and the plan was
  not a structured hard day; it never asked whether the two belonged together.
  A 60-minute commute at 07:30 and a 70-minute ride at 17:30 therefore summed to
  130 minutes and completed a 120-minute endurance day, as two separate
  trainings. Summing now additionally requires the recordings to look like *one*
  session: same activity family (`activity_family`, already the equality gate in
  `are_near_duplicate_activities`) and no gap over 90 minutes between the end of
  one and the start of the next — generous for a café stop, far below the ~9 h
  of a commute pair. A missing start time or duration means no sum, since
  adjacency is a claim about a timeline; all 719 production activities carry
  one, so this only affects fixtures. When the gate does not hold, the existing
  best-fit path takes over unchanged: the closest ride matches and the rest are
  labelled `Additional` or `Too much`. Distinct from duplicate detection, which
  asks whether two files describe the *same* ride and expects them to overlap —
  parts of a split session follow one another. One deliberate test changed with
  this: a four-hour day recorded as a morning road ride plus an afternoon MTB
  ride used to assert both were done, and now pins the same-session reading
  instead. (#543)

### Added

- **The coach prompt's cacheable prefix is now a named, tested thing**
  (`services/prompts.py`, `tests/test_coach_prompt_cache.py` (new),
  `scripts/probe_implicit_cache.py` (new)) — production reported `cached=0` on
  every coach call, ~16,200 input tokens per question at the full rate. #538
  read the varying `prompt_sha` as a varying prefix, but `prompt_sha` hashes the
  *entire* system prompt, which contains today's date and the plan: it varies by
  construction and never could measure the prefix. Measured directly instead,
  the prefix is byte-identical across athletes, dates and every combination of
  optional sections — 20,521 characters, 4,270 tokens as the API counts them —
  so #514's reordering did hold. It is now `prompts.coach_static_prefix()` with
  tests that fail if anything volatile is placed inside it, ahead of it, or if a
  prompt diet takes it under the model's minimum request size. Production can
  say what was charged but never why, so this is the only place the property can
  be checked. `scripts/probe_implicit_cache.py` then answered the question the
  logs could not, against the live API: `gemini-3.5-flash-lite` returned
  `cached=0` on all six calls in both placements, while `gemini-3.5-flash`
  returned 2,030 of 4,284 tokens cached from the second call on. The mechanism
  works and our prefix is fine — the coach model simply does not offer context
  caching, which the pricing page states outright. Nothing to fix in the prompt;
  buying the discount by moving the coach to `flash` would cost about four times
  more, since flash bills input at $1.50/M against flash-lite's $0.30/M. (#538)

- **Per-call LLM cost accounting** (`services/token_accounting.py` (new),
  `services/llm.py`, `crud.py`, `models.py`, migration `20260808_000001`,
  `config.py`) — "which feature is costing me money" was not answerable from
  anything the app recorded; the numbers in #510 were produced by rebuilding
  prompts by hand against the production database. Every provider call now
  emits one line naming the task, the resolved model, the source, the
  input/output/cached split, the latency and a `prompt_sha`, so a cost jump can
  be traced to a prompt edit:

      LLM call task=coach provider=gemini model=gemini-3.5-flash-lite
      source=api:ask_trainer input=12043 output=486 cached=9820 total=12529
      latency_ms=3120 json_mode=false ok=true prompt_sha=d9c3367e7392

  Failed calls are logged too (`ok=false error=…`) — a model that starts
  rejecting every request was otherwise free in the cost logs, which is exactly
  what #401 looked like. `json_repair` firing is reported against the task,
  model and `prompt_sha` of the call that produced the bad JSON, since the
  response is parsed long after the call line is written. `users` gains
  `consumed_input_tokens` / `consumed_output_tokens` / `consumed_cached_tokens`
  next to the existing total: input and output bill ~6× apart and cached input
  at a tenth of input, so a single total cannot be converted to a cost at all.
  Cached is a *subset* of input in both providers' reporting, not a fourth
  bucket. Gemini's thinking tokens are counted as output because that is how
  they bill, so a model ignoring `thinking_budget=0` shows up instead of
  looking free. Prompt/response content stays behind `LOG_LLM_PAYLOADS`,
  default off — prompts carry the athlete's health data (#499). Contrary to
  the issue, the eight weekly generator jobs were already collecting usage; the
  one real hole was the per-ride review chain in `activity_sync`
  (`review_matched_ride_and_adapt`, two calls per matched ride), which ran
  outside every scope and was billed to nobody. It is now wrapped at the sync
  runner, and nesting means a step with its own scope still persists its tokens
  exactly once. (#516)

### Changed

- **One way to collect and persist token usage** (`services/token_accounting.py`,
  `routers/ai.py`, `routers/users.py`, and the nine job/service modules) —
  fifteen call sites each repeated the same begin/try/finally/increment
  boilerplate. They now share `track_llm_usage(db, user, source=...)`, which is
  also where the `source` label comes from: `api:ask_trainer`,
  `job:intervals-sync`, `plan-maintenance`, and so on. (#516)

### Fixed

- **Coach-memory updates are billed to the athlete who caused them**
  (`services/token_accounting.py`, `routers/ai.py`) — the accounting from #516
  went live and immediately reported four of seventeen calls in a 90-minute
  production window as `source=unscoped`, ~3,000 tokens each, all the same
  `prompt_sha`. They came from `_update_memory_bg`, which FastAPI runs as a
  `BackgroundTasks` callback — that is, *after* the response, and therefore
  after the request's session and its `ContextVar` scope are both gone.
  `BackgroundTasks` and a request-scoped scope are structurally incompatible,
  so the task now opens its own via `track_llm_usage_detached(session_maker,
  user_id, source="bg:update_coach_memory")`. That variant exists rather than
  reusing `track_llm_usage` because it must not hold a session across the
  provider call: the memory update deliberately reads and writes in separate
  short sessions so an athlete's concurrent edit is neither blocked nor
  clobbered (#346, #522). It opens one session at the end, only if something
  was actually spent. The whole retry loop shares one scope, so up to three
  attempts bill once — including the attempts that abandon the write, since the
  tokens were spent either way. A call outside every scope is now logged at
  WARNING rather than as the word `unscoped` inside a routine INFO line, which
  is why this took a month to notice. `_update_memory_bg` is the only
  background task that calls the model; the Strava/intervals imports and the
  weather backfill do not. (#537)

- **Activity sync stops re-requesting permanently dead intervals.icu ids**
  (`services/intervals_service.py`, `services/activity_sync.py`, `crud.py`,
  `routers/intervals.py`, migration `20260807_000001`) — the reclassification
  backfill fetched every still-`unknown` intervals ride on every 30-minute tick
  and treated a 404 exactly like an empty detail: skip, forget, ask again. Over
  a 36 h production window that was 1,201 `404 Not Found` requests, 17 distinct
  ids retried 72 times each. The ids are the float64-corrupted ones from #427
  (`978266860298001700`, `6755760447976027000`, …) whose originals are not
  recoverable from anything we stored — the row's `source_metadata` holds the
  same corrupted value — so those activities can never be fetched again.
  `fetch_activity_detail` now raises `IntervalsActivityNotFound` on 404 instead
  of returning `{}`, and the backfill records it on the row
  (`ride_metrics.provider_unfetchable_at`) and excludes it from the candidate
  query from then on. The marker is committed as soon as it is set, because the
  backfill runs first in a tick that only commits at the end — a later
  list-endpoint failure must not roll it back into another round of the same
  404s. Only the marker is written; the ride's imported metrics are untouched.
  The corrupted rows are retired rather than repaired: recovering an id would
  mean guessing it from `(date, duration, distance)` against a fresh listing,
  and a wrong guess attaches one ride's laps to another. 404 stays distinct from
  429/5xx, which still raise `IntervalsDataUnavailable` and are still retried.
  In the import loop a 404 now lets the cursor move past the activity, where a
  transient failure still holds it.

### Changed

- **Science retrieval drops chunks below a similarity floor** (`services/rag.py`)
  — `retrieve_cycling_context` returned the top 5 rows unconditionally, so any
  question produced ~2,400 tokens of text introduced to the coach as "relevant
  cycling science research", however unrelated. Measured against the production
  corpus (140 chunks, `gemini-embedding-001`, 768 dimensions) over 12 questions:
  science questions peak at 0.744–0.792, off-topic ones at 0.551–0.675 — "Move
  my Monday ride to Tuesday" returned strength-training passages at 0.589–0.617.
  `MIN_SIMILARITY = 0.70` sits in that gap, and an all-weak result set now
  returns `("", [])` instead of the five least-bad rows. Note the two
  populations only separate on the *best* hit — an off-topic question can beat
  the weakest kept chunk of a genuine one — so this also trims trailing weak
  chunks on real questions, which is the intended trade: four strong chunks beat
  five padded ones. Filtered in Python rather than SQL so the `WHERE` clause
  cannot stop the HNSW index serving the `ORDER BY`. `classify_question` was
  previously the only thing standing between an unrelated question and that
  block of text.

- **Science RAG works under the Gemini-only production config, and no longer
  costs a classification call when there is nothing to retrieve**
  (`services/embeddings.py` (new), `services/rag.py`, `routers/ai.py`,
  `scripts/ingest_cycling_science.py`, migration `20260806_000001`) — retrieval
  embedded queries by calling OpenAI directly, but prod runs Gemini-only with an
  empty `OPENAI_API_KEY`, so `retrieve_cycling_context` could never return
  anything; `POST /ai/refresh-knowledge` was likewise permanently 503 there,
  which is why `knowledge_chunks` still held 0 rows. Embeddings now go through a
  provider abstraction mirroring `llm.get_provider`. **The model named in #515,
  `text-embedding-004`, no longer exists** — checking the live model list against
  the production key returned only `gemini-embedding-001`, `gemini-embedding-2`
  and `-2-preview`, the same retirement trap as #401 — so the default is
  `gemini-embedding-001`, verified live, and overridable by env. Both Gemini
  models emit 3072 dimensions natively and truncate to **768** via Matryoshka,
  which is what the resized pgvector column stores; 768 also keeps the vector
  under pgvector's 2000-dimension ceiling for an HNSW index. The migration
  **deletes** existing rows rather than casting them: a vector is only comparable
  to others from the same model, so a 1536-dimension corpus is worthless once
  queries are embedded by a different one (prod held 0 rows, so nothing was
  lost). Re-running the ingestion script rebuilds it — it upserts by
  `(source_id, chunk_index)`. Separately, `classify_question` fired on **every**
  ask-trainer request purely to decide whether to retrieve, i.e. ~125 LLM calls a
  month to gate a path that could only return `""`; it is now preceded by a
  cached `SELECT EXISTS` on the corpus and skipped entirely while that corpus is
  empty, which also drops the half-filled `Question classification:` line from
  the prompt rather than emitting `category=None`.
- **Coach prompt is assembled so Gemini's implicit cache can actually hit**
  (`services/prompts.py`, `services/llm.py`) — implicit caching bills a repeated
  prefix at 10 % of the input rate, but it matches from the very first token and
  needs at least 4,096 of them. `ask_trainer_system` opened with today's date and
  closed with thousands of tokens of fixed rules, so the shared prefix between two
  consecutive turns was **676 tokens — below the minimum**. The cache could not
  engage at all; not rarely, never. The rule blocks are identical for every
  athlete on every day, so they now come first and the volatile athlete data
  follows: the shared prefix goes to **5,145 tokens**, 94 % of the prompt, and two
  requests from *different athletes on different days* share it. No instruction
  text changed except one positional reference — "the Current local date context
  above" is now "the Current local date context section", since the rule is read
  before the data it points at. Three things stay at the end on purpose: the JSON
  output contract, because format compliance is worth more than the ~200 tokens it
  would add to the prefix; the weather rules, because they are conditional; and
  the reasoning framework, because its second step depends on whether ride metrics
  exist — anything conditional in the prefix would break it for the whole request.
  `cached_content_token_count` is now read and logged, without which a cache hit
  is indistinguishable from a miss: the token total is identical either way
  (#514, epic #510).

- **The coach stops re-reading its own old ride notes** (`services/prompts.py`,
  `routers/ai.py`) — the ride-metrics history was the largest data section of the
  coach prompt at 5,063 tokens, 24 % of a 21,300-token message. Measured against
  production, the coach's *own* past notes were 2,278 of those tokens — 45 % of
  the section, ~104 tokens per ride across 22 of 30 rides — replayed in full on
  every single turn. `ride_metrics_context_section` gains a `prose_window`: the
  newest N rides keep everything, older rides keep their metrics line and lose
  the `Coach:` note, the classification rationale and the planned-workout title.
  The 30-ride window itself is untouched, so "how has my form trended this month"
  still has every date, TSS and CTL/ATL/TSB it needs — this is a cut of prose,
  not of history. The athlete's own notes are deliberately never windowed: they
  are a fraction of the cost (250 tokens across 30 rides) and the one thing in
  the section the coach cannot reconstruct from data. The coach path uses a
  window of 7 — about a week for this athlete, whose 30 rides span 33 days — and
  the nine analysis call sites pass no window, so their prompts are byte-for-byte
  unchanged. Measured: 5,063 → 2,520 tokens, **−2,543 per coach message**
  (#513, epic #510).

### Fixed

- **`create_all` no longer masks migration drift outside dev/test** (`main.py`) —
  startup ran `Base.metadata.create_all` unconditionally, alongside the Alembic
  history that `entrypoint.sh` applies (`alembic upgrade head`). Because
  `create_all` only creates missing *tables* (never missing columns), in a
  deployed environment it silently hid a forgotten migration and drifted from the
  migrated schema (#327). Schema bootstrap via `create_all` is now gated to
  `APP_ENV in {development, test}` (extracted to `_create_dev_schema`); real
  deployments rely solely on Alembic as the single source of truth. Tests build
  their schema directly and are unaffected.

- **The coach-memory background write no longer deadlocks against its own
  request** (`routers/ai.py`) — `_update_memory_bg` is queued as a background
  task, and FastAPI runs those *before* the `get_db` dependency's teardown
  commits. The handler's transaction was therefore still open when the task
  opened its own session to write `coach_memory`, which is exactly the point of
  that separate session (#346: re-read memory the athlete may have edited while
  the model was generating). Under postgres the two never touch the same rows;
  under sqlite one writer locks the whole file, so in the test suite the write
  waited out the full 5 s busy timeout and then failed — **every time**, not
  intermittently. It failed silently too, because that task swallows exceptions
  by design so a broken memory update cannot break the chat. `ask_trainer` now
  commits as its last statement, before the response and therefore before the
  background task. Measured: 22 tests were paying that 5 s wait (113 s of the
  suite), the suite drops from 263 s to 148 s, and a full run now reports zero
  `database is locked` errors where the affected path previously never once
  succeeded. A test now asserts the memory actually reaches the database — the
  assertion whose absence let 22 green tests cover a guaranteed failure — and
  the `memory_updates_enabled = False` workaround in
  `test_ask_trainer_lifts_flagged_constraint_and_edit_lands`, added to dodge
  what looked like a race, is gone (#522).

### Changed

- **Test suite runs in 4 minutes instead of 19** (`tests/conftest.py`) — the
  autouse `reset_db` fixture rebuilt the entire schema, 26 tables and 35 indexes,
  before *every one* of the 1501 tests, on an on-disk sqlite file. Setup cost
  0.63 s per test against ~0.01 s for the test itself, so roughly 16 of the 19
  minutes were fixture, not test. Two changes, no test touched: the schema is now
  built once per process and each test only empties the tables (`DELETE`, children
  first — deliberately not a wrapping transaction, since many tests commit for
  real and some exercise background tasks that open their own sessions), and the
  database lives in `/dev/shm` under a per-process name instead of a shared
  `./pytest.db`, falling back to the temp directory off Linux. Measured:
  `1501 passed` in 263 s, down from ~19 min; the same three modules went from
  96.3 s to 8.6 s. The per-process name also retires a recurring false alarm —
  a killed run used to leave `pytest.db` corrupted so the *next* run failed with
  `no such table` (which reads exactly like a schema regression, cf. #496), and
  two concurrent runs gave each other `disk I/O error`. Neither is possible now;
  leftovers from killed runs are swept on the next start (#520).
- **Coach prompt carries a bounded set of hypotheses and open questions**
  (`crud.py`, `routers/ai.py`) — both sections used to render *everything* the
  coach had ever wondered about. In production that was 62 hypotheses (every one
  still `proposed` — nothing had ever retired one) and 21 open questions, i.e.
  6,063 tokens, 29 % of a 21,300-token coach message, growing every week that the
  weekly generators ran regardless of what the athlete did. Two new prompt-facing
  accessors mirror the existing `get_prompt_athlete_memory_facts`:
  `get_prompt_athlete_hypotheses` takes the 8 strongest by confidence and
  evidence, and `get_prompt_athlete_open_questions` the 5 best-evidenced;
  both drop records with no fresh evidence in 8 weeks, and hypotheses below the
  seed confidence (only reachable by active decay) are dropped as well. The
  unbounded `list_*` functions are untouched, so the expert-mode UI and the
  memory export still show the athlete the complete picture — the cap is a prompt
  concern only. Measured against the same production athlete: 6,063 → 1,478
  tokens, **−4,585 per coach message** (−22 % of the whole prompt), and adding a
  100th hypothesis no longer makes it bigger (#512, epic #510).
- **Gemini runs Flash-Lite on every task** (`config.py`, `services/llm.py`,
  `.env.example`) — all four task defaults move from `gemini-3.5-flash` to
  `gemini-3.5-flash-lite`: $0.30/$2.50 per M input/output tokens against
  $1.50/$9.00, a flat 5x on input. The workload justifies it — `TASK_CLASSIFY`
  alone covers the whole continuous-learning chain (~8 calls per imported ride:
  insights, athlete model, hypotheses, inquiries, open questions, experiments,
  predictions), which is structured JSON extraction under explicit instructions
  rather than open reasoning. The conversational coach is the one task where the
  difference could show, so each task stays independently overridable:
  `GEMINI_COACH_MODEL=gemini-3.5-flash` raises it back with no deploy. Measured
  against production before the change: ~21,300 tokens per coach message and
  ~8 M tokens over three months (#511, epic #510).

### Fixed

- **Thinking config adapts to the model instead of assuming a zero budget**
  (`services/llm.py`) — `GeminiProvider._build_config` hard-coded
  `thinking_budget=0`, which `gemini-3.5-flash-lite` rejects outright with `400
  INVALID_ARGUMENT`. Left as it was, the model switch above would have failed
  *every* Gemini call rather than degrading quality. Models that refuse a zero
  are now left at their default, which measured at zero thinking tokens across
  repeat calls; `thinking_budget=1` is deliberately **not** the workaround, as
  those models treat it as a hint rather than a cap and spent 0–1,348 thinking
  tokens call to call on an identical prompt (billed at the output rate).
  Which models refuse a zero does not follow naming — `gemini-3.1-flash-lite`
  accepts one while the non-lite `gemini-3.6-flash` does not — so rather than
  infer it from the model string, `_generate` retries a 400 once without the
  thinking config and remembers the model for the process. A future model bump
  therefore degrades instead of taking every call down (cf. #401).

## [0.50.0] - 2026-07-30

### Added

- **Multiple sessions per day — two-a-days** (`schemas.py`, `services/plan_pipeline.py`,
  `services/ride_matching.py`, `services/plan_constraints.py`, `services/dates.py`,
  `models.py`, `crud.py`, `routers/users.py`, migration `20260802_000001`) — a plan
  can now hold an AM yoga session *and* a PM endurance ride on the same date, each
  with its own type, duration, targets and intervals. `PlanDay` gains a `slot`
  (0 = first/AM) and an optional `timeOfDay`, so a session's identity is
  **`(date, slot)`** rather than the date alone — `schemas.session_key` /
  `day_slot` / `normalize_slot` are the one place that is decided. Keeping the
  session as the model, instead of nesting a `sessions` list under a day
  container, is what lets the canonical `PlanDay` persist gate go on owning every
  duration/drift invariant (#368/#422/#424) unchanged.
- **Per-session executed-activity matching** (`services/ride_matching.py`,
  `models.RideMetric.matched_plan_slot`) — `apply_ride_plan_matches` groups a date's
  planned sessions and assigns each of the day's rides to the session it best fits,
  greedy over sport plausibility then duration, with chronological order as the
  tiebreak so "earlier activity → earlier slot" resolves the obvious way. The
  morning gym session and the evening ride now each attribute to their own planned
  session; before, one won and every other activity was forced to
  `MATCH_UNMATCHED`. Rides left over once every session is filled stay cleanly
  unmatched and keep the existing `Additional` / `Too much` labelling.
- **Per-session completion and feedback** (`models.WorkoutLog.slot`,
  `crud.upsert_workout_log`, `POST /users/me/workouts/{date}`) — ticking the morning
  yoga marks *that* session done and leaves the evening ride pending, and each
  session carries its own workout log. `GET /users/me/workouts` keys the first
  session of a date by the bare date, so existing clients read exactly what they
  read before, and only the extra sessions of a two-a-day add a `date#slot` key.
- **Per-session coach prompts** (`services/dates.py`) — `annotate_plan_days` now
  emits `sessionOrder` / `sessionCount` / `sessionLabel` and orders sessions by slot
  within a date, so the coach can reason about AM/PM load ordering (move the hard PM
  session out of the heat, leave the easy AM one alone — #495 × #496). A
  single-session day gets no session annotations at all, keeping every existing
  prompt byte-identical.

### Changed

- **The plan pipeline is keyed by session, not by date** (`services/plan_pipeline.py`)
  — user-pin protection, completed-day preservation, trained-day preservation, the
  concurrent-edit merge, source stamping and `PlanDayHistory` all moved from
  `{date: day}` to `(date, slot)`. A pin now protects one session rather than
  freezing the whole day, and the change log records one row per session instead of
  one conflated row per date. Per-day updates without a `slot` target the date's
  first session, so every pre-existing caller behaves exactly as before.
- **Required-workout constraints apply per day, not per session**
  (`services/plan_constraints.py`) — a required session is satisfied once *some*
  session on the date qualifies, and only the date's first session is coerced when
  none does. Without this a two-a-day would have had both halves rewritten into
  "Required endurance session".
- **`slot: 0` is not serialized** (`schemas.PlanDay`) — slot 0 *is* the legacy
  "no slot" day, so it is omitted on dump. Writing it would rewrite every stored
  plan on the first commit after this change and, worse, make an unchanged plan
  compare unequal to the database — turning every no-op write into a real write that
  cascades a login-summary refresh and a ride-snapshot rebuild.

### Fixed

- **`normalize_slot` no longer accepts arbitrary objects** (`schemas.py`) — a bare
  `int(value)` converts anything defining `__int__`, which silently turned an unset
  slot into a real-looking slot 1 and mis-keyed the session. Only genuine numbers and
  numeric strings are accepted now; booleans are excluded for the same reason.

## [0.49.0] - 2026-07-30

### Added

- **Persisted, editable athlete training location** (`models.py`, `crud.py`,
  `services/home_location.py`, `routers/users.py`, migration
  `20260801_000001`) — the weather anchor is no longer re-derived on every request
  from whichever ride happened to carry GPS last, where a single holiday ride moved
  the whole forecast. A new `athlete_home_location` attribute stores lat/lng, a
  place label, a `source` marker and a confidence, seeded by **clustering typical
  ride start points** (`cluster_ride_starts`) rather than taking the latest ride.
  `resolve_training_location` prefers the stored attribute and still falls back to
  the latest ride with GPS, so nothing regresses for athletes with no stored row.
  The write gate in `upsert_athlete_home_location` refuses to let an `inferred`
  pass overwrite a `user_set` row — the stale-snapshot clobber class (#342/#345/#346)
  applied to location — and `GET`/`PUT /users/me/home-location` make it editable.
- **Coach-agent location override** (`routers/ai.py`, `services/home_location.py`)
  — "I mostly train near Freiburg now" in coach chat is recognised deterministically
  (narrow, high-confidence phrasing, so a place mentioned in passing never moves the
  base), geocoded through Open-Meteo's key-free geocoding API, and stored as
  `user_set`. The router applies it before the model replies and appends a
  deterministic confirmation note, so the athlete is told what actually happened
  rather than what the prose claims (the #437 honesty pattern).
- **Cached daily forecast + planned-day weather API** (`services/weather_service.py`,
  `routers/users.py`, `schemas.py`) — `fetch_daily_forecast` caches Open-Meteo's
  daily outlook for an hour per location, keyed on coordinates rounded to ~11 km so
  nearby athletes share one lookup, and a single 16-day fetch is sliced to serve
  every shorter horizon. No per-request upstream calls. `GET
  /users/me/weather-forecast` exposes the per-day condition, high/low, precipitation,
  wind and coaching `load_flag` that the dashboard shows on planned days.
- **Per-athlete weather tolerances as confidence-scored beliefs**
  (`services/weather_preference.py`, `services/learning_pipeline.py`) — "everyone
  slows in the heat" is a population average, not an athlete. The conditions already
  stored on every `RideMetric` are now correlated against **outcome** (intensity
  factor and session length in each condition bucket versus the athlete's own
  mild-weather baseline) and **behaviour** (rode outdoors in the wet anyway, or moved
  indoors), and written as `AthleteHypothesis` rows under a new
  `weather_preference` category with evidence, alternative explanations and a
  confidence that grows with sample size and effect. Inconclusive or contradictory
  evidence asserts **nothing** rather than inventing a pattern. Beliefs share the
  existing merge/decay lifecycle, and one the athlete stated themselves ("I actually
  love the rain", also captured from chat) is exempt from decay — absent ride data is
  not a counter-argument to what they told us.
- **Weather for indoor rides** (`services/weather_service.py`,
  `services/activity_sync.py`, `routers/strava.py`, `routers/ai.py`) —
  `enrich_activity_weather` now accepts the athlete's training location as a fallback
  for activities with no GPS, tagged `weather_source="open_meteo_home"` and without
  touching `start_lat`/`start_lng`. That is what makes "it was 34 °C and the athlete
  rode inside" a learnable behavioural signal instead of a blank row.

### Changed

- **Weather-aware coaching, conditioned on the learned tolerances**
  (`services/prompts.py`, `services/ai_service.py`, `services/weather_service.py`) —
  `training_weather_context_for_user` now names the training location, and carries the
  learned tolerances alongside the forecast so the coach cannot act on one without the
  other. A new `weather_scheduling_rule` (wired into the coach chat prompt only when a
  forecast exists) tells it to *adapt rather than rewrite*: move or soften intensity on
  extreme-heat days, offer an indoor or cooler window in severe conditions — but never
  for conditions this athlete demonstrably handles well, weighting each call by the
  belief's confidence, and always saying when weather is the reason a session changed.
  The coach chat prompt receives the forecast for the first time (`ask_trainer`), which
  is where "should I ride tomorrow?" is actually asked.
- **Plan-change narration can explain a weather-driven move**
  (`services/coach_summary.py`, `services/plan_maintenance.py`, `services/prompts.py`)
  — the nightly adaptation now passes the forecast it acted on into
  `narrate_plan_changes`, so a session moved off a 38 °C day is explained as exactly
  that instead of reading as unexplained plan churn (#439), with an explicit
  instruction never to invent a weather reason for an unrelated change.
- **Continuous learning refreshes the weather model** (`services/learning_pipeline.py`)
  — two new best-effort steps re-cluster the training location and update the weather
  tolerances after every import, ordered so a ride imported in the same batch can be
  weather-tagged from a freshly seeded location.

## [0.48.0] - 2026-07-29

### Added

- **Update the athlete model before the plan** (`services/prompts.py`) — a new
  `update_model_before_plan_rule`, wired unconditionally into the coach chat system
  prompt (`ask_trainer_system`), stops the coach from treating the training plan as
  its primary state and reflexively editing it in response to new information. The
  coach now follows an explicit belief-before-decision order: new evidence → update
  the athlete model and shift its hypotheses → re-estimate confidence → only then
  decide whether the current plan is still the highest-value choice. A belief update
  does **not** imply a plan change — lower confidence in a hypothesis often leaves
  the best plan unchanged, and "no change" is an explicit, valid outcome. When the
  understanding shifts, the coach names what changed (which hypothesis, roughly from
  what confidence to what, and why) **before** any plan talk, and keeps `planUpdates`
  empty when the model moved but the plan should stand. This prevents oscillating,
  contradictory plan changes where a follow-up question silently reverses an earlier
  edit (#491).

## [0.47.0] - 2026-07-29

### Added

- **Reveal uncertainty in coaching recommendations** (`services/prompts.py`) — a
  new `reveal_uncertainty_rule`, wired unconditionally into the coach chat system
  prompt (`ask_trainer_system`), stops the coach from arguing that a
  recommendation is the single truth. A recommendation is framed as a judgement
  call under incomplete data, not a verdict: when the call is genuinely uncertain
  (low/moderate confidence, diverging physiology vs athlete-context layers, or the
  athlete pushing back with their own signals) the coach must surface **both** the
  supporting **and** the contradicting evidence, state a rough confidence, and name
  the open unknowns (missing HRV, weak recovery model, uncertain other-sport
  power) — optionally as a short *recommendation / confidence / supporting /
  contradicting / unknowns* breakdown — and may defer to the athlete's judgement or
  a low-cost test rather than insisting. High-confidence, one-sided calls are told
  the opposite: state it plainly and do not manufacture doubt (#490).

## [0.46.0] - 2026-07-29

### Added

- **Explainable coaching conversation & proactive insight (Level 2)**
  (`services/prompts.py`, `services/ai_service.py`, `routers/ai.py`) — the coach
  chat (`ask_trainer`) now receives the deterministic Athlete Performance Model
  (#476), its detected limiter (#477), the ROI recommendation (#478) and the
  active testable hypotheses (#479) as structured context. New prompt sections
  (`performance_model_section`, `active_hypotheses_section`, reusing
  `athlete_performance_roi_section`) expose each claim's **evidence, confidence
  and what is still missing**, and a new `coach_explainability_rule` instructs the
  coach to (a) justify any recommendation on demand, drilling *claim → model
  attribute/limiter → concrete workouts/trends → confidence & uncertainty*, never
  presenting an inferred estimate as a measured fact, and (b) **proactively**
  surface a materially higher-return training emphasis — phrased as a hypothesis
  with an offer to explain — when the model implies one. ROI context is omitted
  when the model has no confident limiter so the coach falls back to its usual
  reasoning (#480).

## [0.45.0] - 2026-07-29

### Added

- **Continuous coaching hypotheses (Level 1, automatic)**
  (`services/hypothesis_engine.py`, `crud.py`, `models.py`, `schemas.py`,
  `services/learning_pipeline.py`, Alembic
  `20260731_000001_add_hypothesis_evidence_alternatives`) — a deterministic engine
  forms testable coaching hypotheses **after every session** straight from the
  Athlete Performance Model and its detected limiter (no LLM): e.g. *"the current
  limiter is likely threshold utilization"*, *"the athlete has developed a strong
  aerobic engine"*, *"FTP could be underestimated"*. Each hypothesis carries the
  epic's required `evidence` (the athlete's own FTP/MAP/fractional-utilization
  numbers), `confidence` (inherited from the underlying limiter/attribute, never a
  guess) and `alternative_explanations` (the competing readings still to rule out).
  Hypotheses persist through the existing merge lifecycle so repeated confirming
  observations raise `confidence`/`evidenceCount` instead of duplicating, while a
  hypothesis the model no longer supports decays and is eventually retired
  (`crud.decay_unsupported_model_hypotheses`). Generation runs as a per-import step
  in the continuous-learning pipeline right after the model refresh, and the
  structured `evidence`/`alternativeExplanations` are surfaced on the existing
  `GET /users/me/athlete-hypotheses` API for the conversation layer and frontend
  (#479).

## [0.44.0] - 2026-07-29

### Added

- **ROI-based training recommendation** (`services/roi_recommendation.py`,
  `schemas.py`, `services/prompts.py`, `services/ai_service.py`,
  `routers/ai.py`) — a deterministic engine maps the Athlete Performance Model
  and its detected limiter to an **expected-gain-per-physiological-system** map
  (`threshold`/`vo2max`/`endurance`/`anaerobic`, each `large`/`moderate`/`small`/
  `maintenance`), a suggested weekly emphasis (e.g. `2× Threshold  1× VO₂max
  1× Long endurance`) and a natural-language rationale that cites the athlete's
  own numbers (FTP vs MAP, fractional utilization, aerobic base). Surfaced
  machine-readable on the performance-model API as `recommendations` and injected
  into the physiology layer of `next-ride-recommendation`, so the coach explains
  *why* a stimulus has the highest return rather than prescribing generic
  periodization. When the model has no confident limiter the recommendation is
  `sufficient: false` and the coach falls back to its existing reasoning (#478).

## [0.43.0] - 2026-07-29

### Added

- **Athlete Performance Model — data layer** (`models.py`, `schemas.py`,
  `crud.py`, `routers/ai.py`, migrations `20260729_000001` /
  `20260730_000001`) — a new persistent, per-attribute, evidence-backed
  `AthletePerformanceModel` (one row per athlete) plus an
  `AthletePerformanceSnapshot` time series. Distinct from the LLM-derived
  qualitative `AthleteModel` (#384): this one is deterministic and quantitative,
  where **every** attribute carries its own estimate/score, `confidence`,
  `evidence` and `missingInformation`. Exposed read-only at
  `GET /ai/athlete-performance-model` (empty model, not 404, when never
  derived) with an on-demand `POST /ai/refresh-athlete-performance-model`
  (#475).
- **Cross-workout physiological inference engine**
  (`services/athlete_model_inference.py`, `services/analysis.py`,
  `services/learning_pipeline.py`) — a compact per-ride `perf_signals` blob
  (power-duration envelope, HR drift, first/second-half power & HR splits) is
  persisted on `RideMetric` at the `build_ride_metrics_chain` choke point, and a
  deterministic, pure engine aggregates it over a rolling window to infer FTP,
  MAP, VO₂max, fractional utilization, aerobic endurance, fatigue resistance and
  anaerobic capacity. No attribute is emitted without a confidence; missing
  signals report `unknown` rather than being guessed (VO₂max stays `unknown`
  until a body-weight source exists). Runs as a best-effort step in the
  continuous-learning pipeline after each import (#476).
- **Physiological limiter detection** (`services/limiter_detection.py`,
  `models.py`, `schemas.py`, `crud.py`, migration `20260730_000001`) — a
  deterministic engine reads the performance model and returns a
  confidence-ranked list of candidate limiters (`threshold`, `vo2max`,
  `endurance_durability`), each with `evidence` **and** `counterEvidence`,
  reproducing the "if the engine is already big, raise the floor" reasoning from
  the gap between the aerobic ceiling (MAP) and sustainable threshold power
  (fractional utilization). The top candidate is written back to
  `likelyLimiter` and the full ranking to `limiters`, both surfaced on the
  performance-model API. Missing signals return a low-confidence
  `insufficient_data` entry rather than guessing (#477).
- **`batch_id` on `plan_day_history`** (`models.py`, `crud.py`,
  `routers/users.py`, migration `20260720_000001`) — every per-day row written
  by one pipeline commit (`record_plan_day_changes`) now shares a `batch_id`, so
  a coach run (a plan generation, nightly tune-up or single chat edit) can be
  reassembled from the append-only log for debugging and is exposed on the
  plan-history API as `batchId`. This lets the Coach Timeline collapse a run into
  one card instead of one per changed day (#435).

## [0.42.0] - 2026-07-19

### Added

- **Daily duration-refresh job** (`services/duration_refresh.py`, `main.py`,
  `config.py`) — a scheduled task re-derives `RideMetric.duration_seconds` from
  each source's *current* `moving_time` over a recent lookback window
  (`duration_refresh_lookback_days`, default 21) and recomputes the metrics
  chain. This is the standing prevention for intervals.icu populating
  `moving_time` only minutes after upload: an early sync could store wall-clock
  `elapsed_time` and, because the ride was already imported, never correct it.
  The one-off backfill CLI is now a thin wrapper over the same
  `refresh_user_durations` logic (#429).
- **Long-term athlete model in next-ride recommendations**
  (`services/prompts.py`, `services/ai_service.py`, `services/ride_matching.py`)
  — the durable structured athlete model (#384) is now threaded into the
  next-ride recommendation prompt, not only ask-trainer, so suggestions also
  use FTP, VO2 max, threshold durability, recovery and heat tolerance. Gated on
  `memory_updates_enabled`, mirroring ask-trainer (#403).

### Fixed

- **Activity duration uses `moving_time`, not `elapsed_time`** — imported ride
  durations now exclude pauses, so a long stop no longer inflates a ride to
  wall-clock time (e.g. 8h28 instead of 4h25) (#427).
- **intervals.icu id corruption prevented at the source** — the raw provider id
  is now carried end-to-end as a string `external_id` (schema → intervals
  `_activity_response` → frontend store → analyse persistence), so new rows
  store the true uncorrupted hash instead of a float64-mangled one. A
  float-tolerant join still corrects pre-existing rows (#429).
- **Coach duration changes now update `durationMinutes`** — when the coach
  rewrites a session's prose it also updates the structured duration field, so
  the saved plan matches what the coach promised (#422).
- **One-off production duration correction** for a mis-stored ride on
  2026-07-18 (#426).

### Changed

- **Canonical typed `PlanDay` persist gate** — plan days are normalised by
  `schemas.PlanDay` at a single gate in `plan_pipeline`; field invariants belong
  there rather than in scattered call sites (#424).
- **Source-aware duration backfill** — the `backfill_activity_moving_time`
  script re-derives durations for both Strava (exact id) and intervals.icu
  (float-tolerant id) sources (#429).

## [0.41.0] - 2026-07-13

### Added

- **Continuous athlete-learning pipeline** (`services/learning_pipeline.py`,
  `services/activity_sync.py`, `config.py`) — every completed workout now runs a
  per-athlete learning step immediately, instead of the athlete-knowledge passes
  only running once a week. `run_learning_step` composes the existing passes in
  the weekly schedule's dependency order — insights + athlete-model refresh
  (analyse, compare against the model, update observations and confidence) →
  contradiction/anomaly detection → hypotheses → open questions — with each pass
  best-effort so a failure is recorded and never aborts the others. The
  persisted observations/hypotheses/open questions are the coach's evolving
  notes (they already surface in coach prompts), so the step emits a structured
  audit line rather than clobbering the athlete-editable coach memory (#346).
  `activity_sync._persist_and_adapt` triggers the step after new workouts are
  imported via `learn_from_completed_workouts`, guarded so a learning failure
  can never break the sync. Gated by the new `continuous_learning_enabled`
  setting (default on); the weekly jobs remain registered as a backstop (#388).

## [0.40.0] - 2026-07-05

### Added

- **Athlete-facing plan-change history & analytics** (`routers/users.py`, `crud.py`)
  — two new endpoints scoped to the current user surface the append-only
  `plan_day_history` log (previously admin-read only, #343):
  `GET /users/me/plan-history` (optional `?date=`, newest-first) returns the
  per-day change timeline including blocked automated attempts (`applied=false`,
  where a user pin or completed day kept the athlete's version), and
  `GET /users/me/plan-history/stats` returns aggregate analytics
  (changes by trigger, applied-vs-blocked counts, most-changed days) via the new
  grouped-SQL `crud.plan_day_history_stats`. Trigger keys are returned raw; the
  frontend owns friendly labelling. The live plan still comes from
  `TrainingPlan.plan` — this is read-only history (#357).

## [0.39.9] - 2026-07-04

### Removed

- **`POST /ai/adapt-plan` endpoint and `AdaptPlanRequest` schema** (`routers/ai.py`,
  `schemas.py`) — the route's only consumer was the frontend dashboard's on-load
  auto-adapt, which fired whenever the plan had a past incomplete day. That call
  duplicated `nightly_maintenance` (runs 02:00 daily), which reschedules stale days
  via the same `ai_service.adapt_training_plan` call with identical pin protection.
  A day only becomes "past incomplete" at the midnight rollover, which the 02:00 job
  always catches before the next load, so the on-load call was redundant (extra AI
  tokens plus a redundant plan write) — and was the trigger that surfaced the
  pin-clobber bug in #359. Stale days are now rescheduled solely by nightly
  maintenance. The `adapt_training_plan` / `adapt_plan_*` prompts and the `adapt`
  source in `plan_pipeline` are unchanged — still used by nightly maintenance and
  the flagged-workout `_auto_adapt_plan` trigger (#361).

## [0.39.8] - 2026-07-02

### Fixed

- **Import-progress state is now bounded and its start guard is race-free**
  (`routers/strava.py`, `routers/intervals.py`, `services/progress_store.py`) —
  the per-user Strava and intervals.icu import-progress dicts were written per
  user and never evicted, growing unbounded over a long-running process. Completed
  (`done`/`error`) entries are now evicted on a TTL and capped in size
  (`prune_progress`), and the "is an import already running?" check-and-set is a
  single synchronous step (`try_mark_running`) so it can't be raced on the event
  loop (#326). This is a single-replica fix; cross-replica correctness is
  intentionally out of scope.

### Docs

- **Multi-replica limitations** (`docs/multi_replica.md`) — documents the
  single-replica assumptions (in-process scheduler, Strava OAuth state, import
  progress) that break with more than one backend replica, what is already safe
  across replicas (DB-backed token refresh, coach memory, plan writes), and what
  a real horizontal-scaling effort would require (#326).

## [0.39.7] - 2026-07-02

### Fixed

- **Passwords now hashed with Argon2id, with transparent upgrade on login**
  (`auth.py`, `routers/auth_router.py`) — an Argon2 hasher was configured but every
  new password was hashed with bcrypt, which silently truncates input to 72 bytes
  (weakening long passphrases) while the Argon2 path was dead code (#329).
  `hash_password` now uses Argon2id (no length limit); `verify_password` still
  accepts legacy bcrypt hashes, and a new `password_needs_rehash` lets the login
  flow re-hash a bcrypt (or outdated-parameter) credential to Argon2 after a
  successful verify, so stored hashes converge on the stronger scheme over time.

## [0.39.6] - 2026-07-02

### Fixed

- **Transient intervals.icu fetch failures no longer imported as degraded activities**
  (`services/intervals_service.py`, `services/activity_sync.py`, `routers/intervals.py`)
  — the parallel of #325 for intervals.icu. `fetch_activity_streams` and
  `fetch_activity_detail` returned `{}` for both a genuine empty response and a
  transient 429/5xx/network error, so a blip permanently imported an activity with
  missing stream/detail data and advanced the cursor past it (#352). They now
  raise `IntervalsDataUnavailable` on transient failures; background sync marks the
  activity unhandled so `_safe_intervals_cursor` holds the cursor for a retry, and
  bulk import surfaces it in `failed_activities` instead of importing degraded
  data. A genuine empty/absent response (permanent non-success) still returns
  `{}` and imports summary-only.

## [0.39.5] - 2026-07-02

### Fixed

- **Transient Strava stream failures no longer imported as empty-stream activities**
  (`services/strava_service.py`, `services/activity_sync.py`, `routers/strava.py`) —
  `fetch_activity_streams` returned `{}` for both "activity genuinely has no
  streams" and a transient 429/5xx/timeout, so a blip during stream download
  permanently imported an activity with no power/HR data, silently corrupting
  TSS/CTL/ATL/TSB with no retry (#325). A new `fetch_activity_streams_strict`
  raises `StravaStreamUnavailable` on transient failures (the existing lenient
  wrapper still returns `{}` for enrichment paths). Background sync now skips such
  an activity and holds the cursor below it so it is retried next tick instead of
  being lost; bulk history import skips it (counted in `skipped`) rather than
  baking in degraded data. A genuine no-streams response (2xx-empty / permanent
  4xx) is still imported summary-only as before.

## [0.39.4] - 2026-07-02

### Fixed

- **Race-context sync no longer risks clobbering a concurrent coach-memory edit**
  (`routers/users.py`, `crud.py`) — `_sync_race_context` (run on race-event
  create/update/delete) did an unlocked read-modify-write of the coach memory to
  refresh its race section, so a `PUT /coach-memory` landing in the same instant
  could be lost. The read now locks the row (`crud.get_coach_memory(...,
  for_update=True)`, `SELECT … FOR UPDATE`), serialising the merge against
  concurrent writers. Closes the last instance of the stale-snapshot clobber
  class (#342/#345/#346).

## [0.39.3] - 2026-07-01

### Fixed

- **Coach-memory background update no longer clobbers concurrent edits**
  (`routers/ai.py`, `crud.py`) — after a chat, the background memory update based
  its rewrite on the memory snapshot captured at request time and blind-overwrote
  the row, so an edit the athlete made via `PUT /coach-memory` while the model was
  generating was silently lost (#346). The task now re-reads the current memory as
  its base and writes with a row-locked compare-and-set
  (`crud.update_coach_memory_if_unchanged`), retrying against the fresh value on a
  detected concurrent edit. New helper is atomic (`SELECT … FOR UPDATE`), so a
  committed edit from another transaction is never overwritten.

## [0.39.2] - 2026-07-01

### Tests

- **Cover the plan-day history data layer** (`tests/test_crud.py`) — direct tests
  for `crud.record_plan_day_changes` (the empty-changes no-op guard, row
  insertion, and the `applied` default) and `crud.list_plan_day_history`'s date
  filter (#343).

## [0.39.1] - 2026-07-01

### Added

- **Admin endpoint to inspect plan-day history** (`routers/admin.py`) — new
  `GET /admin/users/{user_id}/plan-history` (admin-authenticated, optional
  `?date=` filter and `limit`) returns a user's per-day plan change log newest
  first, each entry showing the day before/after, the trigger, and whether it was
  applied or blocked. Backs debugging of plan changes (e.g. "why did today revert
  after a ride?") and a future analytics surface. Reuses
  `crud.list_plan_day_history` (#343).

## [0.39.0] - 2026-07-01

### Added

- **Per-day training-plan change history** (`models.py`, `crud.py`,
  `services/plan_pipeline.py`, Alembic `20260701_000001`) — a new append-only
  `plan_day_history` table records one row per changed plan day per write,
  capturing the day before and after and which trigger caused it (manual save,
  coach chat, ride review, nightly maintenance, …). The live plan stays in
  `TrainingPlan.plan` and the UI still reads only the latest version; this log is
  for analytics and learning athlete behaviour (#343). Automated changes blocked
  by a user pin or a completed day (#342/#345) are logged with `applied=False` as
  an "attempted correction" signal, and the recorded source is the real trigger
  so manual and coach-chat edits stay distinguishable. New `crud` helpers
  `record_plan_day_changes` / `list_plan_day_history` back future analytics
  surfaces.

## [0.38.11] - 2026-07-01

### Fixed

- **Manual plan edits no longer silently reverted by automated triggers**
  (`services/plan_pipeline.py`, `schemas.py`, `routers/ai.py`, `routers/users.py`,
  `services/plan_maintenance.py`, `services/ride_matching.py`) — background
  triggers (post-ride review, nightly maintenance, workout-rating auto-adapt)
  could overwrite a day the athlete had set manually, because the pipeline's
  concurrent-edit guard only detected edits made in the read→write window; an
  edit persisted before the trigger read the plan was baked into its baseline and
  treated as fair game (#342). Each day now carries a persistent `source` marker:
  user-authored triggers (manual save, coach chat) pin changed days as `"user"`,
  and background triggers may not overwrite a pinned, not-yet-completed day.
  User-requested replans (generate, adapt, next-ride) stay authoritative. The
  `source` is threaded from all eight trigger call sites via a single
  `PLAN_SOURCES` registry, and only content-changed days are re-stamped so no-op
  writes cause no plan churn or summary invalidation.
- **Completed workout days protected on the full-plan path**
  (`services/plan_pipeline.py`) — the per-day update path already skipped
  completed days, but the full-plan path never consulted the `completed` marker,
  so a background full-plan write could have the model rewrite a completed day or
  drop it entirely when the proposal omitted it (#345). Background triggers now
  restore every currently-completed day unchanged and never drop it; user edits
  still bypass the guard so they can set and correct completed days and feedback.

## [0.38.10] - 2026-06-22

### Added

- **Import historical coach conversations** (`services/prompts.py`,
  `services/ai_service.py`, `routers/ai.py`, `schemas.py`) — new
  `POST /ai/extract-athlete-facts` runs an extraction prompt over a pasted
  conversation transcript and returns candidate durable athlete traits
  (fact, category, confidence, source snippet) **without persisting them**.
  The athlete reviews candidates in Settings and accepts chosen ones via the
  existing `POST /users/me/athlete-memory-facts`, after which they feed coach
  recommendations. Candidates are deduplicated, confidence-capped at 0.9, and
  the transcript is length-bounded.

## [0.38.9] - 2026-06-22

### Added

- **Transparent physiology vs personal-context rationale** (`services/prompts.py`,
  `services/ai_service.py`, `routers/ai.py`, `schemas.py`) — the coach now reasons
  in two explicit layers and returns separate `physiologyRationale` and
  `contextRationale` fields from `/ai/ask-trainer`. When the layers diverge, the
  natural response says so ("the numbers say X, but knowing you I'd do Y"), so the
  athlete can see when a call is about them rather than the workout file. The chat
  surfaces this as a compact, collapsible "Why this advice?" disclosure.

## [0.38.8] - 2026-06-22

### Added

- **Delete learned athlete traits** (`crud.py`, `routers/users.py`) — new
  `DELETE /users/me/athlete-memory-facts/{fact_id}` endpoint permanently removes
  a learned trait, scoped to the owning user (404 when missing). This backs the
  new Settings UI for reviewing, correcting, confirming, and deleting the
  personal traits the coach has learned, so coaching memory stays inspectable
  and correctable.

## [0.38.7] - 2026-06-22

### Fixed

- **Empty AI coach responses** (`services/ai_service.py`, `routers/ai.py`) —
  `/ai/ask-trainer` now retries blank LLM `response` payloads (2 retries with
  0.5s→1s exponential backoff) and rejects them with a 502 before persisting
  chat messages, so transient malformed provider replies recover automatically
  or surface as retryable request failures instead of empty assistant bubbles.

## [0.38.6] - 2026-06-21

### Fixed

- **Contained duplicate ride imports** (`services/activity_identity.py`,
  `crud.py`) — same-day cycling imports are now collapsed when a shorter
  activity is substantially contained within a longer imported activity, while
  preserving genuinely separate rides with different start windows.

## [0.38.5] - 2026-06-21

### Fixed

- **Ride label explanations** (`services/prompts.py`) — recent activity context
  now includes ride duration and displayed match labels, and label explanations
  explicitly avoid inventing missing-data causes unless activity context supports
  that claim.

## [0.38.4] - 2026-06-21

### Fixed

- **Multi-ride plan matching** (`services/ride_matching.py`) — same-day
  duration-focused cycling plans now consider the combined duration of all
  rides, auto-match the closest ride when the day does not collectively satisfy
  the plan, and label extra rides as `Additional` or `Too much` based on
  intensity/TSS evidence.

## [0.38.3] - 2026-06-20

### Fixed

- **Duplicate activity analysis canonicalization** (`services/activity_identity.py`,
  `crud.py`, `services/activity_imports.py`, `routers/ai.py`) — same-day ride
  imports with matching names, nearby start times, and small duration drift now
  collapse to one canonical ride metric, stale duplicate history rows are
  hidden, and near-identical activities are deduplicated before activity
  analysis calls without merging separate same-name rides that start at
  different times.

## [0.38.2] - 2026-06-19

### Fixed

- **Repeat hard-session guard** (`services/prompts.py`) — Ask Trainer,
  plan adaptation, and next-session recommendations now treat actual recent
  hard sessions as authoritative before preserving or creating VO2max/HIIT
  work, so a planned repeat high-intensity day within roughly 48 hours must be
  moved or replaced unless an explicit exception is justified.

## [0.38.1] - 2026-06-19

### Fixed

- **Rest-day coach justification** (`services/prompts.py`) — Ask Trainer now
  evaluates challenged rest-day recommendations against CTL/ATL/TSB, recent
  TSS, RPE, subjective freshness, and availability constraints, so a second
  complete rest day must be justified by concrete fatigue evidence and corrected
  workout timing is re-evaluated before recommending easy Z2 or full rest.

## [0.38.0] - 2026-06-18

### Added

- **Persistent ride match feedback** (`schemas.py`, `routers/users.py`) —
  ride-feedback submissions can now include an athlete plan-match correction,
  which is stored in `ride_metrics.label_override` and returned with the
  updated ride so the frontend can persistently correct badges after feedback.
- **Expiring athlete availability constraints** (`models.py`, `crud.py`,
  `services/availability.py`, `routers/ai.py`) — chat messages such as
  "Friday I have no time for training" are now captured as structured
  `athlete_availability_constraints` rows, included in coach prompts while
  active, and automatically ignored after their expiry date.

### Changed

- **Coach plan updates preserve hard availability constraints**
  (`services/prompts.py`) — Ask Trainer, plan adaptation, and next-ride
  recommendation prompts now treat user availability constraints from coach
  memory, athlete context, or the current conversation as binding, so workouts
  are not moved onto unavailable days even when that placement would be
  physiologically optimal.
- **Server-side guard for unavailable days** (`routers/ai.py`) — generated
  plans, adapted plans, Ask Trainer plan updates, and next-ride recommendation
  updates are sanitized before persistence so non-rest workouts cannot be saved
  on active no-training constraint dates.

## [0.37.0] - 2026-06-18

### Changed

- **Targeted clarification for ambiguous recommendations**
  (`services/prompts.py`) — Ask Trainer and next-session recommendation prompts
  can now ask exactly one short learning question when physiology and athlete
  context leave materially different recommendations plausible, while avoiding
  extra questions when recent data, known context, or fatigue/safety signals
  already make the recommendation clear. Answers to recommendation
  clarification questions are treated as potentially durable coach-memory
  context.

## [0.36.0] - 2026-06-18

### Changed

- **Psychological training tendencies in coach memory** (`services/prompts.py`)
  — coach-memory extraction now has an explicit category for durable patterns
  such as overtraining or undertraining bias, rest anxiety, FOMO, overanalysis,
  reassurance seeking, needing permission to rest, doing too much when fresh,
  and psychological benefit from MTB or easy/social rides, while avoiding
  one-off moods as permanent traits unless repeated, user-confirmed, or
  actionable for recovery, intensity, or rest decisions.

## [0.35.0] - 2026-06-18

### Changed

- **Two-layer coach recommendations** (`services/prompts.py`,
  `services/ai_service.py`, `routers/ai.py`, `services/ride_matching.py`) —
  Ask Trainer and next-session recommendation prompts now explicitly separate
  physiology inputs (CTL/ATL/TSB, recent load, feedback, planned stimulus) from
  athlete-context inputs (motivation, rest tolerance, adherence pattern,
  structured athlete context, and evidence-backed memory facts) so personal
  context can decide between physiologically similar options.

## [0.34.0] - 2026-06-18

### Added

- **Evidence-backed athlete memory facts** (`models.py`, `crud.py`,
  `routers/users.py`, `schemas.py`,
  `alembic/versions/20260618_000002_add_athlete_memory_facts.py`) — adds
  durable per-user coaching facts with category, source snippet/exchange,
  first-observed and last-confirmed timestamps, confidence, observation count,
  and active/stale/rejected/user-confirmed status, exposed via
  `GET/POST/PATCH /users/me/athlete-memory-facts`.
- **Prompt-safe durable memory filtering** (`crud.py`, `routers/ai.py`,
  `services/ai_service.py`, `services/prompts.py`) — Ask Trainer now receives
  only user-confirmed or sufficiently fresh, high-confidence facts, while stale,
  rejected, or low-confidence observations are omitted from coach prompts.

## [0.33.0] - 2026-06-18

### Added

- **Structured athlete context model** (`models.py`, `crud.py`, `routers/users.py`,
  `schemas.py`, `alembic/versions/20260618_000001_add_athlete_context.py`) —
  adds a durable per-user athlete context with training tendency, rest response,
  motivation drivers, adherence pattern, strengths, weaknesses, preferred
  terrain/session types, coaching risks, and notes, exposed via
  `GET/PUT /users/me/athlete-context`.
- **Athlete context in coach prompts** (`services/prompts.py`,
  `services/ai_service.py`, `routers/ai.py`) — Ask Trainer now receives a
  compact structured athlete-context section alongside existing free-text coach
  memory so durable coaching traits can guide replies without breaking coach
  memory behavior.

## [0.32.6] - 2026-06-17

### Fixed

- **Coach chat reload ordering** (`crud.py`, `routers/ai.py`) — chat history
  reloads now preserve user/assistant turn order for messages created in the
  same exchange instead of tie-breaking equal timestamps by UUID, preventing
  assistant replies from shifting under the wrong question after a page reload.

## [0.32.5] - 2026-06-17

### Fixed

- **Ride metric source-key dedupe** (`crud.py`, `models.py`,
  `alembic/versions/20260611_000001_add_activity_source_to_ride_metrics.py`,
  `alembic/versions/20260617_000001_dedupe_ride_metric_source_keys.py`) —
  imported activity metrics now upsert by `(user_id, activity_source,
  external_activity_id)`, stale duplicate source-key rows are cleaned before
  relying on the unique index, and ride-metrics history collapses any remaining
  duplicate visible rows.
- **Persisted Ask Trainer plan updates** (`routers/ai.py`, `schemas.py`) —
  `/ai/ask-trainer` now returns the persisted full `updatedPlan` after applying
  plan updates so clients can refresh from the authoritative stored plan.
- **Coach date grounding coverage** (`tests/test_ai_service_unit.py`) — added a
  June 17, 2026 Europe/Berlin prompt regression covering recent activity
  history, upcoming plan entries, and exact weekday/date labels.

## [0.32.4] - 2026-06-15

### Fixed

- **Coach upcoming-day interpretation** (`services/prompts.py`,
  `tests/test_ai_service_unit.py`) — Ask Trainer now treats "upcoming",
  "next", and "coming days" as today-and-future plan entries only, and must not
  answer those questions from historical plan days or call non-rest recovery
  sessions "pure rest".

## [0.32.3] - 2026-06-14

### Fixed

- **Coach date grounding** (`services/ai_service.py`, `services/prompts.py`,
  `tests/test_ai_service_unit.py`) — Ask Trainer prompts now include
  precomputed weekday/date labels and today/tomorrow annotations on plan days,
  and the coach is instructed to copy those labels instead of inventing weekday
  names from model memory.

## [0.32.2] - 2026-06-14

### Fixed

- **Activity summary refresh identifiers** (`crud.py`, `schemas.py`,
  `tests/test_crud.py`) — process-pending-feedbacks now accepts stable external
  activity IDs as well as numeric IDs, so Intervals/FIT-derived 64-bit activity
  identifiers are not lost to JavaScript number rounding before the backend
  looks up ride metrics.

## [0.32.1] - 2026-06-14

### Fixed

- **Activity summary prompt freshness** (`services/prompts.py`, `crud.py`,
  `tests/test_ai_service_unit.py`) — summaries generated for newly visible
  ride metrics now include activity names and treat the listed activities as
  authoritative, preventing stale assessment notes from causing "Your Recent
  Training Summary" to describe an older hike instead of the latest MTB ride.
  The prompt also marks the latest listed activity explicitly and requires the
  first summary bullet to anchor on it, with ride metric lookups ordered by date,
  start time, and activity id. Older free-form rider-assessment notes are no
  longer included in this refresh prompt, so stale notes cannot reintroduce an
  older activity. If the LLM returns an empty or incomplete summary, the backend
  now writes a deterministic fallback summary based on the newest ride metric.

## [0.32.0] - 2026-06-11

### Added

- **Source-neutral activity import contract** (`services/activity_imports.py`,
  `services/activity_sync.py`, `routers/ai.py`, `routers/strava.py`,
  `routers/users.py`, `services/intervals_service.py`) — normalizes Strava,
  Intervals.icu, and FIT imports into a shared imported-activity payload before
  ride-metric calculation while preserving the existing `strava_activity_id`
  dashboard contract.

- **Ride metric source metadata** (`models.py`, `crud.py`, `schemas.py`,
  `alembic/versions/20260611_000001_add_activity_source_to_ride_metrics.py`) —
  stores `activity_source`, `external_activity_id`, and optional source metadata
  on ride metrics, with centralized source/external-id lookup for dedupe.

- **Import normalization coverage** (`tests/test_activity_imports.py`,
  `tests/test_activity_sync.py`, `tests/test_intervals.py`,
  `tests/test_contract.py`) — verifies multiple sources use the same normalized
  contract and that duplicate detection still skips already-imported activities.

## [0.31.0] - 2026-06-10

### Added

- **Backend scheduler foundation** (`services/scheduler.py`, `main.py`,
  `services/plan_maintenance.py`, `services/activity_sync.py`) — adds a shared
  in-process recurring job registry with per-job duplicate-run protection,
  deterministic `run_once` support, structured lifecycle logs, and clean FastAPI
  lifespan startup/shutdown.

- **Scheduler documentation and coverage** (`README.md`, `tests/test_scheduler.py`)
  — documents the single-backend-replica Docker assumption, job registration
  pattern, current recurring jobs, and fake-sleep/direct-run testing approach.

## [0.30.0] - 2026-06-10

### Added

- **Backend activity-triggered plan updates** (`services/activity_sync.py`,
  `main.py`, `config.py`) — adds an in-process periodic activity sync that
  checks connected Strava and Intervals.icu sources with auto-sync enabled,
  imports newly detected activities, updates ride metrics, matches planned
  workouts, and triggers plan adaptation for matched rides without requiring a
  browser session.

- **Activity sync coverage** (`tests/test_activity_sync.py`) — covers disabled
  source skips, duplicate detection, independent Strava/Intervals cursors, and
  per-user/source failure isolation.

## [0.29.3] - 2026-06-10

### Fixed

- **Intervals.icu rounded cursor matching** (`routers/intervals.py`,
  `tests/test_intervals.py`) — accepts the small JavaScript number-rounding drift
  that can occur when 63-bit Intervals activity hashes are sent back as
  `after_id`, so newly returned activities are not discarded as a missing cursor.

## [0.29.2] - 2026-06-10

### Added

- **Intervals.icu import diagnostics** (`routers/intervals.py`, `routers/ai.py`,
  `routers/users.py`) — logs bounded activity samples, cursor-filter results,
  analysis upserts, history-import persistence, and ride-metrics-history
  responses so missing Intervals activities can be traced from API fetch through
  database visibility.

## [0.29.1] - 2026-06-10

### Fixed

- **Intervals.icu same-day activity fetches** (`routers/intervals.py`,
  `tests/test_intervals.py`) — Intervals activity list and history-import
  windows now use the app-local date and send tomorrow as the `newest` bound so
  activities completed today are included immediately instead of waiting until
  the next calendar day.

## [0.29.0] - 2026-06-09

### Added

- **Daily backend training-plan maintenance** (`services/plan_maintenance.py`,
  `main.py`, `crud.py`) — adds an in-process 02:00 app-timezone maintenance job
  that detects stale incomplete plan days for onboarded users and reuses the
  existing plan adaptation flow so schedules stay current without a browser login.

- **Plan maintenance coverage** (`tests/test_plan_maintenance.py`) — verifies
  stale-plan updates, no-op current plans, non-onboarded user skips, timezone
  cutoffs, idempotency, per-user failure isolation, and 02:00 scheduling math.

## [0.28.5] - 2026-06-09

### Fixed

- **Timestamped application logs** (`logging.yaml`) — root/application logger
  output now uses the same timestamped formatter as Uvicorn logs, so auth and
  validation warnings include dates in Docker output.

## [0.28.4] - 2026-06-09

### Fixed

- **JWT secret length validation** (`auth.py`, `.env.example`, `README.md`) —
  HS256 deployments now fail fast when `JWT_SECRET` is shorter than 32 bytes,
  while dev/test environments log one clear warning and suppress PyJWT's repeated
  per-request `InsecureKeyLengthWarning`.

## [0.28.3] - 2026-06-09

### Fixed

- **Intervals.icu analysis payload compatibility** (`routers/intervals.py`,
  `schemas.py`, `routers/ai.py`) — Intervals.icu activity summaries now include
  the required Strava-shaped numeric defaults, and Intervals-triggered analysis
  updates the Intervals sync cursor instead of Strava state.

- **Validation failure diagnostics** (`main.py`) — request validation errors now
  log the failing route and schema fields so future 422 responses can be diagnosed
  from container logs without recording request bodies.

## [0.28.2] - 2026-06-09

### Fixed

- **Timestamped backend Docker logs** (`entrypoint.sh`, `logging.yaml`) — Uvicorn
  access and application log lines now include full date/time stamps in container
  output, making API requests and health checks easier to correlate.

## [0.28.1] - 2026-06-08

### Fixed

- **Intervals.icu auto-sync migration compatibility**
  (`alembic/versions/20260608_000002_add_intervals_auto_sync_enabled.py`) — adds
  `users.intervals_auto_sync_enabled` in a follow-up migration so databases that
  already applied `20260608_000001` still receive the new column.

## [0.28.0] - 2026-06-08

### Added

- **Activity automatic sync preferences** (`models.py`, `schemas.py`, `routers/users.py`,
  `alembic/versions/20260608_000001_add_strava_auto_sync_enabled.py`,
  `alembic/versions/20260608_000002_add_intervals_auto_sync_enabled.py`) — adds
  persisted per-user `stravaAutoSyncEnabled` and `intervalsAutoSyncEnabled` flags that
  default to enabled for existing behavior while allowing the frontend to disable
  automatic polling for either activity source.

- **Intervals.icu activity importer** (`routers/intervals.py`, `services/intervals_service.py`,
  `models.py`, `crud.py`, `schemas.py`) — added per-user Intervals.icu API-key storage,
  connection management endpoints, manual recent-activity import, best-effort activity
  detail/stream fetching, deterministic import IDs, and ride metric upserts so Strava can
  remain optional.

- **Intervals.icu credentials migration** (`alembic/versions/20260607_000001_add_intervals_tokens.py`)
  — adds encrypted per-user Intervals.icu API key storage with athlete metadata.

- **Intervals.icu import tests** (`tests/test_intervals.py`) — covers credential setup,
  successful import, duplicate-safe re-import, auth failure handling, and summary-only
  fallback when streams are missing.

### Changed

- **Intervals.icu app-open sync parity** (`routers/intervals.py`, `models.py`, `schemas.py`) —
  adds an Intervals activity-list endpoint plus independent profile cursor fields so the
  frontend can poll for new Intervals activities the same way it polls Strava.

## [0.27.2] - 2026-06-05

### Fixed

- **Auto-matched rides with >2.5× duration mismatch now set `label_override = "Mismatch"`**
  (`services/ride_matching.py`, `crud.py`) — `apply_ride_plan_matches` detects gross
  duration divergence at match time and writes a `Mismatch` label override so the
  frontend bypasses score computation and shows a clear warning badge immediately.

## [0.27.1] - 2026-06-03

### Fixed

- **Missing Alembic revision restored** (`alembic/versions/20260530_000001_add_ride_label_override.py`)
  — restored the `20260530_000001` migration so deployed databases stamped with that
  revision can resolve the migration graph and continue upgrading normally.

## [0.27.0] - 2026-06-03

### Added

- **Bulk FIT import fallback** (`routers/users.py`, `schemas.py`) — added
  `POST /users/me/upload-fit/bulk` for multi-file Garmin/Wahoo/Zwift uploads with
  per-file imported, skipped, and failed results while preserving the existing single-file
  upload endpoint.

- **Normalized FIT ride metrics** (`routers/users.py`) — FIT imports now derive a stable
  synthetic activity id from FIT metadata/start time/fingerprint, skip duplicate uploads,
  parse richer record streams (watts, heart rate, cadence, altitude, speed, time, GPS),
  and feed imported activities into the existing ride metrics pipeline.

- **Bulk FIT contract coverage** (`tests/test_contract.py`) — added API tests for batch
  success, duplicate skipping, ride metric creation, and partial failure handling.

## [0.26.3] - 2026-06-01

### Added

- **Strava API 2026 impact note** (`docs/strava-api-2026-impact.md`) — summarizes
  Strava's June 2026 developer-program changes, the app's current Strava API usage,
  policy risks around AI processing, and migration action points for the 2027 base
  URL and OAuth revoke changes.

## [0.26.2] - 2026-05-28

### Fixed

- **Ride-to-plan date matching** (`services/ride_matching.py`, `routers/users.py`) —
  ride history now re-applies plan matching from each activity's own `activity_date`, so
  activities imported or analyzed later cannot keep a planned-day snapshot from the
  processing date.

- **Stale plan snapshot cleanup** (`services/ride_matching.py`) — mismatched stored plan
  snapshots are cleared when no plan entry exists for the actual activity date, while
  same-day rest-day context is preserved for display.

## [0.26.1] - 2026-05-26

### Fixed

- **Coach chat date awareness** (`services/dates.py`, `services/ai_service.py`,
  `services/prompts.py`) — ask-trainer prompts now include an authoritative local
  date context for today, yesterday, and tomorrow, including weekdays and ISO dates,
  so the coach does not infer conflicting calendar dates from model knowledge or
  conversation history.

- **Date-awareness regression coverage** (`tests/test_dates.py`,
  `tests/test_ai_service_unit.py`) — added focused tests for the generated date
  context and its injection into the ask-trainer system prompt.

## [0.26.0] - 2026-05-24

### Added

- **Weather enrichment for ride metrics** (`services/weather_service.py`, `models.py`,
  `schemas.py`, `crud.py`) — Strava activities now persist start coordinates plus
  Open-Meteo temperature, apparent temperature, condition, weather code, wind,
  precipitation, and source metadata on `ride_metrics`.

- **Alembic migration `20260524_000001_add_ride_weather`** — adds the weather and
  coordinate columns to `ride_metrics` while remaining idempotent for existing databases.

- **Weather-aware Strava import and analysis** (`routers/strava.py`, `routers/ai.py`,
  `services/strava_service.py`) — historical imports and new activity analysis request
  Strava GPS `latlng` streams and attach weather using the midpoint activity GPS point
  when available, falling back to Strava `start_latlng`; the lookup uses the activity
  midpoint date/time and switches to Open-Meteo archive data for older activities.
  Recent weather is included in activity analysis prompts.

- **Best-effort backfill for existing rides** (`routers/users.py`) — loading ride history
  attempts to fill missing weather for stored Strava rides by fetching activity details and
  querying Open-Meteo; failures are logged and do not block the dashboard.

### Changed

- **Weather-aware plan generation/adaptation** (`services/prompts.py`, `services/ai_service.py`,
  `routers/ai.py`) — upcoming forecast context near the athlete's usual activity location is
  passed into plan generation and adaptation so the coach can shorten hot-day sessions, add
  warmup/caution for cold days, or swap unsafe weather to indoor, recovery, or strength work.

## [0.25.2] - 2026-04-26

### Added

- **`X-Request-ID` middleware** (`main.py`) — every request is tagged with a correlation
  ID read from the incoming `X-Request-ID` header (client-generated) or a fresh UUID when
  absent; the ID is stored on `request.state.request_id` for route handlers and exception
  handlers, and is echoed back in the response header so browser logs and server logs can
  be correlated by ID.  CORS is updated to allow and expose the header.

- **Global `HTTPException` handler with structured logging** (`main.py`) — replaces the
  default FastAPI handler; logs method, path, status, and `request_id` at `WARNING` for
  4xx and `ERROR` for 5xx, then returns the same `{"detail": …}` JSON body.  Any
  `WWW-Authenticate` or other headers set by the raising code are preserved.

- **`recalculate-metrics` failure log** (`routers/users.py`) — `ValueError` rejections
  are now logged at `WARNING` with `user_id` and `request_id` before the 400 is raised.

- **Background Strava import failure log** (`routers/strava.py`) — unhandled exceptions
  in `_run_import_background` are now logged at `ERROR` with `user_id`, processed/total
  activity counts, and a full traceback (`exc_info=True`) before updating the progress
  state to `"error"`.

## [0.25.1] - 2026-04-25

### Fixed

- **Manual metric recalculation refreshes persisted ride feedback** (`services/metrics_service.py`)
  — rebuilding TSS, CTL, ATL, and TSB now also regenerates `rider_assessment.last_ride_feedback`
  from the latest recalculated ride so coach feedback stays aligned with the new FTP-based metrics.

- **Regression test for recalculation feedback refresh** (`tests/test_users.py`) — added an API
  test that confirms `POST /users/me/recalculate-metrics` updates the stored last-ride feedback
  after recalculation.

## [0.25.0] - 2026-04-25

### Added

- **`use_estimated_ftp` column on `User` model** (`models.py`) — boolean flag (default
  `False`) controlling whether AI-estimated or user-entered FTP is used for training-load
  computations (TSS, CTL, ATL, TSB).

- **Alembic migration `20260425_000001_add_use_estimated_ftp`** — adds the column to the
  `users` table with `server_default=false()`, backward-compatible with existing data.

### Changed

- **FTP priority for training-load calculations** (`routers/ai.py` `analyse_activities`)
  — user-entered FTP (`current_ftp`) is now the default for all TSS/CTL/ATL computations.
  Estimated FTP is only used when `use_estimated_ftp=True`. Previously estimated FTP
  always took precedence.

- **FTP priority in AI plan generation / chat** (`services/ai_service.py`) —
  `adapt_training_plan()` and `ask_trainer()` now check `profile["useEstimatedFTP"]` to
  decide which FTP value is passed to the AI prompt (user-entered first by default).

- **User schemas** (`schemas.py`) — `UserResponse`, `UpdateProfileRequest`, and
  `UserProfileSchema` gain `use_estimated_ftp` / `useEstimatedFTP` field.

- **Users router** (`routers/users.py`) — `_user_to_response()` maps the new field.

## [0.24.0] - 2026-04-25

### Added

- **`compute_readiness_recommendations`** (`services/analysis.py`) — new function that
  produces a list of short, actionable bullet-point tips based on the athlete's current
  CTL, ATL, TSB, overall readiness score, and days until race. Rules cover:
  - TSB/form bands (severe fatigue → very fresh) with specific training advice per band
  - CTL/fitness bands with base-building vs quality-focus guidance
  - Race-countdown phases (>21 days → build; 10–21 → taper; 3–10 → final taper; ≤3 → rest)
  - Overall score nudge when score is below the 65-point "Race Ready" threshold

- **`recommendations` field on `ReadinessScoreResponse`** (`schemas.py`) — `list[str]`
  field (default `[]`) carrying the output of `compute_readiness_recommendations`.

### Changed

- **`GET /ai/readiness-score`** (`routers/ai.py`) — imports and calls
  `compute_readiness_recommendations` and includes the result in every response.

## [0.23.0] - 2026-04-24

### Changed

- **Auto-rate new rides: planned type vs actual type comparison** (`services/prompts.py`,
  `services/ai_service.py`, `routers/ai.py`) — when a new Strava activity is downloaded and
  matched against the training plan, the AI coach now explicitly compares what was *planned*
  against what was *actually performed*:

  - `rate_workout_system()` instructs the AI to first check whether the ride's character
    (e.g. endurance, threshold intervals, VO2max) matches the planned type, and to call out
    any mismatch before commenting on power/HR numbers.

  - `rate_workout_user()` accepts a new `actual_ride_analysis` parameter (dict produced by
    `build_ride_analysis`) and renders an *"Actual ride character (algorithmically derived)"*
    section into the prompt.  The section includes the detected ride category, average power,
    and a bullet for each detected interval block (duration, avg watts, % FTP, avg HR).

  - `rate_workout_user()` now accepts `feedback=None` without crashing — auto-rate calls
    that have no user-entered feedback are handled gracefully.

  - `rate_completed_workout()` in `ai_service.py` gains a `ride_analysis: dict | None`
    keyword argument and forwards it to `rate_workout_user()`.

  - `_auto_rate_ride()` in `routers/ai.py` now calls `build_ride_analysis(streams, ftp)` and
    passes the result as `ride_analysis` so every auto-generated coach note reflects the true
    ride character.

## [0.22.0] - 2026-04-24

### Changed

- **`POST /ai/ask-trainer` — reduced response latency** — two optimisations cut the
  number of sequential LLM round trips the user waits for:

  1. **`classify_question` parallelised with DB fetches** — the question-classification
     call now runs concurrently with the `get_ride_metrics_history` database query instead
     of after it, hiding the 1–2 s classification latency behind an already-necessary DB
     operation.

  2. **`update_coach_memory` moved to a `BackgroundTask`** — coach-memory updates are
     now written after the HTTP response is sent rather than before it.  A dedicated
     `_update_memory_bg` helper manages its own database session via `async_session_maker`
     and swallows all errors gracefully (rate-limit and general exceptions are logged but
     never surfaced to the caller), preserving the existing best-effort semantics.

## [0.21.0] - 2026-04-22

### Added

- **`threshold_heart_rate` in `POST /users/me/estimate-ftp`** — the endpoint now accepts an
  optional `threshold_heart_rate` (bpm) alongside `max_heart_rate` and `resting_heart_rate`.
  When provided the value is immediately persisted to the user profile so subsequent FTP
  estimation and metric snapshots include the athlete's lactate-threshold HR.  The
  `EstimateFTPRequest` schema (`schemas.py`) has been updated accordingly.

- **Threshold HR input in Settings page** (`frontend/src/pages/SettingsPage.tsx`) — a third
  heart-rate field ("Threshold Heart Rate") is now shown in the HR Settings section, with a
  helper note explaining it as the lactate-threshold HR (~87 % of max HR).  The current stored
  value is displayed alongside max and resting HR.  The "Save & Estimate FTP" button is enabled
  when any of the three HR fields (or age) contains a value.

- **Metrics history refresh after recalculate** (`frontend/src/pages/SettingsPage.tsx`) — after
  `handleConfirmFTP()` and `handleRecalculate()` complete successfully, `fetchMetricsHistory()`
  is called and the result written to the Zustand store.  This means the FTP progression chart on
  the Dashboard updates immediately without requiring a page reload.

### Changed

- **`POST /users/me/recalculate-metrics` — per-ride `AthleteMetricSnapshot` creation** — previously
  the endpoint deleted all historical metric snapshots and replaced them with a single final
  snapshot (CTL/ATL/TSB/FTP values at the end of the last ride).  It now creates **one back-dated
  snapshot per ride** (`source = "manual_recalculate"`, `recorded_at` set to the ride date).  This
  preserves a full time-series for the FTP/CTL/ATL progression chart after any manual
  recalculation.

- **`estimateFTP()` frontend service** (`frontend/src/services/user.ts`) — now forwards
  `thresholdHeartRate` to the backend in the request body.

### Fixed

- A `ValueError` when parsing a malformed `activity_date` inside `recalculate_metrics()` now
  emits a warning log entry (including the activity ID) instead of silently using the current
  timestamp without any diagnostic information.

## [0.20.0] - 2026-04-22

### Added

- **`GET /users/me/ride-metrics-history`** — new endpoint that returns the most recent
  90 per-ride CTL/ATL/TSB records in chronological (oldest-first) order, sourced from
  the `ride_metrics` table.  Unlike the sparse `AthleteMetricSnapshot` data (one point
  per analysis run), this gives one data point per ride so expert users can see the full
  time series of how their training load develops.

- **`RideMetricHistoryResponse` schema** (`schemas.py`) — wraps a list of existing
  `RideMetricSchema` objects (`rides: list[RideMetricSchema]`).

- **Backend tests** (`tests/test_users.py`): three new test cases for the new endpoint —
  empty list for a new user; populated list with CTL/ATL/TSB after analyse-activities;
  unauthenticated access rejected with HTTP 401.


### Added

- **`POST /users/me/estimate-ftp`** — new endpoint that saves `max_heart_rate` and/or
  `resting_heart_rate` to the user profile and returns the best available FTP estimate.
  Priority order: most recent `AthleteMetricSnapshot` → `RiderAssessment.estimated_ftp` →
  `User.current_ftp` → `null`.  When `resting_heart_rate` has never been set it is
  automatically defaulted to 60 bpm.  Response includes a `source` field
  (`"ftp_estimation"`, `"strava_analysis"`, `"rider_assessment"`, `"profile"`, or `"none"`)
  so the frontend can give the user meaningful context.

- **`EstimateFTPRequest` / `EstimateFTPResponse` schemas** (`schemas.py`) — both fields of
  the request (`max_heart_rate`, `resting_heart_rate`) are optional.  The response carries
  `estimated_ftp` (nullable int) and a `source` string.

- **Backend tests** (`tests/test_users.py`): four new test cases covering the new endpoint:
  - no data returns `null` FTP with source `"none"`;
  - HR values are persisted on the user profile;
  - missing `resting_heart_rate` is defaulted to 60;
  - existing `AthleteMetricSnapshot` FTP is returned correctly;
  - unauthenticated requests are rejected with HTTP 401.



### Changed

- **`estimate_ftp_over_time()` — improved algorithm** (`services/analysis.py`):

  - **Power-only fallback fixed** — replaced the incorrect `0.95 × steady_5-min_segment_power`
    formula (which could wildly underestimate FTP for sub-threshold efforts) with the standard
    FTP-test protocol: `best_20_min_power × 0.95`.  Rides shorter than 20 minutes are skipped
    in this path since a meaningful 20-min max effort cannot be produced from them.

  - **Smoothing changed from EWMA to 3-week sliding-window max** — for each date point the
    smoothed FTP is now the *maximum* raw estimate from any ride in the preceding 21 days
    (``smoothing_days`` default changed from 42 to 21).  Taking the max rather than an
    exponential average means a single strong ride correctly propagates while easy / recovery
    rides — which produce lower estimates — no longer drag the series down.  FTP reflects
    current capability, not an average of all recent efforts.

  - New private helper `_in_window(date_str, start, end)` used by the sliding-window loop.

## [0.18.0] - 2026-04-22

### Added

- **FTP estimation over time** (`services/analysis.py`) — new `estimate_ftp_over_time()`
  function identifies rides with steady-state power intervals (coefficient of variation < 15 %),
  estimates FTP for each using the existing HR-corrected formula (or a power-only fallback), then
  applies an exponential weighted moving average (42-day time constant) so that the resulting FTP
  series changes gradually.  Returns a list of `{date, ftp, raw_ftp}` dicts ordered by ride date.

- **FTP estimation during activity ingestion** — `estimate_ftp_over_time` is now called inside
  `POST /ai/analyse-activities` and the Strava background import task (`_run_import_background`).
  Per-ride smoothed FTP estimates are persisted as `AthleteMetricSnapshot` rows with
  `source = "ftp_estimation"` and `recorded_at` set to the ride date so they appear correctly
  on the Athlete Progression chart.

- **`POST /users/me/recalculate-metrics`** — new endpoint that rebuilds the full TSS / CTL / ATL /
  TSB chain for all stored rides using a given FTP value.  Accepts an optional `ftp_override`
  (watts); if omitted, the user's current FTP is used.  Clears all previous
  `athlete_metric_snapshots` and writes a fresh summary snapshot.  Used by the new Settings UI.

- **`crud.delete_athlete_metric_snapshots()`** — deletes all AthleteMetricSnapshot rows for a
  user, used before rebuilding the metric history.

- **`crud.get_all_ride_metrics_ordered()`** — returns all RideMetric rows for a user sorted
  ascending by date, used during full-chain recalculation.

- **`crud.create_athlete_metric_snapshot()` — `recorded_at` parameter** — the existing function
  now accepts an optional `recorded_at: datetime` argument so back-dated FTP-estimation snapshots
  can be inserted with the correct ride date.

### Changed

- `strava._run_import_background` now accepts `max_heart_rate` and `resting_heart_rate` from the
  user profile and passes them to `estimate_ftp_over_time` so HR-based correction is applied when
  the athlete's max HR is on record.



### Added

- **Per-ride time-series metrics pipeline** — a structured `ride_metrics` table now stores TSS,
  normalised power, intensity factor, CTL/ATL/TSB, FTP used, ride purpose, rule-based summary,
  and coach/user notes for every ride. This replaces plan-estimate-based fitness tracking with
  values derived from actual completed rides.
  - `models.py` — new `RideMetric` ORM model; `User` now has a `ride_metrics` relationship.
  - `alembic/versions/20260421_000001_add_ride_metrics.py` — idempotent migration creating the
    `ride_metrics` table with a unique index on `(user_id, strava_activity_id)`.
  - `services/analysis.py` — four new pure functions: `compute_ride_tss()`,
    `apply_ctl_atl_decay()` (exponential weighted average with multi-day gap handling),
    `build_rule_based_summary()`, and `build_ride_metrics_chain()`.
  - `crud.py` — five new async functions: `upsert_ride_metric()`, `get_ride_metrics_history()`,
    `get_latest_ride_metric()`, `update_ride_metric_notes()`, `get_ride_metric_by_date()`.
  - `schemas.py` — `RideMetricSchema` and `ImportHistoryResponse` schemas.

- **Historical Strava import endpoint** (`POST /strava/import-history`) — fetches up to 6 months
  of Strava activities (configurable via `?months=` query param, clamped 1–24), computes the full
  CTL/ATL/TSB chain, and bulk-upserts ride metrics. Safe to call repeatedly; does not overwrite
  existing coach or user notes.

- **Ride metrics context injected into all AI prompts** (`services/prompts.py`,
  `services/ai_service.py`, `routers/ai.py`) — `ride_metrics_context_section()` renders the 30
  most recent rides as a compact table (date | purpose | TSS | NP | CTL | ATL | TSB | summary +
  notes). This section is passed to `generate-plan`, `adapt-plan`, and `ask-trainer` so the LLM
  reads pre-computed structured data instead of re-processing raw activities.

- **Coach auto-rating at ingestion** — when `POST /ai/analyse-activities` processes a ride that
  matches a plan day, `rate_completed_workout()` is called automatically and the result is stored
  as `coach_note` on the `RideMetric` row.

- **User feedback inference via chat** (`routers/ai.py`) — the `ask-trainer` endpoint now
  extracts a `ride_note_update` field from the LLM response. If the user mentions how a ride felt
  in conversation, the inferred note is persisted as `user_note` on the matching `RideMetric` row
  without any extra user action.

### Changed

- **`POST /strava/import-history` defaults to 6 months** — the `after` timestamp is sent directly
  to Strava's API so only recent activities are downloaded; no full-history pagination needed.
- **`rate_completed_workout()` guard fix** (`services/ai_service.py`) — changed early-return
  condition from `if not feedback` to `if not feedback and not stream_delta` so the coach can
  auto-rate from stream data alone without requiring user-entered feedback.
- **`rate_workout` endpoint cleanup** (`routers/ai.py`) — inline auto-adapt block replaced with
  the shared `_auto_adapt_plan()` helper.

## [0.16.3] - 2026-04-21

### Changed

- **Token-usage compaction** (`services/ai_service.py`, `services/prompts.py`) — reduces the
  number of tokens sent per `ask-trainer` request to lower the risk of hitting per-minute TPM
  limits:
  - `MAX_CONVERSATION_HISTORY` reduced from 20 → **10** messages (saves ~1.5–3k tokens).
  - Upcoming plan context reduced from 14 → **7 days** ahead (saves ~700–2.8k tokens).
  - Plan entries sent to the model are now **slimmed** via `_slim_plan_entry()` — verbose fields
    (`description`, `intervals`, `keyFocusPoints`, `coachFeedback`) are stripped; only scheduling
    fields (`date`, `workoutType`, `durationMinutes`, `title`, `targetPower`, `completed`) are kept.
    This cuts per-entry token cost by ~50–70%.
  - Coach memory is **capped at 800 characters** (most recent notes), preventing unbounded growth
    from inflating the system prompt.

## [0.16.2] - 2026-04-21

### Changed

- **Gemini model upgrade** (`services/ai_service.py`) — switched `GEMINI_MODEL` from
  `gemini-2.0-flash` to `gemini-2.5-flash` (Gemini Flash 3). The 2.5 generation has higher
  per-minute token throughput limits on paid tiers, reducing the likelihood of 429
  RESOURCE_EXHAUSTED errors observed with 2.0-flash at low monthly spend.

## [0.16.1] - 2026-04-21

### Fixed

- **Gemini 429 RESOURCE_EXHAUSTED** (`services/ai_service.py`, `routers/ai.py`) — when the Gemini
  API returns an HTTP 429 rate-limit error the backend previously let the raw exception propagate,
  producing an opaque `500 Internal Server Error`. The fix adds a dedicated `AIRateLimitError`
  exception: both `_chat()` and `_chat_history()` now catch `google.genai.errors.ClientError` with
  `code == 429` and raise `AIRateLimitError` instead. All AI endpoints (`analyse-activities`,
  `generate-plan`, `adapt-plan`, `ask-trainer`, `rate-workout`, `refresh-login-summary`) convert
  this into an HTTP **503 Service Unavailable** with a user-friendly message. The
  `update_coach_memory` call inside `ask-trainer` is treated as best-effort: a rate-limit there is
  logged at `INFO` level and does not block the response.

  The Gemini free-tier 429 is a **per-minute / per-day token-quota** error rather than a billing
  limit. Common triggers even at low spend: very long conversation histories, large system prompts
  (training plan + RAG context + coach notes all included in every request), or Gemini's own
  per-region capacity limits. The 503 response lets the frontend show a clear retry message instead
  of a generic server crash.

### Tests

- `test_chat_raises_ai_rate_limit_error_on_gemini_429` — `_chat` raises `AIRateLimitError` on a
  mocked 429 `ClientError`.
- `test_chat_history_raises_ai_rate_limit_error_on_gemini_429` — same for `_chat_history`.
- `test_ask_trainer_endpoint_returns_503_on_rate_limit` — `/ask-trainer` returns 503.
- `test_analyse_activities_endpoint_returns_503_on_rate_limit` — `/analyse-activities` returns 503.
- `test_generate_plan_endpoint_returns_503_on_rate_limit` — `/generate-plan` returns 503.

## [0.16.0] - 2026-04-20

### Changed

- **Coach talking style** (`services/prompts.py`) — `COACH_PERSONA` and `RUNNING_COACH_PERSONA`
  are rewritten to give the AI a warm, empathic, friend-like voice:
  - The coach is framed as *"a great friend who happens to know a lot about [sport]"* — casual and
    approachable, never stiff or clinical.
  - The AI is instructed to use the athlete's first name, celebrate wins enthusiastically,
    acknowledge struggles with compassion, and never make the athlete feel judged for missing a
    session or falling short.
  - Honest feedback is preserved but framed with kindness: *"like a great friend who tells you the
    truth because they care about you."*
  - A shared `_FRIEND_COACH_TRAITS` template eliminates duplication between the cycling and running
    personas.

### Tests

- New section `COACH_PERSONA / RUNNING_COACH_PERSONA — friendly talking style` in
  `tests/test_ai_service_unit.py` (10 assertions across 9 test functions):
  - `test_coach_persona_is_friend_framed` — verifies friend framing and sport specificity
  - `test_running_coach_persona_is_friend_framed` — mirrors the above for running
  - `test_personas_are_sport_distinct` — asserts the two personas are unique and non-overlapping
  - `test_coach_persona_empathy_traits` — checks for compassion/no-judgement language
  - `test_coach_persona_encourages_direct_address` — verifies "address the athlete directly"
  - `test_coach_persona_retains_long_term_philosophy` — guards against losing the development ethos
  - `test_analyse_activities_system_uses_correct_persona` — ensures routing to the right persona by sport type
  - `test_generate_plan_system_uses_coach_persona` — checks persona is embedded in plan generation
  - `test_rate_workout_system_uses_coach_persona` — checks persona is embedded in workout rating
  - `test_friend_coach_traits_template_interpolation` — validates `{sport}` placeholder is always resolved



### Added

- **Post-login ride summary (`loginSummary`)** — the `analyse-activities` endpoint now generates a structured 4-part coach summary covering: (1) what the athlete did and what was strong/improvable, (2) FTP and fitness insights, (3) training plan alignment (how recent rides matched the plan), and (4) actionable conclusions for upcoming sessions. Stored in the new `rider_assessments.login_summary` column.

- **Training plan context in activity analysis** — `analyse_strava_activities` now accepts an optional `training_plan` parameter. When a plan exists the prompt receives it so the AI can compare actual rides against planned sessions and produce an accurate plan-alignment section in `loginSummary`.

- **`loginSummary` field added to `RiderAssessmentSchema`** (`schemas.py`) — the field is serialised as `loginSummary` (camelCase) for the frontend.

- **`login_summary` column added to `rider_assessments`** (`models.py`) — nullable `Text` column; Alembic migration `20260420_000001`.

### Changed

- **`analyse_activities_user` prompt helper** (`services/prompts.py`) — accepts optional `training_plan` list; when provided, appends the plan JSON to the user message so the AI can assess alignment for `loginSummary`.

- **`analyse_activities` router** (`routers/ai.py`) — fetches the training plan once and reuses it for both the AI call (plan-alignment context) and the training-load computation (no extra DB round-trip).

## [0.14.0] - 2026-04-20

### Added

- **`analyse_fit_activity` AI function** (`services/ai_service.py`) — lightweight analysis path for `.fit` imports where only summary metrics are available (no per-second streams):
  - Cycling: FTP estimated as `avg_power × AVG_POWER_TO_FTP_RATIO`
  - Running: `estimatedFTP` forced to `null`; threshold HR estimated via `max_heart_rate × LTHR_RATIO` when max HR is provided
  - Returns the same shape as `analyse_strava_activities` so `upsert_rider_assessment` can consume it unchanged

- **`.fit` uploads now trigger AI analysis and write a metric snapshot** (`routers/users.py`):
  - After saving the `WorkoutLog` row, calls `analyse_fit_activity` (best-effort — the upload response is never blocked on AI errors)
  - AI result is persisted to `rider_assessment` via `crud.upsert_rider_assessment`, giving non-Strava users last-ride feedback and ride insights on their dashboard
  - `crud.create_athlete_metric_snapshot` is called with `source="fit_upload"` so the athlete's FTP/HR history chart is populated; falls back to algorithmic estimates when AI is unavailable

- **Sport-type-aware AI prompts** (`services/prompts.py`):
  - `RUNNING_COACH_PERSONA` constant — mirrors `COACH_PERSONA` but with a running background
  - `analyse_activities_system(sport_type)` branches on `sport_type`: running activities receive HR-zone categories, running vocabulary (run/runs), and no references to watts or FTP; cycling retains the previous prompt unchanged
  - `analyse_activities_user(sport_type)` adjusts the instruction note — for running, the AI is explicitly told to set `estimatedFTP` to `null` and use pre-computed threshold HR verbatim

- **`analyse_strava_activities` now accepts `sport_type`** (`services/ai_service.py`) — power-based FTP computation and per-ride interval analysis are skipped when `sport_type` is `"running"` or `"run"`, preventing nonsensical cycling analysis for runners

### Tests

- `analyse_fit_activity` mock added to `conftest.py` `mock_ai_service` fixture
- New contract test `test_fit_upload_writes_metric_snapshot` — uploads a mocked `.fit` file and asserts that an `AthleteMetricSnapshot` row with `source="fit_upload"` appears in `GET /users/me/metrics-history`



### Changed

- **`adapt_plan_system` prompt** — the instruction `"Keep the same date fields"` is replaced with explicit guidance: future days keep their dates; past incomplete days must be rescheduled to upcoming dates starting from today.
- **`adapt_plan_user` prompt** — when incomplete days with past dates are present, a `NOTE:` sentence is appended that tells the AI coach exactly how many sessions are overdue and asks it to reschedule them to current/upcoming dates.

## [0.12.0] - 2026-04-19

### Added

- **`.fit` file upload — multi-sport / non-Strava ingest** (`routers/users.py`, `schemas.py`, `crud.py`, `models.py`):
  - New `POST /users/me/upload-fit` endpoint — accepts a `.fit` file (Garmin / Wahoo / Zwift export), parses it with `fitparse`, normalises session/record messages into a `WorkoutLog` row, and returns summary metrics (`sport_type`, `duration_minutes`, `average_power`, `average_heart_rate`)
  - `sport_type: str` column added to `WorkoutLog` (default `"cycling"`) — populated from the FIT `session.sport` field; enables multi-sport filtering in future
  - `FitUploadResponse` schema added to `schemas.py`
  - Alembic migration `20260419_000001` — adds `sport_type` column to `workout_logs`
  - Dependencies added: `fitparse>=1.2.0`, `python-multipart>=0.0.22`

### Tests

- 4 new backend contract tests covering:
  - `GET /users/me/metrics-history` empty list for new user
  - `GET /users/me/metrics-history` snapshot populated after `analyse-activities`
  - `POST /users/me/upload-fit` rejects non-.fit files with HTTP 422
  - `POST /users/me/upload-fit` rejects corrupt .fit data with HTTP 422

## [0.11.0] - 2026-04-19

### Added

- **Athlete Long-Term Memory / Progression Model** (`models.py`, `crud.py`, `routers/users.py`, `schemas.py`, `routers/ai.py`):
  - New `athlete_metric_snapshots` table — stores per-user time-series snapshots of `ftp`, `threshold_hr`, `ctl`, `atl`, `tsb`, `source`, and `recorded_at`; Alembic migration `20260418_000002`
  - CRUD helpers: `create_athlete_metric_snapshot`, `get_athlete_metric_history(db, user_id, limit=90)` returning snapshots oldest-first
  - `AthleteMetricSnapshotSchema` (camelCase via `CamelModel`) and `MetricsHistoryResponse` schemas
  - `POST /ai/analyse-activities` now records a metric snapshot after every Strava analysis; CTL/ATL/TSB are computed from the current training plan via `compute_training_load` and only stored when a valid FTP is available
  - `GET /users/me/metrics-history` — new authenticated endpoint returning the athlete's complete metric snapshot history

### Tests

- 8 new backend tests covering:
  - `create_athlete_metric_snapshot` and `get_athlete_metric_history` (null values, ascending order, limit)
  - `GET /users/me/metrics-history` (empty list, post-analysis snapshot, auth guard)

## [0.10.0] - 2026-04-19

### Added

- **Race-Day Readiness Score** (`services/analysis.py`, `routers/ai.py`, `schemas.py`):
  - `compute_readiness_score(ctl, atl, tsb, days_until_race) -> dict` — pure Python function producing a 0–100 readiness score as a weighted blend of form (65 %, TSB-based, peaks at TSB +10) and fitness (35 %, CTL-based, capped at 100)
  - `_project_training_load(plan_days, ftp, target_date_str) -> dict` — forward-projects CTL/ATL/TSB to any future date by evaluating only plan days on or before `target_date_str`; enables race-day projections
  - `ReadinessScoreResponse` schema — includes `score`, `form_score`, `fitness_score`, `ctl`, `atl`, `tsb`, `days_until_race`, `race_date`, and optional `projected_score/ctl/atl/tsb` at race day
  - `GET /ai/readiness-score` — authenticated endpoint that computes the current readiness score from plan days up to today, derives `days_until_race` from the user's `race_date`, and (when race is in the future and FTP is known) appends a forward-projected score at race day

- **Auto-taper injection** (`services/ai_service.py`, `services/prompts.py`):
  - `adapt_training_plan()` now detects `0 ≤ days_until_race ≤ 14` and passes a `taper_days_remaining` value to `adapt_plan_user()`
  - `adapt_plan_user()` gains an optional `taper_days_remaining: int | None` parameter; when set it appends an explicit `⚠️ TAPER ALERT` block instructing the LLM to cut volume ~40%, retain intensity, and target TSB +5 to +15 — enforcing taper structurally rather than relying on a passive prompt hint

### Tests

- 6 new unit tests in `backend/tests/test_ai_service_unit.py` covering `compute_readiness_score`:
  - Expected return keys
  - Score/component scores always in 0–100 range across a range of TSB values
  - Peak form (TSB = +10, high CTL → `form_score = 100`, `score > 80`)
  - Severe fatigue (TSB ≤ −30 → `form_score = 0`)
  - Zero load (CTL = ATL = TSB = 0 → `form_score = 50`, `fitness_score = 0`, `score = 32.5`)
  - `days_until_race` preserved in return value

## [0.9.0] - 2026-04-19

### Added

- **Expanded RAG knowledge base** — three new structured seed files added to `backend/knowledge/`:
  - `critical_power.md` — Monod-Scherrer two-parameter critical-power (CP) model, W′ (anaerobic work capacity), power-duration curve equation, field-test protocols (3-min all-out, multiple time-trials), W′ balance reconstitution modelling (Skiba et al., 2012), CP vs FTP distinction, and training implications for raising CP and expanding W′
  - `heat_altitude_adaptation.md` — acute heat-stress physiology, 10–14 day heat acclimatisation protocol with adaptation timeline (plasma volume, sweat rate, core temperature, HR), pre-cooling strategies (ice vest, ice slurry, cold-water immersion), hydration targets; altitude performance decrements by elevation (1500–4000 m), LHTH/LHTL/IHE strategies, practical altitude camp planning, iron status guidance, AMS prevention
  - `nutrition_timing.md` — carbohydrate loading (8–12 g/kg/day × 3 days), pre-race meal windows (3–4 h, 1–2 h, 15–30 min), on-bike intake by duration (0–120 g/h), multiple-transporter carbohydrates (2:1 glucose:fructose, gut training), post-exercise glycogen resynthesis window, MPS protein dosing (20–40 g), bedtime casein, caffeine ergogenic evidence (3–6 mg/kg), stage-race daily CHO targets
- **9 new Semantic Scholar search queries** in `backend/scripts/ingest_cycling_science.py` — covers critical power/W′, heat acclimatisation, altitude training, and nutrition timing; total queries raised from 8 to 17

### Changed

- `docs/update_rag.md` — updated to enumerate all 8 seed files with topic summaries and list all 17 Semantic Scholar search queries

## [0.8.0] - 2026-04-19

### Changed

- **AI service layer clean-up** — resolved several code-quality issues:
  - Stripped `_` prefix from all exported symbols in `analysis.py` (11 symbols, e.g. `_best_n_min_power` → `best_n_min_power`, `_AVG_POWER_TO_FTP_RATIO` → `AVG_POWER_TO_FTP_RATIO`)
  - Moved inline `import math` to module level in `analysis.py`; removed unused `total_time` variable in `classify_ride_purpose`
  - Extracted the FTP-estimation loop (~45 lines) from `ai_service.py` into a new `compute_ftp_from_streams(streams_by_id, max_heart_rate) → (ftp, threshold_hr)` function in `analysis.py`
  - Added `analyse_activities_computed_section()` to `prompts.py` — prompt text that was previously built inline in `ai_service.py`
  - Replaced three `__import__("datetime").datetime.now().date().isoformat()` calls with a top-level `import datetime` and `datetime.date.today().isoformat()`
  - Eliminated `_openai_chat`, `_openai_chat_history`, `_gemini_chat`, `_gemini_chat_history` — provider dispatch inlined directly into `_chat` / `_chat_history`
  - `classify_question` now accepts `provider: str = "openai"` and routes through `_chat(provider, ...)` instead of hardcoding OpenAI
  - `routers/ai.py` passes `provider=_provider(current_user)` to `classify_question` so Gemini callers are no longer silently routed to OpenAI for classification

### Added

- **Workout execution feedback loop with Strava stream analysis** (`services/analysis.py`, `schemas.py`, `routers/ai.py`, `services/ai_service.py`, `services/prompts.py`) — `rate_completed_workout` now optionally fetches per-second Strava stream data and computes an objective planned-vs-actual delta before calling the AI coach:
  - `RateWorkoutRequest` gains an optional `strava_activity_id: int | None` field; when provided the router fetches the activity's streams (watts/HR/cadence/time) from Strava, computes the delta, and forwards it to the prompt. Failures are caught and logged as warnings so the rating always completes.
  - Four new pure-Python functions in `analysis.py`:
    - `_normalized_power()` — standard 30-second rolling-average NP
    - `_time_in_power_zones()` — seconds spent in each of the 7 standard power zones (Z1 < 55 % FTP … Z7 > 150 % FTP)
    - `_detect_intensity_spikes()` — identifies non-overlapping 15-minute windows where average power exceeded the planned target midpoint by more than 10 %
    - `compare_planned_vs_actual(planned, streams, ftp)` — orchestrates the above to produce a structured delta dict: avg/NP power vs target (absolute watts + percentage), time-in-zones, HR drift (linear-regression slope across the session), HR vs target, and a list of intensity spikes
  - `rate_completed_workout()` in `ai_service.py` gains a `stream_delta: dict | None` keyword argument and forwards it to `rate_workout_user()`
  - `rate_workout_system()` instructs the AI to use stream data for precise, actionable language (e.g. *"you went 15 % over Z2 intensity in the first 30 min, which erodes your aerobic base and costs recovery"*)
  - `rate_workout_user()` renders a structured *"Objective stream data (from Strava)"* section in the prompt when `stream_delta` is present, including avg/NP delta lines, per-zone time breakdown, HR drift direction, and per-spike annotations
- **Frontend wiring** (`frontend/src/services/ai.ts`, `frontend/src/pages/WorkoutPage.tsx`) — `rateCompletedWorkout()` accepts an optional `stravaActivityId`; `WorkoutPage` reads the matching Strava activity from the React Query cache (keyed on `start_date` date prefix) and passes its ID automatically

## [0.6.0] - 2026-04-18

### Security

- **JWT secret startup guard** (`auth.py`, `main.py`) — the server now refuses to start when `JWT_SECRET` is still set to the insecure default `"change-me-in-production"` and `APP_ENV` is not a development/test environment (`development`, `dev`, `local`, `test`, `testing`). A `RuntimeError` with a clear message is raised inside the FastAPI `lifespan` handler so misconfigured production deployments fail loudly at boot rather than silently accepting forgeable tokens. Five new tests in `backend/tests/test_auth.py` cover all cases (raises in `production`/`staging` with default; passes in `development`/`test` with default; passes in `production` with a custom secret).

## [0.5.0] - 2026-04-18

### Added

- **`workoutPurpose` and `keyFocusPoints` fields** — `TrainingDaySchema` and `PlanDayUpdateSchema` gain two new optional fields:
  - `workout_purpose: Optional[str]` — 1–2 sentences describing the physiological goal of the session and why it is placed at this point in the plan
  - `key_focus_points: Optional[list[str]]` — 3–5 action-verb coaching cues for the athlete to focus on during execution

### Changed

- **`generate_plan_system()`** (`services/prompts.py`) — now instructs the AI to populate `workoutPurpose` and `keyFocusPoints` on every plan day; strengthens the `description` instruction to state **exact** power/HR targets derived from the athlete's FTP and threshold HR (percentages must always be translated to absolute watts/bpm)
- **`adapt_plan_system()`** (`services/prompts.py`) — same requirements applied to adapted days: `workoutPurpose`, `keyFocusPoints`, and number-grounded `description` are mandatory in every returned day
- **`ask_trainer_plan_updates_rule()`** (`services/prompts.py`) — any `planUpdates` entry emitted by the AI coach must now include `workoutPurpose`, `keyFocusPoints`, and a description with exact targets; the field list in both the context-workout and general rule branches is updated accordingly

## [0.4.0] - 2026-04-18

### Added

- **CTL/ATL/TSB training load metrics** (`services/analysis.py`) — new `_compute_training_load(plan_days, ftp) -> dict` function that computes:
  - Daily TSS approximated from `durationMinutes` + `targetPower` mid-point (or a workout-type heuristic when no power target is given)
  - CTL (chronic training load) — 42-day exponential weighted average of daily TSS
  - ATL (acute training load) — 7-day exponential weighted average of daily TSS
  - TSB (training stress balance / form) — CTL − ATL
  - Returns `{"ctl": float, "atl": float, "tsb": float, "daily_tss": list[float]}`
- **Training load injection into AI prompts** (`services/prompts.py`):
  - `ask_trainer_system` now includes a `training_load_section` with CTL/ATL/TSB and coaching guidance (*TSB < −20 → prioritise recovery; TSB > +10 before a key workout → increase intensity*)
  - `adapt_plan_user` accepts a `training_load` kwarg and surfaces the same metrics alongside rider assessment
- **Chain-of-thought `"thinking"` field** (`services/prompts.py`, `services/ai_service.py`) — `ask_trainer_system` instructs the model to reason through four steps (intent, fatigue state, training-principles conflict, best answer) and place the reasoning in a `"thinking"` field; `ask_trainer()` pops this key before returning so it never reaches the frontend
- **Training plan principles in `adapt_plan_system`** (`services/prompts.py`) — `adapt_plan_system()` now embeds `TRAINING_PLAN_PRINCIPLES` (the same no-back-to-back-hard-days / weekday-duration-cap block already used in `generate_plan_system()`), preventing adapted plans from violating core periodisation rules
- **`rider_assessment` forwarded to plan adaptation** (`services/ai_service.py`, `routers/ai.py`) — `adapt_training_plan()` and `adapt_plan_user()` now accept a `rider_assessment` dict so the AI knows the athlete's FTP and rider type when adapting; the `/ai/adapt-plan` router loads and passes the assessment automatically
- **Structured `rate_completed_workout` response** (`services/prompts.py`, `services/ai_service.py`, `schemas.py`) — `rate_workout_system` now returns JSON `{"feedback": str, "flag_for_adaptation": bool}`; `rate_completed_workout()` returns `dict` instead of `str`; `RateWorkoutResponse` schema gains `flag_for_adaptation: bool`
  - `flag_for_adaptation` is `true` when perceived effort ≫ planned intensity, actual duration is significantly shorter than planned, or athlete notes indicate fatigue/illness/pain
- **Auto-adaptation on flagged workouts** (`routers/ai.py`) — `/ai/rate-workout` automatically calls `adapt_training_plan` when `flag_for_adaptation` is `True`, using the completed workout's feedback as context; failures are caught and logged as warnings so the rating response is always returned
- **Two-step classify → respond in `ask_trainer`** (`services/prompts.py`, `services/ai_service.py`, `routers/ai.py`):
  - New `ask_trainer_classify_system()` / `ask_trainer_classify_user(question)` prompts classify the question into one of five categories and set `needs_science_rag: bool`
  - New `classify_question(question) -> dict` function calls `gpt-4o-mini` (fast/cheap) and returns `{"category": ..., "needs_science_rag": bool}`; returns a safe default on failure with a warning log
  - `/ai/ask-trainer` router calls `classify_question` first and only invokes `retrieve_cycling_context` (pgvector embedding lookup) when `needs_science_rag` is `True`, skipping the RAG overhead for simple plan/schedule queries
  - Classification result is forwarded into the full system prompt so the AI knows what type of answer is expected

### Changed

- `adapt_plan_system()` now includes `TRAINING_PLAN_PRINCIPLES` (previously only `generate_plan_system` did)
- `adapt_plan_user()` signature extended with optional `rider_assessment` and `training_load` kwargs (backward-compatible)
- `ask_trainer_system()` signature extended with optional `training_load` and `classification` kwargs (backward-compatible)
- `ask_trainer()` signature extended with optional `classification` kwarg (backward-compatible)
- `/ai/ask-trainer` router no longer calls `retrieve_cycling_context` unconditionally; RAG is gated behind the classification result
- `/ai/rate-workout` router now depends on `db` (needed for auto-adaptation plan persistence)

### Tests

- `backend/tests/test_ai_service_unit.py` — 15 new tests:
  - `_compute_training_load`: empty input, zero FTP, expected keys and values, `targetPower` mid-point TSS, TSB precision
  - `ask_trainer`: asserts `"thinking"` is never in the return value; asserts RAG is skipped when `needs_science_rag=False`; asserts CTL/ATL/TSB appear in the system prompt when FTP is known
  - `rate_completed_workout`: structured dict return, `flag_for_adaptation=True` for hard/short sessions, `flag_for_adaptation=False` for normal sessions
  - `adapt_plan_system`: includes `TRAINING_PLAN_PRINCIPLES` and TSB guidance text
  - `classify_question`: returns safe default on failure; parses response correctly
- `backend/tests/test_ai.py` — 3 new integration tests:
  - `test_rate_workout_returns_flag_for_adaptation_false` — normal session returns `flag_for_adaptation=False`
  - `test_rate_workout_flag_triggers_adapt_plan` — flagged session calls `adapt_training_plan`
  - `test_ask_trainer_classify_called_and_rag_skipped_when_not_needed` — `classify_question` is called on every ask-trainer request
- `backend/tests/test_rag.py` — 2 existing tests updated to set `needs_science_rag=True` via the `classify_question` mock when testing RAG source forwarding (previously RAG was called unconditionally)
- 211 tests total (up from 168)



### Fixed

- **Alembic migration chain** — `20260418_000001_add_knowledge_chunks` had `down_revision = "20260414_000001"` which branched off the middle of the chain and created two heads, causing `alembic upgrade head` to fail with *"Multiple head revisions are present"*. Corrected `down_revision` to `"20260416_000001"` (the actual latest head at the time), restoring a linear single-head chain.

### Added

- **Migration chain integrity tests** (`backend/tests/test_migrations.py`) — 4 static tests using `alembic.script.ScriptDirectory` (no live database required) that guard against the class of regression fixed above:
  - Exactly one head revision exists
  - Migration chain is linear (no branching / no revision with multiple parents)
  - All `down_revision` values reference revisions that actually exist
  - All revision IDs are unique

## [0.3.0] - 2026-04-18

### Added

- **Cycling science RAG** — retrieval-augmented generation layer that grounds `ask_trainer` responses in peer-reviewed cycling science:
  - `services/rag.py`: `retrieve_cycling_context(db, query, k=5)` embeds the athlete's question with `text-embedding-3-small` and runs cosine-similarity search against the `knowledge_chunks` table using pgvector. Returns `("", [])` gracefully when pgvector is unavailable (e.g. SQLite in tests).
  - `/ai/ask-trainer` now calls `retrieve_cycling_context` on every request and injects the retrieved science context into the system prompt; retrieved source metadata is returned in the response as `sources`.
  - `AskTrainerResponse` schema gains an optional `sources: list` field so callers can surface citations.
  - `services/prompts.py`: `ask_trainer_system` accepts a `science_context` parameter; the system prompt instructs the AI to cite retrieved sources in its JSON response.
  - `services/ai_service.ask_trainer` accepts a `science_context` parameter and returns `sources[]` in the result dict.
- **`knowledge_chunks` table** (Alembic migration `20260418_000001`):
  - Columns: `id`, `source_id`, `chunk_index`, `title`, `content`, `source_type`, `doi`, `url`, `embedding vector(1536)`, `created_at`.
  - Unique constraint on `(source_id, chunk_index)` for idempotent upserts.
  - HNSW index (`vector_cosine_ops`) for fast approximate nearest-neighbour search.
  - SQLite fallback (stores embedding as text) so the test suite continues to run without PostgreSQL.
- **pgvector Postgres image** — `compose.yml` now uses `pgvector/pgvector:pg17` instead of `postgres:17-alpine`; no new service is added.
- **Seed knowledge corpus** (`backend/knowledge/`) — five hand-written markdown files covering the required cycling science topics:
  - `power_zones.md` — Coggan 7-zone model, FTP definitions, zone training guidelines
  - `polarized_training.md` — Seiler 80/20 model, physiological basis, implementation
  - `sweet_spot_training.md` — 88–95% FTP training, classic workouts, SST vs polarized comparison
  - `periodization.md` — macrocycle/mesocycle/microcycle structure, annual planning, taper protocols
  - `recovery.md` — post-exercise nutrition, sleep science, HRV monitoring, overtraining prevention
- **Ingestion script** (`backend/scripts/ingest_cycling_science.py`):
  - Processes `backend/knowledge/*.md` seed files and queries the Semantic Scholar API (8 cycling-science topics, 10 papers each).
  - Chunks text ~500 tokens / 50-token overlap via tiktoken (character-based fallback when tiktoken is unavailable).
  - Embeds chunks using `text-embedding-3-small` via the existing `AsyncOpenAI` client.
  - Idempotent upsert on `(source_id, chunk_index)`; respects the 1 req/s unauthenticated S2 rate limit.
  - Uses `SEMANTIC_SCHOLAR_API_KEY` env var if set to raise the rate limit to 10 req/s.
- **New dependencies**: `pgvector>=0.3.0`, `tiktoken>=0.7.0`.
- **7 new pytest tests** in `backend/tests/test_rag.py` covering retrieval, graceful fallback, source formatting, and HTTP endpoint integration (175 total).
- **CI integration job** — a new `integration` GitHub Actions job spawns the backend with a throwaway SQLite database and runs the 24-test TypeScript integration suite against it, validating the full HTTP contract between the frontend service layer and the FastAPI application.

## [0.2.0] - 2026-04-18

### Changed

- `/ai/ask-trainer` — accepts only `{ question, contextWorkout? }`. Plan, profile, rider assessment, coach memory, and conversation history are now loaded from the database for the authenticated user
- `/ai/adapt-plan` — accepts only `{ recentFeedback }`. Plan and profile loaded from the database
- `/ai/generate-plan` — accepts an empty body. Profile and rider assessment loaded from the database
- `/ai/rate-workout` — accepts only `{ day }`. Profile loaded from the database
- `/ai/ask-trainer` now persists user and assistant chat messages via `crud.create_chat_message`
- `/ai/ask-trainer` now updates coach memory server-side via `ai_service.update_coach_memory` → `crud.upsert_coach_memory`
- `/ai/ask-trainer` now applies any `plan_updates` returned by the AI to the stored training plan via `crud.upsert_training_plan`
- `/ai/adapt-plan` now persists the updated plan via `crud.upsert_training_plan`
- `/ai/generate-plan` now persists the generated plan via `crud.upsert_training_plan`
- Added `_user_to_profile_dict(user)` helper in `routers/ai.py` to build camelCase profile dict from the `User` ORM model

### Removed

- `POST /ai/update-coach-memory` endpoint — memory updates are now handled atomically inside `/ai/ask-trainer`
- `UpdateCoachMemoryRequest` and `UpdateCoachMemoryResponse` schemas removed from `schemas.py`
- `ConversationMessageSchema` schema removed from `schemas.py` (no longer accepted from the client)

## [0.1.0] - 2026-04-14

### Added

- FastAPI application with async SQLAlchemy + PostgreSQL (asyncpg) and Alembic migrations
- User model with profile fields: bike type, training goal, race date, FTP, heart rate metrics, fitness level, AI provider preference
- JWT authentication: `/auth/login`, `/auth/register`, `/auth/session`; Authelia SSO support via `AUTHELIA_AUTH_ENABLED`
- Strava OAuth integration: connect/disconnect, activity fetching, stream data (watts, HR, cadence, velocity, time)
- Repository/data-access layer (`crud.py`) centralising all SQLAlchemy queries
- AI service (`services/ai_service.py`):
  - Training plan generation (`generate_training_plan`)
  - Training plan adaptation (`adapt_training_plan`)
  - AI coach chat with conversation history and coach memory (`ask_trainer`)
  - Coach memory updates (`update_coach_memory`)
  - Strava activity analysis with FTP estimation (`analyse_strava_activities`)
  - Post-workout rating (`rate_completed_workout`)
  - Support for OpenAI (`gpt-4o-mini`) and Google Gemini (`gemini-2.0-flash`)
- Ride analysis algorithms (`services/analysis.py`):
  - Ride purpose classification (recovery / endurance / tempo / VO2max intervals / sprints)
  - Interval detection from power streams
  - HR drift computation
  - Best N-minute power sliding window
  - HR-corrected FTP estimation (`_hr_corrected_ftp`)
  - HR zone computation
- Prompt construction module (`services/prompts.py`) for all AI system and user prompts
- Persistent coach memory and chat history stored in the database (`coach_memory`, `chat_messages` tables)
- Training plan and workout log persistence (`training_plans`, `workout_logs` tables)
- Rider assessment persistence with HR zones (`rider_assessments` table)
- `/ai/ask-trainer`: `contextWorkout` support for workout-scoped coaching; interval-aware plan updates; honest reflection on plan changes
- JWT secret validation at startup: server refuses to start with the insecure default in non-development environments
- Strava token auto-refresh before activity fetches
- Docker support with environment configuration
- Ansible-based Hetzner VPS deployment with Traefik reverse proxy and Let's Encrypt TLS
- Authelia SSO integration for production deployments
- 168 pytest tests with 82% coverage and Codecov integration
- GitHub Actions CI workflow (test → coverage upload)
