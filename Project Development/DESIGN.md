# Design

<!-- impeccable:design-schema 1 -->

Recorded from the built pages (`static/`), not from intention. Where the build
and this file disagree, the build is right and this file is stale.

## World

**A plain chat utility.** Rail, transcript, composer — the shape people already
know, so nothing about the interface needs learning before the product can be
used.

This replaced a HyperCard shoebox stack whose whole thesis was refusing the
centred conversation column. The user pinned that direction and then, later,
overrode it: charcoal and `#CF9FFF`, "simple normal", every instruction moved
off the home screen. That is a deliberate change of direction, not drift. The
old world is recoverable at `cd54899`.

## Colour

Charcoal through black, and one accent.

| Token | Value | Used for |
|---|---|---|
| `--bg` | `#0b0b0d` | the page |
| `--bg-soft` | `#121215` | rail, top bar |
| `--surface` | `#17171b` | composer, the user's own words, tool rows |
| `--surface-2` | `#1e1e23` | hover, inline code, quiet fills |
| `--line` / `--line-soft` | `#2a2a31` / `#202027` | hairlines |
| `--text` / `--text-2` / `--text-3` | `#ececee` / `#a8a8b3` / `#8a8a96` | primary / secondary / metadata |
| `--accent` | `#cf9fff` | see below |
| `--accent-ink` | `#170b24` | type that sits on the accent |
| `--danger` | `#ff9a9a` | a failed tool call, a partial harvest |

The accent is the only hue in the build, and it marks exactly three things:
**the control that sends**, **the thing currently selected**, and **a link**.
Spending it anywhere else would stop it meaning any of them. There is no second
colour and no gradient.

Dark is not a category default here: this sits beside an editor, for long
sessions, and it is where the reference the user gave lives.

## Type

System stacks, deliberately. `ui-sans-serif` for everything and
`ui-monospace` for code, URLs, counts and timestamps. The previous build
self-hosted two bitmap faces because it was making a statement; this one is
asked to be ordinary, and a webfont that only makes a plain utility look
dressed up is weight for nothing. The bitmap faces were deleted rather than
left unreferenced — `git show cd54899:static/fonts/` has them.

Scale: 30px page title, 26px empty-screen wordmark, 22/19/16 headings, 15px
body, 13px controls and metadata, 12px timestamps. Prose is capped at 70–74ch;
tables and code blocks are not, because a table squeezed into a prose measure
is worse than a long line — they scroll inside their own `.scroll-x` box so the
page never scrolls sideways.

## Layout

| Region | Behaviour |
|---|---|
| Sidebar | 56px collapsed, 264px open. Toggle, wordmark, New chat, DocsStore, the chat list, then Docs pinned to the bottom. |
| Top bar | 52px. Conversation title left; model picker right on chat, actions and the storage chip right on DocsStore. |
| Transcript | Scrolls; content centred on a 768px measure. |
| Composer | Pinned below the transcript, same measure, never overlapping it. |

Below 640px the collapsed rail narrows to 48px, starters stack, and the
answer's provider/time stamp takes its own line rather than crushing the
buttons beside it.

## Sidebar

Collapsed it is an icon rail; open it is labelled links plus every past
conversation. Toggled by the panel button or `Ctrl`/`⌘`+`B`, and the state is
remembered across pages — but never restored open on a narrow screen, where it
would cover the page on load.

Every row is the same shape whether or not its label is showing, so nothing
jumps sideways when it opens. Labels and the list use `visibility` rather than
`display` so the text does not reflow mid-change.

**The width snaps; it does not animate.** Animating width reflows the whole
page every frame, and the page next door can be holding a 703-item list. The
softness comes from the labels and the scrim fading — both composited.

Below 760px the panel stops being a column and becomes an overlay with a
scrim: there is no width at which 264px of sidebar and a readable column both
fit. `Escape` closes it there, and so does the scrim.

The delete button on a chat row is hidden until the row is hovered, because it
is destructive and the row's main action is "open" — but it stays reachable by
keyboard via `:focus-visible`.

### Conversations

The list is the reason the sidebar is worth opening, and it is also the fix for
a real fault: **New chat used to destroy the conversation with no way back.**

