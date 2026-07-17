import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import pytest

from emerging_collectibles_agent.config import load_config


@pytest.fixture()
def config():
    cfg_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "config.yaml"))
    return load_config(cfg_path)


TRADING_CARD_HTML = """
<html><head>
  <title>2024 Topps Chrome Victor Wembanyama Rookie Auto /99 Sells Out</title>
  <meta property="og:description" content="The 2024 Topps Chrome Victor Wembanyama rookie autograph is sold out and numbered to 99.">
  <script type="application/ld+json">
  {"@type":"Product","name":"2024 Topps Chrome Victor Wembanyama Rookie Autograph",
   "description":"Serial numbered /99 rookie auto, limited edition, sold out."}
  </script>
</head><body>
  <h1>2024 Topps Chrome Victor Wembanyama Rookie Auto</h1>
  <p>The 2024 Topps Chrome Victor Wembanyama rookie autograph card is a short print
     serial numbered to 99. Hobby boxes are sold out and demand is spiking after his
     record-breaking rookie season. PSA 10 copies are hitting record prices at auction.</p>
</body></html>
"""

NEWS_ARTICLE_HTML = """
<html><head>
  <title>Pokemon announces new 151 set release date; boxes expected to sell out</title>
  <meta name="description" content="The Pokemon Company announced a new set releasing next month.">
  <meta property="article:published_time" content="2026-07-15T10:00:00Z">
</head><body>
  <h1>Pokemon announces new 151 set</h1>
  <p>The Pokemon Company announced the release date for a new Pokemon TCG set.
     The booster boxes are expected to sell out. Collectors are already lining up
     preorders for the limited edition sealed products.</p>
</body></html>
"""
