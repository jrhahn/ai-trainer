"""The base model every camelCase schema shares, and its alias generator."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


# ---------------------------------------------------------------------------
# Alias generator: snake_case → camelCase, preserving FTP / HR as all-caps
# ---------------------------------------------------------------------------

_UPPER_ACRONYMS = {"ftp", "hr"}


def _to_camel(name: str) -> str:
    parts = name.split("_")
    result = parts[0].lower()
    for part in parts[1:]:
        lower = part.lower()
        result += lower.upper() if lower in _UPPER_ACRONYMS else lower.capitalize()
    return result


class CamelModel(BaseModel):
    """Base model that serialises to camelCase JSON."""

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
    )
