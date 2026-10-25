## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

## Testing (backend)

- Run backend tests from `backend/` with `uv run pytest`.
- **Run one suite at a time.** The whole suite (about 6,400 tests) runs on four
  workers: `uv run pytest -n 4 --dist loadfile` (ai-trainer-ops#287).
  - Time: about 4 min on a quiet machine and nearly twice that next to other
    work. CI's run takes about 3 min.
  - Memory: the four workers peak at about 2 GiB together (measured 2026-10-25).
  - Concurrency: the box has 16 GB and usually some swap in use, so two
    concurrent runs are what puts it at risk. Competing runs also make each
    other look pathologically slow: a lone run that "times out" is almost
    always racing a sibling.
  - Each worker gets its own SQLite file, and the suite refuses network access.
    `-m "not slow"` leaves out the Hypothesis-heavy files.
- Skip `--cov` locally; CI runs it with `--cov-fail-under=88` (currently ~96 %).
  A run on four workers fits a 10-minute command timeout.
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
