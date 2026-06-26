## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

## Testing (backend)

- Run backend tests from `backend/` with `uv run pytest`.
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
