#!/usr/bin/env bash
# =============================================================================
# Krylov launcher for ForSight (External Emerging Collectibles Intelligence).
# Sets the eBay proxy + PATH so the agent can hit the net, installs deps, and
# starts BOTH services: the background worker (crawls forever) and the Streamlit
# dashboard. Run from the repo root:  bash run_krylov.sh   (optional: PORT arg)
# =============================================================================

# --- proxy + PATH (lets Krylov hit the net) — re-applied on every run --------
export PATH="/home/$KRYLOV_PRINCIPAL/.local/bin:$PATH"

# The eBay Squid proxy needs Basic auth. If HTTPS_PROXY is already exported with
# credentials (http://user:pass@host:port) we keep it. Otherwise, if a
# credentials.json with a proxy_user/proxy_password (or username/password) is
# present, build an authenticated proxy URL from it. Falls back to the bare proxy.
_BARE_PROXY="http://httpproxy-tcop.vip.ebay.com:80"
if printf '%s' "$HTTPS_PROXY" | grep -q '@'; then
  : # already authenticated — leave it as-is
elif [ -f credentials.json ]; then
  AUTH_PROXY="$(python3 - <<'PY'
import json, urllib.parse
try:
    d = json.load(open("credentials.json"))
    u = d.get("proxy_user") or d.get("username")
    p = d.get("proxy_password") or d.get("password")
    if u and p:
        print(f"http://{urllib.parse.quote(u)}:{urllib.parse.quote(p)}@httpproxy-tcop.vip.ebay.com:80")
except Exception:
    pass
PY
)"
  export HTTPS_PROXY="${AUTH_PROXY:-$_BARE_PROXY}"
else
  export HTTPS_PROXY="$_BARE_PROXY"
fi
export HTTP_PROXY="$HTTPS_PROXY"
export NO_PROXY="localhost,127.0.0.1,::1,.vip.ebay.com,.corp.ebay.com"

PROXY="$HTTPS_PROXY"
PORT="${1:-8888}"
cd "$(dirname "$0")"

echo "[1/3] Installing dependencies (via proxy, --user) ..."
pip install --user --proxy "$PROXY" -r requirements.txt || true
# feedparser's sgmllib3k dep can fail to build on modern setuptools:
pip install --user --proxy "$PROXY" --no-deps feedparser || true
python3 -c "import sgmllib" 2>/dev/null || pip install --user --proxy "$PROXY" sgmllib3k || true

echo "[2/3] Starting background worker (logs -> worker.log) ..."
PYTHONPATH=src nohup python3 -m emerging_collectibles_agent.main run-forever \
  --config config.yaml > worker.log 2>&1 &
echo "      worker pid $!"

echo "[3/3] Freeing port $PORT and starting the dashboard ..."
# terminate any existing app on the port before starting a new instance
pkill -f "streamlit run.*server.port $PORT" 2>/dev/null || true
sleep 1

COLLECTIBLES_CONFIG=config.yaml python3 -m streamlit run \
  src/emerging_collectibles_agent/dashboard/streamlit_app.py \
  --server.port "$PORT" --server.headless true
