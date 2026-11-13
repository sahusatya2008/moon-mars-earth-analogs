"""Worker process: ``python -m terrax.worker``."""

from __future__ import annotations

import logging
import signal
import time

from .config import get_settings
from .db import SessionLocal
from .jobs import handlers, queue

settings = get_settings()
log = logging.getLogger("terrax.worker")

_stop = False


def _handle_signal(signum, _frame):
    global _stop
    log.info("received signal %s; finishing current job then stopping", signum)
    _stop = True


def run() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.terrax_log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    wid = queue.worker_id()
    with SessionLocal() as db:
        recovered = queue.recover_stale(db)
        if recovered:
            log.warning("recovered %s stale job(s)", recovered)
    log.info("worker %s started; polling every %.1fs", wid, settings.terrax_worker_poll_seconds)
    idle_sleep = settings.terrax_worker_poll_seconds
    while not _stop:
        claimed = None
        with SessionLocal() as db:
            try:
                claimed = queue.claim(db, wid)
            except Exception:
                log.exception("failed to claim a job")
                db.rollback()
        if claimed is None:
            time.sleep(idle_sleep)
            continue
        log.info("executing job %s type=%s attempt=%s", claimed.id, claimed.job_type, claimed.attempts)
        with SessionLocal() as db:
            job = db.get(type(claimed), claimed.id)
            if job is not None:
                handlers.execute(db, job)
        log.info("finished job %s", claimed.id)
    log.info("worker %s stopped", wid)


def main() -> None:
    run()


if __name__ == "__main__":
    main()
