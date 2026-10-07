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
import re
import subprocess
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
    assert set(fragment["key_fingerprints"]) == set(SecretPurpose.ALL)
    assert set(fragment["key_vars"]) == set(SecretPurpose.ALL)


def test_there_is_more_than_one_purpose_to_cover():
    """Anti-vacuity for the sweeps above and below.

    Every check in this file that iterates ``SecretPurpose.ALL`` passes
    trivially if it is empty, and an empty inventory is exactly what a backup
    guard must not be allowed to become.
    """
    assert len(SecretPurpose.ALL) >= 4


def test_every_purpose_names_a_variable_an_operator_can_go_and_look_at(keys):
    """The manifest's ``key_vars`` has to be a real environment variable.

    ``restore.sh`` prints it when a purpose fails, and it is the only thing
    telling the operator which value to fix. A name that is not actually the
    variable is worse than no name.
    """
    expected = {field.upper() for field in _DEDICATED_KEY_FIELDS.values()}
    for purpose, variable in backup_manifest.key_vars().items():
        assert variable in expected, f"{purpose} names {variable}, which is not a key variable"


def test_a_purpose_without_a_dedicated_key_names_the_shared_one(keys, monkeypatch):
    monkeypatch.setattr(settings, _DEDICATED_KEY_FIELDS[SecretPurpose.TOTP], "")
    assert backup_manifest.key_vars()[SecretPurpose.TOTP] == "SECRETS_ENCRYPTION_KEY"
    assert backup_manifest.key_fingerprints()[SecretPurpose.TOTP] == (
        backup_manifest.fingerprint(keys["shared"])
    )


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

    variables = backup_manifest.key_vars()
    assert set(variables.values()) == {"STRAVA_ENCRYPTION_KEY"}
    assert backup_manifest.key_fingerprints()[SecretPurpose.STRAVA] == (
        backup_manifest.fingerprint(shared)
    )


def test_a_plaintext_deployment_says_so_rather_than_fingerprinting_nothing(no_keys):
    """No key is a legitimate state to back up, and a distinguishable one.

    It has to be told apart from "this manifest predates that purpose": the
    first means the columns hold plaintext and restore anywhere, the second
    means the manifest is too old to be believed about anything.
    """
    fragment = backup_manifest.manifest_fragment()
    assert set(fragment["key_fingerprints"].values()) == {backup_manifest.PLAINTEXT}
    assert set(fragment["key_vars"].values()) == {backup_manifest.PLAINTEXT}


def test_a_purpose_with_no_key_field_is_a_loud_error(monkeypatch):
    """A fifth purpose added to ``ALL`` and not to ``_DEDICATED_KEY_FIELDS``.

    It must fail rather than silently drop out of the manifest — a purpose
    missing from the manifest is a purpose whose key a restore never checks.
    """
    monkeypatch.setattr(SecretPurpose, "ALL", (*SecretPurpose.ALL, "sleep_tokens"))
    with pytest.raises(ValueError, match="sleep_tokens"):
        backup_manifest.key_fingerprints()


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
    fingerprints = backup_manifest.key_fingerprints()
    assert len(set(fingerprints.values())) == len(SecretPurpose.ALL)


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
    assert {"key_fingerprints", "key_vars", "fingerprint_domain"} <= read
    assert {"dump_sha256", "alembic_revision", "taken_at"} <= read
    assert {"taken_at", "dump_sha256", "secrets_sha256", "manifest_version"} <= written


@pytest.mark.parametrize("script", [BACKUP_SH, RESTORE_SH], ids=lambda p: p.name)
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


@pytest.mark.parametrize("script", [BACKUP_SH, RESTORE_SH], ids=lambda p: p.name)
def test_the_shell_scripts_are_executable(script):
    assert script.stat().st_mode & 0o111, f"{script.name} is not executable"
