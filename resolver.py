"""
Turn a technology name into documentation URLs.

This is the hop DocsForge was missing. Every tool that acquires documentation
needed a URL, but the caller is a model that has just met a library it does not
know — so the one thing it cannot supply is where that library's documentation
lives. Left with no tool, it guesses a URL from the same stale training data
the product exists to bypass, and a guess can resolve to a real, *wrong* page
and be harvested and summarised with complete confidence.

The chain, cheapest first:

    1. package registry   name -> homepage / documentation / repository
    2. convention probe   host -> llms.txt, sitemap, a /docs root
    3. verification       does that page actually document this package?

Only a verified candidate is worth harvesting. A resolver that is merely
*usually* right recreates the guessing bug with extra steps, so "I could not
resolve this, give me a URL" is a supported and preferred outcome.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

from docsforge import Fetcher, ForgeError, Options
from instrument import Budget, ResolveState

#: Registries are keyless and public, but not unlimited: keep the timeout tight
#: and never make more than a couple of calls per resolution.
REGISTRY_TIMEOUT = 12
PROBE_TIMEOUT = 10

#: Paths worth trying on a candidate host when the registry only gave us a
#: marketing homepage. Ordered by how likely each is to *be* the docs root.
DOC_PATHS = ("/llms.txt", "/docs/", "/docs", "/documentation/", "/guide/",
             "/en/latest/", "/latest/")


@dataclass
class Candidate:
    """A possible home for a technology's documentation."""

    url: str
    source: str                  # where the suggestion came from
    confidence: float            # 0..1, before verification
    evidence: str = ""
    verified: bool | None = None  # None = not checked yet
    reason: str = ""
    #: Which identity checks fired. `verified: true` on its own is what made
    #: the wrong answers dangerous — a caller shown the reasons can disagree.
    signals: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "url": self.url, "source": self.source,
            "confidence": round(self.confidence, 2), "evidence": self.evidence,
            "verified": self.verified, "reason": self.reason,
            "signals": list(self.signals),
        }


@dataclass
class Resolution:
    name: str
    ecosystem: str = ""
    candidates: list[Candidate] = field(default_factory=list)
    best: Candidate | None = None
    note: str = ""
    #: "domain", "registry", or "" when nothing resolved. Part of the honesty
    #: contract: how an answer was reached bears on how much to trust it.
    resolved_via: str = ""

    def as_dict(self) -> dict:
        return {
            "name": self.name, "ecosystem": self.ecosystem,
            "best": self.best.as_dict() if self.best else None,
            "candidates": [c.as_dict() for c in self.candidates],
            "note": self.note, "resolved_via": self.resolved_via,
        }


# ─────────────────────────────────────────────────────────────
# Names
# ─────────────────────────────────────────────────────────────
#: Suffixes people attach to a library's name in prose but never in its
#: package name: "Effect.ts", "Vue.js", "pydantic-py".
_DRESSING = re.compile(r"(\.|-)(js|ts|py|rs|go|dev|io)$", re.I)


def normalise(name: str) -> str:
    """A name reduced to the form two spellings of it can be compared in.

    `Effect.ts`, `effect-ts` and `effect` are the same library; a lookup that
    only matches the exact stored slug makes the caller guess our filing
    convention, which it has no way to know.
    """
    text = (name or "").strip().lower()
    if text.startswith("@") and "/" in text:      # @scope/pkg -> pkg
        text = text.split("/", 1)[1]
    text = _DRESSING.sub("", text)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text


def guess_ecosystem(name: str) -> str:
    """The registry a name most likely belongs to, or "" for unknown."""
    if name.startswith("@") and "/" in name:
        return "npm"
    if "::" in name or name.endswith("-rs"):
        return "crates"
    return ""


# ─────────────────────────────────────────────────────────────
# Registries
# ─────────────────────────────────────────────────────────────
def _json(fetcher: Fetcher, url: str) -> dict | None:
    try:
        r = fetcher.get(url, timeout=REGISTRY_TIMEOUT)
    except ForgeError:
        return None
    if r.status_code != 200:
        return None
    try:
        data = json.loads(r.text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _clean_repo(url: str) -> str:
    """A git remote as a browsable https URL."""
    text = (url or "").strip()
    text = re.sub(r"^git\+", "", text)
    text = re.sub(r"^git://", "https://", text)
    text = re.sub(r"^ssh://git@", "https://", text)
    text = re.sub(r"^git@([^:]+):", r"https://\1/", text)
    text = re.sub(r"\.git$", "", text)
    return text if text.startswith("http") else ""


#: Code hosts. Their origin belongs to the forge, not to any project on it, so
#: probing `github.com/llms.txt` finds GitHub's own file and offers it as the
#: documentation for whatever package happened to be asked about.
FORGES = ("github.com", "gitlab.com", "bitbucket.org", "sourceforge.net",
          "codeberg.org", "git.sr.ht", "githubusercontent.com")


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def is_forge(url: str) -> bool:
    """Is this URL on a code host rather than a project's own site?

    Matched by suffix, not equality. Exact matching let `gist.github.com` and
    `raw.githubusercontent.com` through, and probing them offered GitHub's own
    `llms.txt` as the documentation for whatever had been asked about — it
    entered one live resolution at 0.95, the top-scoring candidate of the run,
    and lost only because that file happened not to contain the word.
    """
    host = _host(url)
    return any(host == forge or host.endswith("." + forge) for forge in FORGES)


#: How much readable text a page must carry before it counts as documentation.
#: Tuned against the measured failure: the real Astro docs root returns 3
#: characters, the marketing homepage 6,448.
MIN_PROBE_TEXT = 200

#: `<meta http-equiv="refresh" content="0; url=…">`, the usual shape of the
#: stub that sits where a docs root used to be.
_META_REFRESH = re.compile(
    r"""<meta[^>]+http-equiv\s*=\s*["']?refresh["']?[^>]*content\s*=\s*"""
    r"""["'][^"']*url\s*=\s*([^"'\s>]+)""", re.I)

#: A stub that redirects with a script instead: `location.href = "..."`.
_JS_REDIRECT = re.compile(
    r"""location(?:\.href|\.replace\()?\s*=?\s*\(?\s*["']([^"']+)["']""", re.I)


def _is_docs_host(url: str) -> bool:
    """Is this a host dedicated to documentation, like `docs.astro.build`?

    Such a host is exempt from the content floor. Astro's docs root renders
    entirely client-side and serves an empty shell, so measuring its text
    rejects it — and rejecting it hands the harvest to `astro.build`, whose
    sitemap is mostly blog posts. Pointing `/docs` at `docs.<project>` is an
    explicit statement about where the documentation lives, and it outranks
    what the index page happens to render without JavaScript.
    """
    host = _host(url)
    return host.startswith(("docs.", "developer.", "devdocs.")) or ".readthedocs." in host


def _visible_text(html: str) -> str:
    """Roughly what a reader would see, for measuring whether a page is empty."""
    body = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html or "")
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()


