"""Situations from real coach conversations, replayed against the real model (ai-trainer-ops#76).

Run from ``backend/`` with a provider key in the environment:

    uv run python -m scripts.coach_scenarios [--provider gemini] [--only NAME]

The unit tests pin what the code does with an athlete's words; whether the
model then *coaches* well cannot be unit-tested. Each scenario here is a
situation where the coach went wrong in the owner's chat between May and
October 2026, paraphrased into a synthetic athlete: no names, no health details,
no message copied verbatim. It calls ``ai_service.ask_trainer`` with built
inputs, never a database, so a run reads and writes no athlete's data.

Each scenario passes only the inputs its situation needs, in the parameter the
chat route uses for them (the profile, a stated objective in
``motivation_model``, constraints in the profile); weather, memory facts, the
performance model and the plan's change history are left out. So a scenario
tests the model's answer to that situation, not the whole production prompt,
and "claims-match-changes" checks the reply against the model's own plan
update, not against what the pipeline then applies.

Every check is deterministic and deliberately narrow: it catches the specific
failure the chat showed, not "good coaching" in general. A failed check is a
lead to read the reply, which the report quotes; it is not proof on its own.
Model output varies, so read a check that fails once as a flake and one that
fails run after run as a finding.
"""

from __future__ import annotations

import argparse
import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta

from services import ai_service
from services.dates import app_today

GERMAN_WEEKDAYS = [
    "montag",
    "dienstag",
    "mittwoch",
    "donnerstag",
    "freitag",
    "samstag",
    "sonntag",
]
_GERMAN_WORDS = frozenset(
    [
        "und",
        "der",
        "die",
        "das",
        "ist",
        "nicht",
        "du",
        "dich",
        "dein",
        "deine",
        "heute",
        "morgen",
        "mit",
        "für",
        "auf",
        "ein",
        "eine",
        "wir",
        "es",
        "zu",
        "den",
        "dem",
        "bei",
    ]
)
_ENGLISH_WORDS = frozenset(
    [
        "the",
        "and",
        "you",
        "your",
        "is",
        "not",
        "today",
        "tomorrow",
        "with",
        "for",
        "on",
        "a",
        "we",
        "it",
        "to",
        "of",
        "in",
        "at",
        "this",
        "that",
    ]
)
_CLAIMED_CHANGE = re.compile(
    r"\b(?:habe|hab ich|ich hab|i've|i have|updated|angepasst|eingetragen|umgestellt|"
    r"geändert|gestellt|verschoben|gemacht|moved|changed)\b",
    re.IGNORECASE,
)


def language(text: str) -> str:
    words = re.findall(r"[a-zäöüß]+", text.casefold())
    german = sum(word in _GERMAN_WORDS for word in words)
    english = sum(word in _ENGLISH_WORDS for word in words)
    return "de" if german > english else "en"


@dataclass
class Result:
    reply: str
    updates: list[dict]

    def update_for(self, day: str) -> dict | None:
        return next((u for u in self.updates if u.get("date") == day), None)


Check = Callable[[Result], bool]


@dataclass
class Scenario:
    name: str
    issue: str
    question: str
    plan: list[dict]
    profile: dict
    checks: list[tuple[str, Check]]
    coach_memory: str = ""
    motivation_model: dict | None = None
    history: list[dict[str, str]] = field(default_factory=list)


def _iso(today: date, offset: int) -> str:
    return (today + timedelta(days=offset)).isoformat()


def _day(
    today: date, offset: int, workout: str, title: str, minutes: int, **extra
) -> dict:
    return {
        "date": _iso(today, offset),
        "workoutType": workout,
        "title": title,
        "description": title,
        "durationMinutes": minutes,
        **extra,
    }


def _profile(**overrides) -> dict:
    return {
        "name": "Rider",
        "bikeType": "mountain",
        "trainingGoal": "general_fitness",
        "fitnessLevel": "advanced",
        "currentFTP": 285,
        "maxHeartRate": 185,
        **overrides,
    }


def in_german(result: Result) -> bool:
    return language(result.reply) == "de"


def in_english(result: Result) -> bool:
    return language(result.reply) == "en"


