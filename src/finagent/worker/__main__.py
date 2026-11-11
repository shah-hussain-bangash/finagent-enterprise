"""Worker entrypoint: `python -m finagent.worker [--once] [--max-ticks N] [--faults SPEC]`.

SIGTERM/SIGINT drain the loop: the current step finishes or is abandoned at a fenced boundary,
and every unfinished run is resumed by the next worker (prompt §34 worker drain, durable resume).
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys

from finagent.core.config import get_settings
from finagent.core.logging import configure_logging
from finagent.orchestration.faults import NO_FAULTS, FaultInjector
from finagent.worker.service import Worker


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="finagent.worker")
    parser.add_argument("--once", action="store_true", help="run a single tick and exit")
    parser.add_argument("--max-ticks", type=int, default=0, help="stop after N ticks (0 = forever)")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--worker-id", default=None)
    parser.add_argument("--faults", default=os.environ.get("FINAGENT_FAULTS"), help="LOCAL/TEST ONLY")
    args = parser.parse_args(argv)
    configure_logging()
    settings = get_settings()
    faults = FaultInjector.parse(args.faults) if args.faults else NO_FAULTS
    worker = Worker(settings, faults=faults, **({"worker_id": args.worker_id} if args.worker_id else {}))
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: worker.stop.set())
    if args.once or args.max_ticks:
        for i in range(1 if args.once else args.max_ticks):
            print(json.dumps(worker.tick(), default=str), flush=True)
            if i + 1 < args.max_ticks and worker.stop.wait(args.interval):
                break
        return 0
    worker.run_forever(args.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
