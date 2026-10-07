"""A backup that cannot be opened again (ai-trainer-ops#13).

The restore this guards against is the one that *works*. `pg_restore` succeeds,
migrations are current, the app boots, `/healthz` is green — and every Strava
token, intervals.icu key and TOTP secret in the database is ciphertext no
configured key opens. Nothing raises, because
`EncryptedString.process_result_value` logs a warning and returns the raw value
on `InvalidToken` by design, so that rows predating encryption keep working
(models.py). The athlete discovers it when their second factor stops matching.

What this suite has to prove:

- the manifest covers every purpose, derived from the code rather than listed, so
  a fifth secret type cannot arrive with the backup silently omitting its key.
  That exact failure shipped once already here, as a hand-maintained tuple of two
  that nobody extended (jrhahn/ai-trainer#754);
- the manifest does not contain the keys. It travels next to the dump, and the
  reason those columns are encrypted at all is that the ciphertext and the key
  are not in the same place;
- a wrong key is *detected* — the fingerprints have to differ when the keys do,
  otherwise the check in `restore.sh` passes on last month's secrets bundle;
- `verify_restore` finds every encrypted column, and reports an unopenable value
  as unopenable rather than as a row from before encryption;
- the two shell scripts and the Python agree on the manifest's field names. They
  are in different languages and nothing else would catch a rename until an
  operator is halfway through a restore at the worst possible moment.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

import crud
import models
from config import _DEDICATED_KEY_FIELDS, SecretPurpose, settings
from database import async_session_maker
from scripts import backup_manifest, verify_restore

REPO = Path(__file__).resolve().parents[2]
BACKUP_SH = REPO / "scripts" / "backup.sh"
RESTORE_SH = REPO / "scripts" / "restore.sh"
SSH_SH = REPO / "scripts" / "backup-over-ssh.sh"


def _key() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture
def keys(monkeypatch) -> dict[str, str]:
    """A distinct key for the shared slot and for each purpose."""
    issued = {"shared": _key(), **{purpose: _key() for purpose in SecretPurpose.ALL}}
    monkeypatch.setattr(settings, "secrets_encryption_key", issued["shared"])
    monkeypatch.setattr(settings, "strava_encryption_key", "")
    for purpose, field in _DEDICATED_KEY_FIELDS.items():
        monkeypatch.setattr(settings, field, issued[purpose])
    return issued


@pytest.fixture
def no_keys(monkeypatch) -> None:
    """A deployment storing everything as plaintext, which dev and test do."""
    monkeypatch.setattr(settings, "secrets_encryption_key", "")
    monkeypatch.setattr(settings, "strava_encryption_key", "")
    for field in _DEDICATED_KEY_FIELDS.values():
        monkeypatch.setattr(settings, field, "")


# ---------------------------------------------------------------------------
# The manifest covers every purpose, without a second list
# ---------------------------------------------------------------------------


def test_the_manifest_fingerprints_every_purpose(keys):
    fragment = backup_manifest.manifest_fragment()
    assert set(fragment["key_chains"]) == set(SecretPurpose.ALL)


def test_the_manifest_records_the_whole_chain_not_only_the_encrypting_key(keys):
    """The reviewer's find on #755, and the common case rather than an edge.

    ``MultiFernet`` encrypts with the first key and decrypts with any, so a row
    written before its purpose gained a dedicated key is still under the shared
    one — and right after the ai-trainer-ops#12 split that is *every existing
    row*. A manifest naming only the dedicated key lets a restore onto a host
    with the right dedicated key and a wrong ``SECRETS_ENCRYPTION_KEY`` print
    "matches" and load, losing exactly the legacy data the check is for.
    """
    chains = backup_manifest.key_chains()
    for purpose in SecretPurpose.ALL:
        variables = [link["variable"] for link in chains[purpose]]
        assert variables == [
            _DEDICATED_KEY_FIELDS[purpose].upper(),
            "SECRETS_ENCRYPTION_KEY",
        ], f"{purpose} does not carry its shared-key fallback"


def test_the_chain_order_is_the_one_the_application_decrypts_with(keys):
    """Encrypting key first, matching ``encryption_keys_for``.

    Order is not cosmetic: ``restore.sh`` labels position 0 "encrypts new rows"
    and the rest "opens older rows", which is what tells an operator whether a
    mismatch threatens future writes or existing data.
    """
    for purpose in SecretPurpose.ALL:
        expected = [
            backup_manifest.fingerprint(key)
            for key in settings.encryption_keys_for(purpose)
        ]
        actual = [link["fingerprint"] for link in backup_manifest.key_chains()[purpose]]
        assert actual == expected


def test_there_is_more_than_one_purpose_to_cover():
    """Anti-vacuity for the sweeps above and below.

    Every check in this file that iterates ``SecretPurpose.ALL`` passes
    trivially if it is empty, and an empty inventory is exactly what a backup
    guard must not be allowed to become.
    """
    assert len(SecretPurpose.ALL) >= 4


def test_every_link_names_a_variable_an_operator_can_go_and_look_at(keys):
    """Each link's ``variable`` has to be a real environment variable.

    ``restore.sh`` prints it when a link fails, and it is the only thing telling
    the operator which value to fix. A name that is not actually the variable is
    worse than no name.
    """
    expected = {field.upper() for field in _DEDICATED_KEY_FIELDS.values()} | {
        "SECRETS_ENCRYPTION_KEY",
        "STRAVA_ENCRYPTION_KEY",
    }
    for purpose, chain in backup_manifest.key_chains().items():
        for link in chain:
            assert link["variable"] in expected, (
                f"{purpose} names {link['variable']}, which is not a key variable"
            )


def test_a_purpose_without_a_dedicated_key_has_a_chain_of_one(keys, monkeypatch):
    monkeypatch.setattr(settings, _DEDICATED_KEY_FIELDS[SecretPurpose.TOTP], "")
    chain = backup_manifest.key_chains()[SecretPurpose.TOTP]
    assert [link["variable"] for link in chain] == ["SECRETS_ENCRYPTION_KEY"]
    assert chain[0]["fingerprint"] == backup_manifest.fingerprint(keys["shared"])


def test_a_deployment_on_the_deprecated_name_is_told_the_deprecated_name(monkeypatch):
    """``STRAVA_ENCRYPTION_KEY`` still supplies the shared key (#613).

    An upgraded-rather-than-installed deployment may be running on it, and
    sending that operator to ``SECRETS_ENCRYPTION_KEY`` — which is empty on
    their host — would turn a working restore into a hunt.
    """
    shared = _key()
    monkeypatch.setattr(settings, "secrets_encryption_key", "")
    monkeypatch.setattr(settings, "strava_encryption_key", shared)
    for field in _DEDICATED_KEY_FIELDS.values():
        monkeypatch.setattr(settings, field, "")

    chains = backup_manifest.key_chains()
    assert {link["variable"] for chain in chains.values() for link in chain} == {
        "STRAVA_ENCRYPTION_KEY"
    }
    assert chains[SecretPurpose.STRAVA][0]["fingerprint"] == (
        backup_manifest.fingerprint(shared)
    )


def test_a_plaintext_deployment_has_an_empty_chain_rather_than_a_missing_one(no_keys):
    """No key is a legitimate state to back up, and a distinguishable one.

    An empty chain has to be told apart from a purpose absent from the manifest:
    the first means the columns hold plaintext and restore anywhere, the second
    means the manifest is too old to be believed about anything.
    """
    chains = backup_manifest.manifest_fragment()["key_chains"]
    assert set(chains) == set(SecretPurpose.ALL)
    assert all(chain == [] for chain in chains.values())


def test_a_purpose_with_no_key_field_is_a_loud_error(monkeypatch):
    """A fifth purpose added to ``ALL`` and not to ``_DEDICATED_KEY_FIELDS``.

    It must fail rather than silently drop out of the manifest — a purpose
    missing from the manifest is a purpose whose key a restore never checks.
    """
    monkeypatch.setattr(SecretPurpose, "ALL", (*SecretPurpose.ALL, "sleep_tokens"))
    with pytest.raises(ValueError, match="sleep_tokens"):
        backup_manifest.key_chains()


# ---------------------------------------------------------------------------
# The manifest is not a place keys leak
# ---------------------------------------------------------------------------


def test_the_manifest_contains_no_key(keys):
    """The manifest sits beside the dump and must stay safe to read.

    If it carried the keys, the backup directory would hold the ciphertext and
    everything that opens it — which is strictly worse than not encrypting the
    columns, because it looks protected and is not.
    """
    serialised = json.dumps(backup_manifest.manifest_fragment())
    for name, key in keys.items():
        assert key not in serialised, f"the {name} key is in the manifest"


def test_a_fingerprint_does_not_reveal_its_key(keys):
    printed = backup_manifest.fingerprint(keys["totp"])
    assert keys["totp"] not in printed
    assert len(printed) == 16


def test_two_different_keys_fingerprint_differently(keys):
    """The whole check rests on this.

    If fingerprints collided across keys, ``restore.sh`` would accept last
    month's secrets bundle for this week's dump and report a clean restore.
    """
    encrypting = {
        chain[0]["fingerprint"] for chain in backup_manifest.key_chains().values()
    }
    assert len(encrypting) == len(SecretPurpose.ALL)


def test_the_same_key_fingerprints_the_same_way(keys):
    """And the converse: the check has to pass on the right bundle."""
    assert backup_manifest.fingerprint(keys["totp"]) == backup_manifest.fingerprint(
        keys["totp"]
    )


def test_the_fingerprint_is_domain_separated(keys):
    """Not a bare sha256 of the key.

    A bare digest could be compared against any other system's hash of the same
    value; the prefix keeps the manifest from being an oracle about a key it
    deliberately does not contain.
    """
    import hashlib

    bare = hashlib.sha256(keys["totp"].encode()).hexdigest()[:16]
    assert backup_manifest.fingerprint(keys["totp"]) != bare


# ---------------------------------------------------------------------------
# verify_restore sees every encrypted column
# ---------------------------------------------------------------------------


def test_the_column_sweep_covers_every_purpose():
    """Walked from the mappers, so a new encrypted column is covered on sight."""
    purposes = {purpose for _, _, purpose in verify_restore.encrypted_columns()}
    assert purposes == set(SecretPurpose.ALL)


def test_the_column_sweep_finds_the_columns_we_know_about():
    """Anti-vacuity, and a pin on the ones that exist today.

    An empty sweep would satisfy every other assertion in this section.
    """
    found = {(table, column) for table, column, _ in verify_restore.encrypted_columns()}
    assert {
        ("users", "user_openai_api_key"),
        ("users", "user_gemini_api_key"),
        ("users", "totp_secret"),
        ("strava_tokens", "access_token"),
        ("strava_tokens", "refresh_token"),
        ("intervals_tokens", "api_key"),
    } <= found


def test_a_value_the_keys_cannot_open_is_reported_unreadable(keys):
    """The disaster case, classified as the disaster rather than as an old row."""
    written_under_another_key = Fernet(_key().encode()).encrypt(b"secret").decode()
    assert (
        verify_restore._classify(written_under_another_key, SecretPurpose.TOTP)
        == "unreadable"
    )


def test_a_row_from_before_encryption_is_not_reported_as_a_disaster(keys):
    """Plaintext predating the key must stay quiet, or the check cries wolf."""
    assert verify_restore._classify("TOTPSECRET234567", SecretPurpose.TOTP) == "plaintext"


def test_a_value_the_keys_do_open_is_readable(keys):
    ciphertext = Fernet(keys["totp"].encode()).encrypt(b"secret").decode()
    assert verify_restore._classify(ciphertext, SecretPurpose.TOTP) == "readable"


def test_a_key_removed_from_the_configuration_is_a_disaster_not_plaintext(no_keys):
    """Ciphertext with no key configured at all.

    The tempting reading is "no key, so everything is plaintext" — but a value
    that is unmistakably a Fernet token means the key that wrote it was taken
    away, which is precisely the restore this suite is about.
    """
    orphaned = Fernet(_key().encode()).encrypt(b"secret").decode()
    assert verify_restore._classify(orphaned, SecretPurpose.TOTP) == "unreadable"


async def test_the_sweep_reads_real_rows_and_finds_them_readable(keys):
    """End to end against the database, not only against the classifier."""
    async with async_session_maker() as session:
        user = await crud.create_user(
            session, email="restore-probe@example.com", name="Rider", hashed_password="x"
        )
        user.totp_secret = "TOTPSECRET234567"
        session.add(
            models.IntervalsToken(user_id=user.id, api_key="intervals-not-real")
        )
        await session.commit()

    tallies = await verify_restore.tally()
    assert tallies[SecretPurpose.TOTP].readable >= 1
    assert tallies[SecretPurpose.TOTP].unreadable == 0
    assert tallies[SecretPurpose.INTERVALS].readable >= 1
    assert tallies[SecretPurpose.INTERVALS].unreadable == 0


# ---------------------------------------------------------------------------
# The shell scripts and the Python agree
# ---------------------------------------------------------------------------


def _manifest_fields_written() -> set[str]:
    """Every field the two halves of the backup put in the manifest.

    The Python half from the code; the bash half from the ``fragment.update(...)``
    call in ``backup.sh``, which is where the shell adds what only it knows.
    """
    written = set(backup_manifest.manifest_fragment())
    composer = BACKUP_SH.read_text(encoding="utf-8")
    written |= set(re.findall(r"^\s{4}(\w+)=os\.environ", composer, re.MULTILINE))
    written |= set(re.findall(r"^\s{4}(\w+)=\d+,$", composer, re.MULTILINE))
    return written


def _manifest_fields_read() -> set[str]:
    """Every field ``restore.sh`` reads, in either language it reads it from."""
    script = RESTORE_SH.read_text(encoding="utf-8")
    return set(re.findall(r"""jq -r ['"]\.(\w+)['"]""", script)) | set(
        re.findall(r"""manifest\[["'](\w+)["']\]""", script)
    )


