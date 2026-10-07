"""The headers the shipped frontend sends, pinned (ai-trainer-ops#32).

``frontend/nginx.conf`` is the only place these exist, and nothing in the app
reads them, so an edit that drops one breaks no test and no page — it just
quietly removes a layer. The Security workflow's ZAP baseline would notice, but
it reports rather than gates. This is the gate for the headers already earned.

Read as text, like ``test_remote_user_boundary``: there is no nginx in the test
environment, and the file is the deployment artefact itself.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
NGINX_CONF = REPO_ROOT / "frontend" / "nginx.conf"
FRONTEND_DOCKERFILE = REPO_ROOT / "frontend" / "Dockerfile"


def _headers() -> dict[str, str]:
    text = NGINX_CONF.read_text()
    return {
        name.lower(): value
        for name, value in re.findall(r'add_header\s+(\S+)\s+"([^"]*)"\s+always;', text)
    }


@pytest.mark.parametrize(
    "header, value",
    [
        ("cross-origin-opener-policy", "same-origin"),
        ("cross-origin-resource-policy", "same-origin"),
        ("x-content-type-options", "nosniff"),
        ("referrer-policy", "no-referrer"),
    ],
)
def test_the_frontend_sends_the_header(header, value):
    assert _headers().get(header) == value


def test_the_permissions_policy_switches_off_what_the_app_never_uses():
    policy = _headers()["permissions-policy"]
    for feature in ("camera", "microphone", "geolocation", "payment"):
        assert f"{feature}=()" in policy


def test_the_permissions_policy_leaves_the_clipboard_alone():
    """Recovery codes are copied to the clipboard (TotpSettings)."""
    assert "clipboard" not in _headers()["permissions-policy"]


def test_scripts_stay_same_origin_only():
    """The CSP layer behind the coach-reply egress rules (#677)."""
    csp = _headers()["content-security-policy"]
    assert "script-src 'self';" in csp
    assert "unsafe-inline" not in csp.split("script-src", 1)[1].split(";", 1)[0]


def test_nginx_does_not_announce_its_version():
    assert re.search(r"^\s*server_tokens\s+off;", NGINX_CONF.read_text(), re.M)


def test_there_is_deliberately_no_embedder_policy():
    """Adding COEP is a decision, not a cleanup: see the comment in nginx.conf."""
    assert "cross-origin-embedder-policy" not in _headers()


def test_the_image_is_not_on_an_unmaintained_nginx_branch():
    """1.29 stopped receiving fixes; 126 trivy findings were the result."""
    base = re.search(r"^FROM nginx:(\S+)", FRONTEND_DOCKERFILE.read_text(), re.M)
    assert base is not None
    assert not base.group(1).startswith("1.29")


def test_no_location_sets_headers_of_its_own():
    """nginx's inheritance trap: one ``add_header`` in a location drops them all.

    ``add_header`` is inherited from the server block only by a location that
    has none of its own. A single cache header added to ``location /`` would
    silently strip every header above from every page — and every test above
    would still pass, because they read the server block.
    """
    text = NGINX_CONF.read_text()
    locations = re.findall(r"location\s+[^{]+\{(.*?)\n    \}", text, re.S)
    assert locations, "anti-vacuity: the config has location blocks"
    assert all("add_header" not in body for body in locations)
