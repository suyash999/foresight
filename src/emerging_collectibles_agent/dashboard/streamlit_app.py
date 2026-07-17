"""Live Streamlit dashboard for the Emerging Collectibles Intelligence Agent.

Reads from SQLite / Parquet outputs produced by the background worker. The
worker runs independently; this dashboard only READS and auto-refreshes, so the
agent keeps crawling even when the dashboard is closed. eBay-inspired theme.

Run: streamlit run src/emerging_collectibles_agent/dashboard/streamlit_app.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# make the package importable when run via `streamlit run`
_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

try:
    import plotly.express as px
    _HAVE_PLOTLY = True
except Exception:
    _HAVE_PLOTLY = False

from emerging_collectibles_agent.config import load_config
from emerging_collectibles_agent.dashboard.theme import (COLORS, inject_css,
                                                         status_badge)

st.set_page_config(page_title="Emerging Collectibles Intelligence",
                   page_icon="🃏", layout="wide")

CONFIG_PATH = os.environ.get("COLLECTIBLES_CONFIG", "config.yaml")


@st.cache_resource
def get_config():
    try:
        return load_config(CONFIG_PATH)
    except Exception:
        return None


CFG = get_config()
DB_PATH = CFG.database_path if CFG else "data/collectibles.db"
OUT_DIR = CFG.output_dir if CFG else "outputs"
REFRESH_SECS = int(CFG.get("dashboard.refresh_seconds", 30)) if CFG else 30
STALE_SECS = int(CFG.get("runtime.stale_worker_threshold_seconds", 120)) if CFG else 120
AUTO_REFRESH = bool(CFG.get("dashboard.auto_refresh", True)) if CFG else True


@st.cache_data(ttl=10)
def load_table(table: str, order: str = "", limit: int = 0) -> pd.DataFrame:
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        sql = f"SELECT * FROM {table}"
        if order:
            sql += f" ORDER BY {order}"
        if limit:
            sql += f" LIMIT {limit}"
        df = pd.read_sql_query(sql, con)
        con.close()
        return df
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=10)
def load_products() -> pd.DataFrame:
    pq = os.path.join(OUT_DIR, "products_latest.parquet")
    csv = os.path.join(OUT_DIR, "products_latest.csv")
    try:
        if os.path.exists(pq):
            return pd.read_parquet(pq)
        if os.path.exists(csv):
            return pd.read_csv(csv)
    except Exception:
        pass
    # fall back to sqlite snapshot
    import json
    df = load_table("product_dataframe", order="updated_at DESC")
    if not df.empty and "payload_json" in df:
        rows = [json.loads(x) for x in df["payload_json"]]
        return pd.DataFrame(rows)
    return pd.DataFrame()


def _fmt_ts(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return ts or "n/a"


# ---------------------------------------------------------------------------
# Header + auto refresh
# ---------------------------------------------------------------------------
st.markdown(inject_css(), unsafe_allow_html=True)
if AUTO_REFRESH:
    components.html(
        f"<script>setTimeout(function(){{window.parent.location.reload();}}, {REFRESH_SECS*1000});</script>",
        height=0)

st.markdown(
    f"""<div class="ecia-header">
    <h1>External Emerging Collectibles Intelligence Agent</h1>
    <p>Autonomous, high-recall product-trend discovery across collectibles verticals ·
    external public sources only (eBay excluded) · run reference
    {CFG.run_reference if CFG else 9839} · auto-refresh {REFRESH_SECS}s</p>
    </div>""",
    unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Worker status banner
# ---------------------------------------------------------------------------
hb = load_table("worker_heartbeat", order="id DESC", limit=1)
runs = load_table("runs", order="started_at DESC", limit=1)
col_a, col_b, col_c, col_d = st.columns(4)
if not hb.empty:
    last_ts = hb.iloc[0]["timestamp"]
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(last_ts.replace("Z", "+00:00"))).total_seconds()
    except Exception:
        age = 1e9
    status = hb.iloc[0]["worker_status"]
    live = age <= STALE_SECS
    css = "worker-live" if live else "worker-stale"
    label = ("🟢 LIVE" if live else "🔴 STALE") + f" · {status}"
    col_a.markdown(f"**Backend worker:** <span class='{css}'>{label}</span>", unsafe_allow_html=True)
    col_b.markdown(f"**Loop state:** {hb.iloc[0]['current_loop_state']}")
    col_c.markdown(f"**Last heartbeat:** {_fmt_ts(last_ts)}")
    col_d.markdown(f"**Next run:** {_fmt_ts(hb.iloc[0]['next_run_at']) if hb.iloc[0]['next_run_at'] else 'in cycle'}")
else:
    col_a.markdown("**Backend worker:** <span class='worker-stale'>🔴 no heartbeat yet</span>",
                   unsafe_allow_html=True)
    col_b.info("Start the worker: `python -m emerging_collectibles_agent.main run-forever`")

# ---------------------------------------------------------------------------
# Load core data + sidebar filters
# ---------------------------------------------------------------------------
products = load_products()

st.sidebar.header("Filters")
if not products.empty:
    def msel(label, col):
        if col in products.columns:
            opts = sorted([x for x in products[col].dropna().unique() if str(x).strip()])
            return st.sidebar.multiselect(label, opts)
        return []
    f_vertical = msel("Vertical", "vertical")
    f_category = msel("Category", "category")
    f_status = msel("Final trend status", "final_trend_status")
    f_validation = msel("Validation status", "validation_status")
    f_confidence = msel("Confidence level", "confidence_level")
    f_stype = st.sidebar.multiselect(
        "Source type",
        sorted({t.strip() for v in products.get("source_types", pd.Series(dtype=str)).dropna()
                for t in str(v).split("|") if t.strip()}))
    f_event = st.sidebar.selectbox("Event linkage",
                                   ["All", "Event linked", "Not linked",
                                    "Upcoming event", "Recent/past event"])
    min_eps = st.sidebar.slider("Minimum EPS", 0, 100, 0)
    min_vtm = st.sidebar.slider("Minimum VTM", 0, 100, 0)

    fp = products.copy()
    if f_vertical:
        fp = fp[fp["vertical"].isin(f_vertical)]
    if f_category:
        fp = fp[fp["category"].isin(f_category)]
    if f_status:
        fp = fp[fp["final_trend_status"].isin(f_status)]
    if f_validation:
        fp = fp[fp["validation_status"].isin(f_validation)]
    if f_confidence:
        fp = fp[fp["confidence_level"].isin(f_confidence)]
    if f_stype:
        fp = fp[fp["source_types"].apply(lambda v: any(t in str(v) for t in f_stype))]
    if "EPS" in fp:
        fp = fp[fp["EPS"] >= min_eps]
    if "VTM" in fp:
        fp = fp[fp["VTM"] >= min_vtm]
    if f_event != "All" and "linked_event_status" in fp:
        if f_event == "Event linked":
            fp = fp[fp["linked_event_status"] != "not_found"]
        elif f_event == "Not linked":
            fp = fp[fp["linked_event_status"] == "not_found"]
        elif f_event == "Upcoming event":
            fp = fp[fp["linked_event_status"] == "upcoming"]
        elif f_event == "Recent/past event":
            fp = fp[fp["linked_event_status"].isin(["recently_happened", "historical", "live"])]
else:
    fp = products
    st.sidebar.info("No products yet — the worker will populate this shortly.")

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
tabs = st.tabs([
    "📊 Executive", "🔥 Products", "🔎 URL Sourcing", "🕸️ Crawl Monitor",
    "🏷️ Sources", "📰 News", "📅 Events & Seasonality", "📈 Timeline",
    "🩺 Agent Health", "🛠️ Self-Serve Learning",
])

# ---- Executive --------------------------------------------------------------
with tabs[0]:
    st.subheader("Executive Overview")
    r = runs.iloc[0] if not runs.empty else None
    m = st.columns(6)
    m[0].metric("Current run", (r["run_id"] if r is not None else "—"))
    m[1].metric("URLs discovered", int(r["urls_discovered"]) if r is not None else 0)
    m[2].metric("URLs crawled", int(r["urls_crawled"]) if r is not None else 0)
    m[3].metric("URLs blocked", int(r["urls_blocked"]) if r is not None else 0)
    m[4].metric("Products", int(r["products_extracted"]) if r is not None else 0)
    m[5].metric("Validated", int(r["products_validated"]) if r is not None else 0)
    if not products.empty:
        m2 = st.columns(6)
        strong = int((products["final_trend_status"] == "Strong Emerging").sum())
        watch = int((products["final_trend_status"] == "Watchlist").sum())
        m2[0].metric("Strong emerging", strong)
        m2[1].metric("Watchlist", watch)
        m2[2].metric("Avg EPS", round(products["EPS"].mean(), 1))
        m2[3].metric("Avg VTM", round(products["VTM"].mean(), 1))
        m2[4].metric("Verticals covered", products["vertical"].nunique())
        m2[5].metric("Events tracked", len(load_table("event_calendar")))
        if _HAVE_PLOTLY:
            cc = st.columns(2)
            vc = products["vertical"].value_counts().reset_index()
            vc.columns = ["vertical", "count"]
            fig = px.bar(vc, x="vertical", y="count", title="Products by vertical",
                         color_discrete_sequence=[COLORS["blue"]])
            cc[0].plotly_chart(fig, use_container_width=True)
            sc = products["final_trend_status"].value_counts().reset_index()
            sc.columns = ["status", "count"]
            fig2 = px.pie(sc, names="status", values="count", title="Trend status mix",
                          color_discrete_sequence=[COLORS["green"], COLORS["blue"],
                                                   COLORS["yellow"], COLORS["red"], "#9CA3AF"])
            cc[1].plotly_chart(fig2, use_container_width=True)
    else:
        st.info("Waiting for the first cycle to complete…")

# ---- Products ---------------------------------------------------------------
with tabs[1]:
    st.subheader(f"Emerging Products  ·  {len(fp)} shown (high-recall, filter in sidebar)")
    if not fp.empty:
        cols = ["product_name", "vertical", "category", "subcategory", "brand",
                "EPS", "VTM", "final_trend_status", "confidence_level",
                "validation_status", "linked_event_name", "linked_event_status",
                "primary_source_url", "first_seen_at", "last_seen_at"]
        show = [c for c in cols if c in fp.columns]
        st.dataframe(fp[show].sort_values("EPS", ascending=False), use_container_width=True,
                     height=420)
        st.markdown("#### Product detail")
        names = fp["product_name"].tolist()
        pick = st.selectbox("Select a product", names)
        row = fp[fp["product_name"] == pick].iloc[0]
        st.markdown(status_badge(row.get("final_trend_status", "")), unsafe_allow_html=True)
        d = st.columns(3)
        d[0].metric("EPS", row.get("EPS"))
        d[1].metric("VTM", row.get("VTM"))
        d[2].metric("Validation", row.get("validation_status"))
        st.markdown(f"**AI trend reason:** {row.get('ai_trend_reason','')}")
        st.markdown(f"**Evidence summary:** {row.get('evidence_summary','')}")
        st.markdown(f"**Catalyst:** {row.get('catalyst_summary','')}")
        st.markdown(f"**Counter-signals:** {row.get('counter_signals','')}")
        st.markdown(f"**Recommended next validation:** {row.get('recommended_next_validation','')}")
        st.markdown(f"**Score explanation:** {row.get('score_explanation','')}")
        with st.expander("EPS breakdown"):
            eps_cols = ["freshness_score", "source_credibility_score", "mention_velocity_score",
                        "source_diversity_score", "news_catalyst_score", "event_proximity_score",
                        "marketplace_signal_score", "scarcity_signal_score", "release_signal_score",
                        "ai_confidence_score"]
            st.json({c: row.get(c) for c in eps_cols if c in row})
        with st.expander("VTM breakdown"):
            vtm_cols = ["credible_source_coverage", "independent_confirmation", "mention_acceleration",
                        "evidence_quality", "event_validation_score", "cross_category_catalyst",
                        "recency_consistency", "noise_penalty_adjusted"]
            st.json({c: row.get(c) for c in vtm_cols if c in row})
        with st.expander("Sources & evidence snippets"):
            st.write("**Source URLs:**", row.get("source_urls", ""))
            st.write("**Evidence snippets:**", row.get("evidence_snippets", ""))
    else:
        st.info("No products match the current filters.")

# ---- URL Sourcing Intelligence ---------------------------------------------
with tabs[2]:
    st.subheader("URL Sourcing Intelligence")
    disc = load_table("url_discovery_log", order="id DESC", limit=5000)
    if not disc.empty:
        s = st.columns(5)
        s[0].metric("Discovered (log)", len(disc))
        s[1].metric("Selected", int((disc["selection_status"] == "selected").sum()))
        s[2].metric("Rejected", int((disc["selection_status"] == "rejected").sum()))
        s[3].metric("Duplicates", int(disc["duplicate_flag"].sum()))
        s[4].metric("Methods", disc["discovery_method"].nunique())
        st.markdown("##### URL funnel")
        crawl = load_table("crawl_urls")
        pages = load_table("pages")
        selected_n = int((disc["selection_status"] == "selected").sum())
        crawled_n = len(crawl)
        ok_n = int((crawl["crawl_status"] == "ok").sum()) if not crawl.empty else 0
        prod_urls = int((pages.get("product_signal_score", pd.Series(dtype=float)) > 0).sum()) if not pages.empty else 0
        funnel = pd.DataFrame({
            "stage": ["Discovered", "Selected", "Crawled", "Extracted OK", "Product-signal"],
            "count": [len(disc), selected_n, crawled_n, ok_n, prod_urls]})
        if _HAVE_PLOTLY:
            fig = px.funnel(funnel, x="count", y="stage",
                            color_discrete_sequence=[COLORS["blue"]])
            st.plotly_chart(fig, use_container_width=True)
            st.markdown("##### Discovery source mix")
            mix = disc["discovery_method"].value_counts().reset_index()
            mix.columns = ["method", "count"]
            st.plotly_chart(px.bar(mix, x="method", y="count",
                                   color_discrete_sequence=[COLORS["green"]]),
                            use_container_width=True)
        st.markdown("##### Every discovered URL (observable)")
        cols = ["discovered_at", "discovery_method", "seed_query", "discovered_url",
                "domain", "source_type", "mapped_vertical", "relevance_score",
                "credibility_score", "priority_score", "selection_status",
                "selection_reason", "rejection_reason", "robots_status", "next_action"]
        st.dataframe(disc[[c for c in cols if c in disc.columns]], use_container_width=True,
                     height=420)
    else:
        st.info("No URL discovery yet.")

# ---- Crawl Monitor ----------------------------------------------------------
with tabs[3]:
    st.subheader("URL Crawl Monitor")
    crawl = load_table("crawl_urls", order="id DESC", limit=3000)
    if not crawl.empty:
        cols = ["url", "domain", "source_type", "crawl_status", "page_relevance_score",
                "source_credibility_score", "product_signal_score", "last_crawled_at",
                "error_message"]
        st.dataframe(crawl[[c for c in cols if c in crawl.columns]], use_container_width=True,
                     height=500)
    else:
        st.info("No crawl activity yet.")

# ---- Sources ----------------------------------------------------------------
with tabs[4]:
    st.subheader("Source Credibility & Learning")
    sd = load_table("source_domains", order="pages_seen DESC")
    sq = load_table("source_quality_memory", order="source_quality_score DESC")
    if not sd.empty:
        st.markdown("##### Source credibility")
        st.dataframe(sd, use_container_width=True, height=300)
    if not sq.empty:
        st.markdown("##### Source quality memory (self-learning)")
        cols = ["domain", "source_quality_score", "source_fatigue_score", "recovery_boost",
                "success_count", "failure_count", "blocked_count", "false_positive_count",
                "products_found", "validated_found", "avg_eps", "avg_vtm"]
        st.dataframe(sq[[c for c in cols if c in sq.columns]], use_container_width=True, height=300)
    if sd.empty and sq.empty:
        st.info("No source data yet.")

# ---- News -------------------------------------------------------------------
with tabs[5]:
    st.subheader("News Catalyst Dashboard")
    news = load_table("news_signals", order="id DESC", limit=2000)
    if not news.empty:
        cols = ["news_title", "source_domain", "entity_detected", "mapped_product_name",
                "news_signal_score", "news_to_product_reason", "news_url", "observed_at"]
        st.dataframe(news[[c for c in cols if c in news.columns]], use_container_width=True,
                     height=460)
    else:
        st.info("No news signals yet.")

# ---- Events & Seasonality ---------------------------------------------------
with tabs[6]:
    st.subheader("Event & Seasonality Intelligence")
    ev = load_table("event_calendar", order="event_confidence_score DESC")
    if not ev.empty:
        cols = ["event_name", "event_type", "event_date", "event_status",
                "days_until_event", "days_since_event", "affected_verticals",
                "seasonality_score", "event_confidence_score", "event_source_url",
                "expected_product_impact_reason"]
        st.dataframe(ev[[c for c in cols if c in ev.columns]], use_container_width=True,
                     height=340)
    if not fp.empty and "linked_event_name" in fp:
        st.markdown("##### Event-linked products")
        linked = fp[fp["linked_event_status"] != "not_found"]
        cols = ["product_name", "vertical", "EPS", "VTM", "linked_event_name",
                "linked_event_type", "linked_event_date", "linked_event_status",
                "days_until_event", "event_confidence_score", "event_to_product_reason"]
        if not linked.empty:
            st.dataframe(linked[[c for c in cols if c in linked.columns]],
                         use_container_width=True, height=300)
        else:
            st.caption("No products currently linked to a confirmed event (no hallucinated links).")
    if ev.empty:
        st.info("No events yet.")

# ---- Timeline ---------------------------------------------------------------
with tabs[7]:
    st.subheader("Trend Timeline")
    hist = load_table("products")
    if not hist.empty and _HAVE_PLOTLY:
        runs_df = load_table("runs", order="started_at")
        if not runs_df.empty:
            merged = hist.merge(runs_df[["run_id", "started_at"]], on="run_id", how="left")
            merged["started_at"] = pd.to_datetime(merged["started_at"], errors="coerce")
            top = merged.groupby("product_name")["eps"].max().nlargest(8).index
            sub = merged[merged["product_name"].isin(top)]
            st.plotly_chart(px.line(sub, x="started_at", y="eps", color="product_name",
                                    title="EPS over time (top products)"),
                            use_container_width=True)
            st.plotly_chart(px.line(sub, x="started_at", y="vtm", color="product_name",
                                    title="VTM over time (top products)"),
                            use_container_width=True)
            cat = merged.groupby([merged["started_at"].dt.date, "vertical"]).size().reset_index(name="mentions")
            cat.columns = ["date", "vertical", "mentions"]
            st.plotly_chart(px.area(cat, x="date", y="mentions", color="vertical",
                                    title="Mentions by vertical over time"),
                            use_container_width=True)
    else:
        st.info("Timeline appears after multiple cycles.")

# ---- Agent Health -----------------------------------------------------------
with tabs[8]:
    st.subheader("Agent Health, Failures & Recovery")
    residue = load_table("agent_residue", order="id DESC", limit=500)
    fails = load_table("agent_failures", order="failure_id DESC", limit=1000)
    if not residue.empty:
        st.markdown("##### Agent residue (useless / total outputs)")
        agg = residue.groupby("agent_name").agg(
            total=("total_outputs", "sum"), useless=("useless_outputs", "sum"),
            success=("success_count", "sum"), failure=("failure_count", "sum")).reset_index()
        agg["residue_score"] = (agg["useless"] / agg["total"].clip(lower=1)).round(3)
        st.dataframe(agg, use_container_width=True)
    if not fails.empty:
        st.markdown("##### Failures & recovery actions")
        cols = ["agent_name", "url", "domain", "failure_type", "failure_reason",
                "recovery_action", "decay_applied", "suppress_until", "final_status", "created_at"]
        st.dataframe(fails[[c for c in cols if c in fails.columns]], use_container_width=True,
                     height=360)
        if _HAVE_PLOTLY:
            ft = fails["failure_type"].value_counts().reset_index()
            ft.columns = ["failure_type", "count"]
            st.plotly_chart(px.bar(ft, x="failure_type", y="count",
                                   color_discrete_sequence=[COLORS["red"]]),
                            use_container_width=True)
    if residue.empty and fails.empty:
        st.success("No failures recorded — clean run so far.")

# ---- Self-Serve Learning ----------------------------------------------------
with tabs[9]:
    st.subheader("Self-Serve Learning, Strategy & Tool Discovery")
    strat = load_table("strategy_registry", order="success_rate DESC")
    recov = load_table("recovery_attempts", order="recovery_id DESC", limit=1000)
    tools = load_table("tool_candidates", order="tool_candidate_score DESC")
    installed = load_table("installed_tools", order="installed_tool_id DESC")
    dlm = load_table("domain_learning_memory")
    if not strat.empty:
        st.markdown("##### Strategy registry (best strategy per domain, learned)")
        cols = ["domain", "strategy_name", "success_count", "failure_count", "success_rate",
                "avg_products_extracted", "avg_confidence", "avg_runtime_seconds", "active_flag"]
        st.dataframe(strat[[c for c in cols if c in strat.columns]], use_container_width=True,
                     height=260)
    if not recov.empty:
        st.markdown("##### Recovery attempts")
        cols = ["url", "domain", "failure_type", "original_strategy", "attempted_strategy",
                "attempted_tool", "result", "products_extracted", "notes", "timestamp"]
        st.dataframe(recov[[c for c in cols if c in recov.columns]], use_container_width=True,
                     height=260)
    if not tools.empty:
        st.markdown("##### Tool candidates (scored before use; recommend-only by default)")
        cols = ["tool_name", "source_url", "tool_candidate_score", "maintenance_score",
                "security_score", "license_score", "evaluation_status", "install_status",
                "test_status", "promoted_flag", "recommendation_reason"]
        st.dataframe(tools[[c for c in cols if c in tools.columns]], use_container_width=True,
                     height=240)
    if not installed.empty:
        st.markdown("##### Installed tools (sandboxed)")
        st.dataframe(installed, use_container_width=True, height=180)
    if not dlm.empty:
        st.markdown("##### Domain learning memory")
        st.dataframe(dlm, use_container_width=True, height=200)
    if strat.empty and recov.empty and tools.empty:
        st.info("Self-serve learning data appears once recovery paths are exercised.")

st.caption("External-only intelligence · eBay excluded by default · responsible scraping "
           "(robots.txt, rate limits, backoff) · deterministic scoring with optional LLM enhancement.")
