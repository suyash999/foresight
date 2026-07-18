"""Live Streamlit dashboard — External Emerging Collectibles Intelligence Agent.

Executive, McKinsey-style layout. Reads from SQLite / Parquet produced by the
background worker (which runs independently and auto-refreshes here). eBay accent
palette. Sections: Executive, Trending Products (with date), News → Trending,
Markets & Geography, Live Feeds & Sources, URL Sourcing, Crawl, Sources, Events,
Timeline, Agent Health, Self-Serve Learning.
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
import yaml
import streamlit.components.v1 as components

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
from emerging_collectibles_agent.dashboard.theme import (COLORS, SEQ, accent_bar,
                                                         inject_css, insight,
                                                         section, status_badge)

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
    for path, reader in [(os.path.join(OUT_DIR, "products_latest.parquet"), pd.read_parquet),
                         (os.path.join(OUT_DIR, "products_latest.csv"), pd.read_csv)]:
        try:
            if os.path.exists(path):
                return reader(path)
        except Exception:
            continue
    import json
    df = load_table("product_dataframe", order="updated_at DESC")
    if not df.empty and "payload_json" in df:
        return pd.DataFrame([json.loads(x) for x in df["payload_json"]])
    return pd.DataFrame()


def _fmt_ts(ts) -> str:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return str(ts) if ts else "n/a"


def _explode(series: pd.Series, sep=r"\s*\|\s*") -> list[str]:
    out = []
    for v in series.dropna():
        for part in re.split(sep, str(v)):
            p = part.strip()
            if p:
                out.append(p)
    return out


def sec(kicker, title):
    st.markdown(section(kicker, title), unsafe_allow_html=True)


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
    <h1>External Emerging Collectibles Intelligence</h1>
    <p>Autonomous, high-recall product-trend discovery · external public sources only (eBay excluded) ·
    run reference {CFG.run_reference if CFG else 9839} · live auto-refresh {REFRESH_SECS}s</p>
    </div>""",
    unsafe_allow_html=True)
st.markdown(accent_bar(), unsafe_allow_html=True)

hb = load_table("worker_heartbeat", order="id DESC", limit=1)
runs = load_table("runs", order="started_at DESC", limit=1)
ca, cb, cc, cd = st.columns(4)
if not hb.empty:
    last_ts = hb.iloc[0]["timestamp"]
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(str(last_ts).replace("Z", "+00:00"))).total_seconds()
    except Exception:
        age = 1e9
    live = age <= STALE_SECS
    css = "worker-live" if live else "worker-stale"
    ca.markdown(f"**Worker:** <span class='{css}'>{'🟢 LIVE' if live else '🔴 STALE'} · {hb.iloc[0]['worker_status']}</span>",
                unsafe_allow_html=True)
    cb.markdown(f"**Loop state:** {hb.iloc[0]['current_loop_state']}")
    cc.markdown(f"**Last heartbeat:** {_fmt_ts(last_ts)}")
    cd.markdown(f"**Next run:** {_fmt_ts(hb.iloc[0]['next_run_at']) if hb.iloc[0]['next_run_at'] else 'in cycle'}")
else:
    ca.markdown("**Worker:** <span class='worker-stale'>🔴 no heartbeat yet</span>", unsafe_allow_html=True)
    cb.info("Start it: `python -m emerging_collectibles_agent.main run-forever`")

# ---------------------------------------------------------------------------
# Data + filters
# ---------------------------------------------------------------------------
products = load_products()
news = load_table("news_signals", order="id DESC", limit=4000)
disc = load_table("url_discovery_log", order="id DESC", limit=8000)