def scenarios(today: date) -> list[Scenario]:
    weekday = GERMAN_WEEKDAYS[today.weekday()]
    tomorrow = _iso(today, 1)
    friday_offset = (4 - today.weekday()) % 7 or 7
    friday = _iso(today, friday_offset)
    week = [
        _day(today, offset, "endurance", "Z2 endurance", 90) for offset in range(-3, 10)
    ]

    def with_days(*days: dict) -> list[dict]:
        # In date order: ask_trainer takes the next days from the list as given,
        # so a day appended at the end fell out of the window it reads.
        replaced = {day["date"] for day in days}
        return sorted(
            [d for d in week if d["date"] not in replaced] + list(days),
            key=lambda d: d["date"],
        )

    return [
        Scenario(
            name="past-race",
            issue="ops#71",
            question="Was wäre heute optimal, unabhängig von meiner Präferenz?",
            plan=week,
            profile=_profile(
                trainingGoal="race",
                raceDate=_iso(today, -150),
                raceDescription="80km 4000hm",
            ),
            checks=[
                ("replies in German", in_german),
                (
                    "does not plan toward the race that is over",
                    lambda r: (
                        not re.search(
                            r"rennen|\brace\b|\btaper",
                            re.sub(
                                r"\b(?:kein\w*|no)\s+(?:\w+\s+)?(?:rennen|race)\b",
                                "",
                                r.reply,
                                flags=re.IGNORECASE,
                            ),
                            re.IGNORECASE,
                        )
                    ),
                ),
            ],
        ),
        Scenario(
            name="current-ftp",
            issue="ops#70",
            question="Welche Wattwerte soll ich heute für Z2 fahren?",
            plan=week,
            profile=_profile(currentFTP=285),
            checks=[
                ("replies in German", in_german),
                ("does not use the old 320 W FTP", lambda r: "320" not in r.reply),
                (
                    "names Z2 watts for an FTP of 285 W (about 157–214 W)",
                    lambda r: any(
                        140 <= int(n) <= 225
                        for n in re.findall(
                            r"(\d{3})\s*(?:-|–|bis|to)?\s*\d*\s*(?:w|watt)\b",
                            r.reply,
                            re.IGNORECASE,
                        )
                    ),
                ),
            ],
        ),
        Scenario(
            name="tomorrow-off",
            issue="ops#72",
            question="Morgen kann ich nicht trainieren. Was machen wir mit den Intervallen?",
            plan=with_days(_day(today, 1, "intervals", "VO2 max 5x4", 90)),
            profile=_profile(
                availabilityConstraints=[
                    {
                        "constraintType": "no_training",
                        "constraintDate": tomorrow,
                        "reason": "Athlete said they are unavailable for training.",
                    }
                ]
            ),
            checks=[
                ("replies in German", in_german),
                (
                    "takes the intervals off tomorrow",
                    lambda r: (
                        (u := r.update_for(tomorrow)) is not None
                        and str(u.get("workoutType", "")).lower()
                        in {"rest", "off", "rest day"}
                    ),
                ),
            ],
        ),
        Scenario(
            name="today-is-today",
            issue="date confusion",
            question="Was steht heute an?",
            plan=with_days(_day(today, 0, "endurance", "Long endurance ride", 210)),
            profile=_profile(),
            checks=[
                ("replies in German", in_german),
                (
                    "names today's session, the long ride",
                    lambda r: bool(
                        re.search(
                            r"\b(?:lang\w*|long|210|3[.,]5)\b", r.reply, re.IGNORECASE
                        )
                    ),
                ),
                (
                    f"does not call today another weekday than {weekday}",
                    lambda r: all(
                        day == weekday
                        for day in re.findall(
                            r"\bheute\b[^.]{0,12}?\b("
                            + "|".join(GERMAN_WEEKDAYS)
                            + r")\b",
                            r.reply.casefold(),
                        )
                    ),
                ),
            ],
        ),
        Scenario(
            name="fresh-after-rest",
            issue="readiness, chat 2026-07-29",
            question=(
                "I had two full rest days and my legs feel really fresh. "
                "Could I do a sweet spot session today instead of the easy spin?"
            ),
            plan=with_days(
                _day(today, -2, "rest", "Rest day", 0),
                _day(today, -1, "rest", "Rest day", 0),
                _day(today, 0, "recovery", "Easy spin", 45),
            ),
            profile=_profile(),
            checks=[
                ("replies in English", in_english),
                (
                    "does not turn the day after two rest days into a third",
                    lambda r: (
                        (u := r.update_for(_iso(today, 0))) is None
                        or str(u.get("workoutType", "")).lower() not in {"rest", "off"}
                    ),
                ),
                (
                    "engages with the sweet spot request",
                    lambda r: "sweet spot" in r.reply.casefold(),
                ),
            ],
        ),
        Scenario(
            name="trail-motivation",
            issue="rider identity, chat 2026-08-06",
            question="Heute 2-3h Grundlage: Rennrad oder MTB?",
            plan=week,
            profile=_profile(),
            # Where production puts a stated objective (#563), set by the athlete.
            motivation_model={
                "primary_objective": (
                    "Ride as many technical MTB trails as possible; fitness is the means, "
                    "road riding is only training"
                ),
                "primary_objective_source": "user_set",
            },
            checks=[
                ("replies in German", in_german),
                (
                    "recommends the MTB",
                    lambda r: (
                        (
                            m := re.search(
                                r"\b(mtb|mountain\s*bike|rennrad|road)\b",
                                r.reply,
                                re.IGNORECASE,
                            )
                        )
                        is not None
                        and re.sub(r"\s+", "", m.group(1).lower())
                        in {"mtb", "mountainbike"}
                    ),
                ),
            ],
        ),
        Scenario(
            name="claims-match-changes",
            issue="unapplied changes, chat 2026-06-24",
            question="Bitte mach Freitag zum Ruhetag.",
            plan=with_days(
                _day(today, friday_offset, "intervals", "Threshold 3x12", 90)
            ),
            profile=_profile(),
            checks=[
                ("replies in German", in_german),
                (
                    "makes Friday a rest day in the plan update",
                    lambda r: (
                        (u := r.update_for(friday)) is not None
                        and str(u.get("workoutType", "")).lower()
                        in {"rest", "off", "rest day"}
                    ),
                ),
                (
                    "claims a change only alongside one",
                    lambda r: not _CLAIMED_CHANGE.search(r.reply) or bool(r.updates),
                ),
            ],
        ),
    ]


