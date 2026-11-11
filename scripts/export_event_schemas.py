"""Write the event envelope JSON Schema and the event-type catalogue (prompt §21).

    python scripts/export_event_schemas.py   ->  packages/contracts/events/*.json

CI can diff the output against the committed files to detect contract drift.
"""

from __future__ import annotations

import json
from pathlib import Path

from finagent.worker.events import EVENT_TYPES, EventEnvelope

OUT = Path(__file__).resolve().parents[1] / "packages" / "contracts" / "events"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    schema = EventEnvelope.model_json_schema()
    schema["$id"] = "https://finagent.local/contracts/events/envelope.v1.schema.json"
    schema["description"] = (
        "FinAgent event envelope v1. event_id is the delivery id (never the business key); payloads "
        "carry references only; signature = HMAC-SHA256 over the canonical JSON without `signature`."
    )
    (OUT / "envelope.v1.schema.json").write_text(
        json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    (OUT / "event-types.json").write_text(
        json.dumps(sorted(EVENT_TYPES), indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"wrote {len(EVENT_TYPES)} event types and the envelope schema to {OUT}")


if __name__ == "__main__":
    main()
