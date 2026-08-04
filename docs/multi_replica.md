# Multi-replica limitations

**The backend is designed to run as a single replica.** `compose.yml` defines one
`backend` service with no `deploy.replicas`, and several pieces of state and
scheduling live in that process's memory. Running more than one backend replica
behind a load balancer will cause the problems listed below.

This is documented (rather than fixed) deliberately: making the app
horizontally scalable requires solving *all* of the items below together, not
just one. See issue #326.

## What breaks with >1 replica

1. **Scheduled jobs run on every replica.** `services/scheduler.py`
   (`InProcessScheduler`) runs the nightly plan-maintenance and activity-sync
   jobs in-process (`main.py` `lifespan`). With N replicas each job runs N times
   concurrently — duplicated Strava/intervals syncs, competing plan writes, and
   wasted LLM spend.

2. **Strava OAuth connect fails intermittently.** The OAuth `state` token is held
   in `_oauth_states` (in-process) in `routers/strava.py`. `GET /auth/strava`
   creates it on one replica; the `GET /auth/strava/callback` may be routed to a
   different replica, where the state is absent → "Invalid or expired state" and
   the connect flow fails. (Intervals.icu is unaffected — it uses an API key
   persisted to the DB, with no per-request handshake state.)

3. **Import-progress polling is wrong.** `_import_progress` (Strava) and
   `_intervals_import_progress` (intervals) are in-process, per-replica. The
   background import runs on one replica; a `GET …/import-progress` poll that
   lands on another replica sees `idle`, so the UI shows no progress or a
   premature "idle" while the import is still running elsewhere.

4. **Duplicate imports can start.** The "is an import already running?" guard
   (`try_mark_running`) is atomic only within a single event loop. Two replicas
   each have their own progress dict, so a user double-clicking behind a
   round-robin LB could start two concurrent imports.

## What is already safe across replicas

These use the shared Postgres database, so they are correct regardless of
replica count:

- **Strava token refresh** — row-locked (`SELECT … FOR UPDATE`) in
  `strava_service.ensure_fresh_strava_token`, so concurrent refreshes serialise.
- **Coach-memory writes** — compare-and-set / row lock
  (`crud.update_coach_memory_if_unchanged`, `get_coach_memory(for_update=True)`).
- **Training-plan mutations** — all go through `services/plan_pipeline.py` against
  the DB.
- **All persistent data** — users, tokens, plans, ride metrics, etc. live in
  Postgres.

## Mitigations applied for single-replica (issue #326)

Even on one replica, two of the above were real bugs and are fixed:

- **Unbounded memory growth** — completed import-progress entries are now evicted
  on a TTL and capped in size (`services/progress_store.prune_progress`).
- **Racy start guard** — the check-and-set is a single synchronous step
  (`services/progress_store.try_mark_running`), so it can't be raced within the
  event loop.

Multi-replica *correctness* (cross-replica visibility) is intentionally left
unaddressed.

## To actually go multi-replica

All of these are required together:

1. Move scheduling off the request processes — a single leader (distributed
   lock / advisory lock), an external cron/worker, or a dedicated scheduler
   deployment scaled to exactly one.
2. Move OAuth `state` to a shared store (a DB row or Redis) keyed by the state
   token, with TTL expiry.
3. Move import progress to a shared store keyed by user, and make the
   "already running" guard an atomic DB conditional insert / unique constraint.
