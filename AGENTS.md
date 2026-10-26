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
   - For a backend change, also run `uv run --only-group lint ruff format .` and `uv run --only-group lint ruff check .` from `backend/`. CI's Lint job fails on a file `ruff format` would change, and runs the same rules (`[tool.ruff.lint]` in `backend/pyproject.toml`). The one commit that formatted the whole backend is in `.git-blame-ignore-revs`.
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

A backend module imports only from the layers to its left (ai-trainer-ops#253, #304):

    config → domain → database → models → schemas → crud → services → auth, routers → main

`domain/` holds the pure rules: no database, no I/O, no model call. It imports nothing from the backend but `config` and itself, not even inside a function. So a service imports no router and not `auth`, and models, schemas and crud import no service: a rule they share with the services lives in `domain/`. `tests/test_layering_253.py` checks every module-level import. Its `KNOWN` names the imports that break the rule today, each with its reason, and the list only shrinks. An import moved into a function does not get round the rule: the test lists those in `services/` in `LAZY` and those below services in `LAZY_BELOW`, each with the reason it cannot move to the top, and a new one fails it.

## Packages that re-export

`crud/` is a package, one module per concern, and `crud/__init__.py` re-exports every public name (ai-trainer-ops#250): callers keep writing `crud.get_training_plan`. A new public function goes into its module and into the package's imports and `__all__`; `tests/test_crud_package_250.py` fails until it is. A patch on `crud.<name>` replaces what the package exports, so code that calls `crud.<name>` sees it, but a call inside the module that defines the name does not. To reach that call, patch the module: `monkeypatch.setattr(crud.translations, "get_plan_text_translations", ...)`. For the same reason a policy guard in `tests/policy_guards.py` names the module that defines its constant: `crud.memory:ATHLETE_MEMORY_CONFIDENCE_STEP`. `routers/users/` is a package too, with one router per module (ai-trainer-ops#251); a test patches the module that holds the route, such as `routers.users.fit`.

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