def _follow_client_redirect(response, base: str, fetcher: Fetcher):
    """Follow one hop of a redirect the HTTP layer cannot see.

    `requests` follows 3xx, but a page that redirects with a meta tag or a line
    of JavaScript arrives as a perfectly good 200 holding nothing. That is not
    a page, it is a signpost, and the thing it points at is the answer.
    """
    html = getattr(response, "text", "") or ""
    if len(html) > 4_000:            # a real page, not a signpost
        return None
    match = _META_REFRESH.search(html) or _JS_REDIRECT.search(html)
    if not match:
        return None
    target = urljoin(base, match.group(1).strip())
    if target.rstrip("/") == base.rstrip("/"):
        return None
    try:
        hop = fetcher.get(target, timeout=PROBE_TIMEOUT, allow_redirects=True)
    except ForgeError:
        return None
    return hop if hop.status_code == 200 else None


def _looks_like_docs(url: str) -> bool:
    host = _host(url)
    path = urlparse(url).path.lower()
    return (host.startswith("docs.") or ".readthedocs." in host
            or "/docs" in path or "/documentation" in path or "/guide" in path)


def _score(url: str, field_name: str) -> float:
    """How much to trust a URL, by which field it came out of.

    An explicit `documentation` field is the package author saying where the
    docs are. A homepage is a guess that often lands on marketing. And a field
    of any name pointing at a code host is a *repository* — some registries
    put a GitHub link in `documentation`, and taking that at face value ranks a
    repo above the project's actual documentation site.
    """
    if not url:
        return 0.0
    if is_forge(url):
        return 0.35
    if field_name == "documentation":
        return 0.92
    if field_name == "homepage":
        return 0.78 if _looks_like_docs(url) else 0.55
    return 0.35        # repository


def _npm(name: str, fetcher: Fetcher) -> list[Candidate]:
    data = _json(fetcher, f"https://registry.npmjs.org/{name}")
    if not data:
        return []
    out = []
    home = (data.get("homepage") or "").strip()
    if home.startswith("http"):
        out.append(Candidate(home, "npm:homepage", _score(home, "homepage"),
                             f"npm registry homepage for {name}"))
    repo = data.get("repository")
    repo_url = _clean_repo(repo.get("url") if isinstance(repo, dict) else repo or "")
    if repo_url:
        out.append(Candidate(repo_url, "npm:repository", _score(repo_url, "repository"),
                             f"npm registry repository for {name}"))
    return out


def _pypi(name: str, fetcher: Fetcher) -> list[Candidate]:
    data = _json(fetcher, f"https://pypi.org/pypi/{name}/json")
    if not data:
        return []
    info = data.get("info") or {}
    out = []

    # project_urls is where modern packages actually declare their docs.
    for label, url in (info.get("project_urls") or {}).items():
        if not isinstance(url, str) or not url.startswith("http"):
            continue
        low = label.lower()
        if "doc" in low:
            out.append(Candidate(url, f"pypi:{label}", _score(url, "documentation"),
                                 f"PyPI project_urls[{label}] for {name}"))
        elif "home" in low:
            out.append(Candidate(url, f"pypi:{label}", _score(url, "homepage"),
                                 f"PyPI project_urls[{label}] for {name}"))
        elif "source" in low or "repo" in low:
            out.append(Candidate(url, f"pypi:{label}", _score(url, "repository"),
                                 f"PyPI project_urls[{label}] for {name}"))

    home = (info.get("home_page") or "").strip()
    if home.startswith("http"):
        out.append(Candidate(home, "pypi:home_page", _score(home, "homepage"),
                             f"PyPI home_page for {name}"))
    return out


def _crates(name: str, fetcher: Fetcher) -> list[Candidate]:
    data = _json(fetcher, f"https://crates.io/api/v1/crates/{name}")
    crate = (data or {}).get("crate") or {}
    out = []
    for key, kind in (("documentation", "documentation"),
                      ("homepage", "homepage"),
                      ("repository", "repository")):
        url = (crate.get(key) or "").strip()
        if url.startswith("http"):
            out.append(Candidate(url, f"crates:{key}", _score(url, kind),
                                 f"crates.io {key} for {name}"))
    return out


REGISTRIES = {"npm": _npm, "pypi": _pypi, "crates": _crates}


def _facts_from(found: list[Candidate], ecosystem: str) -> dict:
    """What the registries claimed, for the identity checks to test against.

    These are the independent statements a candidate page can agree with: the
    repository the package declares, and the homepage it declares. Agreement
    between two sources that never consulted each other is the evidence a
    mention count cannot provide.
    """
    facts: dict = {"ecosystem": ecosystem}
    for cand in found:
        tail = cand.source.rsplit(":", 1)[-1].lower()
        if "repo" in tail or "source" in tail:
            facts.setdefault("repository", cand.url)
        elif "home" in tail:
            facts.setdefault("homepage", cand.url)
    return facts


def from_registries(name: str, ecosystem: str, fetcher: Fetcher) -> tuple[list[Candidate], str]:
    """Ask the package registries where this library documents itself.

    With no ecosystem hint every registry is tried, because the same name can
    exist in several and the caller usually does not know which one it meant.
    """
    order = [ecosystem] if ecosystem in REGISTRIES else list(REGISTRIES)
    found: list[Candidate] = []
    hit = ""
    for eco in order:
        got = REGISTRIES[eco](name, fetcher)
        if got:
            hit = hit or eco
            found += got
            if ecosystem:
                break
    return found, hit


