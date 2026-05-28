# Agent Workflow Notes

These instructions apply to the whole repository.

Before finishing any code change, always run the release-hygiene checklist:

1. Decide the version bump level.
   - Use a patch bump for bug fixes, regressions, small UI/API behavior fixes, docs, and test-only changes.
   - Use a feature/minor bump for new user-visible capabilities, new endpoints, new persisted data model behavior, migrations, or larger workflow changes.
   - Keep backend and frontend versions independent. Bump only the side affected by the change unless the behavior crosses both.

2. Update changelogs.
   - Add a dated entry at the top of `backend/CHANGELOG.md` and/or `frontend/CHANGELOG.md`.
   - Use Keep a Changelog sections (`Added`, `Changed`, `Fixed`) and mention the most relevant files or behavior.

3. Run tests.
   - Run focused backend and/or frontend tests that cover the change.
   - For backend tests, run from `backend/` with `uv run pytest ...`.
   - For frontend tests, run from `frontend/` with `npm test -- ... --run`.
   - In the final response, state exactly which tests passed or why a test could not be run.

4. Update the GitHub issue.
   - If the branch name, user request, or local context identifies an issue, update that GitHub issue with a clear description or implementation note.
   - Preserve useful screenshots or existing context.
   - Include acceptance criteria or a concise summary of the completed behavior when appropriate.

5. Final response.
   - Summarize the version bump, changelog updates, tests, and GitHub issue update.
   - Keep it concise and include links or file references when useful.
