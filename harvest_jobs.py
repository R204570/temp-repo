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

**This is still not a job queue and should not become one.** A harvest runs on
a daemon thread, dies with the process that started it, and is never resumed:
the durable record of a harvest is the knowledge-base entry it writes, and a
harvest that vanished before writing one simply did not happen.

What *is* published, since it turned out to be the difference between a working
feature and an invisible one, is a **status record**. Jobs used to live only in
this module's dict, and that is fine for one process and useless the moment
there are two. The `claudecode` provider launches the CLI, which launches
`mcp_server.py` itself, so every turn runs its tools in a fresh subprocess:
`learn_technology` started a harvest in one process and the `list_knowledge_base`
that was supposed to report it ran in another, which had never heard of it. The
tool told the user to watch a progress line that could not exist, and the same
harvest id came back twice because the counter had reset.

So each job writes a small JSON record under `~/.docsforge/harvests/`, refreshed
on a heartbeat while it runs, and every process reads all of them. The record is
status, not state: nothing is ever resumed from it, and when its heartbeat stops
it is reported **stalled**, never "running". A record that outlives its process
is a record whose process died, and saying so is the whole point.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# Long enough to swallow the great majority of harvests whole, short enough to
# sit inside the shortest MCP client timeout. Both bounds matter, so this is
# tunable but not per-call: a caller who could pick it would pick "forever".
DEADLINE = float(os.environ.get("DOCSFORGE_HARVEST_DEADLINE", "25"))

# Finished jobs are kept so the next list_knowledge_base can say how a harvest
# ended. Small enough that nobody mistakes it for storage.
KEEP_FINISHED = 10

#: How often a running job rewrites its record. A harvest can spend a minute
#: resolving without fetching anything, so the heartbeat cannot be driven by
#: progress — it has to be its own clock, or a slow start looks like a death.
#: Kept close to the UI's own poll: at five seconds a page count sat visibly
#: frozen between beats, which reads as a stall rather than as progress.
HEARTBEAT = 2.0

#: A running record older than this is not believed. Six missed heartbeats:
#: loose enough to survive a stalled fetch or a loaded machine, tight enough
#: that a killed process stops being reported as working within half a minute.
STALE_AFTER = 30.0

#: How long a finished record stays readable. Long enough to answer "what
#: happened to that harvest?" on the next turn, short enough not to accumulate.
KEEP_RECORDS_FOR = 900.0

RUNNING, DONE, FAILED, STALLED = "running", "done", "failed", "stalled"


def state_dir() -> Path:
    """Where status records live. Overridable, mainly so tests do not share."""
    return Path(os.environ.get("DOCSFORGE_HARVEST_STATE")
                or Path.home() / ".docsforge" / "harvests")


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

    def __setattr__(self, name: str, value) -> None:
        """Publish a phase change at once rather than at the next heartbeat.

        A phase shorter than the beat was otherwise invisible: a two-second
        `resolving` between two beats never appeared anywhere, so a watcher saw
        `starting` and then `harvesting 21/200` and nothing in between. Page
        counts are left to the heartbeat — they tick hundreds of times and the
        beat is what bounds that to one write every couple of seconds.
        """
        moved = name == "phase" and getattr(self, "phase", None) != value
        object.__setattr__(self, name, value)
        if moved:
            hook = getattr(self, "_moved", None)
            if hook is not None:
                try:
                    hook()
                except Exception:              # noqa: BLE001
                    pass                       # never fail a harvest to report it

    def line(self) -> str:
        if self.phase == "harvesting" and self.expected:
            return f"harvesting {self.pages}/{self.expected} pages"
        if self.phase == "harvesting":
            return f"harvesting, {self.pages} pages so far"
        return self.phase

    def fraction(self) -> float | None:
        """How far along, or None when there is no honest denominator.

        A crawl's frontier is not a denominator — it grows as you walk it — so
        a crawl returns None here and the UI shows elapsed time instead of an
        invented percentage.
        """
        if self.phase == "harvesting" and self.expected:
            return max(0.0, min(1.0, self.pages / self.expected))
        return None


