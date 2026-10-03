"""Per-sport fitness, aggregated fatigue (#713).

CTL and ATL used to be one pair of numbers for the whole athlete, derived from
cycling TSS. Once a run and a gym hour carry a load too (#712), that pair has to
answer two questions it cannot answer at once:

*Fitness* is sport-specific. Adding a marathon's load onto a cycling CTL claims
the athlete got better at cycling by running, which is not what happened — the
adaptations that make 250 W sustainable are not the ones a long run produces. A
summed figure is worse than no figure, because it reads as a bike number.

*Fatigue* is not. Whatever the athlete did yesterday, the legs, the sleep debt
and the endocrine cost are shared, and they are what decides whether today's
session should happen. Summing load into one ATL is arguable — a tempo run and a
tempo ride do not fatigue identically — but it is useful and it errs towards
caution, which is the direction that does not get someone hurt.

So this module keeps **one CTL per sport and one ATL across all of them**, and
derives TSB per sport as ``that sport's CTL − the aggregate ATL``. A hard week of
running then shows up as cycling fatigue, exactly as it is felt, without
inflating cycling fitness. That is the same split the established platforms make,
and for the same reason.

What is deliberately *not* modelled here is cross-sport transfer: a hike does
build aerobic fitness a cyclist benefits from, but quantifying that is the thing
a single shared CTL got wrong in the first place. Each sport keeps its own rung
until there is evidence to price the transfer with.

Pre-#713 rows carry a single ``ctl_after`` and no per-sport breakdown. They are
read back as cycling (see :func:`ledger_from_row`), which is what that number
meant when it was written.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field

from services.activity_identity import (
    SPORT_CYCLING,
    power_model_applies,
    training_sport,
)

# Standard impulse-response time constants: 42 days for chronic load, 7 for
# acute. Named here because the ledger and the single-step primitive below have
# to use the same ones — a per-sport CTL that decayed on a different constant
# from the aggregate ATL would make TSB drift for no physiological reason.
CTL_TIME_CONSTANT_DAYS = 42.0
ATL_TIME_CONSTANT_DAYS = 7.0
ALPHA_CTL = 1.0 - math.exp(-1.0 / CTL_TIME_CONSTANT_DAYS)
ALPHA_ATL = 1.0 - math.exp(-1.0 / ATL_TIME_CONSTANT_DAYS)

# The rung a session goes to when nothing at all can be said about its sport.
# A backstop rather than a live path — ``power_model_applies`` already answers
# ``True`` for a missing or unreadable label, so the gate below catches those
# first — and cycling either way, because splitting one athlete's history on the
# strength of an empty field is the one outcome with no defence.
UNREADABLE_SPORT_LEDGER = SPORT_CYCLING


def ledger_sport(sport_type: str | None) -> str:
    """Which CTL rung a session with this sport type belongs to.

    Gated on :func:`services.activity_identity.power_model_applies` first,
    because the rung has to agree with the currency the load was priced in: if
    the power model was entitled to answer for this sport, the number in ``tss``
    is cycling TSS computed against the athlete's cycling FTP, and it belongs on
    the rung that FTP describes. That covers two cases ``training_sport`` reads
    differently for the planner's purposes — a handcycle, which is a bicycle with
    the cranks in a different place (#711), and the provider catch-all labels
    ("Workout", "Other") that name no sport at all.

    Always answers, unlike ``training_sport``: a ledger has to put every session
    somewhere, and the fallback is a documented choice
    (:data:`UNREADABLE_SPORT_LEDGER`) rather than each caller's guess.
    """
    if power_model_applies(sport_type):
        return SPORT_CYCLING
    return training_sport(sport_type) or UNREADABLE_SPORT_LEDGER


def apply_ctl_atl_decay(
    prev_ctl: float,
    prev_atl: float,
    tss: float,
    gap_days: int = 1,
) -> tuple[float, float]:
    """Advance one CTL/ATL pair by ``gap_days``, applying zero-TSS decay for
    silent days then ``tss`` on the final (session) day.

    ``gap_days=1`` means the session is on the very next day — no silent days.
    ``gap_days=3`` means 2 rest days then the session day.

    The single-sport primitive, kept because two alembic revisions replay the
    chain with it and are frozen history; :class:`LoadLedger` is the multisport
    caller. It lived in ``services.analysis`` until #713 and is still importable
    from there for those revisions.
    """
    # Decay through silent days (TSS = 0 each day before the session)
    silent_days = max(0, gap_days - 1)
    if silent_days > 0:
        # Compounded zero-TSS decay: CTL_n = CTL_0 * (1 - α)^n
        prev_ctl = prev_ctl * (1.0 - ALPHA_CTL) ** silent_days
        prev_atl = prev_atl * (1.0 - ALPHA_ATL) ** silent_days

    # Apply the session's load on the session day
    new_ctl = prev_ctl + ALPHA_CTL * (tss - prev_ctl)
    new_atl = prev_atl + ALPHA_ATL * (tss - prev_atl)
    return new_ctl, new_atl


@dataclass(frozen=True)
class LoadLedger:
    """One CTL per sport plus the one ATL they all contribute to.

    Immutable: :meth:`advance` returns the next state rather than mutating, so a
    chain rebuild cannot half-apply a session the way a mutable accumulator can.
    """

    ctl_by_sport: Mapping[str, float] = field(default_factory=dict)
    atl: float = 0.0

    @classmethod
    def seeded(cls, *, cycling_ctl: float = 0.0, atl: float = 0.0) -> LoadLedger:
        """A ledger holding a single cycling CTL — the pre-#713 reading.

        Used where the only state available is the old scalar pair, and by the
        legacy ``initial_ctl``/``initial_atl`` arguments of
        ``build_ride_metrics_chain``.
        """
        return cls(ctl_by_sport={SPORT_CYCLING: cycling_ctl} if cycling_ctl else {}, atl=atl)

    def ctl(self, sport: str | None) -> float:
        """Chronic load for one sport. ``0.0`` for a sport with no history —
        which is a true statement about that sport, not a missing value.
        """
        return float(self.ctl_by_sport.get(ledger_sport(sport), 0.0))

    def tsb(self, sport: str | None) -> float:
        """Form for one sport: its own fitness against the shared fatigue.

        This is the asymmetry the whole module exists for. ``tsb("cycling")``
        drops after a week of running because ``atl`` rose while cycling CTL
        only decayed, which is what the athlete's legs report.
        """
        return self.ctl(sport) - self.atl

    def advance(
        self,
        *,
        sport: str | None,
        load: float,
        days_since_previous: int = 1,
    ) -> LoadLedger:
        """Apply one session's ``load`` and return the resulting ledger.

        ``days_since_previous`` is the raw calendar delta from the previous
        session: ``0`` for the second half of a two-a-day, ``1`` for the next
        day, ``3`` for two rest days then this one. It is deliberately the raw
        delta rather than the clamped ``gap_days`` the primitive takes, because
        the sports that did *not* happen need to know whether a day actually
        passed — clamping a same-date second session up to 1 would decay every
        other sport for a day that never elapsed.
        """
        elapsed = max(0, days_since_previous)
        active = ledger_sport(sport)

        # Every other sport spent this stretch resting, so its CTL decays for the
        # days that passed and takes none of the load.
        decay = (1.0 - ALPHA_CTL) ** elapsed
        next_ctl = {
            other: value * decay
            for other, value in self.ctl_by_sport.items()
            if other != active
        }

        # The active sport and the aggregate ATL go through the single-sport
        # primitive, so a cycling-only history reproduces the pre-#713 chain to
        # the digit: with one sport there is nothing else to decay.
        active_ctl, atl = apply_ctl_atl_decay(
            self.ctl_by_sport.get(active, 0.0),
            self.atl,
            load,
            gap_days=max(1, elapsed),
        )
        next_ctl[active] = active_ctl
        return LoadLedger(ctl_by_sport=next_ctl, atl=atl)

    def as_dict(self, *, digits: int = 2) -> dict[str, float]:
        """The per-sport CTL map as stored JSON, rounded like ``ctl_after`` is.

        Rungs that have decayed to nothing are dropped: a sport the athlete has
        not touched in a year should not keep a 0.0 entry alive forever, and
        ``ctl()`` already answers 0.0 for an absent sport.
        """
        rounded = {
            sport: round(float(value), digits) for sport, value in self.ctl_by_sport.items()
        }
        return {sport: value for sport, value in rounded.items() if value != 0.0}


def ledger_from_row(
    ctl_by_sport: Mapping[str, float] | None,
    *,
    ctl_after: float | None,
    atl_after: float | None,
) -> LoadLedger:
    """Rebuild a ledger from a stored ``RideMetric``-shaped row.

    Falls back to reading ``ctl_after`` as cycling when ``ctl_by_sport`` is
    absent, which is what a pre-#713 row means: every sport's load was summed
    into one CTL that the dashboard, the coach prompt and the plan projection all
    read as the athlete's cycling fitness.

    What comes back is the **stored** ledger, so an incremental chain continues
    from values rounded to 2 dp and without the rungs :meth:`LoadLedger.as_dict`
    dropped for rounding to zero. Both are deliberate and neither is new: the
    scalar ``ctl_after`` this replaces was already stored at 2 dp and already
    reseeded every incremental import from the rounded figure. The per-step error
    is bounded at 0.005 against an α of 0.0235 and a reported value rounded to
    0.1, and a rung that rounds to zero reads back as the 0.0 it would have
    decayed to anyway. Carrying the unrounded ledger would mean returning state
    the chain does not persist, which is a larger change than the drift is worth.
    """
    atl = float(atl_after or 0.0)
    if ctl_by_sport:
        return LoadLedger(
            ctl_by_sport={
                sport: float(value)
                for sport, value in ctl_by_sport.items()
                if isinstance(value, (int, float))
            },
            atl=atl,
        )
    return LoadLedger.seeded(cycling_ctl=float(ctl_after or 0.0), atl=atl)


def ledger_from_metric(metric: object | None) -> LoadLedger:
    """The ledger to continue an incremental chain from, given the latest row.

    Every import path seeds the chain from the newest stored ``RideMetric``.
    Before #713 each did it by hand off ``ctl_after``/``atl_after``; that scalar
    is now whichever sport was logged last, so reading it as the athlete's
    cycling fitness would hand a cycling chain a running CTL the first time
    someone's most recent activity is a run. One helper, so the five paths
    cannot drift on it.
    """
    if metric is None:
        return LoadLedger()
    return ledger_from_row(
        getattr(metric, "ctl_by_sport", None),
        ctl_after=getattr(metric, "ctl_after", None),
        atl_after=getattr(metric, "atl_after", None),
    )