def test_the_restore_script_reads_only_fields_the_backup_writes():
    """A cross-language contract nothing else checks.

    The manifest's field names are decided in Python and consumed in bash and in
    an embedded Python heredoc. A rename on either side type-checks fine, passes
    every other test here, and surfaces when an operator is partway through a
    restore — the one moment there is no appetite for debugging.
    """
    missing = _manifest_fields_read() - _manifest_fields_written()
    assert not missing, f"restore.sh reads manifest fields nothing writes: {sorted(missing)}"


def test_the_contract_check_is_not_comparing_empty_sets():
    """Anti-vacuity: both regexes have to actually match something."""
    read = _manifest_fields_read()
    written = _manifest_fields_written()
    assert {"key_chains", "fingerprint_domain", "fingerprint_length"} <= read
    assert {"dump_sha256", "alembic_revision", "taken_at"} <= read
    assert {"taken_at", "dump_sha256", "secrets_sha256", "manifest_version"} <= written


# ---------------------------------------------------------------------------
# The forced command that limits the fetching host's key
# ---------------------------------------------------------------------------


def _over_ssh(command: str | None, allowed_dir: str = "/var/backups/ai-trainer"):
    env = {**os.environ, "BACKUP_SSH_ALLOWED_DIR": allowed_dir}
    if command is None:
        env.pop("SSH_ORIGINAL_COMMAND", None)
    else:
        env["SSH_ORIGINAL_COMMAND"] = command
    return subprocess.run(
        ["bash", str(SSH_SH)], capture_output=True, text=True, check=False, env=env
    )


