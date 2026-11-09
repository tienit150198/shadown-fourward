#!/usr/bin/env bash
# SHADOW dev server.
#   ./run.sh          -> http://localhost:8000 (laptop browser; camera works on localhost)
#   ./run.sh tunnel   -> also starts `ngrok http 8000` (phones need HTTPS for the camera)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
PORT="${PORT:-8000}"

if [ ! -x .venv/bin/python ]; then
  echo "==> Creating virtualenv (.venv)"
  PY="$(command -v python3.11 || command -v python3.12 || command -v python3)"
  "$PY" -m venv .venv
fi
echo "==> Installing requirements"
.venv/bin/pip install -q -r requirements.txt

if [ -f .env ]; then
  echo "==> Loading .env"
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

NGROK_PID=""
cleanup() {
  if [ -n "$NGROK_PID" ]; then kill "$NGROK_PID" 2>/dev/null || true; fi
}
trap cleanup EXIT INT TERM

if [ "${1:-}" = "tunnel" ]; then
  if ! command -v ngrok >/dev/null 2>&1; then
    echo "ngrok not found. Install it (brew install ngrok) and run 'ngrok config add-authtoken <token>'." >&2
    exit 1
  fi
  echo "==> Starting ngrok tunnel to port $PORT"
  ngrok http "$PORT" --log=stdout >/tmp/shadow-ngrok.log 2>&1 &
  NGROK_PID=$!
  (
    for _ in $(seq 1 30); do
      sleep 1
      URL="$(curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null \
        | grep -o '"public_url":"https://[^"]*"' | head -n1 | cut -d'"' -f4 || true)"
      if [ -n "$URL" ]; then
        echo ""
        echo "=================================================================="
        echo "  Open on your phone (HTTPS, camera allowed):  $URL"
        echo "=================================================================="
        echo ""
        exit 0
      fi
    done
    echo "Could not read the ngrok URL; check http://127.0.0.1:4040 or /tmp/shadow-ngrok.log" >&2
  ) &
fi

cd "$ROOT/backend"
echo "==> SHADOW on http://0.0.0.0:$PORT  (mode: ${SHADOW_MODE:-auto})"
# ws-max-size: camera frames are ~50-150KB base64 per message (server rejects > ~4 MB); 6 MiB cap.
"$ROOT/.venv/bin/python" -m uvicorn main:app --host 0.0.0.0 --port "$PORT" --ws-max-size 6291456
