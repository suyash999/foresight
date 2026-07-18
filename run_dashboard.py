#!/usr/bin/env python3
"""Launch the Foresight dashboard and print usable URLs (not 0.0.0.0).

    python3 run_dashboard.py           # port 8888
    PORT=8600 python3 run_dashboard.py # custom port

Prints the container Network URL and the Krylov jupyter-server-proxy URL, then
runs Streamlit. Headless/CORS/XSRF come from .streamlit/config.toml (proxy-safe).
"""
import os
import socket
import subprocess
import sys

PORT = int(os.environ.get("PORT", "8888"))

try:
    ip = socket.gethostbyname(socket.gethostname())
except Exception:
    ip = "127.0.0.1"

os.environ.setdefault("COLLECTIBLES_CONFIG", "config.yaml")

print("\n" + "=" * 70)
print("  Foresight dashboard")
print(f"  Network URL (container):  http://{ip}:{PORT}")
print(f"  Krylov proxy URL:         <your-workspace-url>/proxy/{PORT}/")
print("     e.g. https://<host>.aihub.krylov.vip.ebay.com/workspace/"
      "<user>/<ws>/proxy/%d/" % PORT)
print("  (open the proxy URL WITH the trailing slash)")
print("=" * 70 + "\n")

cmd = [
    sys.executable, "-m", "streamlit", "run",
    "src/emerging_collectibles_agent/dashboard/streamlit_app.py",
    "--server.port", str(PORT),
    "--server.address", "0.0.0.0",   # bind all interfaces so the proxy can reach it
]
sys.exit(subprocess.call(cmd))
