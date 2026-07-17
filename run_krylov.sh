#!/usr/bin/env bash
# =============================================================================
# Krylov launcher for ForSight (External Emerging Collectibles Intelligence).
# Sets the eBay proxy + PATH so the agent can hit the net, installs deps, and
# starts BOTH services: the background worker (crawls forever) and the Streamlit
# dashboard. Run from the repo root:  bash run_krylov.sh   (optional: PORT arg)
# =============================================================================

# --- proxy + PATH (lets Krylov hit the net) — re-applied on every run --------
export PATH="/home/$KRYLOV_PRINCIPAL/.local/bin:$PATH"
export HTTP_PROXY="http://httpproxy-tcop.vip.ebay.com:80"
export HTTPS_PROXY="http://httpproxy-tcop.vip.ebay.com:80"
export NO_PROXY="localhost,127.0.0.1,::1"

PROXY="http://httpproxy-tcop.vip.ebay.com:80"
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
