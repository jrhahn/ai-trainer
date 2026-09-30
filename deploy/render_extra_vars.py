"""Turn the GitHub `secrets` and `vars` contexts into Ansible extra-vars.

Run by the deploy workflow in the private ops repository, before Ansible
starts:

    python deploy/render_extra_vars.py --out /tmp/deploy-vars.json

It reads two JSON blobs from the environment -- ``DEPLOY_SECRETS`` and
``DEPLOY_VARS``, which the workflow fills with ``toJSON(secrets)`` and
``toJSON(vars)`` -- applies ``deploy/forwarded-vars.yml``, and writes the
extra-vars file.

Why it exists rather than a list in the workflow
------------------------------------------------
Four times a value was configured in GitHub and never reached production,
because the workflow did not forward it and nothing reported that it had no
effect (#617, #684, #694, #700). The per-name ``env:`` block was the thing
being forgotten, so it is gone: the workflow hands over whole contexts and
names nothing, and the mapping lives in a manifest that
``backend/tests/test_deploy_wiring.py`` checks against the template.

That matters more since #699 moved the deploy into a private repository. The
workflow is no longer visible to this repository's tests -- but this file and
the manifest are.

Nothing here prints a value. Failures name the *variable*, which is the part
an operator needs and the part that is safe to log.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

MANIFEST = Path(__file__).resolve().parent / "forwarded-vars.yml"

REQUIRED = "required"
OPTIONAL = "optional"
OMIT_IF_EMPTY = "omit_if_empty"
FALLBACK = "fallback"
MODES = {REQUIRED, OPTIONAL, OMIT_IF_EMPTY, FALLBACK}

SOURCES = {"secret": "DEPLOY_SECRETS", "variable": "DEPLOY_VARS"}


class ManifestError(RuntimeError):
    """The manifest itself is wrong -- a bug, not a misconfigured deployment."""


def load_manifest(path: Path = MANIFEST) -> dict[str, Any]:
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))

    for entry in manifest.get("forwarded", []):
        for field in ("var", "from", "name", "mode", "why"):
            if not entry.get(field):
                raise ManifestError(f"forwarded entry {entry!r} is missing '{field}'")
        if entry["mode"] not in MODES:
            raise ManifestError(
                f"{entry['var']}: mode {entry['mode']!r} is not one of {sorted(MODES)}"
            )
        if entry["from"] not in SOURCES:
            raise ManifestError(
                f"{entry['var']}: from {entry['from']!r} is not one of {sorted(SOURCES)}"
            )
        # A fallback of "" would be indistinguishable from `optional` while
        # reading as though a real default had been chosen.
        if entry["mode"] == FALLBACK and not entry.get("fallback"):
            raise ManifestError(
                f"{entry['var']}: mode is 'fallback' but no non-empty 'fallback' is set"
            )
        if entry["mode"] != FALLBACK and "fallback" in entry:
            raise ManifestError(
                f"{entry['var']}: has a 'fallback' but mode is {entry['mode']!r}"
            )

    for entry in manifest.get("workflow_only", []):
        for field in ("name", "from", "mode", "why"):
            if not entry.get(field):
                raise ManifestError(f"workflow_only entry {entry!r} is missing '{field}'")

    return manifest


def _context(name: str, raw: str | None) -> dict[str, str]:
    """Parse one GitHub context blob.

    Absent is a hard error rather than an empty dict. A missing context means
    the workflow step is wired wrong, and treating it as "no secrets are set"
    would produce a deploy that renders every optional value empty -- which is
    the #684 failure, arrived at from a different direction.
    """
    if raw is None:
        raise SystemExit(
            f"{name} is not set. The deploy workflow must pass it as "
            f"toJSON of the matching GitHub context."
        )
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{name} is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit(f"{name} must be a JSON object, got {type(parsed).__name__}")
    # GitHub renders every context value as a string; be explicit so a
    # surprising type fails here rather than inside Ansible.
    return {key: "" if value is None else str(value) for key, value in parsed.items()}


def build_extra_vars(
    manifest: dict[str, Any], secrets: dict[str, str], variables: dict[str, str]
) -> tuple[dict[str, str], list[str]]:
    """Map the contexts onto extra-vars. Returns (extra_vars, missing)."""
    contexts = {"secret": secrets, "variable": variables}
    extra_vars: dict[str, str] = {}
    missing: list[str] = []

    for entry in manifest.get("forwarded", []):
        value = contexts[entry["from"]].get(entry["name"], "").strip()
        mode = entry["mode"]

        if value:
            extra_vars[entry["var"]] = value
        elif mode == REQUIRED:
            missing.append(
                f"  {entry['name']} ({entry['from']}) -> {entry['var']}: {entry['why'].strip()}"
            )
        elif mode == OPTIONAL:
            extra_vars[entry["var"]] = ""
        elif mode == FALLBACK:
            extra_vars[entry["var"]] = entry["fallback"]
        # OMIT_IF_EMPTY: deliberately absent, so app.env.j2's default applies.

    for entry in manifest.get("workflow_only", []):
        if not contexts[entry["from"]].get(entry["name"], "").strip():
            if entry["mode"] == REQUIRED:
                missing.append(
                    f"  {entry['name']} ({entry['from']}), used by the workflow: "
                    f"{entry['why'].strip()}"
                )

    return extra_vars, missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", required=True, type=Path, help="where to write the extra-vars JSON"
    )
    args = parser.parse_args(argv)

    manifest = load_manifest()
    secrets = _context("DEPLOY_SECRETS", os.environ.get("DEPLOY_SECRETS"))
    variables = _context("DEPLOY_VARS", os.environ.get("DEPLOY_VARS"))

    extra_vars, missing = build_extra_vars(manifest, secrets, variables)

    if missing:
        print(
            "These deployment inputs are required and are empty or unset:\n"
            + "\n".join(missing)
            + "\n\nSet each one on the `production` environment of the deploying "
            "repository -- as a secret or a repository variable, per the source "
            "shown above. Nothing has been deployed; the running containers are "
            "untouched.",
            file=sys.stderr,
        )
        return 1

    args.out.write_text(json.dumps(extra_vars, indent=2, sort_keys=True), encoding="utf-8")

    # Names only. The point of the summary is that an operator can see which
    # knobs took effect on this deploy, which is exactly what was missing in
    # #694 -- and no value belongs in a build log.
    omitted = sorted(
        entry["var"]
        for entry in manifest.get("forwarded", [])
        if entry["var"] not in extra_vars
    )
    print(f"Wrote {len(extra_vars)} extra-vars to {args.out}")
    print("Forwarded: " + ", ".join(sorted(extra_vars)))
    if omitted:
        print("Left to the template default: " + ", ".join(omitted))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via the workflow
    raise SystemExit(main())