# ─────────────────────────────────────────────────────────────
# Probing
# ─────────────────────────────────────────────────────────────
def probe_docs_root(url: str, fetcher: Fetcher) -> list[Candidate]:
    """Look for a documentation root on a host the registry pointed at.

    A registry homepage is very often a marketing page with the docs one click
    away, so a bare homepage is worth one cheap round of convention-guessing
    before it is either used or discarded.
    """
    if not urlparse(url).netloc or is_forge(url):
        # A repository's origin is the code host, not the project. Probing it
        # offers GitHub's own llms.txt as the docs for whatever was asked for.
        return []
    origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"

    out: list[Candidate] = []
    for path in DOC_PATHS:
        target = urljoin(origin + "/", path.lstrip("/"))
        try:
            r = fetcher.get(target, timeout=PROBE_TIMEOUT, allow_redirects=True)
        except ForgeError:
            continue
        if r.status_code != 200:
            continue
        ctype = (r.headers.get("content-type") or "").lower()
        if "html" in ctype and not path.endswith(".txt"):
            # An HTTP 200 is not a documentation root: a stub that redirects
            # with a meta tag or a line of script arrives as a perfectly good
            # empty page, and accepting one costs the correct answer.
            hop = _follow_client_redirect(r, target, fetcher)
            if hop is not None:
                r = hop
            landed = getattr(r, "url", "") or target
            if (len(_visible_text(getattr(r, "text", ""))) < MIN_PROBE_TEXT
                    and not _is_docs_host(landed)):
                continue
        if path.endswith(".txt"):
            if "html" in ctype:
                continue
            # A published llms.txt is the site describing itself for machines;
            # nothing beats it.
            out.append(Candidate(r.url, "probe:llms.txt", 0.95,
                                 f"{target} exists and is not HTML"))
        elif "html" in ctype:
            out.append(Candidate(r.url, f"probe:{path}", 0.7,
                                 f"{target} returned a page"))
        if out and out[-1].confidence >= 0.95:
            break
    return out


# ─────────────────────────────────────────────────────────────
# The project's own domain
# ─────────────────────────────────────────────────────────────
#: Tried in order, and kept short because each one costs a request. `.com`
#: is last: it is the most heavily squatted, so it is the least trustworthy
#: evidence that the project owns the name.
NAME_TLDS = ("dev", "io", "org", "com")

#: A missing domain fails fast at DNS, so this can be tight.
DOMAIN_TIMEOUT = 6

#: How many live domains get the full docs-root treatment. Bounded because
#: each one costs a handful of requests, and past the second the returns are
#: not worth the latency.
DOMAINS_EXPLORED = 2


#: Evidence that a site is about software at all, rather than merely owning
#: the word. Without this gate `astro` resolves to an astrology site: it owns
#: astro.com, it is enormous, and it says "astro" constantly — which is every
#: signal a name-and-size check has, and none of the ones that matter.
_FORGE_LINK = re.compile(r"https?://(?:www\.)?(?:github|gitlab|bitbucket)\.com/[\w.\-]+/", re.I)
_CODE_BLOCK = re.compile(r"<(?:code|pre)[\s>]", re.I)


def _looks_like_software(body: str, slug: str) -> str:
    """Why this page appears to be a software project's, or "" if it does not."""
    if _install_line(re.sub(r"<[^>]+>", " ", body), slug):
        return "an install command"
    if _FORGE_LINK.search(body):
        return "a link to its source repository"
    if len(_CODE_BLOCK.findall(body)) >= 3:
        return "code samples"
    return ""


def _domain_score(origin: str, landed: str, html: str, slug: str) -> int:
    """How strongly this domain looks like *the* home of the technology.

    Two live domains can both pass the software gate — `terraform.io` and
    `terraform.com` did, and page size preferred the wrong one. What separates
    them is deliberateness: `terraform.io` redirects to
    `developer.hashicorp.com/terraform`, and a name-domain pointed at a
    project-specific path somewhere else is somebody consolidating their
    documentation. A site that simply serves itself has made no such claim.
    """
    score = 0
    if _host(landed) != _host(origin):
        score += 2
        if slug in urlparse(landed).path.lower():
            score += 3
    if _install_line(re.sub(r"<[^>]+>", " ", html), slug):
        score += 2
    if _FORGE_LINK.search(html):
        score += 1
    return score


def _probe_origins(origins: list[tuple[str, str]], slug: str, fetcher: Fetcher,
                   explore: int = DOMAINS_EXPLORED,
                   state=None) -> list[Candidate]:
    """Try a list of `(label, url)` guesses and return what survives.

    Split out of `from_domains` so the name-shape lap can reuse it verbatim.
    That reuse is the point rather than a convenience: a shape hit must meet
    exactly the standard a domain hit meets — same software gate, same text
    floor, same docs-root probing, same scoring — or L3 becomes a second, softer
    way in, and the ladder's whole claim is that more laps never mean a lower
    bar.
    """
    live: list[tuple[int, int, str, str, str]] = []
    for label, origin in origins:
        try:
            r = fetcher.get(origin, timeout=DOMAIN_TIMEOUT, allow_redirects=True)
        except ForgeError:
            continue
        if r.status_code != 200 or "html" not in (
                r.headers.get("content-type") or "").lower():
            continue
        html = getattr(r, "text", "")
        if state is not None:
            # Before the floors, not after. "Every candidate, passed or failed,
            # deposits what its fetch revealed" is the whole point of the
            # hinge — and a page that fails the software gate is exactly the
            # kind that still carries a link to where the real docs are.
            state.record(getattr(r, "url", "") or origin, html)
        text = len(_visible_text(html))
        if text < MIN_PROBE_TEXT:
            continue
        # Owning the word is not the same as being the software. Something on
        # the page has to say "this is a code project" before the domain counts
        # as the project's, or any common noun resolves to whoever bought it.
        why = _looks_like_software(html, slug)
        if not why:
            continue
        landed = getattr(r, "url", "") or origin
        live.append((_domain_score(origin, landed, html, slug), text, label, landed, why))

    # A project can own several of these and put different things on them:
    # `kubernetes.dev` is the contributor portal and `kubernetes.io` the
    # documentation, while `terraform.com` is a different company entirely.
    # Rank on deliberate evidence first and volume of text only as a tiebreak.
    live.sort(reverse=True)

    out: list[Candidate] = []
    for _score, _text, label, landed, why in live[:explore]:
        # The homepage is usually marketing with the docs one click away, so
        # the docs root under it outranks it.
        for found in probe_docs_root(landed, fetcher):
            found.source = f"domain:{label}/{found.source}"
            found.confidence = min(0.97, found.confidence + 0.02)
            out.append(found)
        out.append(Candidate(landed, f"domain:{label}", 0.75,
                             f"{_host(landed)} is the project's own domain and "
                             f"carries {why}"))
    return out


