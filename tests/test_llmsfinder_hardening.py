"""
Integration tests for the LLMSFinder correctness-hardening pass.

Covers, through the real `harvest()` path rather than isolated units:
  - version-scoped llms.txt acquisition (safe vs. ambiguous manifests)
  - hybrid coverage semantics (root vs. manifest vs. corpus completeness)
  - manifest link classification (`expected` excludes off-site links)
  - failed-URL identity (category + normalized URL, for a future retry)
  - failure semantics (no manifest ever silently reported whole)
  - no redundant discovery once an LLMS rung has won
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import docsforge as df


class FakeResponse:
    def __init__(self, text="", status=200, ctype="text/plain", url=""):
        self.text = text
        self.status_code = status
        self.headers = {"content-type": ctype}
        self.url = url

    @property
    def content(self):
        return self.text.encode("utf-8")


class FakeFetcher:
    """Answers from a dict of url -> FakeResponse; 404s anything else."""

    def __init__(self, pages: dict):
        self.pages = pages
        self.asked: list[str] = []

    def get(self, url, **kw):
        self.asked.append(url)
        hit = self.pages.get(url.rstrip("/")) or self.pages.get(url)
        return hit or FakeResponse("not found", status=404, url=url)

    def text(self, url, **kw):
        res = self.get(url, **kw)
        if res.status_code != 200:
            raise df.ForgeError(f"HTTP {res.status_code} for {url}")
        return res.text

    def html(self, url):
        return self.text(url)

    def render(self, url):
        return self.text(url)

    def close(self):
        pass


PAGE_HTML = (
    "<html><head><title>%s</title></head><body><main><h1>%s</h1>"
    "<p>%s</p></main></body></html>"
)


def _page(title):
    return PAGE_HTML % (title, title, "Real content. " * 40)


# ── 1. Version-aware LLMS acquisition ─────────────────────────
def test_version_scope_multi_version_manifest_is_filtered():
    """Case B: a manifest that mixes versions can be safely narrowed."""
    body = "# Docs\n\n" + "\n".join([
        "- [V1 A](https://x.dev/docs/v1/a.md)",
        "- [V2 A](https://x.dev/docs/v2/a.md)",
        "- [V2 B](https://x.dev/docs/v2/b.md)",
        "- [V3 A](https://x.dev/docs/v3/a.md)",
    ])
    det = df.Detection("llms_txt", "https://x.dev/llms.txt", body)
    scoped = df._llms_txt_version_scope("https://x.dev/docs/v2/", det, FakeFetcher({}))
    urls = sorted(u for _t, u in scoped)
    assert urls == ["https://x.dev/docs/v2/a.md", "https://x.dev/docs/v2/b.md"]


def test_version_scope_ambiguous_manifest_returns_none():
    """Case A: a manifest with no version signal cannot be safely scoped."""
    body = "# Docs\n\n" + "\n".join(
        f"- [Page {i}](https://x.dev/docs/page{i}.md)" for i in range(10)
    )
    det = df.Detection("llms_txt", "https://x.dev/llms.txt", body)
    scoped = df._llms_txt_version_scope("https://x.dev/docs/v2/", det, FakeFetcher({}))
    assert scoped is None


def test_harvest_versioned_request_scopes_llms_manifest():
    """End to end: a versioned request against a multi-version site-wide
    manifest acquires only the requested version's pages via llms.txt."""
    manifest = "# Docs\n\n" + "\n".join([
        "- [V1 A](https://x.dev/docs/v1/a.md)",
        "- [V2 A](https://x.dev/docs/v2/a.md)",
        "- [V2 B](https://x.dev/docs/v2/b.md)",
        "- [V3 A](https://x.dev/docs/v3/a.md)",
    ])
    pages = {
        "https://x.dev/llms.txt": FakeResponse(manifest),
        "https://x.dev/docs/v1/a.md": FakeResponse("# V1 A"),
        "https://x.dev/docs/v2/a.md": FakeResponse("# V2 A"),
        "https://x.dev/docs/v2/b.md": FakeResponse("# V2 B"),
        "https://x.dev/docs/v3/a.md": FakeResponse("# V3 A"),
    }
    fetcher = FakeFetcher(pages)
    stats = {}
    docs, strat = df.harvest("https://x.dev/docs/v2/intro", fetcher=fetcher, stats=stats)

    urls = sorted(d.url for d in docs)
    assert urls == ["https://x.dev/docs/v2/a.md", "https://x.dev/docs/v2/b.md"]
    assert "v1" not in "".join(urls) and "v3" not in "".join(urls)
    assert stats["expected"] == 2
    assert stats["acquired"] == 2
    assert stats["whole"] is True
    assert "llms.txt" in strat