@dataclass
class Job:
    """One harvest, running or finished."""

    id: str
    label: str                       # what is being learned, for a human
    started: float
    progress: Progress = field(default_factory=Progress)
    state: str = RUNNING             # running | done | failed | stalled
    result: str = ""
    error: str = ""
    # The exception object itself, not a rendering of it. A caller still
    # inside the deadline re-raises this one, so a failure that would have
    # surfaced as a ForgeError with a candidate list still does.
    exc: BaseException | None = None
    finished: float = 0.0
    done: threading.Event = field(default_factory=threading.Event)
    #: Which process owns this job, and when it last said so. Both only
    #: matter for jobs read back from another process's record.
    pid: int = 0
    updated: float = 0.0
    mine: bool = True                # False when loaded from someone's record

    @property
    def elapsed(self) -> float:
        return (self.finished or time.time()) - self.started

    def line(self) -> str:
        """One line describing this job, for list_knowledge_base."""
        mins = f"{self.elapsed:.0f}s" if self.elapsed < 90 else f"{self.elapsed / 60:.0f}m"
        if self.state == RUNNING:
            return f"- **{self.label}** — {self.progress.line()}, {mins} elapsed  ·  `{self.id}`"
        if self.state == STALLED:
            return (f"- **{self.label}** — STOPPED REPORTING after {mins}, last seen "
                    f"{self.progress.line()}. The process running it most likely "
                    f"exited; nothing was stored.  ·  `{self.id}`")
        if self.state == FAILED:
            return f"- **{self.label}** — FAILED after {mins}: {self.error}  ·  `{self.id}`"
        return f"- **{self.label}** — finished in {mins}  ·  `{self.id}`"

    def as_dict(self) -> dict:
        """The wire and on-disk shape. One definition, so they cannot drift."""
        return {
            "id": self.id,
            "label": self.label,
            "state": self.state,
            "phase": self.progress.phase,
            "url": self.progress.url,
            "pages": self.progress.pages,
            "expected": self.progress.expected,
            "fraction": self.progress.fraction(),
            "line": self.progress.line(),
            "started": self.started,
            "updated": self.updated,
            "finished": self.finished,
            "elapsed": self.elapsed,
            "error": self.error,
            "pid": self.pid,
        }


_LOCK = threading.Lock()
_JOBS: dict[str, Job] = {}
_COUNTER = 0


# ─────────────────────────────────────────────────────────────
# Status records: how one process sees another's harvest
# ─────────────────────────────────────────────────────────────

def _announce(job: Job) -> None:
    """Say it once, on the console and in the log.

    Only on a transition -- a start, a change of phase, an ending -- never on
    a heartbeat, so watching a 211-page harvest costs four lines rather than
    a scrolling wall. stderr rather than stdout because MCP owns stdout.
    """
    said = f"{job.progress.phase}|{job.state}"
    if getattr(job, "_said", None) == said:
        return
    job._said = said                                    # type: ignore[attr-defined]
    try:
        import applog
        applog.harvest(job.id, job.label, job.state, phase=job.progress.phase,
                       pages=job.progress.pages, expected=job.progress.expected,
                       elapsed=job.elapsed, error=job.error)
    except Exception:                                   # noqa: BLE001
        pass
    try:
        if job.state == RUNNING:
            note = f"harvest {job.id}: {job.progress.line()}"
        elif job.state == FAILED:
            note = f"harvest {job.id}: FAILED after {job.elapsed:.0f}s - {job.error}"
        else:
            note = f"harvest {job.id}: {job.state} after {job.elapsed:.0f}s"
        print(f"[docsforge] {note}", file=sys.stderr, flush=True)
    except Exception:                                   # noqa: BLE001
        pass


def _record_path(job_id: str) -> Path:
    return state_dir() / f"{job_id}.json"


def _publish(job: Job) -> None:
    """Write this job's record. Never raises: a harvest must not fail because
    a status file could not be written."""
    job.updated = time.time()
    try:
        directory = state_dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = _record_path(job.id)
        # One file per job, written only by the process that owns it, so two
        # harvests never contend. `os.replace` is atomic on both platforms, so
        # a reader never sees half a record.
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(job.as_dict()), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:                              # noqa: BLE001
        pass


def _forget_record(job_id: str) -> None:
    try:
        _record_path(job_id).unlink(missing_ok=True)
    except Exception:                              # noqa: BLE001
        pass


def _job_from_record(data: dict) -> Job | None:
    try:
        progress = Progress(
            phase=data.get("phase", "starting"),
            url=data.get("url", "") or "",
            pages=int(data.get("pages") or 0),
            expected=data.get("expected"),
        )
        job = Job(
            id=data["id"], label=data.get("label", data["id"]),
            started=float(data.get("started") or 0.0),
            progress=progress,
            state=data.get("state", RUNNING),
            error=data.get("error", "") or "",
            finished=float(data.get("finished") or 0.0),
            pid=int(data.get("pid") or 0),
            updated=float(data.get("updated") or 0.0),
            mine=False,
        )
    except (KeyError, TypeError, ValueError):
        return None

    # A record still claiming to run, whose process stopped saying so. We
    # cannot know it died — but we can no longer say it is working, and
    # saying so anyway is the lie this whole mechanism exists to avoid.
    if job.state == RUNNING and time.time() - job.updated > STALE_AFTER:
        job.state = STALLED
        job.finished = job.updated
    return job


