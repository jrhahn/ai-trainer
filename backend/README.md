# Backend

The FastAPI app behind Train Like a Pro. It holds the coach, the training plans,
the physiological model and the activity sync.

- **Run it:** see the [Quick start](../README.md#quick-start) in the root README.
- **Settings:** [`../.env.example`](../.env.example) lists every setting a
  deployment can set, with its default. `config.py` has them all, each with what
  it does.
- **Tests:** `uv run pytest -n 4 --dist loadfile`. See
  [`../AGENTS.md`](../AGENTS.md) for the checklist, and
  [`../CLAUDE.md`](../CLAUDE.md) for running them on NixOS.
- **Migrations:** Alembic, in `alembic/versions/`. `entrypoint.sh` applies them
  before the app starts. In development the app creates its tables from the
  models instead.
- **Scheduled jobs:** registered with `services.scheduler.InProcessScheduler` in
  the lifespan in `main.py`. They run in the app's own process, which is one
  reason the backend runs as a single replica
  ([`../docs/multi_replica.md`](../docs/multi_replica.md)).
- **Scripts:** [`scripts/README.md`](scripts/README.md) says what each one does
  and what runs it.
- **API:** the interactive docs are at `/docs` while the app runs.
