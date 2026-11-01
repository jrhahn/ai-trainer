"""The logs stay bounded, and request URLs with coordinates stay out of them
(ai-trainer-ops#338).

Docker keeps each container's output in a json file that grows without bound
unless compose or the host caps it, and the host does not. And httpx logs every
request URL at INFO: the weather lookups carry the athlete's coordinates there,
a ride's start and often the home address.
"""

from __future__ import annotations

import logging
import logging.config
from pathlib import Path

import yaml

BACKEND_DIR = Path(__file__).resolve().parent.parent
COMPOSE = BACKEND_DIR.parent / "compose.yml"


def test_every_container_log_is_capped():
    services = yaml.safe_load(COMPOSE.read_text())["services"]

    assert services
    for name, service in services.items():
        logging_config = service.get("logging") or {}
        options = logging_config.get("options") or {}
        assert logging_config.get("driver") == "json-file", name
        assert options.get("max-size"), name
        assert options.get("max-file"), name


def test_request_urls_are_not_logged():
    config = yaml.safe_load((BACKEND_DIR / "logging.yaml").read_text())
    logging.config.dictConfig(config)

    httpx_logger = logging.getLogger("httpx")
    assert not httpx_logger.isEnabledFor(logging.INFO)
    assert httpx_logger.isEnabledFor(logging.WARNING)