def _refused(result) -> bool:
    """Whether the wrapper itself rejected the request.

    Checked by its own prefix rather than by exit status: an allowed request
    ``exec``s rsync, which then fails on its own for want of a real client, so
    "non-zero" does not distinguish permitted from refused.
    """
    return "backup-over-ssh:" in result.stderr


def test_the_fetch_key_gets_no_interactive_session():
    """And is told why, which is the only reason this check exists separately.

    Removing it still refuses — an empty command fails the ``rsync`` check one
    line later — so the guard earns its place by the message, not the verdict.
    A mutation run proved the point: deleting the check broke no test until this
    asserted the wording.
    """
    result = _over_ssh(None)
    assert _refused(result)
    assert "no interactive session" in result.stderr


def test_the_fetch_key_cannot_run_rsync_in_some_other_mode():
    """``--server`` has to be checked in its own right.

    The positional ``--sender`` test does not imply it: with the ``--server``
    check gone, ``rsync <anything> --sender …`` is accepted, and ``--daemon`` is
    among the things ``<anything>`` could be. Also found by mutation.
    """
    assert _refused(_over_ssh("rsync --daemon --sender -e.s . /var/backups/ai-trainer/"))
    assert _refused(_over_ssh("rsync --config=/tmp/x --sender -e.s . /var/backups/ai-trainer/"))