def from_domains(name: str, fetcher: Fetcher, state=None) -> list[Candidate]:
    """Look for the project's own site before asking anyone else.

    Measured, this is the whole ballgame: every correct resolution in the audit
    came from the project's own domain, and every wrong one came through a
    registry. Registries answer "what package is named X", which is a different
    question from "what is the technology X" — and when the two disagree,
    `terraform` is an unrelated static-site tool and `kubernetes` is a Python
    client library.
    """
    slug = normalise(name)
    if not slug or len(slug) < 2:
        return []
    return _probe_origins([(tld, f"https://{slug}.{tld}") for tld in NAME_TLDS],
                          slug, fetcher, state=state)


# ─────────────────────────────────────────────────────────────
# L3 — name shapes
# ─────────────────────────────────────────────────────────────
#: Concatenation gets one extra TLD because it is the highest-yield shape and
#: several large projects sit on a country-code domain (huggingface.co).
SHAPE_TLDS = NAME_TLDS + ("co",)

#: Tokens that are grammar rather than name. "ruby on rails" is published at
#: rubyonrails.org so they are kept when concatenating, but a preposition is
#: never a vendor or a product on its own.
_GLUE = {"on", "of", "for", "the", "and", "in"}

#: Subdomains vendors put developer documentation behind.
DEV_HOSTS = ("docs", "developer", "developers")


def name_shapes(name: str) -> list[tuple[str, str]]:
    """Origins to try for a multi-word name, most likely first.

    A multi-word name carries structure that one slug throws away: the first
    token is usually the vendor and the rest the product, and vendors publish
    on a small set of predictable shapes. `from_domains` already tries the
    hyphenated slug — which is why `react hook form` resolves today and
    `apache airflow` does not. Nobody registered apache-airflow.org; they put
    it at airflow.apache.org.

    Returns `(label, url)` pairs; the label ends up in `resolved_via`.
    """
    slug = normalise(name)
    tokens = [t for t in slug.split("-") if t]
    if len(tokens) < 2:
        return []          # single-word names are from_domains' job, already done

    concat = "".join(tokens)
    first, last = tokens[0], tokens[-1]
    rest = "".join(tokens[:-1])           # everything before the final token
    tail_hyphen = "-".join(tokens[1:])    # everything after the first

    shapes: list[tuple[str, str]] = []

    # 1. Everything run together — opentelemetry.io, rubyonrails.org,
    #    godotengine.org, unrealengine.com. The highest-yield shape by far.
    shapes += [(f"concat.{tld}", f"https://{concat}.{tld}") for tld in SHAPE_TLDS]

    # 2. Product as a subdomain of vendor — airflow.apache.org, ui.shadcn.com,
    #    code.visualstudio.com.
    if rest and last not in _GLUE:
        shapes += [(f"product.vendor.{tld}", f"https://{last}.{rest}.{tld}")
                   for tld in NAME_TLDS]

    # 3. Product as a path under the vendor — tanstack.com/query.
    if tail_hyphen and first not in _GLUE:
        shapes += [(f"vendor.{tld}/product", f"https://{first}.{tld}/{tail_hyphen}")
                   for tld in NAME_TLDS]

    # 4. A vendor's developer portal — docs.github.com/actions,
    #    developer.hashicorp.com/terraform.
    if tail_hyphen and first not in _GLUE:
        for host in DEV_HOSTS:
            shapes += [(f"{host}.vendor.{tld}/product",
                        f"https://{host}.{first}.{tld}/{tail_hyphen}")
                       for tld in ("io", "com", "org")]
        # Some vendors repeat the vendor in the path rather than dropping it:
        # docs.spring.io/spring-boot, not docs.spring.io/boot.
        shapes += [(f"docs.vendor.{tld}/name",
                    f"https://docs.{first}.{tld}/{slug}") for tld in ("io", "com")]

    return shapes


def from_name_shapes(name: str, fetcher: Fetcher, state=None,
                     budget=None) -> list[Candidate]:
    """L3. Multi-word names, through exactly the gate everything else uses."""
    shapes = name_shapes(name)
    if not shapes:
        return []
    if budget is not None:
        # Never spend the whole allowance on guesses: the laps after this are
        # cheaper per candidate and likelier to be right.
        shapes = shapes[:max(0, budget.requests_left - 8)]
        budget.charge(len(shapes))
    if not shapes:
        return []
    return _probe_origins(shapes, normalise(name), fetcher, state=state)


# ─────────────────────────────────────────────────────────────
# Verification
# ─────────────────────────────────────────────────────────────
#: How much of a page to read when checking it is the right library. The name
#: should appear early — in the title, a heading, or the first code sample.
VERIFY_WINDOW = 40_000

#: How many times a page must name the package before it counts as documenting
#: it. One mention is noise; a real docs page repeats it constantly.
MIN_MENTIONS = 3


#: Install commands, per ecosystem. A page that tells you how to install the
#: package is a page about that package, in that ecosystem — which is the
#: distinction `htmx` needed: `npm i htmx` and `cargo add htmx` are different
#: projects that happen to share a word.
_INSTALL = (
    ("npm", r"(?:npm|pnpm|bun)\s+(?:i|add|install|create)\s+(?:-\w+\s+)*"),
    ("npm", r"yarn\s+add\s+"),
    ("pypi", r"(?:pip|pip3|uv pip|poetry add|conda install)\s+(?:install\s+)?"),
    ("crates", r"cargo\s+add\s+"),
    ("go", r"go\s+get\s+(?:[\w.\-/]+/)?"),
)


