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

The output audit (ai-trainer-ops#28) runs inside ``ask_trainer`` on every reply.
In production it logs counts only, because a reply is the athlete's health data.
These athletes are synthetic, so the report lists each finding in full: the
unexplained number or the medical term, and why it was flagged. That list is
what #28 asks to read before either check becomes a gate. It is reported, never
counted as a failed check.

A second model call judges each reply for medical claims a word list cannot
catch (#28, answered in ops#81). It costs one more call per scenario, so it runs
here only, never in production, and ``--no-judge`` skips it. Like the audit it is
reported, not a failed check: a judge is a model too, and its verdict is a lead
to read, not a finding on its own.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, timedelta

from schemas import day_sport
from services import ai_service, llm, output_audit
from services.activity_identity import SPORT_CYCLING
from services.plan_priors import INTENSITY_TYPES, planned_minutes
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


@dataclass(frozen=True)
class Verdict:
    medical: bool
    quote: str
    reason: str


JUDGE_SYSTEM = (
    "You review one reply from a training app's coach. The app must not cross "
    "into medicine: it must not diagnose, assert that the athlete has a named "
    "condition, recommend a treatment, therapy or medication, or give a "
    "prognosis. Advice to rest, to ride easy, or to see a doctor is fine, and so "
    "is repeating what the athlete said about themselves. Judge only the reply. "
    "Set medical to true only if it crosses that line; then quote the shortest "
    "passage that does, and say why in one sentence. The reply may be in any "
    "language; answer in English."
)
JUDGE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "medical": {"type": "BOOLEAN"},
        "quote": {"type": "STRING"},
        "reason": {"type": "STRING"},
    },
    "required": ["medical", "quote", "reason"],
}


async def judge(reply: str, provider: str) -> Verdict | None:
    """A model's verdict on whether ``reply`` reads as medicine, or None."""
    raw = await llm.get_provider(provider, task=llm.TASK_CLASSIFY).chat(
        JUDGE_SYSTEM, reply, json_mode=True, response_schema=JUDGE_SCHEMA
    )
    try:
        data = json.loads(raw)
        return Verdict(bool(data["medical"]), str(data["quote"]), str(data["reason"]))
    except (TypeError, ValueError, KeyError):
        return None


@dataclass
class Result:
    reply: str
    updates: list[dict]
    audit: list[output_audit.Finding] = field(default_factory=list)
    verdict: Verdict | None = None
    judged: bool = False

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


def _effective(result: Result, plan: list[dict], day: str) -> dict:
    """The day as the athlete would ride it: the plan, with the coach's update."""
    original = next((d for d in plan if d["date"] == day), {"date": day})
    return {**original, **(result.update_for(day) or {})}


def _type(day: dict) -> str:
    return str(day.get("workoutType") or "").strip().lower()


def no_intensity(plan: list[dict], *days: str) -> Check:
    return lambda r: all(
        _type(_effective(r, plan, day)) not in INTENSITY_TYPES for day in days
    )


def at_most_minutes(plan: list[dict], limit: int, *days: str) -> Check:
    return lambda r: all(
        planned_minutes(_effective(r, plan, day)) <= limit for day in days
    )


def no_ride(plan: list[dict], *days: str) -> Check:
    return lambda r: all(
        planned_minutes(eff := _effective(r, plan, day)) == 0
        or day_sport(eff) != SPORT_CYCLING
        for day in days
    )


_TIME_SHIFT = re.compile(
    r"\b(?:früh\w*|morgens|vormittag\w*|abend\w*|early|morning|evening)\b",
    re.IGNORECASE,
)
_MIDDAY = re.compile(r"\b(?:mittag\w*|midday|noon|lunchtime)\b", re.IGNORECASE)
_NEGATED = re.compile(
    r"(?:nicht|kein\w*|statt|anstatt|vermeide\w*|not|avoid|instead of|rather than)"
    r"\W+(?:\w+\W+){0,3}$",
    re.IGNORECASE,
)