def test_the_fetch_key_cannot_run_an_arbitrary_command():
    assert _refused(_over_ssh("/bin/sh"))
    assert _refused(_over_ssh("cat /root/ai-trainer-backup.pass"))


def test_the_fetch_key_cannot_take_or_delete_a_backup():
    assert _refused(_over_ssh("/opt/ai-trainer/scripts/backup.sh"))
    assert _refused(_over_ssh("rm -rf /var/backups/ai-trainer"))


def test_the_fetch_key_cannot_write_into_the_backup_directory():
    """``--server`` without ``--sender`` is rsync receiving, i.e. uploading.

    Permitting it would let the fetching host overwrite or truncate the very
    artefacts it is there to collect — a way to destroy backups rather than read
    them, which is the thing a pull architecture is supposed to rule out.
    """
    assert _refused(_over_ssh("rsync --server -logDtpre.iLsfxC . /var/backups/ai-trainer/"))


def test_the_fetch_key_cannot_read_anything_but_the_backup_directory():
    """``--sender`` alone would serve any path, and this runs as root."""
    assert _refused(_over_ssh("rsync --server --sender -logDtpre.iLsfxC . /etc/"))
    assert _refused(_over_ssh("rsync --server --sender -logDtpre.iLsfxC . /root/"))


def test_shell_metacharacters_do_not_get_a_second_command_through():
    """The command is expanded by word splitting, not evaluated.

    So a `;` arrives as a literal rsync argument rather than as an operator —
    and the path check then sees the real last argument and refuses.
    """
    assert _refused(
        _over_ssh("rsync --server --sender -e.s . /var/backups/ai-trainer/; cat /etc/shadow")
    )


