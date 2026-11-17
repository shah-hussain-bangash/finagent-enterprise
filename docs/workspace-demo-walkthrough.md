# FinAgent workspace: demonstration walkthrough

This walkthrough presents a synthetic AML investigation in the local workspace. Use a synthetic account and case that the signed-in user can access. Connected pages depend on the corresponding local backend services and data being available; individual results may be empty or unavailable.

## Suggested flow

1. Open `/workspace` and introduce the dashboard. The investigation and oversight screens contain illustrative examples; the preview role selector changes presentation, not access rights.
2. Open `/workspace/case-overview` and select an accessible synthetic case. Show its status, current evidence and checklist gaps, and any previously recorded indicators. Explain that these records can refer to different points in time.
3. Follow **Review evidence briefing** to inspect available evidence and gaps. If connected search is available, search for a term present in the synthetic case and follow a result to its source record.
4. Return to the case overview and open **Review recorded transactions**. Demonstrate the available search and filters, and explain that totals are presented by currency and apply to the displayed posted records.
5. Open **Review document provenance** to inspect recorded document information and available KYC findings. Where snapshot selection details exist, explain which evidence was selected for a recorded snapshot.
6. Show **Review recorded indicators and calculations** and **Inspect recorded investigation runs** if the case has recorded outputs. Discuss the source and timing of the records; an empty result does not establish that a case is risk-free or that detection ran.
7. Return to the workspace and show the illustrative investigation assistant and oversight screens. The assistant preview uses fixed responses. The separate experimental snapshot-grounded assistant is disabled by default and has no workspace or API entry point.

## Points to communicate

- The connected views are read-only. Decisions and case actions remain with authorized people in the existing workflows.
- Role presentation in the preview is not authorization. Server-side access controls govern connected records.
- Source quotations and risk indicators require human review; they do not establish wrongdoing, relevance, or factual truth by themselves.
- The synthetic examples, focused tests, and local preview demonstrate development work. They do not establish production readiness, deployment, or a live AI integration.

See [workspace interface preview](workspace-interface-preview.md) for the feature boundaries and setup notes.
