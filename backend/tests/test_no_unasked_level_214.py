"""No fitness level unless the athlete picks one (ai-trainer-ops#214).

Onboarding folds the question away, and it used to send "intermediate" for
everyone who left the fold shut. The coach was then told that level as the
athlete's own answer, and said so.
"""

from __future__ import annotations

from datetime import date

import pytest

import models
from services import opening_message


@pytest.mark.asyncio
async def test_onboarding_without_a_level_stores_none(client, auth_headers):
    resp = await client.put("/api/v1/users/me", json={"fitnessLevel": None}, headers=auth_headers)

    assert resp.status_code == 200
    assert resp.json()["fitnessLevel"] is None
    me = await client.get("/api/v1/users/me", headers=auth_headers)
    assert me.json()["fitnessLevel"] is None


def test_the_coach_is_told_no_level_nobody_gave():
    user = models.User(sports=["cycling"], training_goal="general_fitness", fitness_level=None)

    facts = opening_message.build_facts(user, None, date(2026, 10, 24))

    assert "level" not in facts["answers"]
