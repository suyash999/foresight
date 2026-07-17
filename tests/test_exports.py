import os

import pandas as pd

from emerging_collectibles_agent.db import Database
from emerging_collectibles_agent.exports import Exporter, PRODUCT_COLUMNS


def test_exports_write_all_formats(config, tmp_path):
    # point outputs + db at tmp
    config.raw["output_dir"] = str(tmp_path / "outputs")
    db = Database(str(tmp_path / "t.db"))
    exporter = Exporter(config, db)
    row = {c: "" for c in PRODUCT_COLUMNS}
    row.update({"product_id": "p1", "product_name": "Test Card", "EPS": 55.0, "VTM": 42.0,
                "final_trend_status": "Watchlist", "vertical": "Trading Cards"})
    df = exporter.save_products([row], "9839-test")
    assert not df.empty

    out = str(tmp_path / "outputs")
    assert os.path.exists(os.path.join(out, "products_latest.csv"))
    assert os.path.exists(os.path.join(out, "products_latest.json"))
    # parquet may be skipped only if pyarrow missing; it's a dependency so must exist
    assert os.path.exists(os.path.join(out, "products_latest.parquet"))

    loaded = pd.read_csv(os.path.join(out, "products_latest.csv"))
    assert "EPS" in loaded.columns
    assert loaded.iloc[0]["product_id"] == "p1"

    # sqlite snapshot persisted
    snap = db.table_df("product_dataframe")
    assert len(snap) == 1
    db.close()


def test_empty_export_ok(config, tmp_path):
    config.raw["output_dir"] = str(tmp_path / "outputs")
    db = Database(str(tmp_path / "t2.db"))
    exporter = Exporter(config, db)
    df = exporter.save_products([], "9839-empty")
    assert list(df.columns) == PRODUCT_COLUMNS
    assert os.path.exists(os.path.join(str(tmp_path / "outputs"), "products_latest.csv"))
    db.close()


def test_dashboard_data_loads(config, tmp_path):
    # simulate dashboard read path
    db = Database(str(tmp_path / "t3.db"))
    db.insert("runs", {"run_id": "r1", "run_reference": 9839, "started_at": "2026-07-17T00:00:00+00:00",
                       "finished_at": None, "status": "completed", "urls_discovered": 10,
                       "urls_crawled": 5, "urls_blocked": 1, "products_extracted": 3,
                       "products_validated": 1, "events_extracted": 2, "errors": 0})
    runs = db.table_df("runs")
    assert len(runs) == 1
    assert runs.iloc[0]["urls_discovered"] == 10
    db.close()
