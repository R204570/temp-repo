"""
Tests for LLMSFinder implementation (llmsfinder.md).

Tests shape classification, link extraction, acquisition ladder progression,
single-document whole storage, section-level search on single documents,
disclosed truncation headers, manifest completeness failure tracking,
duplicate link deduplication, hybrid partial failures, and non-redundant discovery.
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import docsforge as df
import forge_tools as ft
import kb_store as kbs
import llmsfinder


class FakeResponse:
    def __init__(self, text="", status=200, ctype="text/plain", url="", headers=None):
        self.text = text
        self.status_code = status
        self.headers = headers or {"content-type": ctype}
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


# ── 1. Shape Classification Tests ─────────────────────────
def test_classify_shape_index():
    index_text = "# Documentation\n\n" + "\n".join(
        f"- [Section {i}](https://x.dev/docs/{i}.md): description" for i in range(20)
    )
    assert llmsfinder.classify_llms_shape(index_text) == "index"


def test_classify_shape_dump():
    dump_text = "# Introduction\n\n" + ("This is detailed prose documentation. " * 100)
    assert llmsfinder.classify_llms_shape(dump_text) == "dump"


def test_classify_shape_hybrid():
    hybrid_text = (
        "# Overview\n\nThis is a hybrid document with prose.\n\n"
        + ("Some more detailed prose explanation. " * 50) + "\n\n"
        + "\n".join(f"- [Guide {i}](https://x.dev/guide/{i}.md)" for i in range(5))
    )
    assert llmsfinder.classify_llms_shape(hybrid_text) == "hybrid"


# ── 2. Link Extraction & Twin Detection ───────────────────
def test_parse_llms_links():
    text = """# Docs