st.sidebar.header("Filters")
if not products.empty:
    def msel(label, col):
        if col in products.columns:
            opts = sorted([x for x in products[col].dropna().unique() if str(x).strip()])
            return st.sidebar.multiselect(label, opts)
        return []
    f_vertical = msel("Focus Category", "vertical")
    f_category = msel("Category", "category")
    f_status = msel("Final trend status", "final_trend_status")
    f_validation = msel("Validation status", "validation_status")
    f_country = msel("Country / market", "detected_country")
    f_stype = st.sidebar.multiselect(
        "Source type",
        sorted({t for v in products.get("source_types", pd.Series(dtype=str)).dropna()
                for t in re.split(r"\s*\|\s*", str(v)) if t.strip()}))
    f_event = st.sidebar.selectbox("Event linkage",
                                   ["All", "Event linked", "Not linked", "Upcoming event", "Recent/past event"])
    min_eps = st.sidebar.slider("Minimum EPS", 0, 100, 0)
    min_vtm = st.sidebar.slider("Minimum VTM", 0, 100, 0)

    fp = products.copy()
    for col, sel in [("vertical", f_vertical), ("category", f_category),
                     ("final_trend_status", f_status), ("validation_status", f_validation),
                     ("detected_country", f_country)]:
        if sel and col in fp:
            fp = fp[fp[col].isin(sel)]
    if f_stype and "source_types" in fp:
        fp = fp[fp["source_types"].apply(lambda v: any(t in str(v) for t in f_stype))]
    if "EPS" in fp:
        fp = fp[fp["EPS"] >= min_eps]
    if "VTM" in fp:
        fp = fp[fp["VTM"] >= min_vtm]
    if f_event != "All" and "linked_event_status" in fp:
        m = {"Event linked": fp["linked_event_status"] != "not_found",
             "Not linked": fp["linked_event_status"] == "not_found",
             "Upcoming event": fp["linked_event_status"] == "upcoming",
             "Recent/past event": fp["linked_event_status"].isin(["recently_happened", "historical", "live"])}
        fp = fp[m[f_event]]
else:
    fp = products
    st.sidebar.info("No products yet — the worker will populate this.")

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
tabs = st.tabs([
    "📊 Executive", "🔥 Trending Products", "📰 News → Trending", "🌍 Markets & Geography",
    "📡 Live Feeds & Sources", "🔎 URL Sourcing", "🕸️ Crawl", "🏷️ Sources",
    "📅 Events", "📈 Timeline", "🩺 Agent Health", "🛠️ Self-Serve Learning",
    "⚙️ Controls",
])

# ---- Executive --------------------------------------------------------------
with tabs[0]:
    sec("Situation overview", "Executive Overview")
    r = runs.iloc[0] if not runs.empty else None
    m = st.columns(6)
    m[0].metric("Current run", (r["run_id"] if r is not None else "—"))
    m[1].metric("URLs discovered", int(r["urls_discovered"]) if r is not None else 0)
    m[2].metric("URLs crawled", int(r["urls_crawled"]) if r is not None else 0)
    m[3].metric("URLs blocked", int(r["urls_blocked"]) if r is not None else 0)
    m[4].metric("Products", int(r["products_extracted"]) if r is not None else 0)
    m[5].metric("Validated", int(r["products_validated"]) if r is not None else 0)
    if not products.empty:
        strong = int((products["final_trend_status"] == "Strong Emerging").sum())
        watch = int((products["final_trend_status"] == "Watchlist").sum())
        top_vert = products["vertical"].mode().iat[0] if not products["vertical"].mode().empty else "n/a"
        m2 = st.columns(6)
        m2[0].metric("Strong emerging", strong)
        m2[1].metric("Watchlist", watch)
        m2[2].metric("Avg EPS", round(products["EPS"].mean(), 1))
        m2[3].metric("Avg VTM", round(products["VTM"].mean(), 1))
        m2[4].metric("Verticals covered", products["vertical"].nunique())
        m2[5].metric("Events tracked", len(load_table("event_calendar")))
        st.markdown(insight(
            f"<b>So what:</b> {len(products)} products under watch across "
            f"{products['vertical'].nunique()} verticals; the strongest concentration is in "
            f"<b>{top_vert}</b>. {strong} products qualify as <b>Strong Emerging</b>, "
            f"{watch} sit on the watchlist. Average EPS {round(products['EPS'].mean(),1)} / "
            f"VTM {round(products['VTM'].mean(),1)} — the pipeline is prioritising recall, so "
            f"filter by EPS/VTM/validation to focus."), unsafe_allow_html=True)
        if _HAVE_PLOTLY:
            g = st.columns(2)
            vc = products["vertical"].value_counts().reset_index()
            vc.columns = ["vertical", "count"]
            g[0].plotly_chart(px.bar(vc, x="count", y="vertical", orientation="h",
                                     title="Products by vertical", color_discrete_sequence=[COLORS["blue"]]),
                              use_container_width=True)
            sc = products["final_trend_status"].value_counts().reset_index()
            sc.columns = ["status", "count"]
            g[1].plotly_chart(px.pie(sc, names="status", values="count", hole=0.55,
                                     title="Trend status mix", color_discrete_sequence=SEQ),
                              use_container_width=True)
        # consolidated master CSV download (everything, one file)
        st.markdown("##### Consolidated master export (one CSV, everything)")
        mpath = os.path.join(OUT_DIR, "master_intelligence_latest.csv")
        if os.path.exists(mpath):
            with open(mpath, "rb") as fh:
                st.download_button("⬇ Download master_intelligence_latest.csv", fh.read(),
                                   "master_intelligence_latest.csv", "text/csv",
                                   help="SKU→focus-category precision, row by row: what is trending, "
                                        "why, the linked event, the market, and the sources.")
        st.caption("Also written to disk: products_latest.{csv,parquet,json}, "
                   "master_intelligence_latest.csv, event_calendar_latest.csv, "
                   "url_discovery_latest.csv, news_signal_latest.csv, source_scores_latest.csv")

