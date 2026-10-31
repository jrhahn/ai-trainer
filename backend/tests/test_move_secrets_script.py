"""The deploy's move of stored secrets onto their own keys (ai-trainer-ops#299).

``scripts/move-secrets-to-own-keys.sh`` runs ``reencrypt_secrets`` on the live
deployment after every deploy. It must write nothing unless a dry run finds
something to move and a fresh backup succeeded first, and it must fail the
deploy on anything it cannot vouch for. A fake ``docker compose`` answers here
and records what it was asked.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "move-secrets-to-own-keys.sh"
DEPLOY = REPO / "deploy" / "ansible" / "deploy.yml"

FAKE_COMPOSE = """#!/usr/bin/env bash
echo "compose $*" >> "$CALLS"
case "$*" in
  *" ps "*) printf '%s\\n' "${PS_OUTPUT}" ;;
  *"reencrypt_secrets --commit"*) exit "${COMMIT_RC:-0}" ;;
  *"reencrypt_secrets"*) printf '%s\\n' "${DRY_OUTPUT}"; exit "${DRY_RC:-0}" ;;
  *"verify_restore"*) exit "${VERIFY_RC:-0}" ;;
esac
"""
FAKE_BACKUP = """#!/usr/bin/env bash
echo "backup with $BACKUP_PASSPHRASE_FILE" >> "$CALLS"
exit "${BACKUP_RC:-0}"
"""
HEALTHY = '{"Name":"ai-trainer-backend-1","Health":"healthy"}'


def _dry(moving: int) -> str:
    return (
        "Moving stored secrets onto their own keys (dry run, nothing is written):\n"
        f"  intervals_api_key  intervals  {moving} to move, 0 on its own key\n"
        f"\n{moving} would move. Run again with --commit to write them."
    )


def _executable(path: Path, text: str) -> Path:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def run(tmp_path):
    calls = tmp_path / "calls"
    passphrase = tmp_path / "backup.pass"
    passphrase.write_text("not a real one\n")
    compose = _executable(tmp_path / "compose", FAKE_COMPOSE)
    backup = _executable(tmp_path / "backup", FAKE_BACKUP)

    def go(**env: str) -> tuple[subprocess.CompletedProcess, list[str]]:
        full = {
            "PATH": os.environ["PATH"],
            "CALLS": str(calls),
            "APP_DIR": str(tmp_path),
            "COMPOSE_CMD": str(compose),
            "BACKUP_CMD": str(backup),
            "BACKUP_PASSPHRASE_FILE": str(passphrase),
            "PS_OUTPUT": HEALTHY,
            "DRY_OUTPUT": _dry(3),
            "WAIT_SECONDS": "0",
            **env,
        }
        result = subprocess.run(
            ["bash", str(SCRIPT)], env=full, capture_output=True, text=True
        )
        said = calls.read_text().splitlines() if calls.exists() else []
        return result, said

    return go


def _steps(said: list[str]) -> list[str]:
    names = []
    for line in said:
        if line.startswith("backup"):
            names.append("backup")
        elif "--commit" in line:
            names.append("commit")
        elif "reencrypt_secrets" in line:
            names.append("dry run")
        elif "verify_restore" in line:
            names.append("verify")
    return names


def test_something_to_move_is_backed_up_moved_and_verified_in_that_order(run):
    result, said = run()

    assert result.returncode == 0, result.stderr
    assert _steps(said) == ["dry run", "backup", "commit", "verify"]
    assert "move-secrets: moved 3; every encrypted column opens" in result.stdout


def test_nothing_to_move_writes_nothing_and_takes_no_backup(run):
    result, said = run(DRY_OUTPUT=_dry(0))

    assert result.returncode == 0, result.stderr
    assert _steps(said) == ["dry run"]
    assert "nothing to move" in result.stdout


def test_the_dry_run_s_counts_reach_the_deploy_log(run):
    result, _said = run()

    assert "intervals_api_key  intervals  3 to move" in result.stdout


@pytest.mark.parametrize(
    ("env", "steps", "message"),
    [
        # An unreadable value: reencrypt_secrets exits 1 and writes nothing.
        ({"DRY_RC": "1"}, ["dry run"], "the dry run failed"),
        ({"DRY_OUTPUT": "something else"}, ["dry run"], "could not read"),
        (
            {"BACKUP_PASSPHRASE_FILE": "/nonexistent"},
            ["dry run"],
            "no backup passphrase",
        ),
        ({"BACKUP_PASSPHRASE_FILE": ""}, ["dry run"], "no backup passphrase"),
        ({"BACKUP_RC": "1"}, ["dry run", "backup"], "the backup failed"),
        ({"COMMIT_RC": "1"}, ["dry run", "backup", "commit"], "the move failed"),
        (
            {"VERIFY_RC": "1"},
            ["dry run", "backup", "commit", "verify"],
            "verify_restore failed",
        ),
    ],
)
def test_each_failure_fails_the_deploy_and_stops_there(run, env, steps, message):
    result, said = run(**env)

    assert result.returncode == 1
    assert _steps(said) == steps
    assert message in result.stderr


def test_an_unhealthy_backend_is_not_asked_anything(run):
    result, said = run(PS_OUTPUT='{"Name":"ai-trainer-backend-1","Health":"starting"}')

    assert result.returncode == 1
    assert _steps(said) == []
    assert "was not healthy" in result.stderr


def test_the_backup_gets_the_passphrase_file(run, tmp_path):
    _result, said = run()

    assert f"backup with {tmp_path / 'backup.pass'}" in said


def test_the_deploy_runs_it_after_the_stack_is_up():
    [play] = yaml.safe_load(DEPLOY.read_text())
    names = [task["name"] for task in play["tasks"]]
    move = names.index("Move stored secrets onto their own keys")

    assert move > names.index("Start Docker services")
    task = play["tasks"][move]
    assert task["ansible.builtin.command"]["cmd"].endswith(
        "/scripts/move-secrets-to-own-keys.sh"
    )
    assert (
        task["environment"]["BACKUP_PASSPHRASE_FILE"] == "/root/ai-trainer-backup.pass"
    )


def test_the_script_is_executable():
    assert os.access(SCRIPT, os.X_OK)
