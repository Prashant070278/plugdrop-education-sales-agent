"""Structured (JSON-line) logs for engagement events: sends, retries, webhooks, coordination decisions.

Each line is one JSON object with an event name and fields, so it can be searched or shipped to a log service.
Message bodies and access tokens are never logged; phone numbers are masked.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("plugdrop.engagement")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)
    logger.propagate = False

_PHONE = re.compile(r"\+?\d{6,15}")
_SECRET_KEYS = {"body", "token", "access_token", "authorization", "secret", "password", "text"}


def mask(value: str) -> str:
    return _PHONE.sub(lambda m: m.group(0)[:3] + "•••" + m.group(0)[-2:], value)


def event(name: str, level: int = logging.INFO, **fields: Any) -> None:
    safe = {k: ("[redacted]" if k in _SECRET_KEYS else (mask(v) if isinstance(v, str) else v)) for k, v in fields.items()}
    logger.log(level, json.dumps({"ts": datetime.now(UTC).isoformat(), "event": name, **safe}, default=str))