# ---- Trending Products ------------------------------------------------------
with tabs[1]:
    sec("Emerging demand", "Trending Products")
    if not fp.empty:
        if "EPS" in fp:
            top_row = fp.sort_values("EPS", ascending=False).iloc[0]
            st.markdown(insight(
                f"<b>Top signal:</b> <b>{top_row.get('product_name','')}</b> "
                f"({top_row.get('vertical','')}) — EPS {top_row.get('EPS')}, VTM {top_row.get('VTM')}, "
                f"status <b>{top_row.get('final_trend_status','')}</b>, trending "
                f"{top_row.get('trending_date','')}."), unsafe_allow_html=True)
        cols = ["trending_date", "product_name", "focus_category", "category", "brand",
                "EPS", "VTM", "trend_confidence_pct", "final_trend_status", "confidence_level",
                "validation_status", "matched_items", "detected_country", "linked_event_name",
                "primary_source_url"]
        show = [c for c in cols if c in fp.columns]
        st.dataframe(fp[show].sort_values("EPS", ascending=False), use_container_width=True, height=430)
        st.download_button("⬇ Download this view (CSV)", fp.to_csv(index=False),
                           "trending_products_view.csv", "text/csv")
        st.markdown("##### Product detail")
        pick = st.selectbox("Select a product", fp["product_name"].tolist())
        row = fp[fp["product_name"] == pick].iloc[0]
        st.markdown(status_badge(row.get("final_trend_status", "")), unsafe_allow_html=True)
        d = st.columns(4)
        d[0].metric("EPS", row.get("EPS"))
        d[1].metric("VTM", row.get("VTM"))
        d[2].metric("Validation", row.get("validation_status"))
        d[3].metric("Trending date", row.get("trending_date", "n/a"))
        st.markdown(f"**AI trend reason:** {row.get('ai_trend_reason','')}")
        st.markdown(f"**Catalyst:** {row.get('catalyst_summary','')}")
        st.markdown(f"**Counter-signals:** {row.get('counter_signals','')}")
        st.markdown(f"**Recommended next validation:** {row.get('recommended_next_validation','')}")
        with st.expander("EPS / VTM breakdown & sources"):
            eps_cols = ["freshness_score", "mention_velocity_score", "source_diversity_score",
                        "news_catalyst_score", "event_proximity_score", "marketplace_signal_score",
                        "scarcity_signal_score"]
            vtm_cols = ["credible_source_coverage", "independent_confirmation", "evidence_quality",
                        "event_validation_score", "noise_penalty_adjusted"]
            st.write("**EPS components:**", {c: row.get(c) for c in eps_cols if c in row})
            st.write("**VTM components:**", {c: row.get(c) for c in vtm_cols if c in row})
            st.write("**Sources:**", row.get("source_urls", ""))
            st.write("**Evidence:**", row.get("evidence_snippets", ""))
    else:
        st.info("No products match the current filters.")

