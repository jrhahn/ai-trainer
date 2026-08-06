"""Prometheus exposition for cost and app health (#549).

Two kinds of metric live here, and they get their numbers from different
places on purpose.

*LLM cost* is read from the ``llm_calls`` table at scrape time. In-process
counters would restart at zero on every deploy, which is the exact failure
this whole issue is about — the log-based record only ever covered "since the
last deploy". Reading the table instead means the series survive a restart and
cannot disagree with the records they are derived from. The sums are
cumulative over the whole table, so they are monotonic and legitimately
counters.

*App health* — HTTP, scheduler, database pool — is in-process, because there
is nothing durable to read it from and a reset on restart is exactly what
Prometheus expects of a process counter.

Cardinality is the thing that kills a Prometheus instance, so every label here
is bounded: HTTP uses the matched *route template*, never the raw path, and
``prompt_sha`` is deliberately absent — it is effectively unbounded, and the
question it answers is a SQL query against ``llm_calls``, not a time series.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from prometheus_client.core import CounterMetricFamily
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import models

logger = logging.getLogger(__name__)

# A registry of our own rather than the global default: the default one raises
# on duplicate registration, which makes it hostile to a test suite that
# imports modules more than once.
REGISTRY = CollectorRegistry()

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests handled, by route template and outcome.",
    ["method", "route", "status"],
    registry=REGISTRY,
)

HTTP_DURATION = Histogram(
    "http_request_duration_seconds",
    "Wall time spent handling a request, by route template.",
    ["method", "route"],
    # Tuned for this app: an LLM-backed endpoint takes seconds, so the default
    # buckets (which stop at 10 s) would put every coach answer in +Inf.
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 40, 80),
    registry=REGISTRY,
)

# Not "job": Prometheus writes its own ``job`` label from the scrape config and,
# with the default honor_labels=false, an exposed one is renamed out of the way
# to ``exported_job``. Grouping by ``job`` then yields one line per *scrape
# target* — i.e. one — instead of one per scheduler job. Seen in production
# before anyone tried to read the panel.
SCHEDULER_JOB_LABEL = "scheduler_job"

SCHEDULER_RUNS = Counter(
    "scheduler_job_runs_total",
    "Scheduler job executions, by job and outcome.",
    [SCHEDULER_JOB_LABEL, "status"],
    registry=REGISTRY,
)

SCHEDULER_DURATION = Histogram(
    "scheduler_job_duration_seconds",
    "Wall time of a scheduler job run.",
    [SCHEDULER_JOB_LABEL],
    buckets=(0.5, 1, 5, 15, 30, 60, 120, 300, 600, 1800),
    registry=REGISTRY,
)

DB_POOL = Gauge(
    "db_connection_pool",
    "SQLAlchemy connection pool state.",
    ["state"],
    registry=REGISTRY,
)

# Route label for a request that matched no route. Without this, every 404 from
# a scanner would mint a new series named after whatever it probed.
UNMATCHED_ROUTE = "unmatched"


def record_http_request(
    *, method: str, route: str, status: int, duration_seconds: float
) -> None:
    HTTP_REQUESTS.labels(method=method, route=route, status=str(status)).inc()
    HTTP_DURATION.labels(method=method, route=route).observe(duration_seconds)


def record_scheduler_run(*, job: str, status: str, duration_ms: int) -> None:
    SCHEDULER_RUNS.labels(**{SCHEDULER_JOB_LABEL: job, "status": status}).inc()
    # A skipped run did not do the work, so timing it would drag the histogram
    # towards zero and hide how long the job actually takes.
    if status != "skipped":
        SCHEDULER_DURATION.labels(**{SCHEDULER_JOB_LABEL: job}).observe(
            duration_ms / 1000.0
        )


def _refresh_pool_gauges(engine: Any) -> None:
    """Read the pool counters, if the driver exposes them.

    Best-effort by design: this is a nice-to-have gauge, and a metrics scrape
    must never be the thing that takes the API down.
    """
    pool = getattr(engine, "pool", None)
    if pool is None:
        return
    for state, method in (
        ("size", "size"),
        ("checked_out", "checkedout"),
        ("checked_in", "checkedin"),
        ("overflow", "overflow"),
    ):
        try:
            DB_POOL.labels(state=state).set(float(getattr(pool, method)()))
        except Exception:  # noqa: BLE001 — a gauge is not worth an exception
            continue


async def _llm_families(db: AsyncSession) -> Iterable[Any]:
    """Build the LLM cost series from the durable call records.

    Cumulative over the whole table, so the values only ever grow and Prometheus
    can treat them as counters across restarts and deploys.
    """
    rows = (
        await db.execute(
            select(
                models.LlmCall.source,
                models.LlmCall.task,
                models.LlmCall.provider,
                models.LlmCall.model,
                models.LlmCall.ok,
                func.count().label("calls"),
                func.coalesce(func.sum(models.LlmCall.input_tokens), 0),
                func.coalesce(func.sum(models.LlmCall.output_tokens), 0),
                func.coalesce(func.sum(models.LlmCall.cached_tokens), 0),
                func.coalesce(func.sum(models.LlmCall.latency_ms), 0),
            ).group_by(
                models.LlmCall.source,
                models.LlmCall.task,
                models.LlmCall.provider,
                models.LlmCall.model,
                models.LlmCall.ok,
            )
        )
    ).all()

    calls = CounterMetricFamily(
        "llm_calls",
        "Provider calls made, by what asked for them.",
        labels=["source", "task", "provider", "model", "ok"],
    )
    tokens = CounterMetricFamily(
        "llm_tokens",
        "Provider-reported tokens. 'cached' is a subset of 'input', not a "
        "fourth bucket.",
        labels=["source", "task", "provider", "model", "kind"],
    )
    latency = CounterMetricFamily(
        "llm_latency_seconds",
        "Summed provider latency; divide by llm_calls_total for a mean.",
        labels=["source", "task", "provider", "model"],
    )

    # Tokens and latency are summed across ok/failed: a failed call reports no
    # tokens anyway, and keeping the label would double the series for nothing.
    token_totals: dict[tuple[str, ...], list[int]] = {}
    for source, task, provider, model, ok, n, inp, out, cached, latency_ms in rows:
        calls.add_metric(
            [source, task, provider, model, "true" if ok else "false"], float(n)
        )
        key = (source, task, provider, model)
        acc = token_totals.setdefault(key, [0, 0, 0, 0])
        acc[0] += int(inp)
        acc[1] += int(out)
        acc[2] += int(cached)
        acc[3] += int(latency_ms)

    for (source, task, provider, model), acc in token_totals.items():
        for index, kind in enumerate(("input", "output", "cached")):
            tokens.add_metric([source, task, provider, model, kind], float(acc[index]))
        latency.add_metric([source, task, provider, model], acc[3] / 1000.0)

    return [calls, tokens, latency]


class _Snapshot:
    """Serves already-computed families, because ``collect`` cannot be async."""

    def __init__(self, families: Iterable[Any]) -> None:
        self._families = list(families)

    def collect(self):  # noqa: ANN201 — prometheus_client's collector protocol
        return iter(self._families)


async def render(db: AsyncSession, engine: Any | None = None) -> tuple[bytes, str]:
    """Render the exposition body and its content type."""
    if engine is not None:
        _refresh_pool_gauges(engine)

    body = generate_latest(REGISTRY)
    try:
        snapshot = CollectorRegistry()
        snapshot.register(_Snapshot(await _llm_families(db)))
        body += generate_latest(snapshot)
    except Exception:  # noqa: BLE001 — app health must still be scrapeable
        logger.warning("Could not read LLM call records for metrics", exc_info=True)
    return body, CONTENT_TYPE_LATEST
