## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

## Testing (backend)

- Run backend tests from `backend/` with `uv run pytest`.
- **Run one suite at a time.** Measured cost of the whole suite (2904 tests) on
  this machine: **~330 s and ~256 MiB peak RSS** plain, **~570 s and ~350 MiB**
  with `--cov`. That is cheap — but the box has 7,7 GB and is usually a couple of
  GB into swap, so two or three concurrent runs are what actually puts it at
  risk, and competing runs make each other look pathologically slow (a lone run
  that "times out" after 600 s is almost always racing a sibling). Nothing in the
  suite leaks: the `/dev/shm` database stays under 1 MB, the process holds one
  thread, and per-module growth is bounded.
- Skip `--cov` locally; CI runs it with `--cov-fail-under=88` (currently ~94 %).
  Without it a full run fits inside a 10-minute command timeout, with it barely.
- **NixOS gotcha:** `backend/tests/conftest.py` re-execs the test process via
  `os.execvpe` to fix `LD_LIBRARY_PATH` for C extensions (greenlet, aiosqlite).
  Under `uv run pytest` this re-exec swallows all pytest output (you see an
  empty result with exit 0, even though tests ran). To get visible output,
  pre-set the env so the re-exec is skipped:

  ```sh
  export _PYTEST_NIXOS_REEXEC=1
  export LD_LIBRARY_PATH="$(dirname "$(ls /nix/store/*/lib/libstdc++.so.6 | sort | head -1)")"
  uv run pytest ...
  ```
