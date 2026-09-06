"""A harvest must outlive the process that asked for it.

Measured, driving the real stdio server with a harvest in flight and then
tearing the server down two ways:

    close-stdin   process lived a further 232s   stored: 1.0.0.md
    kill          process lived a further   2s   stored: NOTHING

A client teardown *kills*. The linger that handles a polite hang-up cannot
help, and nothing running inside a process someone else owns survives being
killed — so under a short-lived host the harvest runs in a process of its own.

End to end, with the MCP server killed twelve seconds in, the detached harvest
went on to 209/307 pages and stored 3,563,589 bytes. These cover the pieces
that end-to-end run exercises, without the network.
"""
import json
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harvest_jobs
from harvest_jobs import DONE, FAILED, RUNNING


@pytest.fixture(autouse=True)
def _clean():
    harvest_jobs.clear()
    harvest_jobs.ADOPT = ""
    harvest_jobs.DETACHED = False
    yield
    harvest_jobs.clear()
    harvest_jobs.ADOPT = ""
    harvest_jobs.DETACHED = False


# ── waiting forever must not overflow ───────────────────────────

def test_an_infinite_deadline_waits_rather_than_raising():
    """`Event.wait(float("inf"))` raises OverflowError on Windows: "timestamp
    out of range for platform time_t". The worker sets the deadline that way,
    having nobody to hand back to early, and every harvest it ran died on that
    overflow before this was handled."""
    job = harvest_jobs.start("quick", lambda p: "stored")
    assert harvest_jobs.wait(job, float("inf")) is True
    assert job.state == DONE


def test_an_infinite_module_deadline_also_waits(monkeypatch):
    monkeypatch.setattr(harvest_jobs, "DEADLINE", float("inf"))
    job = harvest_jobs.start("quick", lambda p: "stored")
    assert harvest_jobs.wait(job) is True


def test_a_finite_deadline_still_bounds_the_wait():
    gate = threading.Event()
    job = harvest_jobs.start("slow", lambda p: gate.wait(30) or "never")
    try:
        assert harvest_jobs.wait(job, 0.3) is False
        assert job.state == RUNNING
    finally:
        gate.set()


# ── adopting the launcher's record ──────────────────────────────

def test_start_adopts_the_id_the_worker_was_given():
    harvest_jobs.ADOPT = "mojo-7"
    job = harvest_jobs.start("mojo", lambda p: "stored")
    harvest_jobs.wait(job, 5)
    assert job.id == "mojo-7"
    assert harvest_jobs.ADOPT == "", "consumed once, not left to catch the next"


def test_the_next_harvest_mints_its_own_id_again():
    harvest_jobs.ADOPT = "mojo-7"
    first = harvest_jobs.start("mojo", lambda p: "one")
    harvest_jobs.wait(first, 5)
    second = harvest_jobs.start("mojo", lambda p: "two")
    harvest_jobs.wait(second, 5)
    assert second.id != "mojo-7"


def test_a_worker_is_not_blocked_by_its_own_launchers_record():
    """The launcher publishes the record *before* the worker exists, so the
    duplicate-harvest guard would otherwise read it and conclude the work was
    already in hand — the worker refusing to do the job it was spawned for."""
    import forge_tools

    directory = harvest_jobs.state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "mojo-1.json").write_text(json.dumps({
        "id": "mojo-1", "label": "mojo", "state": RUNNING, "phase": "starting",
        "url": "", "pages": 0, "expected": None,
        "started": time.time(), "updated": time.time(),
        "finished": 0.0, "error": "", "pid": 999, "result": "",
    }), encoding="utf-8")

    running = harvest_jobs.running()
    assert [j.id for j in running] == ["mojo-1"]

    def blocked(adopt: str) -> bool:
        harvest_jobs.ADOPT = adopt
        wanted = forge_tools._kb_slug(forge_tools._normalise("mojo"))
        return any(
            not (j.id and j.id == harvest_jobs.ADOPT)
            and forge_tools._kb_slug(
                forge_tools._normalise(j.label) or j.label) == wanted
            for j in running)

    assert blocked(""), "a plain caller must still be told it is running"
    assert not blocked("mojo-1"), "the worker must not block on its own record"


