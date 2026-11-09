#!/usr/bin/env bash
# One-time setup of the SHADOW try-on worker on a Mac mini (Apple Silicon).
#   - finds (or installs via Homebrew) Python 3.12 / 3.11
#   - creates .venv and installs requirements.txt
#   - checks that PyTorch sees the Apple GPU (MPS)
#   - pre-downloads the model weights (~2.8 GB) into ~/.cache/huggingface
#   - runs the mock self-test and prints the TRYON_WORKER_URL for SHADOW's .env
#
# Usage:  ./setup_macmini.sh            (re-running is safe)
#         PYTHON=/path/to/python3.12 ./setup_macmini.sh
#         SKIP_DOWNLOAD=1 ./setup_macmini.sh
#         RECREATE_VENV=1 ./setup_macmini.sh
set -euo pipefail

cd "$(dirname "$0")"
WORKER_DIR="$(pwd)"
VENV="$WORKER_DIR/.venv"
PORT="${PORT:-8188}"

say()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[!] %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[1;31m[x] %s\033[0m\n' "$*" >&2; exit 1; }

# Load optional settings (TRYON_SAFETY_CHECK, HF_TOKEN, PORT, ...) so the download matches the server config.
if [ -f "$WORKER_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$WORKER_DIR/.env"
  set +a
  PORT="${PORT:-8188}"
fi

# ---------------------------------------------------------------- 1. machine check
say "Kiểm tra máy"
OS="$(uname -s)"
ARCH="$(uname -m)"
[ "$OS" = "Darwin" ] || warn "Script này viết cho macOS (đang chạy trên $OS)."
if [ "$ARCH" != "arm64" ]; then
  if [ "$(sysctl -in sysctl.proc_translated 2>/dev/null || echo 0)" = "1" ]; then
    die "Terminal đang chạy dưới Rosetta (x86_64). Mở Terminal bản gốc (không tích 'Open using Rosetta') rồi chạy lại."
  fi
  warn "Máy không phải Apple Silicon ($ARCH): sẽ không có GPU MPS, chỉ chạy được CPU (rất chậm) hoặc TRYON_MOCK=1."
fi
echo "macOS $(sw_vers -productVersion 2>/dev/null || echo '?') / $ARCH / RAM $(( $(sysctl -n hw.memsize 2>/dev/null || echo 0) / 1073741824 )) GB"

# ---------------------------------------------------------------- 2. python
say "Tìm Python 3.12 / 3.11"
py_ok() {
  # usable = exists, version 3.11 or 3.12, and native arch matches the machine
  [ -n "${1:-}" ] && [ -x "$1" ] || return 1
  "$1" -c 'import sys, platform; ok = sys.version_info[:2] in ((3, 11), (3, 12)); sys.exit(0 if ok else 1)' 2>/dev/null || return 1
  if [ "$ARCH" = "arm64" ]; then
    [ "$("$1" -c 'import platform; print(platform.machine())' 2>/dev/null)" = "arm64" ] || return 1
  fi
  return 0
}

PY=""
if [ -n "${PYTHON:-}" ]; then
  py_ok "$PYTHON" || die "PYTHON=$PYTHON không phải Python 3.11/3.12 (arm64)."
  PY="$PYTHON"
else
  for cand in /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 \
              "$(command -v python3.12 || true)" "$(command -v python3.11 || true)"; do
    if py_ok "$cand"; then PY="$cand"; break; fi
  done
fi

if [ -z "$PY" ]; then
  BREW=""
  if command -v brew >/dev/null 2>&1; then BREW="$(command -v brew)";
  elif [ -x /opt/homebrew/bin/brew ]; then BREW=/opt/homebrew/bin/brew; fi
  if [ -z "$BREW" ]; then
    cat >&2 <<'EOF'
[x] Không tìm thấy Python 3.11/3.12 và cũng chưa có Homebrew.
    Cài Homebrew (dán vào Terminal, nhập mật khẩu máy khi được hỏi):
      /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    Sau đó chạy lại:  ./setup_macmini.sh
EOF
    exit 1
  fi
  say "Cài python@3.12 bằng Homebrew (vài phút)"
  "$BREW" install python@3.12
  PY="$("$BREW" --prefix)/bin/python3.12"
  py_ok "$PY" || die "Đã cài python@3.12 nhưng không chạy được $PY"
fi
echo "Python: $PY ($("$PY" -V 2>&1))"

# ---------------------------------------------------------------- 3. venv + packages
if [ -d "$VENV" ] && { [ "${RECREATE_VENV:-0}" = "1" ] || ! py_ok "$VENV/bin/python"; }; then
  say "Tạo lại .venv"
  rm -rf "$VENV"
fi
if [ ! -d "$VENV" ]; then
  say "Tạo môi trường ảo .venv"
  "$PY" -m venv "$VENV"
fi
VPY="$VENV/bin/python"

say "Cài thư viện (lần đầu tải ~1 GB, 3-10 phút)"
"$VPY" -m pip install --upgrade pip wheel >/dev/null
"$VPY" -m pip install -r requirements.txt

say "Kiểm tra PyTorch / GPU Apple (MPS)"
"$VPY" - <<'EOF'
import platform, torch
mps = torch.backends.mps.is_available()
print(f"torch {torch.__version__} | python {platform.python_version()} {platform.machine()} | MPS available: {mps}")
if mps:
    x = torch.randn(256, 256, device="mps", dtype=torch.float16)
    y = (x @ x).float().sum().item()
    print("MPS matmul OK")
else:
    print("!! MPS không dùng được - server sẽ chạy CPU (chậm). Cần Apple Silicon + macOS 12.3+ + Python arm64.")
EOF

# ---------------------------------------------------------------- 4. weights
if [ "${SKIP_DOWNLOAD:-0}" != "1" ]; then
  say "Tải trọng số mô hình (~2.8 GB, chỉ lần đầu)"
  "$VPY" download_weights.py || die "Tải trọng số thất bại - xem thông báo ở trên rồi chạy lại ./setup_macmini.sh"
else
  warn "SKIP_DOWNLOAD=1: bỏ qua tải trọng số (server sẽ tải khi chạy lần đầu)."
fi

# ---------------------------------------------------------------- 5. self-test
say "Tự kiểm tra (API chế độ mock + mask + pipeline UNet tí hon, ~30 giây)"
if TRYON_MOCK=1 "$VPY" -m pytest -q -p no:cacheprovider tests; then
  echo "Self-test OK"
else
  warn "Self-test thất bại (xem lỗi ở trên). Server có thể vẫn chạy được."
fi

chmod +x "$WORKER_DIR/run.sh" 2>/dev/null || true
[ -f "$WORKER_DIR/.env" ] || { [ -f "$WORKER_DIR/.env.example" ] && cp "$WORKER_DIR/.env.example" "$WORKER_DIR/.env" && echo "Đã tạo .env từ .env.example"; }

# ---------------------------------------------------------------- 6. LAN address
lan_ip() {
  local ip iface
  iface="$(route -n get default 2>/dev/null | awk '/interface:/{print $2; exit}' || true)"
  if [ -n "$iface" ]; then
    ip="$(ipconfig getifaddr "$iface" 2>/dev/null || true)"
    [ -n "$ip" ] && { echo "$ip"; return; }
  fi
  for iface in en0 en1 en2 en3; do
    ip="$(ipconfig getifaddr "$iface" 2>/dev/null || true)"
    [ -n "$ip" ] && { echo "$ip"; return; }
  done
  echo ""
}
IP="$(lan_ip)"
HOSTNAME_LOCAL="$(scutil --get LocalHostName 2>/dev/null || hostname -s)"

say "XONG"
echo "Chạy server:        ./run.sh"
echo "Thử nhanh (mock):   ./run.sh --mock"
echo
echo "Trên laptop chạy SHADOW, thêm vào file .env của SHADOW:"
if [ -n "$IP" ]; then
  echo "    TRYON_WORKER_URL=http://$IP:$PORT"
else
  warn "Không tìm được IP LAN (Mac mini đã kết nối Wi-Fi/Ethernet chưa?)"
fi
echo "    (hoặc dùng tên máy: TRYON_WORKER_URL=http://$HOSTNAME_LOCAL.local:$PORT)"
if [ -n "${TRYON_TOKEN:-}" ]; then
  echo "    TRYON_TOKEN=<giống TRYON_TOKEN trong tryon-worker/.env>"
fi
echo "Nên đặt IP cố định cho Mac mini trong router (DHCP reservation) để địa chỉ không đổi."