def _owns_the_name(url: str, slug: str) -> bool:
    """Is the name a whole label of this host?

    `htmx.org`, `kubernetes.io`, `docs.pydantic.dev`, `fastapi.tiangolo.com` —
    all the project itself. `github.com/sintaxi/terraform` and `docs.rs/htmx`
    are not, and that single distinction separates every correct answer in the
    audit from every wrong one. Owning a label in the hostname is a far
    stronger claim on a bare name than being one package in one namespaced,
    first-come registry.
    """
    if not slug or is_forge(url):
        return False
    labels = _host(url).split(".")
    flat = slug.replace("-", "")
    if any(label == slug or label.replace("-", "") == flat for label in labels):
        return True

    # A multi-word name may be spread across labels rather than run into one:
    # `apache airflow` lives at airflow.apache.org and `visual studio code` at
    # code.visualstudio.com. Requiring EVERY token to appear in the hostname is
    # a stricter test than the single-label match above, not a looser one — it
    # is what makes L3's shapes reachable without softening the bar for
    # anything that already resolves.
    tokens = [t for t in slug.split("-") if t]
    if len(tokens) < 2:
        return False
    host_labels = [l for l in labels if l not in ("www", "com", "org", "io",
                                                  "dev", "net", "co")]
    joined = "".join(host_labels)
    return all(token in joined for token in tokens)


def _install_line(body: str, slug: str) -> str:
    """The ecosystem an install command on this page names, or ""."""
    for eco, prefix in _INSTALL:
        # The package may be scoped or path-qualified; anchor on the bare name.
        if re.search(prefix + r"[\"'@\w./\-]*\b" + re.escape(slug) + r"\b",
                     body, re.I):
            return eco
    return ""


_REPO_PATH = re.compile(
    r"https?://(?:www\.)?(?:github|gitlab|bitbucket)\.com/([\w.\-]+)/([\w.\-]+)", re.I)


def _repo_identity(body: str, slug: str) -> str:
    """A source repository on this page whose own path names this project.

    The strongest identity claim a page can make, and the one nothing read
    before. `tanstack.com/query` links to `github.com/TanStack/query`: owner
    and repository together account for every token in the name, which is a
    statement by the code owner and not a coincidence of hostnames.

    Deliberately segment-exact. Substring matching would let an unrelated
    project called Flask Lists claim `flask` through `github.com/flaskio/web`,
    which is the measured failure this is meant to close rather than widen —
    9 of 20 names resolved to the wrong project, and every one of them owned a
    hostname while none of them owned a matching repository.
    """
    tokens = {t for t in slug.split("-") if t}
    if not tokens:
        return ""

    def words(text: str) -> set[str]:
        return {s for s in re.split(r"[-_.]+", text.lower()) if s}

    for owner, repo in _REPO_PATH.findall(body or ""):
        repo = re.sub(r"\.git$", "", repo)
        repo_words, owner_words = words(repo), words(owner)

        # Containment has to hold BOTH ways, and the outward direction is the
        # one that matters. Requiring only that the repository covers the name
        # lets any longer repository claim it: npm search for "aws lambda"
        # turns up `awslabs/aws-lambda-invoke-store`, whose name contains both
        # tokens and is not AWS Lambda's documentation. Measured — it is
        # exactly how the search lap produced three confident wrong answers the
        # moment it was switched on.
        if not repo_words <= tokens:
            continue
        if tokens <= (repo_words | owner_words | {owner.lower(), repo.lower()}):
            return f"{owner}/{repo}"
    return ""


def identity_signals(candidate: Candidate, name: str, body: str,
                     facts: dict | None = None) -> list[str]:
    """Independent reasons to believe this page documents *this* project.

    Counting how often a page says a word measures its topic, not its
    identity: a page about any project called terraform says "terraform"
    constantly. What distinguishes projects is agreement between sources that
    did not consult each other — the host owning the name, an install line in
    the right ecosystem, a link back to the repository the registry declared.
    """
    slug = normalise(name)
    facts = facts or {}
    text = re.sub(r"<[^>]+>", " ", body)
    found: list[str] = []

    # A project's domain may redirect off itself — terraform.io lands on
    # developer.hashicorp.com — so how we arrived counts, not just where.
    owns = _owns_the_name(candidate.url, slug)
    if facts.get("via_domain") or owns:
        found.append("own-domain")

    # `docs.astro.build` is the project's own documentation host, and that is
    # a statement about identity that survives the page rendering nothing
    # without JavaScript. Deliberately conditional on owning the name:
    # `docs.rs/htmx` is also a "docs." host, and it is a Rust crate registry
    # hosting somebody else's package, not htmx's documentation.
    if owns and _is_docs_host(candidate.url):
        found.append("docs-host")

    eco = _install_line(text, slug)
    if eco:
        wanted = facts.get("ecosystem")
        found.append(f"install:{eco}" if not wanted or wanted == eco
                     else f"install-mismatch:{eco}")

    repo = (facts.get("repository") or "").rstrip("/")
    if repo:
        path = urlparse(repo).path.strip("/").lower()
        if path and path in body.lower() and candidate.url.rstrip("/") != repo:
            found.append("repo-backlink")

    home = facts.get("homepage") or ""
    if home and _host(home) and _host(home) == _host(candidate.url):
        found.append("registry-agreement")

    if _repo_identity(body, slug):
        found.append("repo-identity")

    hay = normalise(text)
    hits = hay.count(slug) if slug else 0
    flat = slug.replace("-", "")
    if slug and flat != slug:
        # A multi-word project almost always writes its own name run together —
        # "OpenTelemetry", "RubyOnRails" — which normalises to the flat form and
        # never matches the hyphenated slug. Counting only the slug therefore
        # missed every mention on the project's own front page, which is exactly
        # where the name is stated most often. Taking the better of the two
        # counts corrects an undercount; it does not lower the bar, because
        # MIN_MENTIONS and the STRONG requirement are both untouched.
        hits = max(hits, hay.count(flat))
    if hits >= MIN_MENTIONS:
        found.append(f"names-it:{hits}")
    return found