def test_harvest_versioned_request_falls_back_when_ambiguous():
    """End to end: an ambiguous manifest is not used at all; the harvest
    falls back to the version-scoped sitemap/crawl path, which still never
    admits another version's pages."""
    manifest = "# Docs\n\n" + "\n".join(
        f"- [Page {i}](https://x.dev/docs/page{i}.md)" for i in range(10)
    )
    sitemap = "<urlset>" + "".join(
        f"<url><loc>{u}</loc></url>" for u in [
            "https://x.dev/docs/v1/a",
            "https://x.dev/docs/v2/a",
            "https://x.dev/docs/v2/b",
            "https://x.dev/docs/v2/c",
            "https://x.dev/docs/v3/a",
        ]
    ) + "</urlset>"
    pages = {
        "https://x.dev/llms.txt": FakeResponse(manifest),
        "https://x.dev/sitemap.xml": FakeResponse(sitemap, ctype="application/xml"),
        "https://x.dev/docs/v2/a": FakeResponse(_page("A")),
        "https://x.dev/docs/v2/b": FakeResponse(_page("B")),
        "https://x.dev/docs/v2/c": FakeResponse(_page("C")),
    }
    fetcher = FakeFetcher(pages)
    stats = {}
    docs, strat = df.harvest("https://x.dev/docs/v2/intro", fetcher=fetcher, stats=stats)

    urls = sorted(d.url for d in docs)
    assert urls == [
        "https://x.dev/docs/v2/a",
        "https://x.dev/docs/v2/b",
        "https://x.dev/docs/v2/c",
    ]
    assert strat == "sitemap"


# ── 2 & 3. Hybrid coverage semantics + manifest link classification ──
def test_hybrid_external_links_excluded_from_expected():
    hybrid_body = (
        "# Overview\n\nMain prose overview text here.\n\n"
        + ("Additional explanatory prose content. " * 30) + "\n\n"
        + "- [Doc 0](https://x.dev/d0.md)\n"
        + "- [Doc 1](https://x.dev/d1.md)\n"
        + "- [External](https://other.example/page)\n"
    )
    pages = {
        "https://x.dev/llms.txt": FakeResponse(hybrid_body),
        "https://x.dev/d0.md": FakeResponse("# D0"),
        "https://x.dev/d1.md": FakeResponse("# D1"),
    }
    fetcher = FakeFetcher(pages)
    stats = {}
    docs, _strat = df.harvest("https://x.dev/llms.txt", fetcher=fetcher, stats=stats)

    urls = [d.url for d in docs]
    assert "https://other.example/page" not in urls
    # Root + 2 in-scope docs; the external link never counted or was fetched.
    assert stats["expected"] == 2
    assert stats["acquired"] == 2
    assert stats["whole"] is True
    assert "https://other.example/page" not in fetcher.asked


def test_hybrid_partial_failure_root_success_not_whole():
    """Root document acquisition succeeding must not imply corpus
    completeness — a hybrid with any failed linked page reports whole=False."""
    hybrid_body = (
        "# Overview\n\nMain prose overview text here.\n\n"
        + ("Additional explanatory prose content. " * 30) + "\n\n"
        + "- [Doc 0](https://x.dev/d0.md)\n"
        + "- [Doc 1](https://x.dev/d1.md)\n"
        + "- [Doc 2](https://x.dev/d2.md)\n"
    )
    pages = {
        "https://x.dev/llms.txt": FakeResponse(hybrid_body),
        "https://x.dev/d0.md": FakeResponse("# D0"),
        "https://x.dev/d2.md": FakeResponse("# D2"),
    }
    fetcher = FakeFetcher(pages)
    stats = {}
    docs, _strat = df.harvest("https://x.dev/llms.txt", fetcher=fetcher, stats=stats)

    assert len(docs) == 3  # root + 2 acquired
    assert stats["whole"] is False
    assert stats["failed"] == 1
    assert len(stats["failed_urls"]) == 1
    assert stats["failed_urls"][0]["url"] == "https://x.dev/d1.md"


# ── 4 & 5. Failed URL identity, ready for a future targeted retry ────
def test_manifest_permanent_failure_identifies_missing_page():
    index_body = "# Index\n\n" + "\n".join(
        f"- [Page {i}](https://x.dev/p{i}.md)" for i in range(3)
    )
    pages = {
        "https://x.dev/llms.txt": FakeResponse(index_body),
        "https://x.dev/p0.md": FakeResponse("# P0"),
        "https://x.dev/p1.md": FakeResponse("# P1"),
        # p2.md permanently 404s
    }
    fetcher = FakeFetcher(pages)
    stats = {}
    docs, _strat = df.harvest("https://x.dev/llms.txt", fetcher=fetcher, stats=stats)

    assert len(docs) == 2
    assert stats["whole"] is False
    assert stats["failed"] == 1
    failed = stats["failed_urls"]
    assert len(failed) == 1
    assert failed[0]["url"] == "https://x.dev/p2.md"
    assert failed[0]["category"] == "http_error"
    # A future retry could reconstruct exactly the failed subset:
    acquired_urls = {d.url for d in docs}
    assert acquired_urls == {"https://x.dev/p0.md", "https://x.dev/p1.md"}
    assert {f["url"] for f in failed} == {"https://x.dev/p2.md"}


