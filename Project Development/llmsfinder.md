# llmsfinder: take the file the site already wrote

Written against the build that implements PROPOSAL-II and PROPOSAL-3 in full —
648 tests passing, 22 skipped.

Every previous proposal has been about crawling better: scope it, measure it,
classify it, overlap it, reason about it. This one is about the case where
crawling is the wrong verb entirely, because the site has already done the work
and published the answer as a file.

The principle is one line:

> **If the site published itself for machines, read that. Crawling is the
> fallback, not the plan.**

---

## 0. The evidence

Measured 31 August 2026 against `adk.dev`, the Agent Development Kit docs.

| URL | Status | Size | Content-Type |
|---|---|---|---|
| `adk.dev/llms.txt` | 200 | 17,262 chars | `text/plain` |
| `adk.dev/llms-full.txt` | 200 | **3,451,848 chars** | `text/plain` |
| `adk.dev/llms-medium.txt` | 404 | — | — |
| `adk.dev/get-started/index.md` | 200 | 1,650 chars | `text/markdown` |
| `adk.dev/agents/models/anthropic/index.md` | 200 | 3,438 chars | `text/markdown` |

And the index itself, parsed:

```
total links : 229
ending .md  : 229      ← 100%
sections    : 5        ← Build Agents, Run Agents, Components, Reference, Community
```

Three ways to acquire the same corpus, and the cost is not close:

| Route | Requests | HTML extraction | Page count |
|---|---|---|---|
| `llms-full.txt` | **1** | none | exact, by construction |
| `.md` twins from the index | **229** | none | exact — the site listed them |
| Crawl | **1,799** | every page | inferred from a frontier |

That 1,799 is not hypothetical. It is what a real harvest of the same product
stored, from the older `google.github.io/adk-docs` host, on 31 August 2026.
Every one of those pages was fetched as HTML, run through nine selectors and a
density score, and converted to Markdown — to arrive at content the project
publishes as Markdown at a known URL.

**Doing 1,799 lossy conversions to reproduce a file you could have downloaded is
the most expensive mistake this system currently makes.** It is more expensive
than the `go.dev` failure, because that one was loud.

---

## 1. The ladder

Acquisition is tried in this order, and stops at the first rung that succeeds.

1. **`llms-full.txt`** (then `llms-medium.txt`) — the site's own complete dump.
   One request. Store it whole.
2. **`llms.txt` whose links are Markdown** — a manifest of `.md` twins. Fetch
   each. No HTML, no extraction, no template guessing.
3. **`llms.txt` whose links are HTML** — still the site's own curated list of
   its pages, which is strictly better than a frontier we inferred. Fetch those
   exact URLs and extract as usual.
4. **Generator manifest** (`search_index.json`, `objects.inv`) — already built.
5. **Sitemap** — already built.
6. **Crawl** — already built, and now genuinely last.

Rungs 1, 4, 5 and 6 exist today. **Rungs 2 and 3 do not, and they are the whole
point of this document.**

---

## 2. Invariants

Continuing the numbering; PROPOSAL-3 ended at 19.

20. **A file the site published about itself outranks anything inferred about
    it.** A sitemap is a hint addressed to crawlers. `llms.txt` is a statement
    addressed to us. Prefer the statement.

21. **A document the site publishes whole is stored whole.** Not chunked, not
    split, not summarised. Read-time relevance narrows what is handed back;
    ingestion never restructures what was published. This is Invariant 6
    applied to a case that predates it.

22. **An index is not documentation.** A file of 229 links is a table of
    contents. Storing it *as* the corpus is the "table of contents stored as
    though it were the documentation" failure the README already warns about —
    and it is what happens today when a site publishes `llms.txt` and no
    fuller sibling.

23. **Completeness from a published manifest is exact, and says so.** When the
    page set came from the site's own list, `expected` is that list's length
    and the coverage claim is the strongest this system can make — stronger
    than a sitemap, which may carry marketing pages and dead URLs.

---

## 3. Three shapes wearing one filename

`llms.txt` is a convention, not a schema, and three genuinely different things
are published under it. Treating them alike is why this is subtle.

**Shape A — the index.** `adk.dev/llms.txt`: 17 KB, 229 links, no prose beyond
one line of description per section. Useless as documentation. Invaluable as a
manifest.

**Shape B — the dump.** `adk.dev/llms-full.txt`: 3.45 MB of actual
documentation, headings and all. This *is* the corpus.

**Shape C — the hybrid.** A short file with real prose *and* links. Rare, and
the one that needs a judgement rather than a rule.

Telling A from B costs nothing and needs no model: **the ratio of link-line
characters to total characters.** An index is almost entirely links; a dump is
almost entirely prose. `adk.dev/llms.txt` is 229 links in 17 KB — about 75
characters per link, which is a link list with nothing else in it. This is the
same shape of measurement as the density score that already decides whether a
page is documentation or navigation, and it should reuse that intuition rather
than invent a second one.

Shape C is where a page falls between the two, and the honest default is to
treat it as a dump *and* follow its links — store the prose, gather the pages,
count both.