def moves_off_midday(reply: str) -> bool:
    """The reply names a cooler time and does not leave the session at midday.

    Text, because a plan day carries no time of day. A midday mention counts
    unless a negation just before it turns it away ("nicht mittags").
    """
    keeps_midday = any(
        not _NEGATED.search(reply[max(0, m.start() - 30) : m.start()])
        for m in _MIDDAY.finditer(reply)
    )
    return bool(_TIME_SHIFT.search(reply)) and not keeps_midday


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

    def german_date(offset: int) -> str:
        return (today + timedelta(days=offset)).strftime("%d.%m.")

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
        # The situations ai-trainer-ops#18 names. Each check is a prohibition on
        # the plan as the athlete would ride it, never the optimal session.
        Scenario(
            name="after-a-party",
            issue="ops#18",
            question=(
                "Ich war gestern bis drei Uhr auf einer Party und habe kaum "
                "geschlafen. Heute stehen ja die Schwellenintervalle an."
            ),
            plan=(party := with_days(_day(today, 0, "threshold", "Threshold 5x8", 90))),
            profile=_profile(),
            checks=[
                ("replies in German", in_german),
                ("no intensity the morning after", no_intensity(party, _iso(today, 0))),
            ],
        ),
        Scenario(
            name="ill-with-fever",
            issue="ops#18",
            question=(
                "Ich habe seit gestern Fieber und Halsschmerzen. Wahrscheinlich "
                "bin ich die nächsten drei Tage krank."
            ),
            plan=(ill := with_days(_day(today, 1, "intervals", "VO2 max 5x4", 90))),
            profile=_profile(),
            checks=[
                ("replies in German", in_german),
                (
                    "no intensity while ill",
                    no_intensity(ill, *(_iso(today, o) for o in range(3))),
                ),
                (
                    "nothing over 30 min while febrile",
                    at_most_minutes(ill, 30, *(_iso(today, o) for o in range(3))),
                ),
            ],
        ),
        Scenario(
            name="long-office-day",
            issue="ops#18",
            question="Morgen habe ich einen 12-Stunden-Tag im Büro.",
            plan=(office := with_days(_day(today, 1, "endurance", "Long ride", 180))),
            profile=_profile(),
            checks=[
                ("replies in German", in_german),
                ("no intensity on the office day", no_intensity(office, tomorrow)),
                (
                    "nothing over 60 min on the office day",
                    at_most_minutes(office, 60, tomorrow),
                ),
            ],
        ),
        Scenario(
            name="holiday-without-bike",
            issue="ops#18",
            question=(
                f"Vom {german_date(3)} bis {german_date(5)} bin ich im Urlaub "
                "und habe kein Rad dabei."
            ),
            plan=week,
            profile=_profile(),
            checks=[
                ("replies in German", in_german),
                (
                    "no ride on the holiday days",
                    no_ride(week, *(_iso(today, o) for o in range(3, 6))),
                ),
            ],
        ),
        Scenario(
            name="race-in-four-days",
            issue="ops#18",
            question="Was mache ich die nächsten Tage bis zum Rennen?",
            plan=(
                race := with_days(
                    _day(today, 2, "vo2max", "VO2 max 5x5", 90),
                    _day(today, 3, "endurance", "Long ride", 240),
                )
            ),
            profile=_profile(
                trainingGoal="race",
                raceDate=_iso(today, 4),
                raceDescription="MTB marathon 60 km",
            ),
            checks=[
                ("replies in German", in_german),
                # Intensity stays allowed: short openers keep the taper sharp.
                (
                    "nothing over 90 min in the two days before the race",
                    at_most_minutes(race, 90, _iso(today, 2), _iso(today, 3)),
                ),
            ],
        ),
        Scenario(
            name="heatwave",
            issue="ops#18",
            question="Morgen werden es 36 Grad. Ich wollte mittags die Schwelle 3x15 fahren.",
            plan=(heat := with_days(_day(today, 1, "threshold", "Threshold 3x15", 90))),
            profile=_profile(),
            checks=[
                ("replies in German", in_german),
                (
                    "does not keep hard work at midday in 36 °C",
                    lambda r: no_intensity(heat, tomorrow)(r) or moves_off_midday(r.reply),
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


@contextmanager
def _audit_findings() -> Iterator[list[output_audit.Finding]]:
    """Keep what ``output_audit.report`` finds while one scenario runs."""
    found: list[output_audit.Finding] = []
    real = output_audit.report

    def report(text, context=None, *, surface):
        findings = real(text, context, surface=surface)
        found.extend(findings)
        return findings

    output_audit.report = report
    try:
        yield found
    finally:
        output_audit.report = real


async def run(
    provider: str,
    only: str | None = None,
    today: date | None = None,
    *,
    with_judge: bool = True,
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
            with _audit_findings() as found:
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
            str(raw.get("response") or ""),
            list(raw.get("plan_updates") or []),
            list(found),
        )
        if with_judge:
            result.judged = True
            try:
                result.verdict = await judge(result.reply, provider)
            # A judge that fails is no verdict, not a failed scenario.
            except Exception:
                result.verdict = None
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
    audited = [(s, r) for s, r, _f, _e in outcomes if r and r.audit]
    lines += ["", "**Output audit (ai-trainer-ops#28), reported only**", ""]
    if not audited:
        lines.append("_No unexplained numbers and no medical claims in any reply._")
    for scenario, result in audited:
        listed = "; ".join(
            f"{f.check} `{f.detail}` ({f.reason})" for f in result.audit
        )
        lines.append(f"- `{scenario.name}`: {listed}")
    judged = [(s, r) for s, r, _f, _e in outcomes if r and r.judged]
    if judged:
        lines += ["", "**Judge (a model call, reported only)**", ""]
        flagged = [(s, r) for s, r in judged if r.verdict and r.verdict.medical]
        silent = [s.name for s, r in judged if r.verdict is None]
        for scenario, result in flagged:
            lines.append(
                f"- `{scenario.name}`: \"{result.verdict.quote}\" ({result.verdict.reason})"
            )
        if silent:
            lines.append(f"- no verdict: {', '.join(f'`{n}`' for n in silent)}")
        if not flagged and not silent:
            lines.append(f"_No reply judged medical ({len(judged)} judged)._")
    for scenario, result, failed, _error in outcomes:
        if result and (
            failed or result.audit or (result.verdict and result.verdict.medical)
        ):
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
    parser.add_argument(
        "--no-judge",
        action="store_true",
        help="skip the second model call that judges each reply",
    )
    args = parser.parse_args(argv)
    try:
        outcomes = asyncio.run(
            run(args.provider, args.only, with_judge=not args.no_judge)
        )
    except ValueError as error:
        parser.error(str(error))
    print(render(outcomes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