# ── 6 & 7. Failure semantics: no manifest reported whole by accident ──
def test_index_manifest_all_links_fail_is_not_reported_whole():
    """Regression: if every linked page fails, `handle_llms_txt` must not
    fall through to storing the raw manifest text as though it were the
    (whole) documentation — the old fallthrough this guards against
    returned `[Doc(det.url, "llms.txt", body)]` with `whole=True`."""
    index_body = "# Index\n\n" + "\n".join(
        f"- [Page {i}](https://x.dev/p{i}.md)" for i in range(3)
    )
    det = df.Detection("llms_txt", "https://x.dev/llms.txt", index_body)
    fetcher = FakeFetcher({"https://x.dev/llms.txt": FakeResponse(index_body)})
    stats = {}
    docs = df.handle_llms_txt(det, fetcher, df.Options(verbose=False), stats=stats)

    assert docs == []
    assert stats["whole"] is False
    assert stats["expected"] == 3
    assert stats["acquired"] == 0
    assert stats["failed"] == 3


def test_harvest_falls_back_to_crawl_when_manifest_totally_fails():
    """End to end: when every manifest link fails, the llms.txt rung has
    failed, so the harvest falls through to the next rung (sitemap here)
    instead of reporting an empty or fabricated success."""
    index_body = "# Index\n\n" + "\n".join(
        f"- [Page {i}](https://x.dev/p{i}.md)" for i in range(3)
    )
    sitemap = ("<urlset>"
               + "".join(f"<url><loc>https://x.dev/docs/p{i}</loc></url>" for i in range(3))
               + "</urlset>")
    pages = {
        "https://x.dev/llms.txt": FakeResponse(index_body),
        "https://x.dev/sitemap.xml": FakeResponse(sitemap, ctype="application/xml"),
        "https://x.dev/docs/p0": FakeResponse(_page("P0")),
        "https://x.dev/docs/p1": FakeResponse(_page("P1")),
        "https://x.dev/docs/p2": FakeResponse(_page("P2")),
    }
    fetcher = FakeFetcher(pages)
    stats = {}
    docs, strat = df.harvest("https://x.dev/llms.txt", fetcher=fetcher, stats=stats)

    assert strat == "sitemap"
    assert len(docs) == 3


def test_root_llms_txt_fetch_failure_falls_back():
    """A root llms.txt that cannot even be fetched must not crash the whole
    harvest — the ladder should fall through to the next rung."""
    sitemap = ("<urlset>"
               + "".join(f"<url><loc>https://x.dev/docs/p{i}</loc></url>" for i in range(3))
               + "</urlset>")
    pages = {
        # llms.txt / llms-full.txt both 404 during detect_source's probe...
        "https://x.dev/sitemap.xml": FakeResponse(sitemap, ctype="application/xml"),
        "https://x.dev/docs/p0": FakeResponse(_page("P0")),
        "https://x.dev/docs/p1": FakeResponse(_page("P1")),
        "https://x.dev/docs/p2": FakeResponse(_page("P2")),
    }
    fetcher = FakeFetcher(pages)
    # Force detection straight to a broken llms.txt reference (body=None),
    # simulating a race where the file vanished between detection and fetch.
    det = df.Detection("llms_txt", "https://x.dev/llms-full.txt", None)
    import unittest.mock as mock
    with mock.patch.object(df, "detect_source", return_value=det):
        stats = {}
        docs, strat = df.harvest("https://x.dev/docs/p0", fetcher=fetcher, stats=stats)

    assert strat == "sitemap"
    assert len(docs) == 3


# ── No redundant discovery after a successful LLMS acquisition ───────
def test_no_redundant_discovery_after_index_manifest_success():
    index_body = "# Index\n\n" + "\n".join(
        f"- [Page {i}](https://x.dev/p{i}.md)" for i in range(3)
    )
    pages = {
        "https://x.dev/llms.txt": FakeResponse(index_body),
        "https://x.dev/p0.md": FakeResponse("# P0"),
        "https://x.dev/p1.md": FakeResponse("# P1"),
        "https://x.dev/p2.md": FakeResponse("# P2"),
    }
    fetcher = FakeFetcher(pages)
    stats = {}
    docs, strat = df.harvest("https://x.dev/llms.txt", fetcher=fetcher, stats=stats)

    assert len(docs) == 3
    assert stats["whole"] is True
    assert not any("sitemap" in u for u in fetcher.asked)
    assert not any("robots" in u for u in fetcher.asked)