# ---- News → Trending --------------------------------------------------------
with tabs[2]:
    sec("Signal from the news", "News → Trending Products")
    if not news.empty:
        # aggregate product names mentioned across news → ranked trending-in-news
        cand = []
        for v in news.get("mapped_product_name", pd.Series(dtype=str)).dropna():
            for part in re.split(r"\s*[|,]\s*", str(v)):
                p = part.strip()
                if p:
                    cand.append(p)
        ranked = Counter(cand).most_common(15)
        if ranked:
            top_name, top_n = ranked[0]
            st.markdown(insight(
                f"<b>Trending in the news:</b> <b>{top_name}</b> leads with {top_n} news mention(s). "
                f"{len(news)} news items scanned; {news['source_domain'].nunique()} distinct outlets."),
                unsafe_allow_html=True)
            rk = pd.DataFrame(ranked, columns=["Product / candidate", "News mentions"])
            gcol = st.columns([2, 3])
            gcol[0].dataframe(rk, use_container_width=True, height=380)
            if _HAVE_PLOTLY:
                gcol[1].plotly_chart(
                    px.bar(rk.head(10).iloc[::-1], x="News mentions", y="Product / candidate",
                           orientation="h", title="Top product names trending per news",
                           color_discrete_sequence=[COLORS["red"]]), use_container_width=True)
        st.markdown("##### News catalysts feed")
        ncols = ["observed_at", "news_title", "entity_detected", "mapped_product_name",
                 "news_signal_score", "source_domain", "news_url"]
        nshow = news[[c for c in ncols if c in news.columns]].copy()
        if "observed_at" in nshow:
            nshow["date"] = nshow["observed_at"].astype(str).str[:10]
        st.dataframe(nshow, use_container_width=True, height=340)
    else:
        st.info("No news signals yet — news→product mapping appears once news pages are crawled.")

# ---- Markets & Geography ----------------------------------------------------
with tabs[3]:
    sec("Where demand is forming", "Markets & Geography")
    if not products.empty and "detected_country" in products.columns:
        geo = products[products["detected_country"].astype(str).str.strip() != ""]
        if not geo.empty:
            agg = geo.groupby("detected_country").agg(
                products=("product_name", "count"),
                avg_eps=("EPS", "mean"), avg_vtm=("VTM", "mean")).reset_index()
            agg["avg_eps"] = agg["avg_eps"].round(1)
            agg["avg_vtm"] = agg["avg_vtm"].round(1)
            lead = agg.sort_values("products", ascending=False).iloc[0]
            st.markdown(insight(
                f"<b>Market read:</b> strongest localized signal in <b>{lead['detected_country']}</b> "
                f"({int(lead['products'])} products, avg EPS {lead['avg_eps']}). "
                f"{int((products['global_signal_flag']==True).sum()) if 'global_signal_flag' in products else 0} "
                f"products carry a global market scope."), unsafe_allow_html=True)
            if _HAVE_PLOTLY:
                gc = st.columns([3, 2])
                fig = px.choropleth(agg, locations="detected_country", locationmode="country names",
                                    color="products", hover_data=["avg_eps", "avg_vtm"],
                                    color_continuous_scale=["#DBEAFE", COLORS["blue"], COLORS["navy"]],
                                    title="Signal density by country")
                fig.update_geos(showframe=False, showcoastlines=True, projection_type="natural earth")
                gc[0].plotly_chart(fig, use_container_width=True)
                if "market_scope" in products:
                    ms = products["market_scope"].value_counts().reset_index()
                    ms.columns = ["market_scope", "count"]
                    gc[1].plotly_chart(px.pie(ms, names="market_scope", values="count", hole=0.5,
                                              title="Market scope", color_discrete_sequence=SEQ),
                                       use_container_width=True)
            st.markdown("##### Country breakdown")
            st.dataframe(agg.sort_values("products", ascending=False), use_container_width=True, height=280)
        else:
            st.info("No country-localized signals detected yet (most signals are global-scope). "
                    "Country is inferred only when a source gives explicit geographic evidence.")
    else:
        st.info("No product geography yet.")