#: Signals that identify a project rather than merely describe one. Mention
#: counts are deliberately excluded: they are corroboration, never proof.
STRONG = ("own-domain", "docs-host", "install:", "repo-backlink",
          "registry-agreement", "repo-identity")


def is_identified(signals: list[str]) -> bool:
    """Two independent sources agreeing, or one strong source plus the name.

    A wrong answer is survivable. A wrong answer stamped `verified` is not,
    because the caller has been given a reason to stop checking — so the bar
    is agreement, not familiarity.
    """
    strong = [s for s in signals if s.startswith(STRONG)]
    named = any(s.startswith("names-it") for s in signals)
    if any(s.startswith("install-mismatch") for s in signals) and len(strong) < 2:
        return False
    return len(strong) >= 2 or (len(strong) == 1 and named)


def verify(candidate: Candidate, name: str, fetcher: Fetcher,
           facts: dict | None = None, state=None) -> Candidate:
    """Confirm a page documents the project that was asked for.

    Without this the resolver is a more elaborate guess: a plausible-looking
    URL gets harvested and summarised, and nobody finds out it was the wrong
    project.
    """
    try:
        body = fetcher.text(candidate.url, timeout=PROBE_TIMEOUT)[:VERIFY_WINDOW]
    except ForgeError as e:
        candidate.verified = False
        candidate.reason = f"could not be read: {e}"
        return candidate

    if state is not None:
        # The hinge, and the reason it is here rather than at the call sites:
        # this function is the only place that holds a candidate's page, and
        # it used to read it once and throw it away. A candidate that fails is
        # exactly the one whose page is worth keeping — it frequently links
        # straight to the documentation that would have passed.
        state.record(candidate.url, body)

    slug = normalise(name)
    if not slug:
        candidate.verified = False
        candidate.reason = "no usable name to check against"
        return candidate

    candidate.signals = identity_signals(candidate, name, body, facts)
    candidate.verified = is_identified(candidate.signals)
    if candidate.verified:
        candidate.reason = "identified by " + ", ".join(candidate.signals)
    elif candidate.signals:
        candidate.reason = (
            f"not enough to identify {name!r} — only {', '.join(candidate.signals)}. "
            f"Naming a project is not the same as being it.")
    else:
        candidate.reason = f"nothing on the page identifies it as {name!r}"
    return candidate


# ─────────────────────────────────────────────────────────────
# The chain
# ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────
# L4 — evidence
# ─────────────────────────────────────────────────────────────
#: Keyless, rate-limited, and the only forge with a stable public field saying
#: where a project's documentation lives. GitLab's API exposes no equivalent
#: `homepage`, so a repository there contributes its backlink but not this.
_GITHUB_REPO = re.compile(r"https?://(?:www\.)?github\.com/([\w.\-]+)/([\w.\-]+)", re.I)

#: How much of the accumulated evidence to spend requests on. Each of these is
#: a fetch, and the lap after this one is cheaper per candidate.
MAX_EVIDENCE_REPOS = 3
MAX_EVIDENCE_LINKS = 4


def from_evidence(name: str, state, fetcher: Fetcher, budget=None) -> list[Candidate]:
    """L4. Read back what the candidates that already failed gave away.

    This is the lap that makes the ladder a loop rather than a list. `verify()`
    fetches a candidate, fails it, and would otherwise discard the page —
    including the repository backlink whose `homepage` field is the code owner
    stating, in public, where the documentation lives. `ResolveState` has been
    recording that all along; nothing read it back until here.

    Three kinds of evidence, in descending order of how much they claim:

    1. A repository's declared homepage. The strongest: it is the project
       saying where its own documentation is.
    2. An outbound link that leaves the candidate's host and looks like
       documentation — often a project pointing at its own docs SaaS.
    3. A canonical URL that points somewhere else, which is a site saying "the
       real version of this page lives there".
    """
    if state is None:
        return []
    out: list[Candidate] = []
    slug = normalise(name)

    for repo in list(state.repos_seen)[:MAX_EVIDENCE_REPOS]:
        if budget is not None and budget.exhausted:
            break
        found = _GITHUB_REPO.match(repo)
        if not found:
            continue
        owner, project = found.group(1), re.sub(r"\.git$", "", found.group(2))
        # Only ask about repositories whose own name relates to what was asked.
        # Every docs page links to something on GitHub; most of it is unrelated.
        if slug and slug.replace("-", "") not in f"{owner}{project}".lower().replace("-", ""):
            continue
        if budget is not None:
            budget.charge()
        data = _json(fetcher, f"https://api.github.com/repos/{owner}/{project}")
        home = ((data or {}).get("homepage") or "").strip()
        if home.startswith(("http://", "https://")):
            out.append(Candidate(
                home, "evidence:repo-homepage", 0.88,
                f"github.com/{owner}/{project} declares its homepage as {home}"))

    for href in list(state.outbound)[:MAX_EVIDENCE_LINKS]:
        out.append(Candidate(href, "evidence:outbound", 0.6,
                             "linked as documentation from a candidate page"))

    for source, canonical in list(state.canonical.items())[:MAX_EVIDENCE_LINKS]:
        if _host(canonical) and _host(canonical) != _host(source):
            out.append(Candidate(
                canonical, "evidence:canonical", 0.7,
                f"{_host(source)} names {canonical} as the canonical location"))

    seen, unique = set(), []
    for cand in out:
        key = cand.url.rstrip("/")
        if key not in seen:
            seen.add(key)
            unique.append(cand)
    return unique


# ─────────────────────────────────────────────────────────────
# L0 — memory
# ─────────────────────────────────────────────────────────────
#: A resolution is a fact about where a project publishes, and projects move
#: rarely. A refusal is a fact about what we could not find today, which is a
#: much weaker claim with a much shorter life — a site can add the evidence
#: tomorrow, so refusals are retried an order of magnitude sooner.
CACHE_TTL = 30 * 86400
REJECT_TTL = 7 * 86400


def _cache_file() -> Path:
    return Path(os.environ.get("DOCSFORGE_RESOLVE_CACHE")
                or Path.home() / ".docsforge" / "resolutions.json")


