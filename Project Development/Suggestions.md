# Suggestions

## Main Issues

- Harvest works, but end-to-end latency is still too high for large docs sites.
- Background harvest exists, but status is not visible enough during normal use.
- Large page storage/search is awkward because very big bodies do not fit cleanly into one `tsvector` path.
- Chat answers do not surface enough of what was actually resolved, harvested, skipped, or stored.
- There is not enough backend harvest logging to debug crawler mistakes quickly.

## Short Suggestions

- Do not chase speed with a full rewrite first. Most of the delay is likely network, JS rendering, repeated verification, and whole-site crawling, not Python alone.
- Split the pipeline into two modes: a fast “resolve + map + enough to answer” path, and a slower full harvest path that continues in background.
- Store and search chunks/sections, not giant whole pages. Keep raw full content separately, but build search/indexing on smaller units.
- Add a first-class harvest status view: phase, pages fetched, expected pages, current URL, elapsed time, last error, and final outcome.
- Add a first-class harvest log/ledger per run: candidates tried, rejected reasons, revisions, unreadable pages, skipped corpora, and coverage outcome.
- Make chat always show the important harvest facts: resolved URL, selected corpora, unselected/refused corpora, coverage state, and where the result was stored.
- Prefer targeted native acceleration only after profiling. If you want C++, use it for hot loops only: HTML parsing, extraction, cleaning, chunking, or search prep. Do not rewrite resolver/orchestration/storage logic just for language purity.

## On C++

- Yes, C++ can make some parts faster.
- No, it is not the first fix for the current pain.
- A full C++ rewrite will add complexity and probably not remove the biggest waits.
- The better path is: measure first, then move only proven hotspots to a native worker/library if needed.

## Not Fully Landed Yet From Proposal II

- The identity gate still needs a decision; wrong-but-verified resolutions remain a live risk.
- Several thresholds are still provisional and need calibration from measurements.
- Direct MCP-side elicitation is still not wired; the round trip is indirect through the model.
- Corpus is still not a first-class store field.
- Federated top-level completeness is not fully enforced in the surfaced result.
- Shape classification is built but not actually reached, so `page`/`api` acquisition is still effectively unwired.
- Corpus magnitude is not being set, so selection/escalation cannot explain size properly.
- An unclassified entry corpus can still be wrongly deselected.
- Soft 404 detection is still open.

## Priority Order

1. Visibility first: status, logs, and chat surfacing.
2. Storage/indexing next: chunked search instead of giant-page indexing.
3. Speed next: reduce unnecessary work before considering native code.
4. Native acceleration last: only for measured hotspots.