def test_the_fetch_key_may_collect_the_backup_directory():
    """The permitted case, or the restriction is just a closed door.

    The protocol options in the middle are deliberately not pinned: they encode
    rsync's negotiated features and change with versions on either side, so a
    pinned string breaks on upgrade — and a restriction that breaks gets removed.
    """
    for options in ("-logDtpre.iLsfxC", "-vlogDtpre.iLsfxC", "-e.LsfxCIvu"):
        result = _over_ssh(f"rsync --server --sender {options} . /var/backups/ai-trainer/")
        assert not _refused(result), f"{options} was refused: {result.stderr}"


def test_a_trailing_slash_does_not_change_the_verdict(tmp_path):
    """rsync is asked for the directory with or without one, depending on caller."""
    assert not _refused(_over_ssh("rsync --server --sender -e.s . /var/backups/ai-trainer"))
    assert not _refused(_over_ssh("rsync --server --sender -e.s . /var/backups/ai-trainer/"))


@pytest.mark.parametrize("script", [BACKUP_SH, RESTORE_SH, SSH_SH], ids=lambda p: p.name)
def test_the_shell_scripts_parse(script):
    """``bash -n`` on both.

    Nothing else in CI executes these, and a restore script that dies on a
    syntax error is discovered at the worst moment. This caught a real one while
    the backup script was being written: environment assignments placed after
    the command instead of before it.
    """
    result = subprocess.run(
        ["bash", "-n", str(script)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("script", [BACKUP_SH, RESTORE_SH, SSH_SH], ids=lambda p: p.name)
def test_the_shell_scripts_are_executable(script):
    assert script.stat().st_mode & 0o111, f"{script.name} is not executable"


# ---------------------------------------------------------------------------
# The guard, run as restore.sh runs it
# ---------------------------------------------------------------------------


def _guard_source() -> str:
    """The exact key check out of ``restore.sh``, not a copy of it.

    Extracted and executed rather than reimplemented, because a copy here would
    pass while the shipped one was broken — which is the failure mode this whole
    file is about.
    """
    script = RESTORE_SH.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(script) if line == "import hashlib")
    end = next(i for i, line in enumerate(script) if i > start and line == "PYTHON")
    return "\n".join(script[start:end])


def _run_guard(tmp_path, manifest: dict, env_values: dict[str, str]):
    """Run the guard against a manifest and a .env; return the CompletedProcess."""
    manifest_path = tmp_path / "m.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    env_path = tmp_path / ".env"
    env_path.write_text(
        "".join(f"{name}={value}\n" for name, value in env_values.items()),
        encoding="utf-8",
    )
    guard = tmp_path / "guard.py"
    guard.write_text(_guard_source(), encoding="utf-8")
    return subprocess.run(
        # This interpreter rather than a bare "python3": restore.sh resolves it
        # from PATH on a Debian host, which is not this one.
        [sys.executable, str(guard)],
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "MANIFEST": str(manifest_path),
            "ENV_FILE": str(env_path),
        },
    )


