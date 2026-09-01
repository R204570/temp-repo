"""
LLMSFinder: Acquisition ladder engine for DocsForge.

Implements published-first documentation acquisition as specified in `llmsfinder.md`.
Prefers published machine-readable files (llms-full.txt, llms.txt index/manifest)
over inferred sitemaps and deep crawling.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse


#: Markdown link pattern: [title](url)
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def classify_llms_shape(text: str) -> str:
    """Classify an `llms.txt` document into Shape A (index), Shape B (dump), or Shape C (hybrid).

    - **Shape A (index)**: High ratio of link-line characters to total characters.
      A table of contents / list of links.
    - **Shape B (dump)**: Low link density, predominantly prose documentation.
    - **Shape C (hybrid)**: Substantial prose *and* multiple manifest links.

    Uses arithmetic character and link density ratios without model calls.
    """
    body = (text or "").strip()
    if not body:
        return "index"

    links = _LINK_RE.findall(body)
    if not links:
        return "dump"

    lines = body.splitlines()
    non_empty_lines = [l.strip() for l in lines if l.strip()]
    if not non_empty_lines:
        return "index"

    link_lines = [l for l in non_empty_lines if _LINK_RE.search(l)]
    link_line_chars = sum(len(l) for l in link_lines)
    total_chars = len(body)

    density = link_line_chars / max(1, total_chars)

    # An index is almost entirely link lines or points at a fuller file
    if re.search(r"llms-(full|medium)\.txt", body, re.I) or (len(links) >= 1 and density >= 0.5):
        return "index"

    # Hybrid has substantial prose AND multiple links
    if len(links) >= 3 and 0.03 <= density < 0.5 and total_chars >= 300:
        return "hybrid"

    return "dump"


def parse_llms_links(text: str, base_url: str) -> list[tuple[str, str]]:
    """Extract (title, absolute_url) from an llms.txt index."""
    found: list[tuple[str, str]] = []
    seen: set[str] = set()

    for title, target in _LINK_RE.findall(text or ""):
        target = target.strip()
        if not target or target.startswith(("#", "mailto:", "javascript:")):
            continue
        try:
            full_url = urljoin(base_url, target)
            p = urlparse(full_url)
            if p.scheme not in ("http", "https") or not p.netloc:
                continue
        except Exception:
            continue

        # Strip fragment for document fetching deduplication
        clean_url = full_url.split("#")[0]
        if clean_url in seen:
            continue
        seen.add(clean_url)
        clean_title = title.strip() or "Untitled"
        found.append((clean_title, clean_url))

    return found


def is_markdown_link(url: str) -> bool:
    """Check if a URL points directly to a Markdown document twin."""
    path = urlparse(url).path.lower()
    return path.endswith((".md", ".markdown", ".raw.txt")) or "/raw/" in path
