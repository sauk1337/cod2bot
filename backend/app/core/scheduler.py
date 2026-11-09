"""Background job scheduler.

Each job runs in its own thread, never overlaps itself, and backs off
exponentially (max 1h) after failures, so a broken website or a bug in one
job never stops the others or the API.
"""
from __future__ import annotations

import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable

from . import log

L = log.get("scheduler")


@dataclass
class Job:
    name: str
    func: Callable
    interval: float
    next_run: float = 0.0
    running: bool = False
    failures: int = 0
    last_ok: float = 0.0
    last_error: str = ""
    runs: int = 0
    enabled: Callable[[], bool] = field(default=lambda: True)


class Scheduler:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def add(self, name: str, func: Callable, interval: float, delay: float = 5,
            enabled: Callable[[], bool] | None = None) -> None:
        self.jobs[name] = Job(name, func, interval, time.time() + delay, enabled=enabled or (lambda: True))

    def run_now(self, name: str) -> bool:
        job = self.jobs.get(name)
        if not job:
            return False
        job.next_run = 0
        return True

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        L.info("scheduler started with %d jobs", len(self.jobs))
        while not self._stop.is_set():
            now = time.time()
            for job in list(self.jobs.values()):
                if job.running or now < job.next_run:
                    continue
                try:
                    if not job.enabled():
                        job.next_run = now + 60
                        continue
                except Exception:
                    job.next_run = now + 60
                    continue
                with self._lock:
                    job.running = True
                threading.Thread(target=self._run, args=(job,), name=f"job:{job.name}", daemon=True).start()
            self._stop.wait(1.0)

    def _run(self, job: Job) -> None:
        start = time.time()
        try:
            job.func()
            job.failures = 0
            job.last_ok = time.time()
            job.last_error = ""
            job.next_run = time.time() + job.interval
        except Exception as e:  # never let a job kill the scheduler
            job.failures += 1
            job.last_error = f"{type(e).__name__}: {e}"
            backoff = min(3600, job.interval * (2 ** min(job.failures, 6)) / 4 + 30)
            job.next_run = time.time() + backoff
            L.error("job %s failed (%d): %s - retry in %ds\n%s", job.name, job.failures, e, backoff,
                    traceback.format_exc())
        finally:
            job.runs += 1
            job.running = False
            L.debug("job %s finished in %.2fs", job.name, time.time() - start)
