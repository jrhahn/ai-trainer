"""Traefik answers on the hosts it names, and www. redirects (ai-trainer-ops#99).

The frontend and backend routers used to match any subdomain with HostRegexp.
The DNS wildcard sends every name to the server, so api., app., rustore. and
any made-up name served the app. Let's Encrypt cannot issue for a pattern, so
those names showed Traefik's default certificate, and the bare domain's HSTS
includeSubDomains made that an error a browser does not let anyone click
through. Each router now names its hosts, and www. redirects to the bare domain
with a certificate of its own.
"""

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
SERVICES = yaml.safe_load((REPO / "compose.yml").read_text())["services"]
LABELS = {
    key: value
    for service in SERVICES.values()
    for key, _, value in (label.partition("=") for label in service.get("labels", []))
}
RULES = {
    key.split(".")[3]: value
    for key, value in LABELS.items()
    if re.fullmatch(r"traefik\.http\.routers\.[\w-]+\.rule", key)
}
WWW_REDIRECT = "traefik.http.middlewares.www-to-bare-domain.redirectregex"


def test_no_router_matches_a_pattern_of_hosts():
    assert [name for name, rule in RULES.items() if "HostRegexp" in rule] == []


def test_www_redirects_to_the_bare_domain_keeping_the_path():
    # Compose turns $$ into $, and Traefik's ${1} is Python's \1.
    replacement = LABELS[f"{WWW_REDIRECT}.replacement"].replace("$${1}", r"\1")
    for url, expected in [
        ("https://www.trainlikea.pro/plan?day=1", "https://trainlikea.pro/plan?day=1"),
        ("http://www.train-like-a.pro/", "https://train-like-a.pro/"),
    ]:
        assert re.sub(LABELS[f"{WWW_REDIRECT}.regex"], replacement, url) == expected
    assert LABELS[f"{WWW_REDIRECT}.permanent"] == "true"


def test_www_has_a_certificate_of_its_own():
    """A failed www. renewal must not take the bare domain's certificate with it."""
    for router in ("www-http", "www-https"):
        for host in ("www.trainlikea.pro", "www.train-like-a.pro"):
            assert f"Host(`{host}`)" in RULES[router], (router, host)
    assert LABELS["traefik.http.routers.www-https.tls.certresolver"] == "letsencrypt"
    assert "www" not in RULES["frontend-https"]


def test_the_www_redirect_carries_the_security_headers():
    """Headers first: the redirect answers without passing the request on."""
    assert LABELS["traefik.http.routers.www-https.middlewares"].split(",") == [
        "security-headers@docker",
        "www-to-bare-domain@docker",
    ]
