"""The production backup is taken before the home server fetches it.

The fetch runs at 03:30 Europe/Berlin, and the production host's clock is UTC.
A bare "03:00" there is 04:00 or 05:00 in Berlin, after the fetch, and the
fetch would collect the night before's backup without failing.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "ansible" / "deploy.yml"
TIMER = "/etc/systemd/system/ai-trainer-backup.timer"
# The fetch on the home server; its configuration lives outside this repository.
FETCH = (3, 30)
BERLIN = ZoneInfo("Europe/Berlin")
# A backup takes about a minute; this is the room left before the fetch.
MARGIN = timedelta(minutes=10)


def _tasks(tasks: list[dict]):
    for task in tasks:
        yield task
        for nested in ("block", "rescue", "always"):
            yield from _tasks(task.get(nested, []))


def _timer() -> str:
    plays = yaml.safe_load(DEPLOY.read_text())
    [content] = [
        task["ansible.builtin.copy"]["content"]
        for play in plays
        for task in _tasks(play.get("tasks", []))
        if (task.get("ansible.builtin.copy") or {}).get("dest") == TIMER
    ]
    return content


def _setting(name: str) -> str:
    match = re.search(rf"^{name}=(.+)$", _timer(), re.MULTILINE)
    assert match, f"{TIMER} has no {name}"
    return match.group(1).strip()


def test_the_backup_time_names_its_zone():
    """Bare "03:00" is the host's own zone, which is how this went wrong."""
    assert re.fullmatch(r"\*-\*-\* \d{2}:\d{2}:\d{2} UTC", _setting("OnCalendar"))


@pytest.mark.parametrize(
    "day",
    [
        date(2026, 1, 15),  # winter: Berlin is UTC+1
        date(2026, 7, 15),  # summer: UTC+2
        date(2026, 3, 29),  # the night the clocks go forward
        date(2026, 10, 25),  # the night they go back
    ],
)
def test_the_backup_is_done_before_the_fetch_in_every_season(day: date):
    hour, minute, second = map(
        int, re.search(r"(\d{2}):(\d{2}):(\d{2})", _setting("OnCalendar")).groups()
    )
    latest = datetime(
        day.year, day.month, day.day, hour, minute, second, tzinfo=timezone.utc
    ) + timedelta(seconds=int(_setting("RandomizedDelaySec")))
    fetch = datetime(day.year, day.month, day.day, *FETCH, tzinfo=BERLIN)

    assert latest + MARGIN <= fetch