def _full_manifest(keys: dict[str, str]) -> dict:
    fragment = backup_manifest.manifest_fragment()
    fragment["manifest_version"] = 2
    return fragment


def _env_for(keys: dict[str, str]) -> dict[str, str]:
    values = {"SECRETS_ENCRYPTION_KEY": keys["shared"]}
    for purpose, field in _DEDICATED_KEY_FIELDS.items():
        values[field.upper()] = keys[purpose]
    return values


def test_the_guard_accepts_the_keys_that_wrote_the_dump(tmp_path, keys):
    result = _run_guard(tmp_path, _full_manifest(keys), _env_for(keys))
    assert result.returncode == 0, result.stderr
    assert "matches" in result.stdout


def test_the_guard_refuses_a_wrong_dedicated_key_and_names_it(tmp_path, keys):
    env = _env_for(keys)
    env["TOTP_ENCRYPTION_KEY"] = _key()
    result = _run_guard(tmp_path, _full_manifest(keys), env)
    assert result.returncode == 1
    assert "totp" in result.stderr
    assert "TOTP_ENCRYPTION_KEY" in result.stderr


def test_the_guard_refuses_a_wrong_shared_key_even_when_every_dedicated_one_is_right(
    tmp_path, keys
):
    """The reviewer's finding, as a test.

    Before this, the manifest recorded only the encrypting key and the guard
    checked only that, so this exact configuration — correct dedicated keys, a
    rotated ``SECRETS_ENCRYPTION_KEY`` — printed "matches" for all four purposes
    and loaded the dump. Every row written before the ai-trainer-ops#12 split is
    under the shared key, so that is the data most likely to be in a real dump.
    """
    env = _env_for(keys)
    env["SECRETS_ENCRYPTION_KEY"] = _key()
    result = _run_guard(tmp_path, _full_manifest(keys), env)
    assert result.returncode == 1
    assert "SECRETS_ENCRYPTION_KEY" in result.stderr
    assert "opens older rows" in result.stderr


def test_the_guard_refuses_an_absent_key(tmp_path, keys):
    env = _env_for(keys)
    del env["TOTP_ENCRYPTION_KEY"]
    result = _run_guard(tmp_path, _full_manifest(keys), env)
    assert result.returncode == 1
    assert "empty or absent" in result.stderr


def test_the_guard_does_not_cry_wolf_over_a_plaintext_backup(tmp_path, keys, no_keys):
    """A backup taken with no keys restores anywhere, and must not be refused."""
    manifest = _full_manifest(keys)
    result = _run_guard(tmp_path, manifest, _env_for(keys))
    assert result.returncode == 0, result.stderr
    assert "plaintext" in result.stdout


