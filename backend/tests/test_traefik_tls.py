"""The edge TLS policy stays wired and stays AEAD-only.

compose.yml hands Traefik a file provider so the `default` TLS options set in
traefik/dynamic/tls-options.yml applies to every websecure router. Two ways
that silently regresses: the provider line or the mount gets dropped in a
compose edit (the file is then present but never read), or a CBC suite creeps
back into the options (the LUCKY13-adjacent finding this closes returns). Both
are cheap to assert and neither shows up in a running health check.
"""

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
COMPOSE = REPO / "compose.yml"
TLS_OPTIONS = REPO / "traefik" / "dynamic" / "tls-options.yml"

FILE_PROVIDER_ARG = "--providers.file.directory=/etc/traefik/dynamic"
MOUNT = "./traefik/dynamic:/etc/traefik/dynamic:ro"


def test_compose_wires_the_file_provider_and_mount():
    compose = COMPOSE.read_text()
    assert FILE_PROVIDER_ARG in compose, "Traefik file provider arg missing"
    assert MOUNT in compose, "dynamic config mount missing — options never load"


def test_default_tls_options_are_aead_only_tls12_min():
    options = yaml.safe_load(TLS_OPTIONS.read_text())["tls"]["options"]["default"]
    assert options["minVersion"] == "VersionTLS12"
    suites = options["cipherSuites"]
    assert suites, "no cipher suites pinned"
    # CBC suites (…_CBC_SHA) are the LUCKY13-adjacent ones; AEAD is GCM/ChaCha.
    assert all("CBC" not in s for s in suites), f"CBC suite present: {suites}"
    assert all(("GCM" in s) or ("CHACHA20_POLY1305" in s) for s in suites)