def _load_cache() -> dict:
    try:
        return json.loads(_cache_file().read_text(encoding="utf-8"))
    except Exception:
        # A corrupt or unreadable cache must never be the reason a resolution
        # fails; it is an optimisation, not a source of truth.
        return {}


def _save_cache(data: dict) -> None:
    try:
        path = _cache_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except Exception:
        pass


def recall(name: str) -> Resolution | None:
    """A remembered resolution for this name, or None. Costs no requests."""
    entry = _load_cache().get(normalise(name))
    if not entry:
        return None
    age = time.time() - entry.get("at", 0)
    if age > (CACHE_TTL if entry.get("url") else REJECT_TTL):
        return None

    result = Resolution(name=name, ecosystem=entry.get("ecosystem", ""))
    result.resolved_via = entry.get("resolved_via", "") or "memory"
    if not entry.get("url"):
        result.note = entry.get("note") or f"Recently could not resolve {name!r}."
        return result
    cand = Candidate(entry["url"], "memory", 0.95, entry.get("evidence", ""),
                     True, entry.get("reason", "remembered from an earlier run"))
    cand.signals = list(entry.get("signals") or [])
    result.candidates = [cand]
    result.best = cand
    result.note = entry.get("note", "")
    return result


def remember(name: str, result: Resolution) -> None:
    """File what this resolution found, successful or not."""
    data = _load_cache()
    best = result.best
    data[normalise(name)] = {
        "at": time.time(),
        "url": best.url if best else "",
        "evidence": best.evidence if best else "",
        "reason": best.reason if best else "",
        "signals": list(best.signals) if best else [],
        "ecosystem": result.ecosystem,
        "resolved_via": result.resolved_via,
        "note": result.note,
    }
    _save_cache(data)


def forget_resolution(name: str = "") -> str:
    """Drop one remembered resolution, or all of them.

    Ships with the cache rather than after it. A cache the user cannot clear is
    a trap: the one moment you need it gone is when it is confidently wrong.
    """
    data = _load_cache()
    if not name:
        _save_cache({})
        return f"Forgot {len(data)} remembered resolution(s)."
    slug = normalise(name)
    if data.pop(slug, None) is None:
        return f"Nothing remembered for {name!r}."
    _save_cache(data)
    return f"Forgot the remembered resolution for {name!r}."


# ─────────────────────────────────────────────────────────────
# L5 — fuzzy search
# ─────────────────────────────────────────────────────────────
def from_search(name: str, fetcher: Fetcher, budget=None) -> list[Candidate]:
    """Registry fuzzy search, then an optional configured hook.

    Never a search engine's HTML endpoint: brittle, against their terms, and a
    poor look for a resolver whose entire pitch is trustworthiness. These are
    documented JSON APIs that the exact-name laps simply do not use.
    """
    out: list[Candidate] = []
    query = quote(name.strip())
    if not query:
        return out
    if budget is not None and budget.exhausted:
        return out

    npm = _json(fetcher, f"https://registry.npmjs.org/-/v1/search?text={query}&size=4")
    for obj in (npm or {}).get("objects", [])[:4]:
        pkg = obj.get("package") or {}
        links = pkg.get("links") or {}
        for field_name in ("homepage", "repository"):
            url = links.get(field_name) or pkg.get(field_name)
            if url:
                out.append(Candidate(url, f"search:npm/{pkg.get('name','')}",
                                     _score(url, field_name) * 0.8,
                                     f"npm search matched {pkg.get('name','')!r}"))

    crates = _json(fetcher, f"https://crates.io/api/v1/crates?q={query}&per_page=4")
    for crate in (crates or {}).get("crates", [])[:4]:
        for field_name in ("documentation", "homepage", "repository"):
            url = crate.get(field_name)
            if url:
                out.append(Candidate(url, f"search:crates/{crate.get('name','')}",
                                     _score(url, field_name) * 0.8,
                                     f"crates search matched {crate.get('name','')!r}"))

    hook = os.environ.get("DOCSFORGE_SEARCH", "").strip()
    if hook:
        try:
            payload = _json(fetcher, hook.replace("{query}", query))
        except ForgeError:
            payload = None
        items = payload if isinstance(payload, list) else (payload or {}).get("results", [])
        for item in (items or [])[:4]:
            url = item if isinstance(item, str) else (item or {}).get("url", "")
            if url:
                out.append(Candidate(url, "search:hook", 0.6,
                                     "returned by DOCSFORGE_SEARCH"))
    return out