async def run(
    provider: str, only: str | None = None, today: date | None = None
) -> list[tuple[Scenario, Result | None, list[str], str]]:
    """Ask each scenario once; return the scenario, its result, failed checks, error."""
    today = today or app_today()
    names = [scenario.name for scenario in scenarios(today)]
    if only and only not in names:
        # A typo must not read as a clean run of nothing.
        raise ValueError(f"no scenario named {only!r}; choose from {', '.join(names)}")
    outcomes = []
    for scenario in scenarios(today):
        if only and scenario.name != only:
            continue
        try:
            raw = await ai_service.ask_trainer(
                scenario.question,
                scenario.plan,
                scenario.profile,
                provider=provider,
                coach_memory=scenario.coach_memory or None,
                motivation_model=scenario.motivation_model,
                conversation_history=scenario.history or None,
            )
        # A broken call is a result too, not a crash of the run.
        except Exception as error:
            outcomes.append((scenario, None, [], f"{type(error).__name__}: {error}"))
            continue
        result = Result(
            str(raw.get("response") or ""), list(raw.get("plan_updates") or [])
        )
        failed = [label for label, check in scenario.checks if not check(result)]
        outcomes.append((scenario, result, failed, ""))
    return outcomes


def render(outcomes) -> str:
    passed = sum(
        1
        for _, result, failed, error in outcomes
        if result and not failed and not error
    )
    lines = [
        f"**Coach scenarios: {passed} of {len(outcomes)} passed**",
        "",
        "| Scenario | From | Result |",
        "|---|---|---|",
    ]
    for scenario, result, failed, error in outcomes:
        verdict = (
            f"error: {error}"
            if error
            else ("pass" if not failed else "fail: " + "; ".join(failed))
        )
        lines.append(f"| `{scenario.name}` | {scenario.issue} | {verdict} |")
    for scenario, result, failed, _error in outcomes:
        if result and failed:
            excerpt = " ".join(result.reply.split())[:400]
            lines += [
                "",
                f"<details><summary><code>{scenario.name}</code> reply</summary>",
                "",
                f"> {excerpt}",
                "",
                "</details>",
            ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Print the report and exit 0 whatever it says: a run is a reading to post
    and compare over time, and one model reply is not a gate."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", default="gemini")
    parser.add_argument("--only", help="run one scenario by name")
    args = parser.parse_args(argv)
    try:
        outcomes = asyncio.run(run(args.provider, args.only))
    except ValueError as error:
        parser.error(str(error))
    print(render(outcomes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
