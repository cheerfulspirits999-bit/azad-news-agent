#!/usr/bin/env bash
# run.sh - Roman Urdu translator server ko aasani se start karein
#
#   ./run.sh                      # .env parh kar server start (default: port 8080)
#   ./run.sh --mock               # offline demo mode (koi API key nahi chahiye)
#   ./run.sh --test               # sirf tests chalayein
#   PORT=9000 ./run.sh            # doosra port
#
set -euo pipefail
cd "$(dirname "$0")"

MOCK=0
TEST_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --mock) MOCK=1 ;;
    --test) TEST_ONLY=1 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "unknown flag: $arg (use --mock or --test)"; exit 2 ;;
  esac
done

# .env mojood ho to load karein
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
  echo "[run.sh] .env loaded"
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"

if [[ $TEST_ONLY -eq 1 ]]; then
  exec "$PYTHON_BIN" tests/test_offline.py
fi

if [[ -z "${RU_API_KEY:-}${OPENAI_API_KEY:-}${GEMINI_API_KEY:-}${ANTHROPIC_API_KEY:-}" && $MOCK -eq 0 ]]; then
  echo "[run.sh] WARNING: koi API key set nahi hai. Server MOCK mode mein chalega"
  echo "[run.sh]          (structure test theek hai, lekin Roman Urdu quality achhi NAHI hogi)."
  echo "[run.sh] Real mode: cp .env.example .env  -> RU_API_KEY bharein -> ./run.sh"
  MOCK=1
fi

PORT="${PORT:-8080}"
if [[ $MOCK -eq 1 ]]; then
  echo "[run.sh] starting MOCK server on port $PORT"
  exec "$PYTHON_BIN" http_server.py --host 0.0.0.0 --port "$PORT" --provider mock
fi

echo "[run.sh] starting server on port $PORT (provider=${RU_PROVIDER:-openai}, model=${RU_MODEL:-gpt-4o-mini})"
exec "$PYTHON_BIN" http_server.py --host 0.0.0.0 --port "$PORT"