# ---- Live Feeds & Sources ---------------------------------------------------
with tabs[4]:
    sec("Provenance & live ingestion", "Live Feeds & Data Sources")
    st.markdown(insight(
        "<b>Every signal is traceable.</b> This view segments the live ingestion by <b>channel</b> "
        "(RSS, GDELT, Wikipedia, Wikidata, sitemap, search API, recursive), by <b>source type</b> "
        "(manufacturer, auction house, hobby publication, news…), and by <b>domain</b> — so you always "
        "know where a product signal was pulled from."), unsafe_allow_html=True)
    if not disc.empty:
        c1, c2 = st.columns(2)
        by_method = disc["discovery_method"].value_counts().reset_index()
        by_method.columns = ["channel", "urls"]
        by_type = disc[disc["source_type"].astype(str).str.strip() != ""]["source_type"].value_counts().reset_index()
        by_type.columns = ["source_type", "urls"]
        if _HAVE_PLOTLY:
            c1.plotly_chart(px.bar(by_method, x="channel", y="urls", title="Live feed by channel",
                                   color_discrete_sequence=[COLORS["green"]]), use_container_width=True)
            if not by_type.empty:
                c2.plotly_chart(px.bar(by_type.head(12), x="urls", y="source_type", orientation="h",
                                       title="By source type", color_discrete_sequence=[COLORS["blue"]]),
                                use_container_width=True)
        st.markdown("##### Live discovery feed (most recent, segmented)")
        channel = st.selectbox("Filter feed by channel",
                               ["All"] + sorted(disc["discovery_method"].dropna().unique().tolist()))
        feed = disc if channel == "All" else disc[disc["discovery_method"] == channel]
        fcols = ["discovered_at", "discovery_method", "discovery_source", "seed_query",
                 "discovered_url", "domain", "source_type", "mapped_vertical",
                 "relevance_score", "selection_status"]
        st.dataframe(feed[[c for c in fcols if c in feed.columns]].head(400),
                     use_container_width=True, height=340)
        st.download_button("⬇ Download exhaustive URL list (CSV)", disc.to_csv(index=False),
                           "url_discovery_full.csv", "text/csv",
                           help="Every discovered URL with channel, source, mapped focus category, "
                                "scores, and why it was selected or rejected.")
    # top domains providing products
    if not products.empty and "source_domains" in products.columns:
        st.markdown("##### Top domains feeding product signals")
        doms = Counter(_explode(products["source_domains"]))
        dd = pd.DataFrame(doms.most_common(20), columns=["domain", "product_signals"])
        st.dataframe(dd, use_container_width=True, height=260)
    if disc.empty and products.empty:
        st.info("No ingestion yet.")

# ---- URL Sourcing -----------------------------------------------------------
with tabs[5]:
    sec("Top of funnel", "URL Sourcing Intelligence")
    if not disc.empty:
        s = st.columns(5)
        s[0].metric("Discovered", len(disc))
        s[1].metric("Selected", int((disc["selection_status"] == "selected").sum()))
        s[2].metric("Rejected", int((disc["selection_status"] == "rejected").sum()))
        s[3].metric("Duplicates", int(disc["duplicate_flag"].sum()))
        s[4].metric("Channels", disc["discovery_method"].nunique())
        crawl = load_table("crawl_urls")
        pages = load_table("pages")
        funnel = pd.DataFrame({
            "stage": ["Discovered", "Selected", "Crawled", "Extracted OK", "Product-signal"],
            "count": [len(disc), int((disc["selection_status"] == "selected").sum()), len(crawl),
                      int((crawl["crawl_status"] == "ok").sum()) if not crawl.empty else 0,
                      int((pages.get("product_signal_score", pd.Series(dtype=float)) > 0).sum()) if not pages.empty else 0]})
        if _HAVE_PLOTLY:
            st.plotly_chart(px.funnel(funnel, x="count", y="stage",
                                      color_discrete_sequence=[COLORS["blue"]]), use_container_width=True)
        cols = ["discovered_at", "discovery_method", "seed_query", "discovered_url", "domain",
                "source_type", "mapped_vertical", "relevance_score", "priority_score",
                "selection_status", "selection_reason", "rejection_reason", "next_action"]
        st.dataframe(disc[[c for c in cols if c in disc.columns]], use_container_width=True, height=380)
    else:
        st.info("No URL discovery yet.")

