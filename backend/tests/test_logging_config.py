from __future__ import annotations

import logging
import logging.config
from pathlib import Path

import yaml


BACKEND_DIR = Path(__file__).parent.parent


def test_uvicorn_access_log_config_includes_timestamp():
    config = yaml.safe_load((BACKEND_DIR / "logging.yaml").read_text())
    logging.config.dictConfig(config)

    logger = logging.getLogger("uvicorn.access")
    formatter = logger.handlers[0].formatter
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:40078", "GET", "/healthz", "1.1", 200),
        None,
    )

    formatted = formatter.format(record)

    assert '127.0.0.1:40078 - "GET /healthz HTTP/1.1" 200 OK' in formatted
    assert formatted[:10].count("-") == 2
    assert formatted[10] == " "


async def test_request_validation_errors_are_logged(client, auth_headers, caplog):
    with caplog.at_level(logging.WARNING, logger="main"):
        response = await client.post(
            "/api/v1/ai/analyse-activities",
            headers=auth_headers,
            json={"activities": [{"id": 1}]},
        )

    assert response.status_code == 422
    assert (
        "Request validation failed on POST /api/v1/ai/analyse-activities" in caplog.text
    )
    assert "totalElevationGain" in caplog.text