Stored in `localStorage`, forty most recent, oldest evicted on quota. Only the
Markdown is kept — the rendered HTML is several times larger and the server can
rebuild it, so restoring a chat re-renders each answer through `/api/render`
rather than showing a wall of raw Markdown. A conversation is saved when a turn
finishes, when an answer is edited or reverted, and on `beforeunload`. Deleting
the conversation you are currently reading drops you into a fresh one rather
than leaving you on a transcript that no longer exists anywhere.

## Components

- **Turn** — the user's words as a right-aligned bubble on `--surface`; the
  answer as plain text at full column width, no bubble and no avatar. The
  asymmetry is what makes the two readable apart at a glance.
- **Work rows** — one row per tool call above the answer it produced: a spinner
  while running, then a tick or a bang, the target, the source kind and the
  character count. Shown rather than hidden, because an answer built from a
  page that was actually fetched should not look like one that was not.
  **The whole row is the control.** It carries `aria-expanded` and a chevron
  that rotates, and clicking anywhere on it opens the trace below — a row that
  holds evidence and looks inert is worse than one that holds nothing.
- **Answer actions** — Copy, Download .md, Edit, and Revert once edited. Edit
  swaps the rendered answer for a textarea; edited text is then shown as the
  plain text it now is rather than guessed at as HTML.
- **Model picker** — a pill in the top bar. Unavailable providers are disabled
  and say *why* — "needs GROQ_API_KEY in .env" for a hosted one, "not running
  locally" for Ollama or the CLI. Those are different problems with different
  fixes.
- **Send / Stop** — one circular control. Accent-filled, disabled until there
  is something to send, and it becomes a stop square while an answer streams.
  The stop is real: an `AbortController` cancels the request, and whatever
  arrived is kept and labelled unfinished rather than thrown away.
- **Empty screen** — mark, wordmark, one line, three starters. Nothing that
  needs a paragraph lives here.
- **Footer line** — the one thing worth knowing before trusting an answer, and
  a link to `/docs` for everything else.

## Execution trace

Opened from a work row; nested under it, never in a separate panel that would
divorce the evidence from the claim it supports.

Each node is a stage or an event: a state glyph, a name, and — where there is
one — the input it was given and the output it returned, each in a `<pre>` that
scrolls inside itself. Nesting is by parent id and indents by depth, so a
harvest reads as `learn_technology → resolving → harvesting → this page, that
page` rather than a flat list of 230 lines.

**Rows update in place.** Events are keyed by id, not appended, so a stage
re-emitted with a new count replaces itself. Before that, a 211-page harvest
painted 211 rows and the panel was unreadable exactly when it mattered most.

Arrival is incremental over its own `EventSource` — the answer stream and the
trace stream have different lifetimes, and a harvest that outlives the answer
keeps emitting. Repaints target the turn that owns the trace rather than the
last turn on screen, because a background harvest finishes while you are
already reading the next answer.

No new colour: `--accent` for a completed marker, `--danger` for a failed one,
`--surface-2` for the panel fill, `--text-3` for an empty trace. A percentage
appears only where the backend has a real denominator — a manifest length is
one, a crawl frontier is not — so nothing here invents progress it cannot
measure.

Truncation is stated, never silent: strings clip at 4,000 characters and
outputs at 20,000, and the row says how much it left out.

## Harvest tracker

A fixed card, bottom-right, on both the chat and DocsStore. When nothing is
being harvested there is no card — this is the only element on either page that
appears unprompted, so it has to earn the space every time.

It owns no layout. The two pages share nothing but `style.css`, and a strip in
the flow would have had to be placed twice and would move the transcript under
a reader mid-sentence. Below 640px it spans the width instead of floating, and
at 420px the page still does not scroll sideways.

One row per harvest: the name, elapsed time in monospace, and what it is doing
right now. **The bar is drawn only where there is a real denominator.** A
manifest states how many pages it lists, so `41/213` fills to 19%. A crawl's
frontier grows as it is walked, so there is no fraction to show and the row
gets an indeterminate sweep that says "working" without implying how far along
it is. Inventing that number would be the same lie as a coverage figure nobody
measured.

A harvest that stops reporting says so in `--danger` and names the likely
cause, rather than spinning forever. Finished harvests linger twenty seconds
and then go; DocsStore refreshes itself when one lands, because the whole
complaint was documentation appearing there as though from nowhere.