def test_the_guard_refuses_a_manifest_too_old_to_describe_the_whole_chain(
    tmp_path, keys
):
    """Version 1 recorded only the encrypting key, so it cannot answer the
    shared-key question. Refusing beats running a check that silently drops half
    its subject, because the operator believes it ran."""
    manifest = _full_manifest(keys)
    manifest["manifest_version"] = 1
    result = _run_guard(tmp_path, manifest, _env_for(keys))
    assert result.returncode == 1
    assert "version 1" in result.stderr


# ---------------------------------------------------------------------------
# The two script bugs the review found
# ---------------------------------------------------------------------------


def test_placing_the_authelia_directory_replaces_it_rather_than_nesting_it(tmp_path):
    """``cp -a src dst`` copies *into* dst when dst already exists.

    On any ``RESTORE_FORCE=1`` restore that produced
    ``authelia/authelia/users_database.yml`` and left the **stale** users file
    exactly where login reads it — a restore reporting success while
    authenticating against the old passwords. Reproduced before the fix.

    Runs the placement lines out of ``restore.sh`` rather than asserting on its
    text, so a future rewrite that reintroduces the nesting fails here even if
    it uses different words.
    """
    script = RESTORE_SH.read_text(encoding="utf-8")
    placement = [
        line.strip()
        for line in script.splitlines()
        if "authelia.restoring" in line or 'rm -rf "${APP_DIR}/authelia"' in line
    ]
    assert placement, "no authelia placement found in restore.sh"

    staging = tmp_path / "staging" / "secrets"
    (staging / "authelia").mkdir(parents=True)
    (staging / "authelia" / "users_database.yml").write_text("restored\n")
    app_dir = tmp_path / "app"
    (app_dir / "authelia").mkdir(parents=True)
    (app_dir / "authelia" / "users_database.yml").write_text("stale\n")

    subprocess.run(
        ["bash", "-euo", "pipefail", "-c", "\n".join(placement)],
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "APP_DIR": str(app_dir),
            "staging": str(tmp_path / "staging"),
        },
    )

    assert not (app_dir / "authelia" / "authelia").exists(), "the directory was nested"
    assert (app_dir / "authelia" / "users_database.yml").read_text() == "restored\n"


@pytest.mark.skipif(
    shutil.which("gpg") is None, reason="backup.sh encrypts the secrets bundle with gpg"
)
def test_a_failed_backup_leaves_no_artefacts_behind(tmp_path):
    """Run the real script against a Postgres that is not there.

    Behavioural rather than a grep, because the thing worth knowing is what is
    on disk afterwards. Pruning keys off ``*.manifest.json`` and the manifest is
    written last, so before the fix an aborted run left a ``.dump`` and a
    ``.secrets.tar.gz.gpg`` that nothing would ever delete — a nightly job
    against a stopped Postgres would grow the directory without bound and
    accumulate copies of every key this deployment has.
    """
    app_dir = tmp_path / "app"
    backup_dir = tmp_path / "backups"
    app_dir.mkdir()
    (app_dir / ".env").write_text(
        "POSTGRES_USER=aitrainer\nPOSTGRES_DB=aitrainer\nSECRETS_ENCRYPTION_KEY=x\n",
        encoding="utf-8",
    )
    passphrase = tmp_path / "pass"
    passphrase.write_text("not-a-real-passphrase\n", encoding="utf-8")

    result = subprocess.run(
        ["bash", str(BACKUP_SH)],
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "APP_DIR": str(app_dir),
            "BACKUP_DIR": str(backup_dir),
            "BACKUP_PASSPHRASE_FILE": str(passphrase),
            # Every compose call fails, so the run dies at pg_dump — after the
            # secrets bundle has already been written.
            "COMPOSE_CMD": "false",
        },
    )

    assert result.returncode != 0, "the backup should have failed"
    leftovers = sorted(p.name for p in backup_dir.iterdir())
    assert leftovers == [], f"a failed run left {leftovers} behind"
