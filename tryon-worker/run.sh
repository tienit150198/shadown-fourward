#!/usr/bin/env bash
# Start the SHADOW try-on worker.
#   ./run.sh            real CatVTON model (needs ./setup_macmini.sh first)
#   ./run.sh --mock     no model, returns a simple composite (to test SHADOW <-> worker)
#   ./run.sh --warmup   load the model and run a 2-step warm-up at startup
# Settings come from tryon-worker/.env (see .env.example) and the environment.
set -euo pipefail

cd "$(dirname "$0")"
WORKER_DIR="$(pwd)"

if [ -f "$WORKER_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$WORKER_DIR/.env"
  set +a
fi

for arg in "$@"; do
  case "$arg" in
    --mock) export TRYON_MOCK=1 ;;
    --warmup) export TRYON_WARMUP=1 ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg (use --mock, --warmup)" >&2; exit 2 ;;
  esac
done

VPY="$WORKER_DIR/.venv/bin/python"
if [ ! -x "$VPY" ]; then
  if [ "${TRYON_MOCK:-0}" = "1" ] && command -v python3 >/dev/null 2>&1 \
     && python3 -c 'import fastapi, uvicorn, PIL' >/dev/null 2>&1; then
    VPY="$(command -v python3)"
  else
    echo "Chưa có .venv - hãy chạy ./setup_macmini.sh trước." >&2
    exit 1
  fi
fi

export PORT="${PORT:-8188}"
# Ops not implemented on MPS fall back to CPU instead of crashing.
export PYTORCH_ENABLE_MPS_FALLBACK="${PYTORCH_ENABLE_MPS_FALLBACK:-1}"
export TOKENIZERS_PARALLELISM=false
export HF_HUB_DISABLE_TELEMETRY=1

# Print the address to put in SHADOW's .env
IFACE="$(route -n get default 2>/dev/null | awk '/interface:/{print $2; exit}' || true)"
IP=""
if [ -n "$IFACE" ]; then IP="$(ipconfig getifaddr "$IFACE" 2>/dev/null || true)"; fi
if [ -z "$IP" ]; then IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"; fi
echo "------------------------------------------------------------"
echo " SHADOW try-on worker (mock=${TRYON_MOCK:-0}) - port $PORT"
if [ -n "$IP" ]; then
  echo " Trên laptop, .env của SHADOW: TRYON_WORKER_URL=http://$IP:$PORT"
fi
echo " Kiểm tra: curl http://localhost:$PORT/health      Dừng: Ctrl+C"
echo "------------------------------------------------------------"

# caffeinate -i keeps the Mac from idle-sleeping while the server runs.
if command -v caffeinate >/dev/null 2>&1; then
  exec caffeinate -i "$VPY" server.py
fi
exec "$VPY" server.py
