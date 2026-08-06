"""Prometheus exposition (#549).

Two properties matter more than the numbers: the series have to survive a
restart (which is why the LLM ones are read from ``llm_calls`` and not from
in-process counters), and the labels have to stay bounded (which is why HTTP is
labelled by route template and ``prompt_sha`` appears nowhere).
"""

from __future__ import annotations

import pytest

import crud
import models
from auth import hash_password
from database import engine
from services import metrics as metrics_service
from services import token_accounting
from tests.conftest import TestSessionLocal


async def _render() -> str:
    async with TestSessionLocal() as db:
        body, content_type = await metrics_service.render(db, engine=engine)
    assert "text/plain" in content_type
    return body.decode()


async def _seed_call(email: str, *, source: str, task: str = "coach") -> None:
    async with TestSessionLocal() as db:
        user = await crud.create_user(
            db, email=email, name="T", hashed_password=hash_password("pw")
        )
        await db.commit()
        user = await db.get(models.User, user.id)
        async with token_accounting.track_llm_usage(db, user, source=source):
            token_accounting.record_call(
                task=task,
                provider="gemini",
                model="gemini-3.5-flash-lite",
                system_prompt="You are a cycling coach.",
                json_mode=False,
                latency_ms=2000,
                input_tokens=1000,
                output_tokens=100,
                cached_tokens=0,
                total_tokens=1100,
            )
        await db.commit()


# ---------------------------------------------------------------------------
# LLM cost, read from the durable records
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_series_are_built_from_the_stored_calls():
    await _seed_call("metrics-llm@example.com", source="api:ask_trainer")

    body = await _render()
    lines = body.splitlines()

    def line(prefix: str) -> str:
        return next(ln for ln in lines if ln.startswith(prefix) and "api:ask_trainer" in ln)

    call = line("llm_calls_total")
    assert 'model="gemini-3.5-flash-lite"' in call
    assert 'task="coach"' in call and 'provider="gemini"' in call
    assert 'ok="true"' in call
    assert call.endswith(" 1.0")

    assert line('llm_tokens_total{kind="input"').endswith(" 1000.0")
    assert line('llm_tokens_total{kind="output"').endswith(" 100.0")
    # 2000 ms, exported in seconds as Prometheus expects.
    assert line("llm_latency_seconds_total").endswith(" 2.0")


@pytest.mark.asyncio
async def test_llm_series_do_not_reset_when_the_process_does():
    """The whole reason they are read from the table and not counted in memory.

    Nothing here restarts a process — it asserts the weaker fact that makes the
    strong one true: the numbers come from the rows, so two renders of the same
    rows agree, and a fresh registry would not change them.
    """
    await _seed_call("metrics-durable@example.com", source="job:x")

    first = await _render()
    second = await _render()

    def tokens(body: str) -> list[str]:
        return sorted(
            line
            for line in body.splitlines()
            if line.startswith("llm_tokens_total") and 'kind="input"' in line
        )

    assert tokens(first) == tokens(second)
    assert any("1000.0" in line for line in tokens(first))


@pytest.mark.asyncio
async def test_the_prompt_hash_is_never_a_label():
    """Unbounded cardinality is how a Prometheus instance dies (#549)."""
    await _seed_call("metrics-nosha@example.com", source="api:x")

    body = await _render()

    assert "prompt_sha" not in body


@pytest.mark.asyncio
async def test_app_health_still_renders_when_the_records_cannot_be_read(monkeypatch):
    """A metrics scrape must never be the thing that takes the API down."""

    async def explode(*args, **kwargs):
        raise RuntimeError("no table")

    monkeypatch.setattr(metrics_service, "_llm_families", explode)

    body = await _render()

    assert "http_requests_total" in body
    assert "llm_tokens_total" not in body


# ---------------------------------------------------------------------------
# App health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_endpoint_serves_the_exposition_prometheus_expects(client):
    """Mounted at the root, outside /api — Traefik routes only /api and
    /healthz, so this path has no public route (#549)."""
    response = await client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "http_requests_total" in response.text


@pytest.mark.asyncio
async def test_a_request_is_counted_against_its_route_template(client):
    await client.get("/healthz")

    body = await _render()

    assert 'route="/healthz"' in body
    assert 'method="GET"' in body


@pytest.mark.asyncio
async def test_an_unmatched_path_does_not_mint_a_series_per_probe(client):
    """Otherwise every scanner writes its own metric name into the registry."""
    await client.get("/this-does-not-exist-42")
    await client.get("/nor-does-this-99")

    body = await _render()

    assert 'route="unmatched"' in body
    assert "this-does-not-exist-42" not in body
    assert "nor-does-this-99" not in body


def _runs(job: str, status: str):
    return metrics_service.SCHEDULER_RUNS.labels(
        **{metrics_service.SCHEDULER_JOB_LABEL: job, "status": status}
    )


def _duration(job: str):
    return metrics_service.SCHEDULER_DURATION.labels(
        **{metrics_service.SCHEDULER_JOB_LABEL: job}
    )


def test_scheduler_runs_are_recorded():
    metrics_service.record_scheduler_run(
        job="metrics-test-job", status="success", duration_ms=1500
    )

    assert _runs("metrics-test-job", "success")._value.get() >= 1


def test_a_skipped_run_is_counted_but_not_timed():
    """Timing a run that did no work would drag the histogram towards zero."""
    before = _duration("metrics-skip-job")._sum.get()

    metrics_service.record_scheduler_run(
        job="metrics-skip-job", status="skipped", duration_ms=0
    )

    assert _runs("metrics-skip-job", "skipped")._value.get() >= 1
    assert _duration("metrics-skip-job")._sum.get() == before


@pytest.mark.asyncio
async def test_the_scheduler_label_is_not_called_job():
    """Prometheus owns ``job``, and quietly takes it (#549).

    The scrape config writes its own ``job`` label, and with the default
    ``honor_labels: false`` an exposed one is renamed to ``exported_job``.
    Production showed `scheduler_job_runs_total{job="ai-trainer-backend",
    exported_job="activity-sync"}` — so a panel grouping by ``job`` drew one
    line for the whole scrape target instead of one per scheduler job.
    """
    metrics_service.record_scheduler_run(
        job="label-check-job", status="success", duration_ms=1000
    )

    body = await _render()
    line = next(
        ln
        for ln in body.splitlines()
        if ln.startswith("scheduler_job_runs_total") and "label-check-job" in ln
    )
    assert 'scheduler_job="label-check-job"' in line
    # Checked at the label boundary: 'job="' is a substring of
    # 'scheduler_job="', so the naive assertion passes either way.
    assert '{job="' not in line and ',job="' not in line