# ---- Crawl ------------------------------------------------------------------
with tabs[6]:
    sec("Acquisition", "URL Crawl Monitor")
    crawl = load_table("crawl_urls", order="id DESC", limit=3000)
    if not crawl.empty:
        cols = ["url", "domain", "source_type", "crawl_status", "page_relevance_score",
                "source_credibility_score", "product_signal_score", "last_crawled_at", "error_message"]
        st.dataframe(crawl[[c for c in cols if c in crawl.columns]], use_container_width=True, height=480)
    else:
        st.info("No crawl activity yet.")

# ---- Sources ----------------------------------------------------------------
with tabs[7]:
    sec("Credibility", "Source Credibility & Learning")
    sd = load_table("source_domains", order="pages_seen DESC")
    sq = load_table("source_quality_memory", order="source_quality_score DESC")
    if not sd.empty:
        st.dataframe(sd, use_container_width=True, height=280)
    if not sq.empty:
        st.markdown("##### Source quality memory (self-learning)")
        st.dataframe(sq[[c for c in ["domain", "source_quality_score", "source_fatigue_score",
                                     "recovery_boost", "success_count", "failure_count",
                                     "products_found", "validated_found", "avg_eps", "avg_vtm"]
                         if c in sq.columns]], use_container_width=True, height=280)
    if sd.empty and sq.empty:
        st.info("No source data yet.")

# ---- Events -----------------------------------------------------------------
with tabs[8]:
    sec("Catalysts & seasonality", "Events & Seasonality")
    ev = load_table("event_calendar", order="event_confidence_score DESC")
    if not ev.empty:
        upcoming = ev[ev["event_status"] == "upcoming"].copy()
        if not upcoming.empty and "days_until_event" in upcoming:
            nxt = upcoming.sort_values("days_until_event").iloc[0]
            st.markdown(insight(
                f"<b>Next catalyst:</b> <b>{nxt['event_name']}</b> in "
                f"~{int(nxt['days_until_event']) if pd.notna(nxt['days_until_event']) else '?'} days "
                f"({nxt.get('event_date','')}) — likely to lift: "
                f"{str(nxt.get('mapped_products','') or 'related products')[:160]}."),
                unsafe_allow_html=True)

        st.markdown("##### Focus Category → Upcoming events & likely SKUs")
        # explode affected_verticals so each focus category lists its events
        fc = st.selectbox("Focus Category",
                          ["All"] + (CFG.verticals if CFG else []))
        evx = upcoming if not upcoming.empty else ev
        if fc != "All":
            evx = evx[evx["affected_verticals"].astype(str).str.contains(re.escape(fc))]
        cols = ["event_name", "event_type", "event_date", "days_until_event",
                "affected_verticals", "mapped_products", "seasonality_score",
                "event_confidence_score"]
        show = evx.sort_values("days_until_event") if "days_until_event" in evx else evx
        st.dataframe(show[[c for c in cols if c in show.columns]].rename(
            columns={"mapped_products": "likely_trending_SKUs",
                     "affected_verticals": "focus_categories"}),
            use_container_width=True, height=320)
    if not fp.empty and "linked_event_name" in fp:
        linked = fp[fp["linked_event_status"] != "not_found"]
        if not linked.empty:
            st.markdown("##### Event-linked products")
            cols = ["trending_date", "product_name", "vertical", "EPS", "VTM", "linked_event_name",
                    "linked_event_type", "linked_event_date", "linked_event_status",
                    "days_until_event", "event_confidence_score"]
            st.dataframe(linked[[c for c in cols if c in linked.columns]], use_container_width=True, height=300)
    if ev.empty:
        st.info("No events yet.")

