# Open issues

Difficulties and deferred decisions hit while building PROPOSAL-II. All seven
phases are now built; what is left here is judgement calls and calibration, not
unbuilt machinery. Items resolved along the way are kept at the bottom rather
than deleted, because what a list like this stops saying matters too.

Each entry says what is wrong, how it was found, and what it would take to fix.
Entries marked **DECISION** need a human answer, not just work — they change
something PROPOSAL-II declares fixed.

Status key: `open` · `deferred` (deliberate, revisit later) · `wontfix` ·
`fixed` (with the change that closed it, so the entry still teaches something)

---

## PROPOSAL-3 acceptance gaps

Two of PROPOSAL-3 §6's own acceptance criteria are **not** met by the four
phases. §6 also asks that `ISSUES.md` gain no entries across the four phases,
so writing these down breaks that rule — deliberately. A criterion met by not
recording a known gap is met dishonestly, and this file exists precisely so
that what a project has not done stays as visible as what it has.

### P1 — an interrupted harvest leaves nothing readable, not 60% · **DECISION** · open

§6 asks that "a harvest interrupted at 60% leaves 60% of its pages readable and
the previous version intact". Only the second half is true. Blue/green writes
put a harvest in `state = 'harvesting'`, which every read path filters out, so
an interrupted harvest is durable on disk and invisible to readers until it
settles.

This is not an oversight in the implementation; it is a contradiction inside
§6. The same section, and Invariant 17, require that the previously stored
version stay intact and that an in-flight harvest never be mistaken for a
finished one — and a partial version that readers can see is exactly the
"undisclosed subset" the whole product refuses. `test_a_harvest_in_progress_is_
not_visible_to_readers` pins the behaviour that was chosen.

**The decision to make:** whether a partial harvest should be readable at all,
and if so how a reader is stopped from mistaking 60% of a manual for the whole
of one. A `state = 'partial'` that reads only through an explicitly opted-in
call would satisfy both halves; nothing weaker does.

### P2 — harvest progress does not survive the process · open

§6 asks that "`list_knowledge_base` reports progress that survives killing the
process". `harvest_jobs._JOBS` is an in-process dictionary, so it does not: a
restart loses every running and recently-finished job, and a harvest killed
mid-flight leaves no trace in the listing at all.

The pages themselves survive — that is Phase 1, and it is the part that
matters. What is lost is the *report* of them. Fixing it means persisting the
job table beside the store rather than in memory, which is a small change made
awkward by the file backend having no obvious place to put it.

---

## Resolution

### R1 — `own-domain + names-it` lets a name-squatter through · **DECISION** · open

**Mitigated, not closed, by PROPOSAL-3 Phase 4.** Decision point 3 asks a
model whether a page documents *this project* or a different one sharing the
name — the distinction arithmetic cannot draw, because the evidence it counts
is identical either way. The consultation may only **veto**: a host the
algorithmic gate refused is never re-admitted by asking, so reasoning makes
the gate stricter and never looser, and turning it on cannot make this worse.
Cached per host, so a 40-page corpus costs one call.

It stays open and stays a **DECISION**, for two reasons. Reasoning is off by
default, so the default path is unchanged and still admits the parked domain.
And the underlying question — whether Invariant 1 should require project
identity rather than name evidence — is a human's to answer, not something a
consultation settles.

The one remaining path by which a wrong project is confidently identified. A
hostname containing the name, plus the name appearing three times, is enough.

