"""
Integration tests for execution-trace observability: forge_tools.run_tool()
through to the real tool functions, and app.py's SSE surface.

Unlike tests/test_tracing.py (the trace log in isolation), these drive the
actual instrumented call paths -- tool_learn_technology, tool_harvest_docs,
run_tool's dispatch -- with the network stubbed out, the same way
tests/test_learn.py already does for the tools themselves.
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import forge_tools as ft
import harvest_jobs
import resolver
import tracing as tr
from kb_store import FileStore

PAGES = [("Intro", "https://x.dev/docs/intro", "welcome")]


@pytest.fixture(autouse=True)
def clean_state(tmp_path):
    ft.reset_store(FileStore(tmp_path))
    tr.clear()
    harvest_jobs.clear()
    yield
    ft.reset_store(None)
    tr.clear()
    harvest_jobs.clear()


def resolution(url="https://x.dev/docs/", verified=True, name="effect"):
    got = resolver.Resolution(name=name, ecosystem="npm")
    cand = resolver.Candidate(url, "npm:homepage", 0.8, "stubbed", verified,
                              "names it 9 times" if verified else "never mentions it")
    got.candidates = [cand]
    got.best = cand if verified else None
    got.resolved_via = "registry" if verified else ""
    if not verified:
        got.note = "Found 1 candidate(s) but none could be confirmed to document it."
    return got


def last_trace():
    tid = ft.last_trace_id()
    assert tid, "run_tool() should have minted a trace id"
    trace = tr.get(tid)
    assert trace is not None
    return trace


def by_name(trace, name):
    matches = [e for e in trace.events() if e.name == name]
    assert matches, f"no event named {name!r} in {[e.name for e in trace.events()]}"
    return matches[-1]


# ── run_tool(): every call gets a trace, closed when it returns ──
def test_run_tool_traces_an_untraced_tool_as_an_empty_but_real_trace():
    ft.run_tool("detect_source_type", {"url": "not a url"})
    trace = last_trace()
    assert trace.finished is not None, "an ordinary tool call's trace closes immediately"
    assert trace.events() == []  # nothing to report, and nothing invented


def test_run_tool_records_a_tool_failed_event_on_error():
    ft.run_tool("read_knowledge_base", {"name": "nothing-stored-under-this"})
    trace = last_trace()
    failed = by_name(trace, "tool failed")
    assert failed.state == tr.FAILED
    assert failed.error


# ── tool_learn_technology: resolving -> harvesting stages ────
def test_learn_technology_traces_resolution_and_harvest_stages(monkeypatch):
    monkeypatch.setattr(ft, "_resolve", lambda *a, **k: resolution())
    monkeypatch.setattr(
        ft, "tool_harvest_docs",
        lambda url, name=None, max_pages=0, js=False, version=None, **kw: (
            kw["trace"].event("stub harvest", message="2 pages") or "Harvested **effect** — 2 pages"
        ))

    out = ft.run_tool("learn_technology", {"name": "effect"})
    assert "Resolved" in out
    trace = last_trace()

    resolving = by_name(trace, "resolving identity")
    assert resolving.state == tr.COMPLETED
    assert resolving.result["best"]["url"] == "https://x.dev/docs/"
    assert resolving.result["resolved_via"] == "registry"

    stub = by_name(trace, "stub harvest")
    # tool_harvest_docs was handed the SAME trace context learn_technology
    # opened, not a disconnected one of its own.
    assert stub.trace_id == trace.id


def test_failed_resolution_is_traced_with_every_candidate_considered(monkeypatch):
    monkeypatch.setattr(ft, "_resolve", lambda *a, **k: resolution(verified=False))

    out = ft.run_tool("learn_technology", {"name": "ghost"})
    assert out.startswith("Error:")
    trace = last_trace()

    resolving = by_name(trace, "resolving identity")
    assert resolving.state == tr.FAILED
    assert len(resolving.result["candidates"]) == 1
    assert resolving.result["candidates"][0]["verified"] is False

    # The parent tool call is ALSO recorded as failed -- run_tool()'s own
    # bookkeeping -- without erasing the more specific resolution failure.
    failed = by_name(trace, "tool failed")
    assert failed.state == tr.FAILED
    assert resolving.state == tr.FAILED  # still true; not overwritten


def test_already_stored_technology_is_traced_without_a_harvest(monkeypatch):
    ft.store().save("effect", "v3", "https://x.dev/docs/v3/", "crawl", PAGES, complete=True)
    monkeypatch.setattr(ft, "_resolve",
                        lambda *a, **k: pytest.fail("must not resolve when stored"))

    ft.run_tool("learn_technology", {"name": "effect"})
    trace = last_trace()
    already = by_name(trace, "already stored")
    assert already.result["technology"] == "effect"


# ── tool_harvest_docs: harvesting / storing / corpus selection ──
def test_harvest_docs_traces_its_three_stages(monkeypatch):
    import docsforge as df

    def fake_harvest(url, opts=None, stats=None, sink=None, fetcher=None):
        stats["discovered"] = 1
        stats["whole"] = True
        doc = df.Doc(url, "Intro", "# Intro\nwelcome")
        if sink is not None:
            sink.add(doc.title, doc.url, doc.markdown)
        return [doc], "crawl"

    monkeypatch.setattr(ft, "harvest", fake_harvest)
    monkeypatch.setattr(ft, "_federate", lambda *a, **k: "")

    out = ft.run_tool("harvest_docs", {"url": "https://x.dev/docs/"})
    assert "Harvested" in out
    trace = last_trace()

    harvesting = by_name(trace, "harvesting")
    assert harvesting.state == tr.COMPLETED
    assert harvesting.result["pages"] == 1
    assert harvesting.result["strategy"] == "crawl"

    storing = by_name(trace, "storing")
    assert storing.state == tr.COMPLETED

    corpus = by_name(trace, "corpus selection")
    assert corpus.state == tr.COMPLETED

    completed = by_name(trace, "harvest_docs completed")
    assert completed.result["pages"] == 1
    assert completed.result["complete"] is True


def test_harvest_docs_traces_a_failed_fetch(monkeypatch):
    def fake_harvest(url, opts=None, stats=None, sink=None, fetcher=None):
        raise ft.ForgeError("connection refused")

    monkeypatch.setattr(ft, "harvest", fake_harvest)

    out = ft.run_tool("harvest_docs", {"url": "https://x.dev/docs/"})
    assert out.startswith("Error:")
    trace = last_trace()
    harvesting = by_name(trace, "harvesting")
    assert harvesting.state == tr.FAILED
    assert "connection refused" in harvesting.error


# ── page-fetch progress ticks: aggregated, no fake percentage ──
def test_counting_fetcher_ticks_report_bare_counts_without_a_total(monkeypatch):
    import docsforge as df

    monkeypatch.setattr(df.Fetcher, "html", lambda self, url: "<html><body>ok</body></html>")

    ctx = tr.start("x")
    stage = ctx.stage("harvesting")
    stage.start()
    progress = harvest_jobs.Progress()
    stats = {}  # no "discovered" key: the total is genuinely unknown
    fetcher = ft._CountingFetcher(df.Options(), progress, stats, stage=stage)
    for i in range(ft.TICK_EVERY_PAGES):
        fetcher.html(f"https://x.dev/{i}")

    ev = tr.get(ctx.trace_id).events()[-1]
    assert ev.counters.get("pages") == ft.TICK_EVERY_PAGES
    assert "expected" not in ev.counters
    assert "%" not in ev.message


def test_counting_fetcher_ticks_include_the_total_once_known(monkeypatch):
    import docsforge as df

    monkeypatch.setattr(df.Fetcher, "html", lambda self, url: "<html><body>ok</body></html>")

    ctx = tr.start("x")
    stage = ctx.stage("harvesting")
    stage.start()
    progress = harvest_jobs.Progress()
    stats = {"discovered": 50}
    fetcher = ft._CountingFetcher(df.Options(), progress, stats, stage=stage)
    for i in range(ft.TICK_EVERY_PAGES):
        fetcher.html(f"https://x.dev/{i}")

    ev = tr.get(ctx.trace_id).events()[-1]
    assert ev.counters == {"pages": ft.TICK_EVERY_PAGES, "expected": 50}


# ── the still-running / detach path ───────────────────────
def test_a_harvest_past_the_deadline_keeps_tracing_after_run_tool_returns(monkeypatch):
    """The scenario the whole background-trace design exists for: run_tool()
    returns "still running" while `work()` keeps executing (and tracing) on
    harvest_jobs' own thread."""
    release = __import__("threading").Event()

    def slow_resolve(*a, **k):
        release.wait(timeout=2)
        return resolution()

    monkeypatch.setattr(ft, "_resolve", slow_resolve)
    monkeypatch.setattr(
        ft, "tool_harvest_docs",
        lambda url, name=None, max_pages=0, js=False, version=None, **kw: "Harvested — 1 page")
    # Force the "did not finish in time" branch without a real 25s wait.
    monkeypatch.setattr(harvest_jobs, "wait", lambda job, seconds=None: False)

    out = ft.run_tool("learn_technology", {"name": "effect"})
    assert "still running" in out
    trace = last_trace()

    # run_tool() must NOT have closed this trace -- the real work is still
    # in flight on harvest_jobs' thread.
    assert trace.finished is None
    assert trace.keep_open is True

    release.set()
    for _ in range(100):
        if trace.finished is not None:
            break
        time.sleep(0.02)
    assert trace.finished is not None, "the background thread should close its own trace"
    resolving = by_name(trace, "resolving identity")
    assert resolving.state == tr.COMPLETED
