# Phase B findings: what the numbers say before any rule is written

Measured 2026-08-23 with `measure.py` against the build described in
`PROPOSAL-II.md`. Two runs:

- **20 single-word technologies**, 452 pages sampled and measured
  (`measurements/`).
- **20 multi-word names**, resolution only (`measurements/f6/`).

PROPOSAL-II asked for exactly this before Phase D, and predicted that "two of
the five revision rules [would] prove worthless and one unanticipated rule
prove essential". That prediction was correct, and the essential one is not in
the document.

---

## The headline: the proposal has its priorities inverted

The roadmap treats **extraction** as the next big problem after resolution
(Phase D, adaptive crawling, before Phase E federation). The measurements say
extraction is in decent health and **resolution precision is broken**.

| What was measured | Result |
|---|---|
| Multi-word names that resolve at all (**F6**) | **1 / 20 — 5%** |
| Single-word names that resolve to a candidate | 20 / 20 — 100% |
| Single-word names that resolve to *the right documentation* | **11 / 20 — 55%** |
| Fall-through to `<body>` on correctly-resolved docs | **2.8%** of 388 pages |
| Fall-through to `<body>` on mis-resolved sites | 60.9% of 64 pages |

The often-quoted "11.1% of pages fall through to `<body>`" is an artefact of
mixing those last two rows. On documentation that was actually found, the
extractor is fine: **8 of 11 sites fall through on zero pages.**

---

## 1. F6 is worse than the document implies — 5%, not "frequently fails"

One name in twenty reached a candidate: `react hook form`, and only because
`react-hook-form.com` happens to be a guessable domain. The other nineteen
refused, including `spring boot`, `ruby on rails`, `aws lambda`,
`github actions`, `visual studio code` and `hugging face transformers`.

Refusing is the correct behaviour — it is Invariant 10 working — but a tool
that refuses 95% of multi-word technologies is not usable for them at all.
Layer 1's L3 name-shape lap is well aimed. Nothing here argues against it.

## 2. The unanticipated finding: the identity gate confirms the *name*, not the *project*

Nine of twenty single-word names resolved to something that is not the
documentation, and the failures are not near-misses:

| Name | Resolved to | What it actually is |
|---|---|---|
| `django` | `github.com/npm/security-holder#readme` | An npm placeholder package. Not Django. |
| `serde` | `rossmacarthur.github.io/serde/` | Real docs for a **different** library also called Serde — the PyPI one, not the Rust crate. |
| `flask` | `flask.io` | "Flask Lists", an unrelated to-do list SaaS. |
| `polars` | `polars.dev` | A third-party PySpark-transition site. |
| `numpy` | `numpy.org` | The marketing homepage, not `/doc/stable/`. One sampled page was titled **"NumPy - 404"**. |
| `tailwindcss` | `tailwindcss.com` | Marketing homepage; docs live under `/docs`. |

`serde` is the instructive one. It is genuine, well-formed documentation. It
names Serde on every page. It passes `is_identified()` honestly. It is simply
the wrong project.

**This is the finding that is not in PROPOSAL-II.** The document's Invariant 1
holds that `is_identified()` must never take a lower bar — "two STRONG signals,
or one plus `names-it`". The data says the bar is not too *low*; it is measuring
the wrong *thing*. Two strong signals that a page discusses something called
"Flask" are fully satisfied by a to-do app named Flask Lists.

Three consequences for the design as written:

- **Layer 1 adds recall, not precision.** Six laps all funnel into the same
  unchanged gate. More candidates through a gate that cannot tell projects
  apart produces more wrong answers, faster.
- **Layer 3 propagates the error.** Federation admits a new host by running
  `verify()` + `is_identified()` against it. On these results that will admit
  hosts belonging to different projects that share a name.
- **The `install-mismatch` veto did not fire** on `django` → an npm package,
  for a name overwhelmingly associated with Python. Resolution was called
  without an `ecosystem`, which is the ordinary case when a model passes a bare
  name.

