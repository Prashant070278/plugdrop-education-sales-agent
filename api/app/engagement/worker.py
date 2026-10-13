"""Background loop for Stage 4: sends queued WhatsApp messages (with retries and rate limits) and runs the
time-based follow-up rules. Runs inside the API by default (ENGAGEMENT_WORKER=embedded), or separately with
``python -m app.engagement.worker``. Safe to run more than once at a time (row locks + idempotent rules)."""

from __future__ import annotations

import threading
import time
from typing import Any

from app.engagement.logs import event

_started = False


def run_once(session_factory: Any) -> None:
    from app.engagement.followups import sweep
    from app.whatsapp.service import dispatch_due

    with session_factory() as db:
        dispatch_due(db)
    with session_factory() as db:
        sweep(db)


def start_embedded_worker(session_factory: Any, interval: float = 30.0) -> None:
    global _started
    if _started:
        return
    _started = True

    def loop() -> None:
        while True:
            try:
                run_once(session_factory)
            except Exception as e:  # noqa: BLE001
                event("engagement_worker_error", error=str(e))
            time.sleep(interval)

    threading.Thread(target=loop, name="engagement-worker", daemon=True).start()


if __name__ == "__main__":  # pragma: no cover
    from app.db import SessionLocal

    while True:
        try:
            run_once(SessionLocal)
        except Exception as e:  # noqa: BLE001
            event("engagement_worker_error", error=str(e))
        time.sleep(30)
