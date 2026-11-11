# Contributing

1. Read `CLAUDE.md` and the relevant source requirements in `docs/requirements/source/`.
2. Small PRs, one concern each. Reference requirement IDs (namespaced per ADR-0011) in the PR.
3. New behaviour needs tests tagged with `@pytest.mark.req(...)`; security boundaries also need
   negative tests and, where critical, `@pytest.mark.release_blocker`.
4. Run `make lint typecheck security test` locally; CI runs the same gates plus web and secret scans.
5. Derived design decisions -> new ADR. Business/control questions -> open-decisions register.
6. Update `docs/traceability/trace-map.yaml` for implemented requirements. Never edit generated
   traceability files by hand.
7. Synthetic data only. Never copy production data into fixtures (SPR-045).