What is missing is a *project-identity* signal distinct from a *name-match*
signal: repository linkage agreeing with the registry entry, the registry's own
ecosystem agreeing with the name's usual ecosystem, or the docs host agreeing
with the repo's declared homepage. That work belongs in Phase C and is not
currently scoped.

## 3. Extraction is healthier than assumed — Phase D can wait

On the eleven technologies that resolved correctly, across 388 pages:

| Technology | Fell through | Templates | Median link-text |
|---|---|---|---|
| pydantic | 15.0% | 22 | 6% |
| astro | 7.5% | 14 | 17% |
| fastapi | 5.0% | 14 | 1% |
| effect, kubernetes, prisma, sqlalchemy, terraform, tokio, vite, vue | **0.0%** | 3–22 | 0–20% |

Density scoring and the removal of the `soup.body` fall-through are still
worth doing — 2.8% of pages silently storing navigation is still wrong — but
this is not the emergency the roadmap ordering implies.

## 4. Template clustering does not hold up as specified

PROPOSAL-II §2.2 argues a rule should be learned per template because "sites
have a handful of layouts, so a rule is learned per layout — finer than
per-site, cheaper than per-page".

Measured with the proposal's own signature (three-level ancestry plus a coarse
shape): **0.42 distinct templates per page.** Pydantic and Terraform each
produced **22 signatures across 40 pages**. That is nearly per-page, which is
the cost the design explicitly set out to avoid.

Either the signature is too fine — three levels of ancestry with a first class
is sensitive to per-page utility classes — or the "handful of layouts" premise
is wrong. **This must be calibrated before Phase D builds cluster rules on top
of it**, or the rules will be learned on clusters of size one.

## 5. `<meta name="generator">` carries less than the design assumes

Declared by **5 of 20** sites: `zensical-0.0.51` (FastAPI), `Astro v7.0.2`,
`VitePress v2.0.0-alpha.17` and `-alpha.19` (Vue, Vite), `Docutils 0.19`.

§2.2 leans on "about ten generators, each identifiable from
`<meta name="generator">` or a two-marker class fingerprint". At 25% coverage
the meta tag is a bonus, not a mechanism; the class-fingerprint half has to do
nearly all the work, and it is the half with no design detail yet.

## 6. Smaller findings

- **A 404 page was stored as documentation** (`numpy`, "NumPy - 404"). Nothing
  checks HTTP status before a page becomes a stored doc.
- **`llms.txt` is where six of twenty names resolve** (react, svelte, vue,
  vite, prisma, pydantic). Correct — `detect_source` handles that path — but it
  means "the docs root" is frequently a *file*, which Layer 3's shape
  classification should treat as a first-class case rather than an exception.
- **Median page size varies 25×** across correctly-resolved sites, from 1,254
  chars (astro) to 31,687 (sqlalchemy). Any absolute byte threshold is
  meaningless, which the proposal already says about `page` shape and should
  say everywhere.

---

## What this changes about the plan

1. **Add resolution *precision* to Phase C.** It is currently a recall story
   (six laps, more candidates). Precision is the measured defect and needs a
   project-identity signal that `names-it` cannot provide.
2. **Re-order D and E, or shrink D.** Extraction is at 2.8% on real docs.
   Federation and resolution correctness are worth more per unit of work.
3. **Calibrate the template signature before writing any per-cluster rule.**
   At 0.42 templates per page the clustering premise is not yet true.
4. **Do not let Layer 3 reuse the identity gate unchanged.** Admitting hosts on
   a gate that confuses projects spreads the error across corpora, and
   Invariant 8 means a wrongly admitted corpus is recorded as complete.

## How to reproduce

```bash
python measure.py                                    # the 20, ~40 pages each
python measure.py --multiword --resolve-only --out measurements/f6
```

Correctness of a resolution was judged by hand from the resolved URL and the
titles of its sampled pages; that classification is a judgement and is listed
in full above so it can be disputed. Sample sizes are small for several
technologies where page enumeration returned few URLs, and two entries
(`requests`, with 14 of 15 fetches failing, and the single-page `llms.txt`
results) are driver artefacts rather than product defects — all are excluded
from the 2.8% figure and named here so the exclusion can be checked.
