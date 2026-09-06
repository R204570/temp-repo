#!/usr/bin/env python3
"""
One harvest, in a process nobody else owns.

`harvest_jobs` runs a harvest on a daemon thread, which is right when the host
outlives the turn — `app.py` does, and a thread there is simpler and is what
feeds the live trace. The stdio MCP server does not: the `claude` CLI launches
it per turn and tears it down afterwards, and a daemon thread dies with its
process. Measured, driving the real server over stdio with a harvest running:

    close-stdin   process lived a further 232s   stored: 1.0.0.md
    kill          process lived a further   2s   stored: NOTHING

The linger that handles the first case is powerless against the second, and
the second is what a client teardown actually does. Nothing running inside a
process someone else owns survives being killed — so under that host the
harvest is launched here instead, detached, and outlives everyone.

This is still not a queue. It takes one job, runs it once, writes its status
record, and exits. Nothing is resumed and nothing is retried: a worker that
dies leaves a record whose heartbeat stops, and which is therefore reported
stalled rather than pretended about.

Not run by hand. `harvest_jobs.spawn_detached` writes the spec and launches it.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Configuration comes from the environment this process was handed, and from
# nowhere else. It deliberately does **not** load `.env`.
#
# It did, and that was wrong twice over. A launcher's environment is already
# inherited, so re-reading the file adds nothing a correctly configured parent
# had — and it *resurrects* what the parent deliberately removed. A test that
# unset `DOCSFORGE_DB` to keep off a real database got a worker that found the
# file by walking up from the repo, reconnected to that database, decided the
# technology was already stored, and returned in one second having harvested
# nothing. A detached process that quietly re-acquires config its parent
# dropped is a process nobody can point at a test store.

import harvest_jobs  # noqa: E402

#: The tools a job may name. An allow-list rather than `getattr`, because the
#: spec file is an instruction to run code and should only be able to name the
#: entry point that actually needs detaching.
#:
#: `harvest_docs` is deliberately not here. It is synchronous — it never calls
#: `harvest_jobs.start`, so it publishes no record, and running it here would
#: harvest correctly while leaving the launcher's record to go stale and be
#: reported as a process that vanished. A caller who invokes it directly is
#: waiting for it, which is a different contract.
TOOLS = ("learn_technology",)


def run(spec_path: Path) -> int:
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except Exception as e:                                  # noqa: BLE001
        print(f"unreadable spec: {e}", file=sys.stderr)
        return 2

    job_id = str(spec.get("job") or "")
    label = str(spec.get("label") or job_id)
    tool = str(spec.get("tool") or "")
    kwargs = spec.get("kwargs") or {}
    if tool not in TOOLS or not job_id or not isinstance(kwargs, dict):
        print(f"refusing spec: tool={tool!r} job={job_id!r}", file=sys.stderr)
        return 2

    # Imported late and deliberately: `forge_tools` reads configuration at
    # import time, and it must see the environment this process was given.
    import forge_tools
    from docsforge import ForgeError

    # Off in here, or the tool would launch another worker and this one would
    # have nothing to do but watch.
    harvest_jobs.DETACHED = False
    # Adopt the record the launcher already published rather than minting a
    # second id for the same harvest.
    harvest_jobs.ADOPT = job_id
    # This process exists for exactly this harvest, so there is nobody to hand
    # back to early. The deadline is for a caller who has to answer a client;
    # here it would only cut the work short.
    harvest_jobs.DEADLINE = float("inf")

    function = {"learn_technology": forge_tools.tool_learn_technology}[tool]

    code = 0
    try:
        function(**kwargs)
    except (ForgeError, Exception) as e:                    # noqa: BLE001
        # A failure inside the harvest is already on the record, written by
        # `start`. This catches the rest — a bad URL, a store that will not
        # open — which would otherwise leave the launcher's record to go
        # quietly stale and be reported as a process that vanished.
        code = 1
        existing = harvest_jobs.get(job_id)
        if existing is None or existing.state == harvest_jobs.RUNNING:
            failed = harvest_jobs.Job(id=job_id, label=label,
                                      started=time.time(), pid=os.getpid())
            failed.state = harvest_jobs.FAILED
            failed.error = str(e) or type(e).__name__
            failed.finished = time.time()
            harvest_jobs._publish(failed)

    try:
        spec_path.unlink(missing_ok=True)
    except Exception:                                       # noqa: BLE001
        pass
    return code


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    began = time.time()
    code = run(Path(args[0]))
    print(f"harvest worker finished in {time.time() - began:.0f}s, exit {code}",
          file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