---

## 4. What splitting was for, and why it is over

`handle_llms_txt` splits any dump over `SPLIT_ABOVE = 60,000` characters into
between 3 and 4,000 parts, on headings. The reason is in the code and it was a
good one:

> A multi-megabyte dump kept as a single page is technically stored and
> practically unsearchable: every query matches "page 1" and the snippet
> ranking has nothing to choose between.

That was true when it was written. **PROPOSAL-3 Phase 2 removed the condition
that made it true.** `page.search` is now generated over a bounded prefix, and
anything past that bound is indexed section by section through the `section`
table. A 3.45 MB document is storable, and its five-hundredth heading is
findable, without fragmenting the artifact.

So splitting is now a workaround for a limitation that no longer exists — and
it costs something real: it turns one document the publisher wrote into 4,000
documents we invented, with URLs (`…llms.txt#some-anchor`) that we made up and
that nobody can visit.

This is the change the request asks for, stated precisely: **the content is
already Markdown, so stop treating it as a text dump to be chunked and start
treating it as a Markdown document to be stored.** On the file surface it is
already written as `.md`; what changes is that it arrives whole.

---

## 5. What this costs, stated before it is chosen

Storing whole is right, and it has two consequences that must be handled in the
same change or it is a regression wearing a principle.

**One document is one search hit.** `PostgresStore.search` returns one row per
page — deliberately, so a page matching both its own index and one of its
sections is not returned twice. A corpus that *is* one page therefore returns
exactly one result, however many sections matched. For a 3.45 MB document that
is a worse answer than the 4,000 fragments it replaces.

*Resolution:* where a corpus is a single document, search must return
**section** rows rather than the page. The `section` table already holds them
with their heading paths. This is a change to the read path, not to storage,
and it is the price of Invariant 21.

**One document is larger than a read.** `read_knowledge_base` is capped by
`DOCSFORGE_MAX_CHARS` (60,000). Reading back a 3.45 MB page returns its first
60 KB and silently omits 98% of it — an undisclosed subset, which is the exact
dishonesty this project exists to refuse.

*Resolution:* a read of a single-document corpus must be addressed by heading
path, and a read that truncates must say what it left out. Silent truncation
here would be worse than the splitting it replaced.

Both are read-path work. Neither is optional.

---

## 6. Phases

**Phase 1 — stop splitting, and say what was stored.** Remove the chunking from
`handle_llms_txt`; store the dump whole. Fix the two read-path consequences in
§5 in the same phase, because shipping the first without the second is a
regression.
*Ends when a 3.45 MB dump is stored as one document, a query for a term in its
last section returns that section with its heading path, and a read that
truncates discloses the omission.*

**Phase 2 — the index as a manifest.** Detect shape A by link density. Parse the
links. Where they are `.md`, fetch them directly — no HTML path at all. Where
they are HTML, hand them to the existing extraction as an exact page list.
`expected` is the list length.
*Ends when `adk.dev` harvests 229 pages in 229 requests with zero HTML
extractions, and reports coverage as exact.*

**Phase 3 — discovery.** Probe the ladder in order from any starting URL:
`llms-full.txt` and `llms.txt` in the docs directory and at the origin, before
the sitemap and long before a crawl. Record which rung answered, in the result.
*Ends when a harvest of a site with a full dump makes one content request, and
the result says which rung it came from.*

---

## 7. Acceptance criteria

- A site with `llms-full.txt` is harvested in **one** content request.
- A site with a Markdown index is harvested in **one request per listed page**,
  and none of them are parsed as HTML.
- A 3.45 MB single document is stored whole, and a term in its final section is
  findable with its heading path.
- A read that cannot return a whole document says so, naming what it omitted.
- An `llms.txt` that is purely an index is **never** stored as the corpus.
- `expected` from a published manifest equals the manifest length, exactly.
- A site with no `llms.txt` behaves exactly as it does today.
- The rung that answered is recorded in the result, so a coverage claim can be
  traced to its evidence.

---

## 8. What this deliberately does not do

- **No new model calls.** Every decision here is a ratio or a suffix check.
  Shape detection is arithmetic, and it stays arithmetic.
- **No trusting `llms.txt` about identity.** A file at a domain claims that
  domain documents something; it does not establish *which* project. The
  identity gate still applies unchanged, and `ISSUES.md` R1 is untouched by
  this — a name-squatter with a tidy `llms.txt` is still a name-squatter.
- **No rewriting the dump.** Whatever the site published is what is stored,
  byte for byte after decoding. We do not reformat, re-heading, or "clean" it.
- **No abandoning the crawl.** It stays, it stays correct, and it stays the
  answer for the large majority of documentation sites that publish no such
  file.

---

## 9. Why this is worth doing before anything else on the list

Every open item in `ISSUES.md` is about crawling well: identity, density,
templates, breadth, coverage. This proposal makes a growing fraction of
harvests skip that machinery altogether — and the fraction is growing because
publishing `llms.txt` is becoming standard practice.

The cheapest page to extract correctly is the one that arrived as Markdown.
