"""Tiny in-process DAG registry for backend pipelines.

Backend work is organised into *pipelines* (e.g. plan revision, login-summary
refresh). A pipeline declares which other pipelines it depends on; when an
upstream pipeline produces a change it calls :meth:`PipelineGraph.notify_changed`,
and every downstream dependent is invalidated/refreshed in topological order.

The registry guarantees the dependency graph stays a DAG: :meth:`validate`
runs a topological sort and raises on a cycle or an unknown dependency. Call it
once at startup (and it is covered by a unit test) so an accidental cycle fails
loudly instead of looping at runtime.

This is deliberately ~100 lines of in-house code rather than a heavyweight
orchestrator (Airflow/Dagster/Prefect): our pipelines are request/event-triggered
in-process async steps, not scheduled distributed batch jobs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

# Handler invoked on a downstream pipeline when an upstream one changed. It
# receives the same keyword context passed to ``notify_changed`` (e.g. db, user).
ChangeHandler = Callable[..., Awaitable[None]]


@dataclass(frozen=True)
class Pipeline:
    name: str
    depends_on: tuple[str, ...] = ()


@dataclass
class PipelineGraph:
    _nodes: dict[str, Pipeline] = field(default_factory=dict)
    _handlers: dict[str, ChangeHandler] = field(default_factory=dict)

    def register(
        self,
        name: str,
        *,
        depends_on: tuple[str, ...] = (),
        on_upstream_changed: ChangeHandler | None = None,
    ) -> None:
        """Register (or re-register) a pipeline node and its upstream edges."""
        self._nodes[name] = Pipeline(name=name, depends_on=tuple(depends_on))
        if on_upstream_changed is not None:
            self._handlers[name] = on_upstream_changed

    def _topological_order(self) -> list[str]:
        """Kahn's algorithm; raises on unknown dependency or cycle."""
        for node in self._nodes.values():
            for dep in node.depends_on:
                if dep not in self._nodes:
                    raise ValueError(
                        f"Pipeline {node.name!r} depends on unknown pipeline {dep!r}"
                    )
        indegree = {name: 0 for name in self._nodes}
        for node in self._nodes.values():
            indegree[node.name] = len(node.depends_on)
        ordered: list[str] = []
        queue = sorted(name for name, deg in indegree.items() if deg == 0)
        while queue:
            current = queue.pop(0)
            ordered.append(current)
            for node in self._nodes.values():
                if current in node.depends_on:
                    indegree[node.name] -= 1
                    if indegree[node.name] == 0:
                        queue.append(node.name)
            queue.sort()
        if len(ordered) != len(self._nodes):
            raise ValueError("Pipeline dependency graph contains a cycle")
        return ordered

    def validate(self) -> None:
        """Raise if the registered graph is not a DAG. Safe to call repeatedly."""
        self._topological_order()

    def downstream_of(self, name: str) -> list[str]:
        """Transitive dependents of ``name`` in topological order."""
        order = self._topological_order()
        dependents: set[str] = set()
        for node_name in order:
            deps = self._nodes[node_name].depends_on
            if name in deps or dependents.intersection(deps):
                dependents.add(node_name)
        return [n for n in order if n in dependents]

    async def notify_changed(self, name: str, **context) -> None:
        """Invoke each downstream dependent's change handler, best-effort."""
        for dependent in self.downstream_of(name):
            handler = self._handlers.get(dependent)
            if handler is None:
                continue
            try:
                await handler(**context)
            except Exception:  # pragma: no cover - defensive
                logger.warning(
                    "Pipeline %r downstream handler %r failed",
                    name,
                    dependent,
                    exc_info=True,
                )


# Module-global registry shared by all pipelines.
graph = PipelineGraph()