Measured survivors: `flask` → **flask.io** (an unrelated to-do app, "Flask
Lists"), `polars` → **polars.dev** (a third-party PySpark site), `github
actions` → **githubactions.com** (an 8 KB parked page). The last only became
reachable once L3's concatenation shape started working — reaching further finds
wrong answers as readily as right ones.

Closing it means changing `is_identified()`, which **Invariant 1 declares
unchanged**. The invariant forbids a *lower* bar; raising it is not forbidden but
is a deliberate change to a stated invariant, so it needs an explicit decision.
Options, in increasing blast radius, are in `FINDINGS-C.md`.

**Two feeder paths closed, without touching `is_identified()`.** Both were bugs
in what counted as `own-domain`, not changes to how many signals are required,
so Invariant 1 is untouched:

- A domain probe that **redirected onto a code host** kept its ownership claim.
  `mojo.dev` redirects to `github.com/gdejohn/procrastination` — a Java library,
  since Maven plugins are also called "mojos" — and that plus a registry package
  named `mojo` was two strong signals. The gate stamped `verified` on a page
  that never says the word. `_owns_the_name` already refused forges outright;
  arriving by redirect does not change who owns the host.
- A language was refused a claim on the **`lang` domain it publishes from**.
  Names that are common words take the suffix for exactly that reason, and
  nothing probed them, so `zig` resolved to an npm templating library and `nim`
  to an unrelated repository — both *verified*, each on a registry package that
  agreed with itself.

Measured after: `zig` → `ziglang.org`, `nim` → `nim-lang.org/documentation.html`,
`mojo` → `mojolang.org`, each on `own-domain` plus mentions. **R1's own four
survivors have not been re-measured**, and this does not claim them.

What remains is what R1 has always been: a squatter that genuinely owns a
hostname carrying the name, and says the name enough times. Neither fix reaches
that.

### R2 — `_looks_like_software` is satisfied by a single footer forge link · open

The gate meant to stop `astro` resolving to an astrology site. Almost every
parked and marketing page now carries a GitHub link, so the gate passes them.
Cheaper than R1 and probably fixes most of R1's cases: require more than one
software marker, or discount a page whose only marker is a footer link.

### R3 — `resolve()` returns the first verified candidate, not the best-evidenced · open

The verify loop breaks on the first candidate that passes, in shape order. So
`githubactions.com` (concat shape, tried early) beats `docs.github.com/actions`
(portal shape, tried later) even though the latter would carry more signals.
Ranking verified candidates by signal strength would fix it. Behaviour change;
needs its own measurement run.

### R4 — nine multi-word names still refuse · deferred

`spring boot`, `next auth`, `framer motion`, `argo cd`, `elastic search`,
`azure blob storage`, `google cloud storage`, `aws lambda`,
`hugging face transformers`, `postgres full text search`, `unreal engine`.

Several are honest refusals: `framer motion` renamed to motion.dev and no longer
names Framer; the cloud-provider names live under deep paths on enormous portals
(`learn.microsoft.com/azure/storage/blobs`) that no name shape reaches. Would
need a vendor-portal map or a real search backend (L5's `DOCSFORGE_SEARCH` hook
exists for exactly this and is unconfigured).

### R5 — resolution can land on a marketing homepage rather than the docs root · open

`numpy` → `numpy.org` rather than `numpy.org/doc/stable/`; `tanstack query` →
`tanstack.com` rather than the Query docs. `probe_docs_root` looks for a small
set of conventional paths and `/doc/stable/` is not among them. Related to R3:
the homepage verifies first and the loop stops.

### R6 — a refusal this machine caused was filed as a fact about the name · **correctness** · fixed

**Fixed** by `learned_nothing()`. `verify()` distinguishes two refusals and the
cache did not: a candidate whose page was *read* and found to document something
else is a real finding about the name; a candidate that could not be **fetched**
says only that the network could not answer.

Found by running a real resolution and getting `best: None, via: memory,
candidates: 0` in one second. The cache held `mojo resolved=False age=12.1h
"Found 6 candidate(s) but none could be confirmed"` — written during the NAT64
outage, when every fetch was refused as a private address. `REJECT_TTL` is seven
days; the cause was fixed within one. Six days of confident wrong answers, from
one bad afternoon of networking.

`remember()` now files nothing when every candidate was refused with
`could not be read`. A partly-unreachable refusal is still remembered, because
something was learned.

### R7 — a fix could not reach the cache · **correctness** · fixed

The sharper version of R6, and it bites *successes*. When R1's forge bug
resolved `mojo` to a Java library, that answer was filed as a **success** — so
`CACHE_TTL` would have served it for thirty days, outliving its own fix by four
weeks, on the very name whose failure prompted the fix. `learn_technology`
returned it in one second without a request.

Found the same way both times: by looking at what a live run actually did rather
than at whether the tests were green. They were.

**Fixed** with a `RULES` stamp. An entry records which set of identity rules
decided it, `recall` discards anything else, and entries written before the
stamp existed carry no `rules` key and are discarded on sight — which is the
intended effect, since every one of them predates two changes to those rules.
Bump `RULES` whenever the gate changes.

The general lesson, worth more than either fix: **a cache is a claim with a
provenance, and code is part of that provenance.** Any TTL long enough to be
useful is long enough to outlive the bug that wrote the entry.

---

## Extraction

### E1 — the template signature was too fine to cluster on · RESOLVED in Phase D

PROPOSAL-II §2.2 argues a rule should be learned per template because "sites
have a handful of layouts". Measured with the proposal's own signature
(three-level ancestry + coarse shape): **0.42 distinct templates per page** —
22 signatures across 40 pages for both pydantic and terraform. That is nearly
per-page, the cost the design set out to avoid.

**Cause found:** the shape half. All 22 Terraform signatures shared an
*identical* ancestry; the heading and code buckets were splitting them. Those
describe what a page says, not how it is built.

**Fixed:** the signature is now the layout ancestry alone, with CSS-module build
hashes stripped so it survives a redeploy. The shape is still recorded, just not
as part of the template's identity. Re-measured over the same 452 pages:
**0.46 → 0.08 templates per page**, with most sites collapsing to one or two.
The design's premise was right; the implementation was wrong.

### E4 — the adaptive yield map · RESOLVED in the second pass

PROPOSAL-II §2.2's revision rule R5 refreshes a "yield map — mean score per path
neighbourhood" and reprioritises the frontier mid-crawl. The first pass built a
static frontier only — chaff last, documentation first, shallow before deep.

**Built in the second pass.** `Plan.refresh_yield` computes mean readability per path
neighbourhood over the whole crawl, and `_Frontier.reprioritise` rescores the
queue on it — bounded to fifteen steps, so a well-written changelog can never
outrank the manual. A test pins that bound, and another pins that reprioritising
drops nothing.

The *thresholds* remain provisional; see F2.

### E2 — `<meta name="generator">` is present on only 25% of sites · open

Declared by 5 of 20: zensical (FastAPI), Astro, VitePress ×2, Docutils. §2.2
leans on "about ten generators, each identifiable from `<meta name=generator>`
or a two-marker class fingerprint". The meta half is a bonus, not a mechanism —
the class-fingerprint half has to carry it, and it has no design detail yet.

### E3 — a *soft* 404 was stored as documentation · open

Observed on `numpy`: a sampled page titled "NumPy - 404" was extracted and would
have been stored.

Recorded originally as "nothing checks HTTP status", which was wrong —
`Fetcher.html` already raises on any status ≥ 400. The real problem is the soft
404: the site answers **HTTP 200** and renders an error page, which no status
check can catch. Detecting it means looking at the content (a title that is
mostly "404" or "not found", a body with no headings and little text), and that
is a heuristic with a false-positive cost, so it wants measuring before it
ships. A soft 404 counts toward `expected` and toward `complete`, so it inflates
exactly the figure the product is built on.

### E5 — the manifest path ignored the politeness delay · fixed

`_crawl_html` has honoured `opts.delay` and `HOST_CONCURRENCY` since the
beginning. `_acquire_manifest_links` honoured neither, so the 211-page Mojo
harvest went out as 211 back-to-back requests to one host.

Nothing failed, which is why it survived: the site tolerated it. That is not the
same as it being acceptable, and a manifest is a crawl in everything but name.

**Fixed** by giving the loop its own `_Pace(opts.delay)`. Acquisition there is
sequential, so spacing the starts is the whole of the politeness — there is
never more than one request open — and at the default 0.4s the two paths now
cost a host the same. Costs the suite about 12s, all of it in tests that harvest
a manifest with the default delay rather than an explicit zero.

Worth noting as a class: **the ladder's rungs were built at different times and
did not inherit each other's manners.** Anything the crawl learned about being a
good citizen is worth re-checking on rungs 1–5.

---

## Federation

### F1 — the corpus is not a first-class store column · RESOLVED enough · open

`Corpus.key` produces the `corpus/version` fragment PROPOSAL-II asks for, and
the accounting that depends on it (Invariants 8 and 9) is built and tested.

**Filing is done, by a different route.** `forge_tools.corpus_key()` files each corpus as
`{tech}--{corpus}`, which is isomorphic to the three-part key and needs no
migration of a store that already holds harvests. Selected corpora are now
genuinely fetched and filed separately, each settling its own count.

**Still open:** a real column would let `list_knowledge_base` group corpora
under their technology instead of listing them beside it. Cosmetic today,
awkward once a technology has four corpora.

### F2 — the tuned numbers are all still guesses · open

PROPOSAL-II's own open question — "What is `BREADTH_LIMIT`? Eight is a guess" —
and it still is, along with `MIN_KIND_CONFIDENCE` (0.5) and the revision
thresholds in `Plan` (window of 12, three wins to pin, 40% shells).

**`DENSITY_FLOOR` is no longer among them.** It is now fitted per template from
the site's own distribution (rule R6): `min(median - 1.5*MAD, median/2)`,
clamped to [0.05, 0.60]. The constant survives only as the value used until a
template has been seen five times. Measured live on docs.astro.build, that moves
the floor from 0.30 to 0.46 with no page lost.

**`BREADTH_LIMIT` should not be fitted the same way — the count is the wrong
variable.** What makes a platform hard is not how many peer corpora it has but
how *evenly* the magnitude is spread: if one corpus is most of the mass, intent
has effectively chosen; if twenty service manuals are all the same size, nothing
has. Replace the count with a concentration measure (top-corpus share, or
entropy over magnitudes) and the constant disappears rather than being tuned.

Every one of them is a starting point rather than a finding. `measure.py`
collects what is needed and `measurements/` already holds 452 observations. Fit
them before treating any of them as measured, and expect at least one to be
wrong — that expectation is why Phase B exists.

## Storage — from the Go harvest failure

Found in use, not by reading: `go.dev` crawled for 16 minutes and stored
nothing. `AUDIT.md` §11 has the full trace. S1-S3 are one defect seen from three
distances.

### S1 — an oversized page cannot be stored at all · **correctness** · fixed

**Fixed** in PROPOSAL-3, in two steps. Phase 1 made a refused page cost one page
rather than the harvest. Phase 2 removed the refusal: `page.search` is a
GENERATED column, so an unbounded index expression made the 1 MB tsvector
ceiling a *storage* limit rather than an indexing one. It is now generated over
`left(content, 300_000)` (`_upgrade_v4`), the page is stored whole, and anything
past the bound is indexed section by section so none of it drops out of search.

`page.search` is a generated `tsvector` column, so `to_tsvector` runs during the
write and Postgres's 1 MB tsvector ceiling makes the row un-insertable. Measured:
1,189,416 bytes against a 1,048,575 limit.

**Fix, one line, loses nothing:** the limit is on the tsvector, not the column.
Index a bounded prefix and keep the full content —
`to_tsvector('english', left(coalesce(content,''), 1000000))`. Only the tail of
a very large page becomes unsearchable; it is still stored and still readable.

Needs a `_upgrade_v3` to rebuild the generated column on existing databases.

### S2 — one bad page discards every good one · **correctness** · fixed

**Fixed** in PROPOSAL-3 Phase 1. The single-`COPY`-in-one-transaction write is
gone; `save()` now delegates to `writer()` in both stores, so there is one write
path and the batch case cannot rot separately. Regression test: a harvest with a
deliberately oversized page (150,000 distinct lexemes) stores the other two.

`PostgresStore.save` writes the whole harvest in a single `COPY` in one
transaction. ~1,200 extractable Go pages were thrown away because one row was
rejected. Extraction already honours "one dead page must never end a run"
(`stats["unextractable"]`); storage does not.

**Fix:** write pages in batches and collect per-row rejections into the same
`unextractable` channel that extraction failures already use, so a harvest
reports "1,212 of 1,213 stored, one page too large to index" instead of failing.

*Not* a data-loss bug: the `delete from doc_version` is inside the same
transaction, so a failed harvest leaves the previously stored version intact.

### S3 — nothing bounds a single page, anywhere · open

**Still open after Phase 3.** Concurrency was the phase this was waiting
for, and it makes the memory side slightly worse rather than better: peak
memory is now up to `workers` pages in flight rather than one. Still
bounded, still small against a whole corpus, and still nothing that caps a
single pathological page.

**Narrowed** by PROPOSAL-3 Phase 2, not closed. The *consequence* that made
this urgent is gone — an unbounded page no longer costs a harvest, because
the index it feeds is bounded. What remains is the original problem: nothing
bounds what is fetched or extracted, so a pathological page is still held in
memory whole. That belongs with Phase 3's concurrency work, where peak memory
stops being one page and becomes as many as there are workers.

`FETCH_PAGE_CAP` and `HARVEST_PAGE_CAP` bound the page *count*; `MAX_CHARS`
bounds what a model is handed. Nothing bounds what is fetched, extracted or
stored. A page is unbounded from socket to database, and S1 is merely the first
hard limit that unboundedness has met.

**Fix:** a per-page ceiling with a stated default, applied at extraction, with
the overflow disclosed rather than silently trimmed.

### S4 — the two backends accept different things · fixed

**Fixed** in PROPOSAL-3 Phase 2. The divergence was entirely the tsvector
ceiling: `FileStore` took any page and `PostgresStore` refused large ones.
With the index bounded, Postgres accepts every page the file store does.

The same harvest succeeds on `FileStore` and fails on `PostgresStore`. The
product claims one engine and byte-identical results across surfaces; storage
breaks that silently, so `DOCSFORGE_DB` changes *what can be stored*, not only
where it goes. Fixing S1 mostly closes this.

### S5 — the whole harvest is resident in memory · fixed

**Fixed** in PROPOSAL-3 Phase 1, earlier than planned, because streaming made it
nearly free. The crawl hands each body to the sink and appends `Doc(url, title,
"")`, so peak memory is the page *count*, not their total size.

`harvest()` returns `list[Doc]` and `save()` takes the full list. Peak memory is
the whole corpus. Irrelevant for most sites; the same class as S3 for something
the size of `pkg.go.dev`.

### S6 — a storage failure is reported as a size problem · fixed

**Fixed** in PROPOSAL-3 Phase 1. `_PgWriter._why` translates the driver's
"string is too long for tsvector" into "too large for the full-text index (a
single page over Postgres's 1 MB tsvector ceiling)". The distinction is the whole
point: the first sends a reader looking for a smaller subset to harvest, which
violates Invariant 4; the second names one page.

The raw Postgres message propagated, and the calling model concluded "too large
for the current database format" and offered to harvest a subset — a curated
subset presented as a success, which is what Invariant 4 exists to prevent. A
storage error needs a diagnosis naming the real constraint and the real remedy,
or callers route around the guarantee.

### S7 — W2 is load-bearing, and this proves it · **correctness** · fixed

**Fixed** in PROPOSAL-3 Phase 2, from both ends. `classify_shape` now has a
caller (`_measure_corpora`), so a specification discovered as its own corpus is
fetched once and split on its headings rather than crawled as a site. And the
storage half no longer depends on getting that classification right: a huge page
met *inside* a tree crawl is now storable too. The second is what makes this
properly closed — the first alone would have left one classification mistake
standing between a harvest and losing a page.

`classify_shape` being unwired (W2) is what made S1 reachable. `go.dev/ref/spec`
is a `page`-shape corpus by §2.3's own test: fetch once, split on `h2`/`h3`,
`expected` becomes the section count. Split that way, no row approaches 1 MB.

Raise W2's priority accordingly — it is not tidying, it is the difference
between storing a large single-document corpus and being unable to.

## Wiring — from the 2026-08-24 audit

Four things that are built, tested, and never called. `AUDIT.md` §10 has the
verification for each. They share a shape, and it is worth naming: a unit test
proves a function behaves, not that anything invokes it, so the suite grew from
349 to 536 without noticing any of these.

### W1 — federation-level completeness never reaches the user · **correctness** · fixed

**Fixed** in PROPOSAL-3 Phase 2, by reordering. `_federate` is what discovers,
admits and harvests the other corpora, so until it has run there is no
federation-level completeness to report — and a headline written before it can
only ever describe the entry corpus. It now runs first, records the roll-up in
`stats['federation']`, and the headline uses it whenever more than one corpus is
in play. A harvest that got all of one corpus and half of another no longer
announces itself complete with the shortfall buried in a note at the bottom.

`Federation.complete` implements Invariant 9 and is tested. Nothing calls it.
The headline coverage of a harvest still comes from the entry corpus's `stats`,
so a federation where the manual is complete and the API reference came back
2 of 50 reports the manual's `complete` at the top.

This is the defect PROPOSAL-II opens with, one level above where it was fixed.

**Fix:** have `tool_harvest_docs` take its top-line `complete` from
`Federation.complete` when a federation exists, rather than from `stats["whole"]`
alone. Guard it with a wiring test.

### W2 — `classify_shape` is never called · **correctness** · fixed

**Fixed** in PROPOSAL-3 Phase 2. `_measure_corpora` probes each admitted corpus
with one GET and classifies it against its peers. Two things found by wiring it:
`has_manifest` had been accepted and never read for the whole of PROPOSAL-II
(it now rules out `page`, which is what a site publishing its own page list
means), and a corpus was being compared against a median it was itself in —
with two or three corpora the one enormous document dominates that median and so
can never be six times it, leaving the `page` branch unreachable in exactly the
case it exists for. The median is now over peers. Same mistake `_neighbourhood`
made counting a URL as its own neighbour.

Every corpus is `tree`, so the `page` branch of `_harvest_corpus` is unreachable
and a specification published as one huge document is crawled as a tree, yielding
about one page instead of an exact section count.

**Fix:** classify in `_federate`, after admission and after any render decision —
the ordering matters, because a JS-driven API reference measures as a 2 KB shell
and would classify as a tree. Needs the corpus's median page size, which means
sampling a page or two.

### W3 — `Corpus.magnitude` is never set · **correctness** · fixed

**Fixed** in PROPOSAL-3 Phase 2, by the same probe that fixed W2 — they need one
measurement, so they were wired together. Magnitude is the site's own page count
where it publishes one, and the in-scope link count otherwise. The entry corpus
is measured from the crawl that already happened rather than re-fetched.

It is `0` everywhere, so every option in an escalation question reads
**"size unknown"** and "options ordered by magnitude" orders by zero.

This lands squarely on the platform-scale case the escalation exists for: the
question put to a human is a list of URLs with no sizes, which is most of what
makes such a question answerable.

**Fix:** a cheap estimate at admission — the corpus's sitemap or generator
manifest length where one is reachable, otherwise the count of distinct
in-scope links already seen for that host in `Federation._votes`. The second
costs nothing.

### W4 — an already-harvested corpus can be reported `not requested` · **correctness** · fixed

**Fixed** in PROPOSAL-3 Phase 2. `Corpus.entry` marks the URL the caller handed
in, and `_mark` never deselects it: it was requested by name and it is already
in the store by the time selection runs, so "not requested" is false twice over.
Invariant 5 still applies to every other corpus — a peer that genuinely was not
requested still says so, with its magnitude.

`classify_kind` returns `("", 0.0)` for a docs root with no kind token in its
path, which is most of them. Under a kind-specific intent, `_wanted()` then
excludes it — so the entry corpus, already crawled and stored, is printed as
`**not requested**`.

**Fix:** the corpus that was actually harvested is never a candidate for
deselection. Either pin it as selected before `select()` runs, or treat an
unclassified kind as optional rather than excluded. The first is narrower.

### W5 — `needs_selection` is prose, not machine-readable · open

§2.4 promises automated callers a machine-readable result. `Selection.as_dict()`
produces one and is never called; the tool returns formatted text. A model can
parse it; FlowIT gating on a string is not the stated contract.

### W6 — six dead symbols, and two ways to name a corpus · fixed

**Fixed** in PROPOSAL-3 Phase 1, five of six. `_federated_note`,
`passages.passages` and `Corpus.key` are deleted; `Federation.single` and
`Federation.note` are now called by `_federate`, which had been rendering its own
coverage note. That second renderer had drifted into saying the coverage
described "only the corpus that was crawled" long after selection began
harvesting the others — the drift this issue predicted, found by deleting it.
`Selection.as_dict` remains dead; it is W5, not a stray symbol.

Deleting `Corpus.key` also removed the only expression of "an unversioned corpus
files under `undated`", which turned out to live *only* in dead code — the live
path labelled such corpora with today's date, a claim about when the content is
from that an undated corpus cannot make. Now `forge_tools.corpus_label()`.

`_federated_note`, `Federation.single`, `Federation.note`, `passages.passages`,
`Selection.as_dict`, and `Corpus.key` — the last duplicating
`forge_tools.corpus_key()` in a different format. All referenced only by tests.

The duplicated key is the one that matters: it is exactly the drift the
`pick_main` refactor existed to prevent, reintroduced.

### W7 — the suite has no wiring tests · fixed

**Fixed** across PROPOSAL-3 Phases 1 and 2, which is the only reason W1–W4 and
W6 could be closed with any confidence that they stay closed. There are now
wiring assertions for: the harvest opening a writer rather than batching into
`save()`; both harvest paths streaming; page bodies being released rather than
carried; `classify_shape` having a production caller; measurement running before
selection; the coverage note having exactly one renderer; and the federation
roll-up reaching the headline.

Two of them earned their place immediately by failing for real reasons rather
than cosmetic ones — the one-renderer assertion caught `_federate` still
building its own note, and the bounded-index assertion caught a migration guard
that would have rebuilt a table on every startup.

The root cause of W1-W4. `test_selection.py` already demonstrates the fix —
it greps `selection.py` and asserts no page-level filter exists. Three
equivalent assertions would have caught W1, W2 and W3 on the day each landed.

## Measurement

### M1 — `measure.py` treats every page as HTML · open

Six of twenty names resolve to an `llms.txt`, which the real pipeline handles via
`detect_source` but the driver fetches and measures as HTML. Their extraction
numbers are meaningless and were excluded by hand from the 2.8% figure. The
driver should branch on source kind the way `harvest()` does.

### M2 — `requests` lost 14 of 15 fetches · open

`https://requests.readthedocs.io` (no trailing slash) enumerated 15 URLs and
fetched one. Probably a redirect or scope interaction in `candidate_pages`.
Small sample sizes elsewhere (polars 1 page, tokio 4) may share the cause.

### M3 — correctness is judged by hand · deferred

Whether a resolution is *right* cannot be measured automatically, so
`FINDINGS-B.md` and `FINDINGS-C.md` list every judgement so it can be disputed.
A fixture of known-correct docs roots would make regressions catchable in CI.

---

## Packaging and platform

### P1 — Phase A's "gitignore `.impeccable/`" was deliberately skipped · wontfix

`.gitignore` already ignores `.impeccable/refs/` and `.impeccable/typetest/`
under the comment *"keep the decision record, drop the scratch"*. The proposal's
version would discard a record kept on purpose. Left alone deliberately.

### P2 — the web UI is a checkout-only surface · deferred

`app.py` resolves `static/` beside itself, and a wheel does not install it. The
`web` extra pulls the right packages but `pip install docsforge` does not give a
working web chat. Fixing it means making `static/` package data, which means
making the flat modules a real package — a larger refactor than Phase A wanted.

### P3 — background harvests do not survive a restart · deferred

Jobs are in-process and daemon-threaded by design, so a server restart loses
in-flight harvests. Documented, not hidden. Anything better needs a real
scheduler, which a documentation tool does not obviously need.

### P4 — cosmetic: wrong type annotation in `_probe_origins` · open

`live` is annotated `list[tuple[int, int, str, str, str]]` and the sort relies on
tuple ordering. It was `list[tuple[int, str, str]]` before Phase C and wrong
then too. Harmless, but it is the kind of thing that misleads the next reader.


## Resolved in the second pass

Kept for the record, because what a list like this *stops* saying matters as
much as what it says.

- **The crawl did not revise its own plan.** `Plan.revise` now re-derives from a
  rolling window of twelve every twelfth page, emitting all five rules of §2.2;
  `Frontier.reprioritise` rescores the queue; pinned selectors and density
  routing re-extract pages the CONTENT order got wrong. Revisions land in
  `stats["revisions"]`.
- **L4 did not exist.** `from_evidence` reads back the repository backlinks,
  outbound documentation links and canonical URLs that `ResolveState` had been
  recording all along — and `verify()` now records the page of a candidate it is
  about to fail, which is the one most likely to say where to look next.
- **Federation reported corpora but harvested one.** `_harvest_corpus` fetches
  each selected corpus per its shape and files it separately.
- **Selection could refuse but not ask.** `selection.set_asker` takes any
  channel; the CLI uses the terminal; over MCP the model relays the question and
  answers with `corpora=`.
- **`adk` was not a kind.** It is now, and distinct from `sdk`.