# ---- Timeline ---------------------------------------------------------------
with tabs[9]:
    sec("Momentum over time", "Trend Timeline")
    hist = load_table("products")
    if not hist.empty and _HAVE_PLOTLY:
        runs_df = load_table("runs", order="started_at")
        if not runs_df.empty:
            merged = hist.merge(runs_df[["run_id", "started_at"]], on="run_id", how="left")
            merged["started_at"] = pd.to_datetime(merged["started_at"], errors="coerce")
            top = merged.groupby("product_name")["eps"].max().nlargest(8).index
            sub = merged[merged["product_name"].isin(top)]
            st.plotly_chart(px.line(sub, x="started_at", y="eps", color="product_name",
                                    title="EPS over time (top products)", color_discrete_sequence=SEQ),
                            use_container_width=True)
            cat = merged.groupby([merged["started_at"].dt.date, "vertical"]).size().reset_index(name="mentions")
            cat.columns = ["date", "vertical", "mentions"]
            st.plotly_chart(px.area(cat, x="date", y="mentions", color="vertical",
                                    title="Mentions by vertical over time", color_discrete_sequence=SEQ),
                            use_container_width=True)
    else:
        st.info("Timeline appears after multiple cycles.")

# ---- Agent Health -----------------------------------------------------------
with tabs[10]:
    sec("Operations", "Agent Health, Failures & Recovery")
    residue = load_table("agent_residue", order="id DESC", limit=500)
    fails = load_table("agent_failures", order="failure_id DESC", limit=1000)
    if not residue.empty:
        agg = residue.groupby("agent_name").agg(
            total=("total_outputs", "sum"), useless=("useless_outputs", "sum")).reset_index()
        agg["residue_score"] = (agg["useless"] / agg["total"].clip(lower=1)).round(3)
        st.dataframe(agg, use_container_width=True)
    if not fails.empty:
        cols = ["agent_name", "url", "domain", "failure_type", "recovery_action",
                "decay_applied", "suppress_until", "final_status", "created_at"]
        st.dataframe(fails[[c for c in cols if c in fails.columns]], use_container_width=True, height=320)
        if _HAVE_PLOTLY:
            ft = fails["failure_type"].value_counts().reset_index()
            ft.columns = ["failure_type", "count"]
            st.plotly_chart(px.bar(ft, x="failure_type", y="count",
                                   color_discrete_sequence=[COLORS["red"]]), use_container_width=True)
    if residue.empty and fails.empty:
        st.success("No failures recorded — clean run so far.")

# ---- Self-Serve Learning ----------------------------------------------------
with tabs[11]:
    sec("Continuous improvement", "Self-Serve Learning, Strategy & Tools")
    strat = load_table("strategy_registry", order="success_rate DESC")
    recov = load_table("recovery_attempts", order="recovery_id DESC", limit=1000)
    tools = load_table("tool_candidates", order="tool_candidate_score DESC")
    if not strat.empty:
        st.markdown("##### Strategy registry (learned best strategy per domain)")
        st.dataframe(strat[[c for c in ["domain", "strategy_name", "success_count", "failure_count",
                                        "success_rate", "avg_products_extracted", "active_flag"]
                            if c in strat.columns]], use_container_width=True, height=240)
    if not recov.empty:
        st.markdown("##### Recovery attempts")
        st.dataframe(recov[[c for c in ["url", "domain", "failure_type", "attempted_strategy",
                                        "attempted_tool", "result", "notes", "timestamp"]
                            if c in recov.columns]], use_container_width=True, height=220)
    if not tools.empty:
        st.markdown("##### Tool candidates (scored before use; recommend-only by default)")
        st.dataframe(tools[[c for c in ["tool_name", "tool_candidate_score", "security_score",
                                        "license_score", "evaluation_status", "install_status",
                                        "recommendation_reason"] if c in tools.columns]],
                     use_container_width=True, height=220)
    if strat.empty and recov.empty and tools.empty:
        st.info("Self-serve learning data appears once recovery paths are exercised.")