# ── watching another process's record settle ────────────────────

def _write(job_id: str, **over) -> None:
    directory = harvest_jobs.state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    data = {
        "id": job_id, "label": job_id, "state": RUNNING, "phase": "harvesting",
        "url": "", "pages": 1, "expected": 10,
        "started": time.time(), "updated": time.time(),
        "finished": 0.0, "error": "", "pid": 4242, "result": "",
    }
    data.update(over)
    (directory / f"{job_id}.json").write_text(json.dumps(data), encoding="utf-8")


def test_await_record_returns_the_result_the_worker_wrote():
    """What keeps a small harvest feeling as it always did: a detached harvest
    that finishes inside the deadline still returns its own summary inline."""
    _write("mojo-1", state=DONE, finished=time.time(),
           result="Harvested **mojo** 1.0.0 - 213 pages")

    settled = harvest_jobs.await_record("mojo-1", 3)
    assert settled is not None
    assert settled.state == DONE
    assert "213 pages" in settled.result


def test_await_record_gives_up_while_the_harvest_is_still_running():
    _write("mojo-1")
    began = time.time()
    assert harvest_jobs.await_record("mojo-1", 0.5) is None
    assert time.time() - began < 3


def test_await_record_reports_a_failure_rather_than_waiting_it_out():
    _write("mojo-1", state=FAILED, finished=time.time(), error="the site refused us")
    settled = harvest_jobs.await_record("mojo-1", 3)
    assert settled is not None and settled.state == FAILED
    assert "refused us" in settled.error


# ── the worker only runs what it is meant to ────────────────────

def test_the_worker_refuses_a_tool_it_was_not_built_to_run(tmp_path):
    """The spec is an instruction to run code. It may name one function."""
    import harvest_worker

    spec = tmp_path / "bad.spec.json"
    spec.write_text(json.dumps({"job": "x-1", "label": "x",
                                "tool": "os.system", "kwargs": {}}),
                    encoding="utf-8")
    assert harvest_worker.run(spec) == 2


def test_the_worker_refuses_a_spec_with_no_job_id(tmp_path):
    import harvest_worker

    spec = tmp_path / "bad.spec.json"
    spec.write_text(json.dumps({"tool": "learn_technology", "kwargs": {}}),
                    encoding="utf-8")
    assert harvest_worker.run(spec) == 2


def test_the_worker_refuses_an_unreadable_spec(tmp_path):
    import harvest_worker

    spec = tmp_path / "gone.spec.json"
    assert harvest_worker.run(spec) == 2


def test_harvest_docs_is_not_detachable():
    """It is synchronous and publishes no record, so a detached run would
    harvest correctly while leaving the launcher's record to go stale and be
    reported as a process that vanished."""
    import harvest_worker

    assert harvest_worker.TOOLS == ("learn_technology",)


def test_the_worker_takes_its_configuration_only_from_its_environment():
    """It must not load `.env`. A launcher's environment is inherited already,
    and re-reading the file resurrects what the launcher deliberately removed —
    a test that unset `DOCSFORGE_DB` got a worker that found the file,
    reconnected to a real database, and returned in one second having
    harvested nothing."""
    import inspect

    import harvest_worker

    source = inspect.getsource(harvest_worker)
    assert "load_dotenv" not in source.split('"""')[-1], \
        "the worker must not load .env outside its own explanation"


# ── detaching is off unless a short-lived host asks for it ──────

def test_detaching_is_off_by_default():
    assert harvest_jobs.DETACHED is False


def test_the_stdio_server_turns_detaching_on():
    import inspect

    import mcp_server

    source = inspect.getsource(mcp_server.main)
    assert "DETACHED = True" in source
