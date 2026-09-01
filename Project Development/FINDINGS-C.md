# Phase C findings: the ladder works, and it proved the gate is the bottleneck

Measured 2026-08-23/24 with `measure.py --multiword --resolve-only`, three cold
runs against the same 20 multi-word names (`measurements/f6`, `f6-after`,
`f6-final`). Phase C built L0 memory, L3 name shapes, L5 registry search, and
wired `Budget` and `ResolveState` into `resolve()`.

## F6: 1 in 20, to 8 in 20

| Run | Resolved | Correct |
|---|---|---|
| Before Phase C | 1 / 20 | 1 |
| Phase C, first cut | 11 / 20 | 8 — three were wrong |
| Phase C, after the fix below | 9 / 20 | **8** |

Now resolving correctly, none of which resolved before:

`apache airflow` → airflow.apache.org/docs/ · `godot engine` → godotengine.org ·
`open telemetry` → opentelemetry.io · `ruby on rails` → rubyonrails.org/docs ·
`shadcn ui` → ui.shadcn.com · `tanstack query` → tanstack.com ·
`visual studio code` → code.visualstudio.com

The productive shapes were the two that hyphenation cannot reach:
**concatenation** (`opentelemetry.io`, `rubyonrails.org`, `godotengine.org`) and
**product-under-vendor** (`airflow.apache.org`, `ui.shadcn.com`,
`code.visualstudio.com`). `from_domains` already tried the hyphenated slug,
which is why `react hook form` was the single name that worked before.

Nine still refuse: `spring boot`, `next auth`, `framer motion`, `argo cd`,
`elastic search`, `azure blob storage`, `google cloud storage`, `aws lambda`,
`hugging face transformers`, `postgres full text search`, `unreal engine`.
Several of these are hard for honest reasons — `framer motion` renamed itself to
motion.dev and no longer names Framer, and the cloud-provider names live under
deep paths on enormous portals (`learn.microsoft.com/azure/storage/blobs`) that
no name shape reaches.

## The self-inflicted defect, and what it teaches

Phase C added a `repo-identity` signal: a source repository whose own path names
the project. It was written to close the Phase B precision defect, and it did
close three of the six measured cases.

Its first version only required the repository name to **contain** the name's
tokens. The moment the fuzzy-search lap started supplying near-miss candidates,
that produced three confident wrong answers:

| Name | Wrongly resolved to |
|---|---|
| `aws lambda` | `github.com/awslabs/aws-lambda-invoke-store` |
| `github actions` | `github.com/estruyf/playwright-github-actions-reporter` |
| `google cloud storage` | `github.com/strapi-community/strapi-provider-upload-google-cloud-storage` |

Containment now has to hold **both ways**: the repository's own words must be a
subset of the name's tokens, not merely a superset. All three dropped to refusal
and every correct hit survived.

The general lesson, which matters more than the bug: **every lap ends at the
same gate, so widening recall multiplies whatever looseness the gate already
has.** A fuzzy lap is the worst case because near-misses are exactly what it
supplies. No future lap — federation host admission above all — should ship
without re-measuring precision on the names it newly reaches.

## The residual defect is unchanged, and now has a second example

Phase B found that `is_identified()` confirms a page is about something *with
that name*, not that it is the right project. Phase C narrowed this but did not
close it, because the surviving path does not involve repositories at all:

> **own-domain** (the hostname contains the name) **+ names-it** (the page says
> the name three times) **= identified.**

Three measured cases still ride on it:

- `flask` → **flask.io**, an unrelated to-do app called Flask Lists.
- `polars` → **polars.dev**, a third-party PySpark-transition site.
- `github actions` → **githubactions.com**, an 8 KB parked page titled
  "GitHubActions.com" — newly reachable *because* L3's concatenation shape works.

The last one is the important one. L3 did its job: it found a domain matching
the shape of the name. The gate then could not tell a parked domain from
documentation. Reaching further found more wrong answers as readily as right
ones, exactly as predicted.

`_looks_like_software` is supposed to prevent this — it is what stops `astro`
resolving to an astrology site — but a single forge link anywhere on the page
satisfies it, which almost every parked or marketing page now has.

## What this argues for next

1. **The gate, before any further reach.** `own-domain + names-it` is the one
   remaining path that a name-squatter satisfies. Closing it means changing
   `is_identified()`, which PROPOSAL-II's Invariant 1 declares unchanged — so
   this is a decision about the invariant, not something to slip in. The
   invariant says never a *lower* bar; raising it here is not forbidden, but it
   is a change and should be made deliberately.
2. **Strengthen the software gate rather than the name gate.** A cheaper option
   than touching `is_identified()`: require more than one software marker, or
   discount a page whose only marker is a footer forge link. Parked and
   marketing pages fail that; documentation does not.
3. **Prefer the best-evidenced candidate, not the first verified one.**
   `resolve()` breaks at the first candidate that verifies, in shape order.
   Ranking verified candidates by strength of signals would prefer
   `docs.github.com/actions` over `githubactions.com`. This is a behaviour
   change and needs its own measurement run.
4. **Federation (Phase E) must not reuse the gate as-is.** It admits hosts by
   re-running `verify()` + `is_identified()`. On these numbers that admits
   parked domains and same-named projects into a corpus set, and Invariant 8
   then records them complete.

## How to reproduce

```bash
python measure.py --multiword --resolve-only --out measurements/f6-final
```

Correctness was judged by hand from the resolved URL, and for
`githubactions.com` by fetching the page and reading its title. The 20 names are
listed in `measure.py` as `MULTIWORD`. Nine names refusing is the honest current
state, not a rounding error — refusal remains the correct behaviour where the
evidence is absent.
