"""
Tests for LLMSFinder implementation (llmsfinder.md).

Tests shape classification, link extraction, acquisition ladder progression,
single-document whole storage, section-level search on single documents,
and disclosed truncation headers.
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


# ── 4. Rung 2: Markdown Index Manifest Harvesting ──────────
def test_harvest_md_manifest_index():
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
    assert stats.get("whole") is True


# ── 5. Section Search on Single-Document Corpus ───────────
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


# ── 6. Disclosed Truncation in read_knowledge_base ─────────
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
