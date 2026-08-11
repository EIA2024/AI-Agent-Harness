#!/usr/bin/env bash
# End-to-end smoke test: boots the API (EchoProvider demo mode), then drives
# the full chain: health → session → message → run → SSE → auth.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${SMOKE_PORT:-8321}"
BASE="http://127.0.0.1:${PORT}"

cd "$ROOT"
mkdir -p data

echo "==> Starting API on :${PORT} (EchoProvider demo mode)"
DATABASE_URL="sqlite+aiosqlite:///./data/smoke.db" \
PYTHONPATH="$ROOT/src" .venv/Scripts/python.exe -m uvicorn \
  apps.api.main:create_app --factory --host 127.0.0.1 --port "$PORT" \
  > /tmp/paos_smoke.log 2>&1 &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null || true' EXIT

for i in $(seq 1 30); do
  curl -sf "$BASE/healthz" > /dev/null 2>&1 && break
  sleep 1
done

echo "==> 1. health"
curl -s "$BASE/healthz" && echo ""

echo "==> 2. tools (expect 6 builtin connectors)"
TOOLS=$(curl -s "$BASE/v1/tools" -H "X-API-Key: dev-key")
echo "$TOOLS" | python -c "import sys,json; print([t['name'] for t in json.load(sys.stdin)])"
echo "$TOOLS" | python -c "import sys,json; d=json.load(sys.stdin); assert len(d)>=3, 'expected >=3 tools'; assert any('calculator' in t['name'] for t in d), 'calculator missing'"

echo "==> 3. create session"
SID=$(curl -s -X POST "$BASE/v1/sessions" -H "X-API-Key: dev-key" -H "Content-Type: application/json" -d '{}' \
  | python -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "session=$SID"

echo "==> 4. send message -> run"
printf '{"text":"hello, introduce yourself"}' > /tmp/smoke_body.json
RESP=$(curl -s -X POST "$BASE/v1/sessions/$SID/messages" \
  -H "X-API-Key: dev-key" -H "Content-Type: application/json" --data-binary @/tmp/smoke_body.json)
RID=$(echo "$RESP" | python -c "import sys,json; print(json.load(sys.stdin)['run_id'])")
echo "run=$RID"

echo "==> 5. run completes"
curl -s "$BASE/v1/runs/$RID" -H "X-API-Key: dev-key" \
  | python -c "import sys,json; d=json.load(sys.stdin); assert d['status']=='completed', d['status']; print('status:', d['status']); print('steps:', [s['step_type'] for s in d['steps']])"

echo "==> 6. SSE replay"
curl -s -N "$BASE/v1/runs/$RID/stream" -H "X-API-Key: dev-key" --max-time 5 | grep -c "^event:" | xargs echo "SSE events:"

echo "==> 7. auth: wrong key -> 401"
CODE=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/v1/sessions" -H "X-API-Key: wrong-key")
[ "$CODE" = "401" ] || { echo "FAIL: expected 401, got $CODE"; exit 1; }
echo "OK (401)"

echo "==> SMOKE TEST PASSED"
