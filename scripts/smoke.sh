#!/usr/bin/env bash
# End-to-end smoke test: boots the API (EchoProvider demo mode), then drives
# the full chain: health -> session -> message -> run -> SSE -> auth.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${SMOKE_PORT:-8321}"
BASE="http://127.0.0.1:${PORT}"
LOG_FILE="$(mktemp "${TMPDIR:-/tmp}/paos_smoke.XXXXXX")"
DB_FILE="$(mktemp "${TMPDIR:-/tmp}/paos_smoke_db.XXXXXX")"
BODY_FILE="$(mktemp "${TMPDIR:-/tmp}/paos_smoke_body.XXXXXX")"
SERVER_PID=""

if command -v uv >/dev/null 2>&1; then
  PYTHON=(uv run python)
elif [ -x "$ROOT/.venv/bin/python" ]; then
  PYTHON=("$ROOT/.venv/bin/python")
elif command -v python3 >/dev/null 2>&1 \
  && python3 -c "import uvicorn" >/dev/null 2>&1; then
  PYTHON=(python3)
else
  echo "ERROR: install uv or create a POSIX .venv with project dependencies." >&2
  exit 1
fi

show_log() {
  echo "==> Server log:" >&2
  cat "$LOG_FILE" >&2
}

cleanup() {
  status=$?
  trap - EXIT
  if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  if [ "$status" -ne 0 ]; then
    show_log
  fi
  rm -f "$LOG_FILE" "$DB_FILE" "$DB_FILE-shm" "$DB_FILE-wal" "$BODY_FILE"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

cd "$ROOT"

SMOKE_API_KEY="${PERSONAL_AI_DEV_API_KEY:-}"
if [ -z "$SMOKE_API_KEY" ]; then
  SMOKE_API_KEY="$("${PYTHON[@]}" -c \
    "import secrets; print('paios-smoke-' + secrets.token_urlsafe(32))")"
fi
SMOKE_HASH_SECRET="${PERSONAL_AI_KEY_HASH_SECRET:-}"
if [ -z "$SMOKE_HASH_SECRET" ]; then
  SMOKE_HASH_SECRET="$("${PYTHON[@]}" -c \
    "import secrets; print('paios-hash-' + secrets.token_urlsafe(48))")"
fi
export PERSONAL_AI_DEV_API_KEY="$SMOKE_API_KEY"
export PERSONAL_AI_API_KEY="$SMOKE_API_KEY"
export PERSONAL_AI_KEY_HASH_SECRET="$SMOKE_HASH_SECRET"
AUTH_HEADER="X-API-Key: $SMOKE_API_KEY"

echo "==> Starting API on :${PORT} (EchoProvider demo mode)"
DATABASE_URL="sqlite+aiosqlite:///$DB_FILE" \
PYTHONPATH="$ROOT/src" "${PYTHON[@]}" -m uvicorn \
  apps.api.main:create_app --factory --host 127.0.0.1 --port "$PORT" \
  >"$LOG_FILE" 2>&1 &
SERVER_PID=$!

READY=0
for _ in $(seq 1 60); do
  if curl -fsS "$BASE/healthz" >/dev/null 2>&1; then
    READY=1
    break
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "ERROR: API process exited before becoming ready." >&2
    exit 1
  fi
  sleep 1
done
if [ "$READY" -ne 1 ]; then
  echo "ERROR: API did not become ready within 60 seconds." >&2
  exit 1
fi

echo "==> 1. health"
curl -fsS "$BASE/healthz"
echo ""

echo "==> 2. tools (expect builtin connectors)"
TOOLS="$(curl -fsS "$BASE/v1/tools" -H "$AUTH_HEADER")"
printf '%s' "$TOOLS" | "${PYTHON[@]}" -c \
  "import sys,json; print([t['name'] for t in json.load(sys.stdin)])"
printf '%s' "$TOOLS" | "${PYTHON[@]}" -c \
  "import sys,json; d=json.load(sys.stdin); assert len(d)>=3, 'expected >=3 tools'; assert any('calculator' in t['name'] for t in d), 'calculator missing'"

echo "==> 3. create session"
SID="$(curl -fsS -X POST "$BASE/v1/sessions" -H "$AUTH_HEADER" \
  -H "Content-Type: application/json" -d '{}' \
  | "${PYTHON[@]}" -c "import sys,json; print(json.load(sys.stdin)['id'])")"
echo "session=$SID"

echo "==> 4. send message -> run"
printf '{"text":"hello, introduce yourself"}' >"$BODY_FILE"
RESP="$(curl -fsS -X POST "$BASE/v1/sessions/$SID/messages" \
  -H "$AUTH_HEADER" -H "Content-Type: application/json" --data-binary "@$BODY_FILE")"
RID="$(printf '%s' "$RESP" \
  | "${PYTHON[@]}" -c "import sys,json; print(json.load(sys.stdin)['run_id'])")"
echo "run=$RID"

echo "==> 5. wait for run terminal status"
RUN_STATUS=""
RUN_JSON=""
for _ in $(seq 1 60); do
  RUN_JSON="$(curl -fsS "$BASE/v1/runs/$RID" -H "$AUTH_HEADER")"
  RUN_STATUS="$(printf '%s' "$RUN_JSON" \
    | "${PYTHON[@]}" -c "import sys,json; print(json.load(sys.stdin)['status'])")"
  case "$RUN_STATUS" in
    completed)
      break
      ;;
    failed|cancelled|waiting_approval)
      echo "ERROR: run reached unexpected terminal status: $RUN_STATUS" >&2
      printf '%s\n' "$RUN_JSON" >&2
      exit 1
      ;;
  esac
  sleep 1
done
if [ "$RUN_STATUS" != "completed" ]; then
  echo "ERROR: run did not reach a terminal status within 60 seconds (last: $RUN_STATUS)." >&2
  exit 1
fi
printf '%s' "$RUN_JSON" | "${PYTHON[@]}" -c \
  "import sys,json; d=json.load(sys.stdin); print('status:', d['status']); print('steps:', [s['step_type'] for s in d['steps']])"

echo "==> 6. SSE replay"
SSE="$(curl -fsS -N "$BASE/v1/runs/$RID/stream" -H "$AUTH_HEADER" --max-time 5 || true)"
SSE_EVENTS="$(printf '%s\n' "$SSE" | grep -c "^event:" || true)"
[ "$SSE_EVENTS" -gt 0 ] || { echo "ERROR: expected SSE replay events." >&2; exit 1; }
echo "SSE events: $SSE_EVENTS"

echo "==> 7. auth: wrong key -> 401"
CODE="$(curl -sS -o /dev/null -w "%{http_code}" "$BASE/v1/sessions" \
  -H "X-API-Key: wrong-key")"
[ "$CODE" = "401" ] || { echo "ERROR: expected 401, got $CODE" >&2; exit 1; }
echo "OK (401)"

echo "==> SMOKE TEST PASSED"
