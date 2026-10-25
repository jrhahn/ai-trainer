# Backend scripts

Every script here says where it runs. `tests/test_scripts_documented_290.py`
fails when a script is not listed below, or when a listed one is gone, so a
script nothing runs any more gets deleted rather than forgotten
(ai-trainer-ops#290). The git history keeps what was deleted, the one-off
repairs of July 2026 included.

Run them from `backend/`, as `uv run python -m scripts.<name>` unless the
script's docstring says otherwise.

| Script | What it does | Runs |
|---|---|---|
| `backfill_activity_moving_time.py` | Corrects the whole ride history's durations, distances and climbing from the provider; the daily `duration-refresh` job covers the recent window with the same code (`services/duration_refresh.py`) | by hand, dry run first |
| `backtest_report.py` | Backtests the performance model against its own later estimate | the operator's weekly model reports |
| `backup_manifest.py` | Names the keys a dump was written under, so a restore can check before it runs | `scripts/backup.sh` |
| `calibration_report.py` | The coach's calibration, as Markdown | the operator's weekly model reports |
| `check_policy_guards.py` | Checks that the numbers the code decides with are load-bearing | CI (Policy Constant Guards) |
| `coach_scenarios.py` | Situations from real coach conversations, replayed against the real model | the operator's scenario runs |
| `coach_week_digest.py` | One athlete's week as the coach saw it | the operator's weekly coach review |
| `cost_report.py` | LLM cost per active user, aggregates only | by hand |
| `export_openapi.py` | Prints the API's OpenAPI schema | `npm run types:api` and `types:api:check` (`frontend/scripts/api-types.mjs`) |
| `generate_admin_totp.py` | Generates an `ADMIN_TOTP_SECRET` and the QR code to enrol it | by hand, once per deployment (`docs/security.md`) |
| `ingest_cycling_science.py` | Builds the cycling-science knowledge base | by hand (`docs/update_rag.md`) |
| `plan_behaviour_report.py` | How the coach's plans behave over weeks | the operator's weekly model reports; `coach_week_digest.py` |
| `verify_restore.py` | Opens every encrypted column and reports what could not be opened | `scripts/restore.sh` (`docs/runbook-restore.md`) |
| `wave_gates_report.py` | Where a tester wave stands against the gates for the next one | by hand, against a deployment's database |
| `wrong_flags_report.py` | How many "this is wrong" flags came in per week | the operator's weekly model reports |
