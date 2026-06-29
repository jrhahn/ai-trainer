"""Unit tests for the pipeline DAG registry (services/pipeline_graph.py)."""

from __future__ import annotations

import pytest

from services.pipeline_graph import PipelineGraph


def test_validate_accepts_dag_and_orders_dependents():
    g = PipelineGraph()
    g.register("a")
    g.register("b", depends_on=("a",))
    g.register("c", depends_on=("a", "b"))
    g.validate()  # does not raise
    assert g.downstream_of("a") == ["b", "c"]
    assert g.downstream_of("b") == ["c"]
    assert g.downstream_of("c") == []


def test_downstream_is_transitive():
    g = PipelineGraph()
    g.register("a")
    g.register("b", depends_on=("a",))
    g.register("c", depends_on=("b",))
    assert g.downstream_of("a") == ["b", "c"]


def test_validate_rejects_cycle():
    g = PipelineGraph()
    g.register("a", depends_on=("b",))
    g.register("b", depends_on=("a",))
    with pytest.raises(ValueError, match="cycle"):
        g.validate()


def test_validate_rejects_unknown_dependency():
    g = PipelineGraph()
    g.register("a", depends_on=("missing",))
    with pytest.raises(ValueError, match="unknown"):
        g.validate()


@pytest.mark.asyncio
async def test_notify_changed_invokes_downstream_handlers_in_order():
    g = PipelineGraph()
    order: list[str] = []

    async def handler_b(**_):
        order.append("b")

    async def handler_c(**_):
        order.append("c")

    g.register("a")
    g.register("b", depends_on=("a",), on_upstream_changed=handler_b)
    g.register("c", depends_on=("b",), on_upstream_changed=handler_c)

    await g.notify_changed("a")
    assert order == ["b", "c"]


@pytest.mark.asyncio
async def test_notify_changed_passes_context():
    g = PipelineGraph()
    received: list[dict] = []

    async def handler(**ctx):
        received.append(ctx)

    g.register("a")
    g.register("b", depends_on=("a",), on_upstream_changed=handler)
    await g.notify_changed("a", value=42)
    assert received == [{"value": 42}]


def test_registered_app_graph_is_acyclic():
    # Importing the pipelines registers the real nodes on the global graph.
    import services.plan_pipeline  # noqa: F401
    import services.summary_pipeline  # noqa: F401
    from services.pipeline_graph import graph

    graph.validate()
    assert "summary" in graph.downstream_of("plan")
