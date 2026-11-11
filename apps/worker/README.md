# Worker

Container entrypoint for `python -m finagent.worker` (code: `src/finagent/worker/`).

Each tick it relays the transactional outbox, consumes events with inbox dedup, executes workflow
runs under fenced leases (LangGraph with the tenant-scoped PostgreSQL checkpointer), recovers runs
whose worker died, and evaluates escalations. See ADR-0015.

```bash
python -m finagent.worker            # loop forever (SIGTERM/SIGINT drain)
python -m finagent.worker --once     # one tick, prints stats
docker compose --profile app up worker
```

`--faults` / `FINAGENT_FAULTS` inject crashes for recovery tests and `scripts/demo_slice2.py`; the
worker refuses them outside `local`/`test`.
