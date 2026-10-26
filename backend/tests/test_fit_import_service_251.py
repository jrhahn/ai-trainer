"""The .fit import lives in a service, and the routers reach it there (ai-trainer-ops#251).

Some 600 lines of routers/users.py were the FIT subsystem, and routers/tools.py
imported three of its private helpers from there. The service raises its own
errors for the two things that stop an upload before parsing; the routers turn
them into 503 and 413.
"""

from __future__ import annotations

import ast
import builtins
from pathlib import Path

import pytest
from fastapi import HTTPException

from routers import dependencies
from services import fit_import

BACKEND = Path(__file__).resolve().parents[1]


def test_no_router_imports_from_the_users_router():
    found = []
    for path in sorted((BACKEND / "routers").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module == "routers.users":
                found.append(path.name)
            elif isinstance(node, ast.Import) and any(
                alias.name == "routers.users" for alias in node.names
            ):
                found.append(path.name)

    assert found == []


def test_the_service_knows_nothing_of_http():
    source = (BACKEND / "services" / "fit_import.py").read_text()
    imported = {
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert not {name for name in imported if name and name.startswith("fastapi")}
    assert "HTTPException" not in source


class _Upload:
    """Just the async ``read(size)`` an upload offers."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    async def read(self, size: int) -> bytes:
        chunk, self._data = self._data[:size], self._data[size:]
        return chunk


@pytest.mark.asyncio
async def test_an_upload_over_the_cap_is_refused_before_it_is_all_read(monkeypatch):
    monkeypatch.setattr(fit_import.settings, "fit_upload_max_bytes", 10)

    assert await fit_import.read_upload_capped(_Upload(b"x" * 10), "ride.fit") == (
        b"x" * 10
    )
    with pytest.raises(fit_import.UploadTooLarge, match="ride.fit is larger than"):
        await fit_import.read_upload_capped(_Upload(b"x" * 11), "ride.fit")
    with pytest.raises(HTTPException) as refused:
        await dependencies.read_upload_capped(_Upload(b"x" * 11), "ride.fit")
    assert refused.value.status_code == 413


def test_a_missing_parser_library_is_the_services_error_and_a_503(monkeypatch):
    real_import = builtins.__import__

    def without_fitparse(name, *args, **kwargs):
        if name == "fitparse":
            raise ImportError("no fitparse here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_fitparse)

    with pytest.raises(fit_import.FitParserUnavailable):
        fit_import.fit_file_parser()
    with pytest.raises(HTTPException) as refused:
        dependencies.fit_file_parser_or_503()
    assert refused.value.status_code == 503
    assert "fitparse" in refused.value.detail
