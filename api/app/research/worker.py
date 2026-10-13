"""Standalone research worker: ``python -m app.research.worker``.

Use this (with RESEARCH_WORKER=external on the API) when you want research to run in its own
process or container. Several workers can run at once; runs are claimed with SKIP LOCKED.
"""

import logging
import signal
import time

from app.db import SessionLocal
from app.research.service import process_next

_running = True


def _stop(*_: object) -> None:
    global _running
    _running = False


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log = logging.getLogger("plugdrop.research")
    log.info("research worker started")
    while _running:
        try:
            with SessionLocal() as db:
                run = process_next(db)
            time.sleep(0.2 if run else 2.0)
        except Exception:  # noqa: BLE001
            log.exception("worker loop error")
            time.sleep(5)
    log.info("research worker stopped")


if __name__ == "__main__":
    main()