- [Intro](/docs/intro.md)
- [Guide](https://x.dev/docs/guide.html)
- [Anchor](#section)
"""
    links = llmsfinder.parse_llms_links(text, "https://x.dev/llms.txt")
    urls = [url for _title, url in links]
    assert "https://x.dev/docs/intro.md" in urls
    assert "https://x.dev/docs/guide.html" in urls
    assert len(links) == 2  # Anchor skipped


def test_is_markdown_link():
    assert llmsfinder.is_markdown_link("https://x.dev/page.md")
    assert llmsfinder.is_markdown_link("https://x.dev/page.markdown")
    assert not llmsfinder.is_markdown_link("https://x.dev/page.html")


# ── 3. Rung 1: llms-full.txt Single Document Whole Storage ─
def test_handle_llms_txt_stores_dump_whole(tmp_path):
    big_dump = "# Header 1\n\n" + "Prose content 1\n\n" + "\n\n".join(
        f"## Section {i}\n\nDetailed content for section {i}." for i in range(50)
    )
    det = df.Detection("llms_txt", "https://x.dev/llms-full.txt", big_dump)
    fetcher = FakeFetcher({})
    opts = df.Options()
    docs = df.handle_llms_txt(det, fetcher, opts)

    # Must be stored as ONE single document, not split into fragments
    assert len(docs) == 1
    assert docs[0].url == "https://x.dev/llms-full.txt"
    assert "Section 49" in docs[0].markdown


# ── 4. Manifest Complete Success vs Partial Failure ───────
def test_manifest_complete_success():
    index_body = "# Index\n\n" + "\n".join(
        f"- [Page {i}](https://x.dev/p{i}.md)" for i in range(5)
    )
    pages = {"https://x.dev/llms.txt": FakeResponse(index_body, ctype="text/plain")}
    for i in range(5):
        pages[f"https://x.dev/p{i}.md"] = FakeResponse(f"# Page {i}\nContent {i}", ctype="text/plain")

    fetcher = FakeFetcher(pages)
    stats = {}
    docs, strat = df.harvest("https://x.dev/llms.txt", fetcher=fetcher, stats=stats)

    assert strat == "llms.txt (md manifest)"
    assert len(docs) == 5
    assert stats["expected"] == 5
    assert stats["acquired"] == 5
    assert stats["failed"] == 0
    assert stats["whole"] is True


def test_manifest_partial_failure():
    index_body = "# Index\n\n" + "\n".join(
        f"- [Page {i}](https://x.dev/p{i}.md)" for i in range(5)
    )
    pages = {"https://x.dev/llms.txt": FakeResponse(index_body, ctype="text/plain")}
    # Only 4 of 5 pages exist, p4.md will 404
    for i in range(4):
        pages[f"https://x.dev/p{i}.md"] = FakeResponse(f"# Page {i}\nContent {i}", ctype="text/plain")

    fetcher = FakeFetcher(pages)
    stats = {}
    docs, strat = df.harvest("https://x.dev/llms.txt", fetcher=fetcher, stats=stats)

    assert strat == "llms.txt (md manifest)"
    assert len(docs) == 4
    assert stats["expected"] == 5
    assert stats["acquired"] == 4
    assert stats["failed"] == 1
    assert stats["whole"] is False
    assert "manifest declared 5 unique pages" in stats.get("reason", "")


def test_duplicate_manifest_links():
    index_body = """# Index
- [Page 0](https://x.dev/p0.md)
- [Page 1](https://x.dev/p1.md)
- [Page 0 Repeat](https://x.dev/p0.md#section)
- [Page 2](https://x.dev/p2.md)
- [Page 1 Repeat](https://x.dev/p1.md)
"""
    pages = {
        "https://x.dev/llms.txt": FakeResponse(index_body, ctype="text/plain"),
        "https://x.dev/p0.md": FakeResponse("# P0", ctype="text/plain"),
        "https://x.dev/p1.md": FakeResponse("# P1", ctype="text/plain"),
        "https://x.dev/p2.md": FakeResponse("# P2", ctype="text/plain"),
    }
    fetcher = FakeFetcher(pages)
    stats = {}
    docs, _strat = df.harvest("https://x.dev/llms.txt", fetcher=fetcher, stats=stats)

    # 5 link lines, but only 3 unique clean URLs
    assert stats["expected"] == 3
    assert stats["acquired"] == 3
    assert len(docs) == 3
    assert stats["whole"] is True


def test_hybrid_partial_failure():
    hybrid_body = (
        "# Overview\n\nMain prose overview text here.\n\n"
        + ("Additional explanatory prose content. " * 30) + "\n\n"
        + "- [Doc 0](https://x.dev/d0.md)\n"
        + "- [Doc 1](https://x.dev/d1.md)\n"
        + "- [Doc 2](https://x.dev/d2.md)\n"
    )
    pages = {
        "https://x.dev/llms.txt": FakeResponse(hybrid_body, ctype="text/plain"),
        "https://x.dev/d0.md": FakeResponse("# D0", ctype="text/plain"),
        # d1.md fails
        "https://x.dev/d2.md": FakeResponse("# D2", ctype="text/plain"),
    }
    fetcher = FakeFetcher(pages)
    stats = {}
    docs, _strat = df.harvest("https://x.dev/llms.txt", fetcher=fetcher, stats=stats)

    # Root doc stored + 2 acquired linked docs = 3 docs
    assert len(docs) == 3
    assert stats["expected"] == 3
    assert stats["acquired"] == 2
    assert stats["failed"] == 1
    assert stats["whole"] is False
    assert "hybrid root document stored" in stats.get("reason", "")


def test_malformed_links_handling():
    text = """# Docs
- [Valid](https://x.dev/valid.md)
- [Invalid JS](javascript:alert(1))
- [Bad Scheme](invalid-scheme://bad)
- [Another Valid](https://x.dev/another.md)
"""
    links = llmsfinder.parse_llms_links(text, "https://x.dev/llms.txt")
    urls = [url for _t, url in links]
    assert "https://x.dev/valid.md" in urls
    assert "https://x.dev/another.md" in urls
    assert len(links) == 2  # Malformed links safely excluded


# ── 5. Full Dump Discovery & Non-Redundant Probing ─────────
def test_full_dump_discovery_from_nested_url():
    pages = {
        "https://x.dev/llms-full.txt": FakeResponse("# Complete Docs Dump\n" + ("prose " * 400), ctype="text/plain"),
        "https://x.dev/docs/sub/page.html": FakeResponse("<html><body><p>Nested</p></body></html>"),
    }
    fetcher = FakeFetcher(pages)
    stats = {}
    docs, strat = df.harvest("https://x.dev/docs/sub/page.html", fetcher=fetcher, stats=stats)

    assert strat == "llms-full.txt"
    assert len(docs) == 1
    assert docs[0].url == "https://x.dev/llms-full.txt"
    assert stats["whole"] is True


def test_no_unnecessary_post_success_discovery():
    dump_text = "# Complete Docs Dump\n\n" + ("Full documentation text. " * 100)
    pages = {
        "https://x.dev/llms-full.txt": FakeResponse(dump_text, ctype="text/plain"),
    }
    fetcher = FakeFetcher(pages)
    stats = {}
    docs, strat = df.harvest("https://x.dev/llms-full.txt", fetcher=fetcher, stats=stats)

    assert strat == "llms-full.txt"
    assert len(docs) == 1

    # Verify FakeFetcher only asked for llms-full.txt and zero sitemaps or robots.txt
    asked_urls = fetcher.asked
    assert len(asked_urls) == 1
    assert asked_urls[0] == "https://x.dev/llms-full.txt"
    assert not any("sitemap" in u for u in asked_urls)
    assert not any("robots" in u for u in asked_urls)


# ── 6. Section Search on Single-Document Corpus ───────────
def test_file_store_section_search_on_single_doc(tmp_path):
    store = kbs.FileStore(tmp_path)
    body = (
        "# Main Title\n\nIntro\n\n"
        "## Section A\n\nContains target keyphrase alpha.\n\n"
        "## Section B\n\nContains target keyphrase alpha again."
    )
    store.save("mytech", "v1", "https://x.dev/llms-full.txt", "llms-full.txt",
               [("llms.txt", "https://x.dev/llms-full.txt", body)], complete=True)

    hits = store.search("keyphrase alpha", tech="mytech")
    assert len(hits) >= 2
    titles = [h["title"] for h in hits]
    assert any("Section A" in t for t in titles)
    assert any("Section B" in t for t in titles)


# ── 7. Disclosed Truncation in read_knowledge_base ─────────
def test_read_knowledge_base_discloses_omission(tmp_path, monkeypatch):
    monkeypatch.setattr(ft, "store", lambda: kbs.FileStore(tmp_path))
    monkeypatch.setattr(ft, "MAX_CHARS", 100)

    long_body = (
        "## Overview\n\nSource: <https://x.dev/llms-full.txt>\n\n"
        "# Title\n\nFirst part\n\n"
        "## Omitted Section 1\n\nTail content here\n\n"
        "## Omitted Section 2\n\nMore tail content"
    )
    store = ft.store()
    store.save("longtech", "v1", "https://x.dev/llms-full.txt", "llms-full.txt",
               [("llms.txt", "https://x.dev/llms-full.txt", long_body)], complete=True)

    res = ft.tool_read_knowledge_base("longtech")
    assert "showing the first 100" in res
    assert "Omitted" in res