# ---- Controls ---------------------------------------------------------------
with tabs[12]:
    sec("Runtime controls", "Configuration & Toggles")
    st.caption("Changes are written to **runtime_overrides.yaml** (config.yaml stays "
               "untouched) and picked up by the worker on its next cycle. Source "
               "on/off toggles need a worker restart; mode & Hermes toggles apply live.")

    def _cfg(key, default):
        return CFG.get(key, default) if CFG else default

    with st.form("controls_form"):
        st.markdown("##### Run mode")
        light = st.checkbox(
            "Light mode (no website crawling — build products from feed/API metadata only)",
            value=bool(_cfg("runtime.light_mode", True)),
            help="ON = fast, no 407/robots waits. OFF = heavy crawler that also fetches full pages.")
        interval = st.number_input("Cycle interval (seconds)", min_value=60, max_value=86400,
                                   value=int(_cfg("runtime.crawl_interval_seconds", 900)), step=60)

        st.markdown("##### Output / Hermes")
        keep_csv = st.checkbox(
            "Keep the master CSV after each run (don't clear it)",
            value=not bool(_cfg("hive_sink.clear_csv_after_write", False)))
        hive_on = st.checkbox("Write to Hermes (Hive sink)",
                              value=bool(_cfg("hive_sink.enabled", False)),
                              help="Requires Kerberos/ODBC access. Leave off for CSV-only.")
        hive_method = st.selectbox("Hermes connection method", options=["odbc", "pyhive"],
                                   index=0 if _cfg("hive_sink.method", "odbc") == "odbc" else 1)

        st.markdown("##### Sources (need worker restart to take effect)")
        c1, c2, c3 = st.columns(3)
        with c1:
            s_rss = st.checkbox("RSS", value=bool(_cfg("url_discovery.enable_rss_discovery", True)))
            s_reddit = st.checkbox("Reddit", value=bool(_cfg("url_discovery.enable_reddit_discovery", True)))
            s_shopify = st.checkbox("Shopify", value=bool(_cfg("url_discovery.enable_shopify_discovery", True)))
        with c2:
            s_gnews = st.checkbox("Google News", value=bool(_cfg("url_discovery.enable_google_news_discovery", True)))
            s_bing = st.checkbox("Bing News", value=bool(_cfg("url_discovery.enable_bing_news_discovery", True)))
            s_gdelt = st.checkbox("GDELT", value=bool(_cfg("url_discovery.enable_gdelt_discovery", True)))
        with c3:
            s_pv = st.checkbox("Wikipedia pageviews", value=bool(_cfg("url_discovery.enable_wikipedia_pageviews_discovery", True)))
            s_scry = st.checkbox("Scryfall", value=bool(_cfg("url_discovery.enable_scryfall_discovery", True)))
            s_ph = st.checkbox("Product Hunt", value=bool(_cfg("url_discovery.enable_producthunt_discovery", True)))

        submitted = st.form_submit_button("💾 Save controls")

    if submitted:
        overrides = {
            "runtime": {"light_mode": bool(light), "crawl_interval_seconds": int(interval)},
            "hive_sink": {"enabled": bool(hive_on),
                          "clear_csv_after_write": (not bool(keep_csv)),
                          "method": hive_method},
            "url_discovery": {
                "enable_rss_discovery": bool(s_rss),
                "enable_reddit_discovery": bool(s_reddit),
                "enable_shopify_discovery": bool(s_shopify),
                "enable_google_news_discovery": bool(s_gnews),
                "enable_bing_news_discovery": bool(s_bing),
                "enable_gdelt_discovery": bool(s_gdelt),
                "enable_wikipedia_pageviews_discovery": bool(s_pv),
                "enable_scryfall_discovery": bool(s_scry),
                "enable_producthunt_discovery": bool(s_ph),
            },
        }
        ov_path = os.path.join(os.path.dirname(os.path.abspath(CONFIG_PATH)), "runtime_overrides.yaml")
        try:
            with open(ov_path, "w", encoding="utf-8") as fh:
                yaml.safe_dump(overrides, fh, sort_keys=False)
            st.success(f"Saved to {ov_path}. The worker applies mode/Hermes changes on its next "
                       f"cycle; source toggles apply after a worker restart.")
            get_config.clear()  # bust the cached config so the dashboard reflects it
        except Exception as exc:
            st.error(f"Could not write overrides: {exc}")

    # show the current effective override file, if any
    ov_path = os.path.join(os.path.dirname(os.path.abspath(CONFIG_PATH)), "runtime_overrides.yaml")
    if os.path.exists(ov_path):
        with open(ov_path, "r", encoding="utf-8") as fh:
            st.markdown("##### Current runtime_overrides.yaml")
            st.code(fh.read(), language="yaml")

st.caption("External-only intelligence · eBay excluded by default · responsible scraping "
           "(robots.txt, rate limits, backoff) · deterministic scoring with optional LLM enhancement.")
