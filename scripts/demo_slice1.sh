#!/usr/bin/env bash
# Prompt section 52, demonstration 1, over real HTTP. Requires PostgreSQL on localhost:5432 (make deps-up).
# Usage: set -a; source .env; set +a; bash scripts/demo_slice1.sh
set -e
export FINAGENT_ENVIRONMENT=${FINAGENT_ENVIRONMENT:-local} FINAGENT_AUTH_MODE=${FINAGENT_AUTH_MODE:-local}
export FINAGENT_DATABASE_URL=postgresql+psycopg://finagent:finagent@localhost:5432/finagent
: "${FINAGENT_DEV_JWT_SECRET:?set it in .env or the environment}"
python3 -m alembic upgrade head
python3 -m finagent.database.seeds
python3 -m uvicorn finagent.api.main:create_app --factory --port 8000 >/tmp/api.log 2>&1 &
PID=$!; trap "kill $PID" EXIT; sleep 3
CONN=$(python3 scripts/dev_token.py a.tm-connector@synthetic)
SUP_A=$(python3 scripts/dev_token.py a.supervisor1@synthetic)
SUP_B=$(python3 scripts/dev_token.py b.supervisor1@synthetic)
BODY='{"schema_version":"1.0","event_id":"evt-demo-1","source_system":"tm-synthetic-a","source_event_time":"2026-10-15T07:00:00Z","payload":{"source_alert_id":"AML-SYN-001","alert_type":"TM_STRUCTURING","occurred_at":"2026-10-15T06:59:00Z","subject_refs":["customer-test-001"],"severity":"HIGH","window_start":"2026-09-15T00:00:00Z","window_end":"2026-10-15T00:00:00Z"}}'
echo "1) ingest:"; R=$(curl -s -w ' HTTP%{http_code}' -X POST localhost:8000/api/v1/alerts -H "Authorization: Bearer $CONN" -H "Idempotency-Key: demo-key-0001" -H 'Content-Type: application/json' -d "$BODY"); echo "$R"
CASE=$(echo "$R" | python3 -c "import sys,json;print(json.loads(sys.stdin.read().rsplit(' ',1)[0])['case_id'])")
echo "2) replay:"; curl -s -o /dev/null -w 'HTTP%{http_code}\n' -X POST localhost:8000/api/v1/alerts -H "Authorization: Bearer $CONN" -H "Idempotency-Key: demo-key-0001" -H 'Content-Type: application/json' -d "$BODY"
echo "3) tenant A supervisor:"; curl -s -w ' HTTP%{http_code}\n' localhost:8000/api/v1/cases/$CASE -H "Authorization: Bearer $SUP_A" | python3 -c "import sys;t=sys.stdin.read();import json;b,c=t.rsplit(' ',1);d=json.loads(b);print(d['status'],[x['to'] for x in d['timeline']],c)"
echo "4) tenant B supervisor:"; curl -s -w ' HTTP%{http_code}\n' localhost:8000/api/v1/cases/$CASE -H "Authorization: Bearer $SUP_B"
echo "5) audit (tenant A auditor):"; curl -s localhost:8000/api/v1/audit-events -H "Authorization: Bearer $(python3 scripts/dev_token.py a.auditor1@synthetic)" | python3 -c "import sys,json;print([e['action'] for e in json.load(sys.stdin)['items']])"
