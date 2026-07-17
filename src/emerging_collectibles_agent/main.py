"""Command-line entrypoint.

Commands:
  run-once     — run a single agentic cycle
  run-forever  — run continuously (background worker)
  run-app      — start worker + Streamlit dashboard together
  export       — export the latest dataframe from the database
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

from .config import load_config
from .logging_config import get_logger, setup_logging

log = get_logger("main")


def _apply_network_proxy(config):
    """If configured (Krylov), set HTTP(S)_PROXY in the environment BEFORE any
    HTTP client is created, so all outbound crawling routes through the proxy.
    httpx/requests/urllib read these from the environment automatically."""
    net = config.get("network", {})
    if not net.get("apply_proxy_env", False):
        return
    hp = net.get("http_proxy", "")
    hsp = net.get("https_proxy", hp)
    if hp:
        os.environ["HTTP_PROXY"] = hp
        os.environ["http_proxy"] = hp
    if hsp:
        os.environ["HTTPS_PROXY"] = hsp
        os.environ["https_proxy"] = hsp
    if net.get("no_proxy"):
        os.environ["NO_PROXY"] = net["no_proxy"]
        os.environ["no_proxy"] = net["no_proxy"]
    log.info("Outbound proxy applied from config: %s", hsp or hp)


def _cmd_run_once(args):
    from .orchestrator import Orchestrator
    config = load_config(args.config)
    _apply_network_proxy(config)
    orch = Orchestrator(config)
    try:
        result = orch.run_cycle()
        print(f"\nCycle complete: run_id={result['run_id']}")
        print(f"  URLs discovered : {result['urls_discovered']}")
        print(f"  URLs crawled    : {result['urls_crawled']}")
        print(f"  URLs blocked    : {result['urls_blocked']}")
        print(f"  Products        : {result['products_extracted']}")
        print(f"  Validated       : {result['products_validated']}")
        print(f"  Events          : {result['events_extracted']}")
        print(f"  Vertical coverage: {result['coverage']['vertical_coverage_score']}")
        print(f"\nOutputs written to: {config.output_dir}/")
    finally:
        orch.close()


def _cmd_run_forever(args):
    from .scheduler import Scheduler
    config = load_config(args.config)
    _apply_network_proxy(config)
    Scheduler(config).run_forever()


def _cmd_export(args):
    from .orchestrator import Orchestrator
    config = load_config(args.config)
    _apply_network_proxy(config)
    orch = Orchestrator(config)
    try:
        # rebuild latest dataframe from product_dataframe snapshot
        import json
        import pandas as pd
        from .exports import PRODUCT_COLUMNS
        rows = orch.db.query("SELECT payload_json FROM product_dataframe ORDER BY updated_at DESC")
        data = [json.loads(r["payload_json"]) for r in rows]
        df = pd.DataFrame(data, columns=PRODUCT_COLUMNS) if data else pd.DataFrame(columns=PRODUCT_COLUMNS)
        out = config.output_dir
        os.makedirs(out, exist_ok=True)
        df.to_csv(os.path.join(out, "products_latest.csv"), index=False)
        try:
            df.to_parquet(os.path.join(out, "products_latest.parquet"), index=False)
        except Exception as exc:
            log.warning("parquet export skipped: %s", exc)
        df.to_json(os.path.join(out, "products_latest.json"), orient="records", indent=2)
        orch.exporter.save_aux()
        print(f"Exported {len(df)} products to {out}/")
    finally:
        orch.close()


def _cmd_run_app(args):
    """Start the background worker and the Streamlit dashboard together."""
    config = load_config(args.config)
    _apply_network_proxy(config)  # child processes inherit the proxied environment
    here = os.path.dirname(os.path.abspath(__file__))
    dashboard = os.path.join(here, "dashboard", "streamlit_app.py")

    worker = subprocess.Popen([sys.executable, "-m", "emerging_collectibles_agent.main",
                               "run-forever", "--config", args.config])
    log.info("Started background worker (pid=%s)", worker.pid)
    refresh = config.get("dashboard.refresh_seconds", 30)
    env = dict(os.environ, COLLECTIBLES_CONFIG=args.config)
    dash = subprocess.Popen(
        ["streamlit", "run", dashboard,
         "--server.port", str(args.port),
         "--server.address", args.address],
        env=env)
    log.info("Started Streamlit dashboard (pid=%s) on %s:%s", dash.pid, args.address, args.port)
    try:
        while True:
            if worker.poll() is not None:
                log.error("Worker exited (code %s); restarting.", worker.returncode)
                worker = subprocess.Popen([sys.executable, "-m", "emerging_collectibles_agent.main",
                                           "run-forever", "--config", args.config])
            if dash.poll() is not None:
                log.error("Dashboard exited (code %s); stopping.", dash.returncode)
                break
            time.sleep(5)
    except KeyboardInterrupt:
        log.info("Shutting down worker + dashboard.")
    finally:
        for p in (worker, dash):
            try:
                p.terminate()
            except Exception:
                pass


def main(argv=None):
    setup_logging()
    parser = argparse.ArgumentParser(
        prog="emerging_collectibles_agent",
        description="External Emerging Collectibles Intelligence Agent")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    p_once = sub.add_parser("run-once", help="Run a single agentic cycle")
    p_once.add_argument("--config", default="config.yaml")
    p_once.set_defaults(func=_cmd_run_once)

    p_forever = sub.add_parser("run-forever", help="Run continuously")
    p_forever.add_argument("--config", default="config.yaml")
    p_forever.set_defaults(func=_cmd_run_forever)

    p_export = sub.add_parser("export", help="Export latest dataframe")
    p_export.add_argument("--config", default="config.yaml")
    p_export.set_defaults(func=_cmd_export)

    p_app = sub.add_parser("run-app", help="Run worker + dashboard together")
    p_app.add_argument("--config", default="config.yaml")
    p_app.add_argument("--port", default=8501, type=int)
    p_app.add_argument("--address", default="0.0.0.0")
    p_app.set_defaults(func=_cmd_run_app)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
