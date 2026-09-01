"""
Background harvests, so the headline tool stops timing out.

`learn_technology` used to block for as long as the harvest took — around twelve
minutes for a 703-page site — while every MCP client gives up long before that.
The tool was not merely slow, it read as broken, which is worse.

What changes here is not the harvest but who waits for it. A call returns
whatever is ready by a deadline (`DOCSFORGE_HARVEST_DEADLINE`, 25 seconds).
A harvest that finishes inside the deadline returns exactly what it always
returned, word for word — that is the common case and it is deliberately
untouched. One that does not keeps running on its own thread, and the caller
gets a harvest id and a progress line instead of a stalled connection.

This is not a job queue and should not become one. Jobs live in this process,
die with it, and are never persisted: the durable record of a harvest is the
knowledge-base entry it writes, and a job that vanished before writing one
simply did not happen. Anything more wants a real scheduler, and a
documentation tool does not need a real scheduler.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

# Long enough to swallow the great majority of harvests whole, short enough to
# sit inside the shortest MCP client timeout. Both bounds matter, so this is
# tunable but not per-call: a caller who could pick it would pick "forever".
DEADLINE = float(os.environ.get("DOCSFORGE_HARVEST_DEADLINE", "25"))

# Finished jobs are kept so the next list_knowledge_base can say how a harvest
# ended. Small enough that nobody mistakes it for storage.
KEEP_FINISHED = 10


@dataclass
class Progress:
    """What a running harvest is willing to say about itself.

    Written by the worker thread, read by everyone else. Every field is a
    plain assignment of an immutable value, which is the only reason this
    needs no lock of its own.
    """

    phase: str = "starting"          # resolving | harvesting | storing
    url: str = ""
    pages: int = 0                   # pages fetched so far
    expected: int | None = None      # the site's own count, when known

    def line(self) -> str:
        if self.phase == "harvesting" and self.expected:
            return f"harvesting {self.pages}/{self.expected} pages"
        if self.phase == "harvesting":
            return f"harvesting, {self.pages} pages so far"
        return self.phase


@dataclass
class Job:
    """One harvest, running or finished."""

    id: str
    label: str                       # what is being learned, for a human
    started: float
    progress: Progress = field(default_factory=Progress)
    state: str = "running"           # running | done | failed
    result: str = ""
    error: str = ""
    # The exception object itself, not a rendering of it. A caller still
    # inside the deadline re-raises this one, so a failure that would have
    # surfaced as a ForgeError with a candidate list still does.
    exc: BaseException | None = None
    finished: float = 0.0
    done: threading.Event = field(default_factory=threading.Event)

    @property
    def elapsed(self) -> float:
        return (self.finished or time.time()) - self.started

    def line(self) -> str:
        """One line describing this job, for list_knowledge_base."""
        mins = f"{self.elapsed:.0f}s" if self.elapsed < 90 else f"{self.elapsed / 60:.0f}m"
        if self.state == "running":
            return f"- **{self.label}** — {self.progress.line()}, {mins} elapsed  ·  `{self.id}`"
        if self.state == "failed":
            return f"- **{self.label}** — FAILED after {mins}: {self.error}  ·  `{self.id}`"
        return f"- **{self.label}** — finished in {mins}  ·  `{self.id}`"


_LOCK = threading.Lock()
_JOBS: dict[str, Job] = {}
_COUNTER = 0


def _new_id(label: str) -> str:
    """A short id a model can quote back, unique within this process."""
    global _COUNTER
    _COUNTER += 1
    safe = "".join(c for c in label.lower() if c.isalnum() or c == "-")[:24] or "harvest"
    return f"{safe}-{_COUNTER}"


def _prune() -> None:
    """Drop the oldest finished jobs. Caller holds the lock."""
    finished = sorted(
        (j for j in _JOBS.values() if j.state != "running"),
        key=lambda j: j.finished,
    )
    for job in finished[:-KEEP_FINISHED] if len(finished) > KEEP_FINISHED else []:
        _JOBS.pop(job.id, None)


def start(label: str, work: Callable[[Progress], str]) -> Job:
    """Run `work` on its own thread. Returns immediately.

    `work` is handed the job's Progress to update as it goes, and whatever
    string it returns becomes the job's result — the same string the caller
    would have received had it waited.
    """
    with _LOCK:
        job = Job(id=_new_id(label), label=label, started=time.time())
        _JOBS[job.id] = job
        _prune()

    def run() -> None:
        try:
            job.result = work(job.progress)
            job.state = "done"
        except BaseException as e:                      # noqa: BLE001
            # Including SystemExit and KeyboardInterrupt: a worker thread that
            # died silently would leave a job "running" forever, and a harvest
            # that reports nothing is the failure mode this module exists to
            # prevent.
            job.exc = e
            job.error = str(e) or type(e).__name__
            job.state = "failed"
        finally:
            job.finished = time.time()
            job.done.set()

    # Daemon: a harvest must never keep the process alive at shutdown. The
    # cost is that shutdown loses in-flight harvests, which is why the caller
    # is told the harvest runs "while this server is running" rather than
    # promised a result.
    threading.Thread(target=run, name=f"harvest:{job.id}", daemon=True).start()
    return job


def wait(job: Job, seconds: float | None = None) -> bool:
    """Block up to `seconds`. True if the job finished within them."""
    return job.done.wait(DEADLINE if seconds is None else seconds)


def get(job_id: str) -> Job | None:
    with _LOCK:
        return _JOBS.get(job_id)


def running() -> list[Job]:
    with _LOCK:
        return sorted((j for j in _JOBS.values() if j.state == "running"),
                      key=lambda j: j.started)


def recent() -> list[Job]:
    """Finished jobs, newest first."""
    with _LOCK:
        return sorted((j for j in _JOBS.values() if j.state != "running"),
                      key=lambda j: j.finished, reverse=True)


def clear() -> None:
    """Forget every job. For tests; does not stop running threads."""
    global _COUNTER
    with _LOCK:
        _JOBS.clear()
        _COUNTER = 0
