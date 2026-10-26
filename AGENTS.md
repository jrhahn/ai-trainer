# Agent Workflow Notes

These instructions apply to the whole repository.

Before finishing any code change, always run the release-hygiene checklist:

1. Decide the version bump level.
   - Use a patch bump for bug fixes, regressions, small UI/API behavior fixes, docs, and test-only changes.
   - Use a feature/minor bump for new user-visible capabilities, new endpoints, new persisted data model behavior, migrations, or larger workflow changes.
   - Keep backend and frontend versions independent. Bump only the side affected by the change unless the behavior crosses both.

2. Do not edit the changelogs.
   - The release notes are generated per version from the merged pull requests (ai-trainer-ops#288), so the PR title is the release-note line: one sentence a maintainer or an athlete understands. A trailing `(ai-trainer-ops#N)` is fine — the notes drop it, since the tracker is private; no other tracker numbers in the title.
   - `backend/CHANGELOG.md` and `frontend/CHANGELOG.md` are frozen archives; a test fails when either gets an `## [Unreleased]` section again.

3. Run tests.
   - Run focused backend and/or frontend tests that cover the change.
   - For backend tests, run from `backend/` with `uv run pytest ...`. The whole suite runs in a few minutes on four workers (`uv run pytest -n 4 --dist loadfile`); `-m "not slow"` leaves out the Hypothesis-heavy files.
   - The suite refuses every connection that leaves the machine and fails the test that tried, and the model SDKs answer as an unreachable provider: mock what a test needs. Passwords hash at a cheap Argon2 cost; a test about what a hash costs takes `@pytest.mark.production_hashing` (ai-trainer-ops#287).
   - For a backend change, also run `uv run --only-group lint ruff check .` from `backend/`. CI's Lint job runs the same rules (`[tool.ruff.lint]` in `backend/pyproject.toml`).
   - For frontend tests, run from `frontend/` with `npm test -- ... --run`.
   - In the final response, state exactly which tests passed or why a test could not be run.

4. Update the issue.
   - Issues live on the private Forgejo tracker (`ai-trainer-ops`), not on GitHub. If the branch name, user request, or local context identifies an issue, update it there with a clear description or implementation note.
   - Preserve useful screenshots or existing context.
   - Include acceptance criteria or a concise summary of the completed behavior when appropriate.

5. Final response.
   - Summarize the version bump, tests, and issue update.
   - Keep it concise and include links or file references when useful.

## Backend layering

A backend module imports only from the layers to its left (ai-trainer-ops#253):

    config → database → models → schemas → crud → services → auth, routers → main

So a service imports no router and not `auth`, and models, schemas and crud import no service. `tests/test_layering_253.py` checks every module-level import. Its `KNOWN` names the imports that break the rule today, each with its reason, and the list only shrinks. An import moved into a function does not get round the rule: the test counts those in `services/` too.

## Best-effort database work

A `try/except Exception` that lets a step fail without failing the request runs
that step in `async with db.begin_nested():` whenever the step touches the
session, reads included. A failed flush, and in Postgres any failed statement,
leaves the transaction unusable; without the savepoint the request's own commit
fails afterwards and takes the main write with it (ai-trainer-ops#281).
`PipelineGraph.notify_changed` already gives each handler its own savepoint.
A step that commits itself keeps the commit outside its savepoint: a commit
inside a savepoint ends the outer transaction under it (see the intervals
reclassify backfill in `services/activity_sync.py`).
A write that something outside the database already relies on gets a savepoint
of its own that ends before the next step, so a later failure cannot roll it
back: a Strava token refresh, after which Strava has revoked the old token
(`rate_workout`, `analyse_activities`, `resolve_ride_match` in `routers/ai.py`).
Likewise a plan commit and the narration of it are two savepoints, not one.