def _records() -> list[Job]:
    """Every other process's jobs, and a sweep of the expired ones."""
    directory = state_dir()
    out: list[Job] = []
    try:
        entries = list(directory.glob("*.json"))
    except Exception:                              # noqa: BLE001
        return out

    now = time.time()
    for path in entries:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:                          # noqa: BLE001
            continue                               # mid-write or corrupt
        job = _job_from_record(data)
        if job is None:
            continue
        settled = job.finished or job.updated
        if job.state != RUNNING and settled and now - settled > KEEP_RECORDS_FOR:
            try:
                path.unlink(missing_ok=True)
            except Exception:                      # noqa: BLE001
                pass
            continue
        out.append(job)
    return out


def _merged() -> list[Job]:
    """This process's jobs plus everyone else's, ours winning on collision."""
    with _LOCK:
        mine = dict(_JOBS)
    seen = dict(mine)
    for job in _records():
        seen.setdefault(job.id, job)
    return list(seen.values())


# ─────────────────────────────────────────────────────────────

def _new_id(label: str) -> str:
    """A short id a model can quote back, unique across processes.

    It used to be a per-process counter, which restarted at 1 in every new
    subprocess — so two different langchain harvests were both `langchain-1`
    and their records would have overwritten each other. The counter now
    starts above whatever is already on disk for this label.
    """
    global _COUNTER
    _COUNTER += 1
    safe = "".join(c for c in label.lower() if c.isalnum() or c == "-")[:24] or "harvest"

    taken = set()
    try:
        for path in state_dir().glob(f"{safe}-*.json"):
            suffix = path.stem[len(safe) + 1:]
            if suffix.isdigit():
                taken.add(int(suffix))
    except Exception:                              # noqa: BLE001
        pass
    taken |= {int(k.rsplit("-", 1)[-1]) for k in _JOBS
              if k.startswith(f"{safe}-") and k.rsplit("-", 1)[-1].isdigit()}

    number = _COUNTER
    while number in taken:
        number += 1
    _COUNTER = number
    return f"{safe}-{number}"


def _prune() -> None:
    """Drop the oldest finished jobs. Caller holds the lock."""
    finished = sorted(
        (j for j in _JOBS.values() if j.state != RUNNING),
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
        job = Job(id=_new_id(label), label=label, started=time.time(),
                  pid=os.getpid())
        _JOBS[job.id] = job
        _prune()

    def moved() -> None:
        _publish(job)
        _announce(job)

    object.__setattr__(job.progress, "_moved", moved)
    _publish(job)
    _announce(job)

    def run() -> None:
        try:
            job.result = work(job.progress)
            job.state = DONE
        except BaseException as e:                      # noqa: BLE001
            # Including SystemExit and KeyboardInterrupt: a worker thread that
            # died silently would leave a job "running" forever, and a harvest
            # that reports nothing is the failure mode this module exists to
            # prevent.
            job.exc = e
            job.error = str(e) or type(e).__name__
            job.state = FAILED
        finally:
            job.finished = time.time()
            _publish(job)
            _announce(job)
            job.done.set()

    def beat() -> None:
        # Its own clock, not progress-driven: a harvest can spend a minute
        # resolving without fetching a page, and a silent minute must not
        # read as a dead process.
        while not job.done.wait(HEARTBEAT):
            _publish(job)
            _announce(job)      # no-op unless the phase actually moved

    # Daemon: a harvest must never keep the process alive at shutdown. The
    # cost is that shutdown loses in-flight harvests, which is why the caller
    # is told the harvest runs "while this server is running" rather than
    # promised a result — and why a record whose heartbeat stopped is reported
    # as stalled rather than quietly left claiming to run.
    threading.Thread(target=run, name=f"harvest:{job.id}", daemon=True).start()
    threading.Thread(target=beat, name=f"heartbeat:{job.id}", daemon=True).start()
    return job


def wait(job: Job, seconds: float | None = None) -> bool:
    """Block up to `seconds`. True if the job finished within them."""
    return job.done.wait(DEADLINE if seconds is None else seconds)


def get(job_id: str) -> Job | None:
    with _LOCK:
        job = _JOBS.get(job_id)
    if job is not None:
        return job
    return next((j for j in _records() if j.id == job_id), None)


def running() -> list[Job]:
    """Harvests believed to be working, in this process or any other."""
    return sorted((j for j in _merged() if j.state == RUNNING),
                  key=lambda j: j.started)


def recent() -> list[Job]:
    """Finished, failed and stalled jobs, newest first."""
    return sorted((j for j in _merged() if j.state != RUNNING),
                  key=lambda j: j.finished, reverse=True)


def clear() -> None:
    """Forget every job. For tests; does not stop running threads."""
    global _COUNTER
    with _LOCK:
        ids = list(_JOBS)
        _JOBS.clear()
        _COUNTER = 0
    for job_id in ids:
        _forget_record(job_id)
    try:
        for path in state_dir().glob("*.json"):
            path.unlink(missing_ok=True)
    except Exception:                              # noqa: BLE001
        pass