Polling, not SSE: two seconds while something runs, fifteen when nothing does,
and a catch-up when a hidden tab comes back. The trace needed a stream because
it is a nested log that must arrive in order; this is four numbers.

## Icons

Authored 1-bit SVG `<symbol>`s inline at the top of each page, all on a 24px
grid at 1.5 stroke, round caps and joins. No icon font, no emoji.

## Motion

One authored moment: a turn arrives from 8px below over 240ms on an
exponential ease-out, **once** — a turn that has already been on screen is
pinned with `animation: none` so a repaint does not replay it. Everything else
that moves is state: the tool spinner and the streaming caret. All of it is off
under `prefers-reduced-motion`.

## /docs

Everything that used to clutter the home screen: what it reads, harvesting,
scope, versions, DocsStore, the tools, models, storage, keyboard, MCP. Sticky
contents at 200px beside a 70ch column; below 860px the contents become a
scrolling strip of pills above the prose.

FastAPI mounts Swagger UI at `/docs` by default and silently won the route the
first time, so the app now sets `docs_url=None`. A test pins that.

## DocsStore

`/library`, same palette and chrome. Three levels: a technology, every crawled
version of it, and the pages of the version you open.

- The technology list is **paged**, twelve at a time, because it grows with
  every harvest and has no natural ceiling. The stepper stays put at one page —
  its disabled arrows are the answer to "is there more?".
- The **spine** glyph shows how many versions are stored, drawn as stacked card
  edges behind a tabbed card. The tab is load-bearing: without it, a
  single-version row is a bare rectangle beside a label, which reads as an
  empty checkbox and invites a click that does nothing.
- Search inside a version marks matches with `<mark>` in the accent tint.
  Snippets arrive marked with `«` `»` rather than markup, and the client
  escapes first and promotes the guillemets second, so a page full of angle
  brackets cannot smuggle HTML into the index.
- Actions appear in the top bar as they become possible rather than sitting
  greyed out: on this screen "nothing open yet" is the common state, and a row
  of dead controls is not information.
- **Right-click a technology** for Open and *Delete all N versions*. Deleting
  one version at a time was the only route to removing a technology, which is
  not a route at all once it has eight of them. The destructive item **arms
  before it fires** — it asks "Really delete everything?" and needs a second
  click — and it sits in a context menu rather than on the row, because a
  hover-revealed delete beside a whole language's documentation is too easy to
  hit. The menu is repositioned after insertion so it never opens past the
  viewport edge, and deleting whatever is currently open returns you to the
  listing rather than to a detail pane for something that no longer exists.
- The **storage chip** names which backend answered. Postgres ranks search and
  a folder of files cannot, so hiding which one you are reading would be a lie.

Every view is addressable: `#/effect/v3/41`.

## Verified

- `detect.mjs` clean over every file in `static/`.
- Captured at 1440 and 420 across the empty chat, the model picker, a finished
  answer with tool rows, in-place editing, `/docs`, and DocsStore at all three
  levels. No horizontal overflow and no console errors on any of them.
- The sidebar driven rather than eyeballed: opens to 264px with labels visible,
  two conversations recorded and listed newest first, switching restores the
  right transcript **rendered** rather than raw, the open state survives moving
  to `/library` and `/docs`, deleting removes exactly one, and at 420px the
  panel overlays the page (main column stays at the rail's edge) with the scrim
  at full opacity.
- Contrast measured on the built pages with alpha properly composited, not read
  off the palette. Every text pair clears 4.5:1; the lowest are the placeholder
  at 5.24:1 and the send glyph on the accent at 4.87:1. Search highlight —
  accent on a 13% accent tint over near-black — measures 7.72:1.
- The harvest tracker driven in a real browser at 1440 and 420, on both pages,
  with two harvests seeded as another process would have left them: the row
  with a denominator fills to 19% and the crawl without one draws the
  indeterminate sweep and no number, console clean, no horizontal overflow at
  either width.
- The trace driven rather than eyeballed: a work row opens on click and on
  `Enter`, `aria-expanded` tracks the panel, a stage re-emitted with the same id
  replaces its row instead of adding one, and a harvest that finishes after the
  next turn has started repaints its own turn rather than the last one on
  screen. Backed by `tests/test_tracing.py`, `test_trace_integration.py` and
  `test_app_trace.py`.