def _ladder_tail(result: Resolution, name: str, fetcher: Fetcher, state, budget,
                 verify_best: bool, limit: int) -> Resolution:
    """The laps that run only once domains and registries have both failed.

    Kept separate because there are two ways to arrive here — no registry knew
    the name at all, or every candidate failed verification — and both deserve
    the rest of the ladder rather than one of them being a dead end. That
    asymmetry is what made F6 look like a resolution problem when it was
    partly a control-flow one.
    """
    if not verify_best:
        return result

    # ── L3: the shape of the name ──
    if budget is None or not budget.exhausted:
        shaped = from_name_shapes(name, fetcher, state=state, budget=budget)
        for cand in shaped:
            # No `via_domain` here. Whether a shape host really owns the name
            # is precisely the question, so `_owns_the_name` answers it rather
            # than the lap asserting it on the way in.
            verify(cand, name, fetcher, {}, state=state)
            if cand.verified:
                result.candidates = (shaped + result.candidates)[:limit]
                result.best = cand
                result.resolved_via = f"shape:{cand.source}"
                result.note = (
                    f"Resolved {name!r} from the shape of its name: vendors "
                    f"publish on a small set of predictable hosts, and no "
                    f"registry lists this one."
                )
                return result
        if shaped:
            result.candidates = (result.candidates + shaped)[:limit]

    # ── L4: evidence the failed candidates already gave away ──
    if budget is None or not budget.exhausted:
        evidence = from_evidence(name, state, fetcher, budget=budget)
        # A declared homepage is usually marketing with the docs one click
        # away, so the docs root beneath it outranks it — the same treatment a
        # registry homepage gets.
        probed: list[Candidate] = []
        for cand in evidence:
            if cand.source == "evidence:repo-homepage" and not _looks_like_docs(cand.url):
                for found in probe_docs_root(cand.url, fetcher):
                    found.source = f"evidence:repo-homepage/{found.source}"
                    found.confidence = min(0.95, found.confidence + 0.02)
                    probed.append(found)
        evidence = probed + evidence

        for cand in evidence:
            verify(cand, name, fetcher, {}, state=state)
            if state is not None:
                state.record(cand.url)
            if cand.verified:
                result.candidates = (evidence + result.candidates)[:limit]
                result.best = cand
                result.resolved_via = f"evidence:{cand.source}"
                result.note = (
                    f"Resolved {name!r} from evidence left by candidates that "
                    f"themselves failed — the page that could not be verified "
                    f"still said where to look next."
                )
                return result
        if evidence:
            result.candidates = (result.candidates + evidence)[:limit]

    # ── L5: fuzzy search, the last and least certain lap ──
    if budget is None or not budget.exhausted:
        searched = from_search(name, fetcher, budget=budget)
        for cand in searched:
            if budget is not None:
                budget.charge()
            verify(cand, name, fetcher, {}, state=state)
            if state is not None:
                state.record(cand.url)
            if cand.verified:
                result.candidates = (searched + result.candidates)[:limit]
                result.best = cand
                result.resolved_via = "search"
                result.note = (
                    f"Resolved {name!r} through registry search rather than an "
                    f"exact name match. The identity gate is the same one every "
                    f"other lap uses."
                )
                return result
        if searched:
            result.candidates = (result.candidates + searched)[:limit]

    if result.best is None and budget is not None and budget.exhausted:
        result.note = f"{result.note} Resolution {budget.why()}.".strip()
    return result


def resolve(name: str, ecosystem: str = "", fetcher: Fetcher | None = None,
            verify_best: bool = True, limit: int = 6,
            use_memory: bool = True) -> Resolution:
    """Find where `name` documents itself, consulting memory first.

    L0 is a wrapper rather than a lap inside the ladder so that a hit costs
    exactly zero HTTP requests — a cache that still opens a connection to check
    itself is not a cache. `forget_resolution()` clears it.
    """
    if use_memory and verify_best:
        remembered = recall(name)
        if remembered is not None:
            return remembered

    result = _resolve_uncached(name, ecosystem, fetcher, verify_best, limit)

    if use_memory and verify_best:
        remember(name, result)
    return result


def _resolve_uncached(name: str, ecosystem: str = "", fetcher: Fetcher | None = None,
                      verify_best: bool = True, limit: int = 6) -> Resolution:
    """Find where `name` documents itself.

    Returns every candidate with its evidence rather than silently picking one,
    so a caller that disagrees can see why and choose differently.
    """
    result = Resolution(name=name, ecosystem=ecosystem or guess_ecosystem(name))
    own = fetcher is None
    fetcher = fetcher or Fetcher(Options(delay=0.0))
    # The hinge: every candidate, passed or failed, deposits what its fetch
    # revealed where a later lap can read it. And a bound, so a pathological
    # name refuses within a stated budget rather than wandering.
    state = ResolveState(name=name)
    budget = Budget()

    try:
        # 1. The project's own domain. Checked first because the data says so:
        #    it produced every correct answer and none of the wrong ones.
        domain = from_domains(name, fetcher, state=state)
        if verify_best:
            for cand in domain:
                verify(cand, name, fetcher, {"via_domain": True}, state=state)
                if cand.verified:
                    result.candidates = domain[:limit]
                    result.best = cand
                    result.resolved_via = "domain"
                    result.note = (
                        f"Resolved from {name!r}'s own domain. Registries were not "
                        f"consulted: owning the name is the stronger claim, and "
                        f"where the two disagree the registry is usually a "
                        f"different project that shares the word.")
                    return result

        # 2. Registries, as the fallback.
        found, hit = from_registries(name, result.ecosystem, fetcher)
        if hit:
            result.ecosystem = result.ecosystem or hit
        if not found:
            # No registry knows it. That used to end the search, which is what
            # made every multi-word name unreachable — no registry knows
            # "apache airflow" either, and it is very much a real technology.
            # Fall through to the shape lap instead.
            result.candidates = domain[:limit]
            result.note = (
                f"No registry knows {name!r}. If it is private or internal, "
                f"pass the documentation URL directly to harvest_docs."
            )
            return _ladder_tail(result, name, fetcher, state, budget,
                                verify_best, limit)
        found += domain

        # A homepage is worth one round of convention-guessing before use.
        extra: list[Candidate] = []
        for cand in list(found):
            if cand.confidence < 0.9 and not _looks_like_docs(cand.url):
                extra += probe_docs_root(cand.url, fetcher)

        seen, ranked = set(), []
        for cand in sorted(found + extra, key=lambda c: c.confidence, reverse=True):
            key = cand.url.rstrip("/")
            if key in seen:
                continue
            seen.add(key)
            ranked.append(cand)
        result.candidates = ranked[:limit]

        facts = _facts_from(found, result.ecosystem)
        if verify_best:
            for cand in result.candidates:
                verify(cand, name, fetcher,
                       dict(facts, via_domain=cand.source.startswith("domain:")),
                       state=state)
                if cand.verified:
                    result.best = cand
                    result.resolved_via = ("domain" if cand.source.startswith("domain:")
                                           else "registry")
                    # The ecosystem is whichever registry actually produced the
                    # answer, not whichever one happened to reply first: the
                    # same name often exists in several, on different projects.
                    won = cand.source.split(":", 1)[0]
                    if won in REGISTRIES:
                        result.ecosystem = won
                    break
            if result.best is None:
                result.note = (
                    f"Found {len(result.candidates)} candidate(s) for {name!r} but none "
                    f"could be confirmed to document it. Harvesting an unverified page "
                    f"risks storing the wrong project — pass a URL directly if you know it."
                )
                return _ladder_tail(result, name, fetcher, state, budget,
                                    verify_best, limit)
        elif result.candidates:
            result.best = result.candidates[0]
        return result
    finally:
        if own:
            fetcher.close()
