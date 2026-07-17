"""SQLite schema. All tables created idempotently on startup.

Covers the operational pipeline (runs, urls, pages, products, sources, news,
events) and the self-learning memory (failures, recovery, strategies, tools,
domain learning, source quality) that makes the agent improve over time.
"""
from __future__ import annotations

import sqlite3

SCHEMA: list[str] = [
    # ---- operational ------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY,
        run_reference INTEGER,
        started_at TEXT,
        finished_at TEXT,
        status TEXT,
        urls_discovered INTEGER DEFAULT 0,
        urls_crawled INTEGER DEFAULT 0,
        urls_blocked INTEGER DEFAULT 0,
        products_extracted INTEGER DEFAULT 0,
        products_validated INTEGER DEFAULT 0,
        events_extracted INTEGER DEFAULT 0,
        errors INTEGER DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS crawl_urls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT,
        url TEXT,
        domain TEXT,
        source_type TEXT,
        discovered_at TEXT,
        last_crawled_at TEXT,
        crawl_status TEXT,
        crawl_priority_score REAL,
        page_relevance_score REAL,
        source_credibility_score REAL,
        product_signal_score REAL,
        error_message TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT,
        url TEXT,
        domain TEXT,
        title TEXT,
        published_at TEXT,
        crawled_at TEXT,
        text_hash TEXT,
        extraction_method TEXT,
        page_relevance_score REAL,
        source_credibility_score REAL,
        product_signal_score REAL,
        status TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT,
        product_id TEXT,
        product_name TEXT,
        canonical_product_name TEXT,
        vertical TEXT,
        category TEXT,
        subcategory TEXT,
        brand TEXT,
        eps REAL,
        vtm REAL,
        final_trend_status TEXT,
        validation_status TEXT,
        confidence_level TEXT,
        ai_trend_reason TEXT,
        evidence_summary TEXT,
        first_seen_at TEXT,
        last_seen_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS product_sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id TEXT,
        run_id TEXT,
        url TEXT,
        domain TEXT,
        source_type TEXT,
        source_credibility_score REAL,
        evidence_snippet TEXT,
        observed_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS news_signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT,
        news_url TEXT,
        news_title TEXT,
        source_domain TEXT,
        entity_detected TEXT,
        mapped_product_id TEXT,
        mapped_product_name TEXT,
        news_signal_score REAL,
        news_to_product_reason TEXT,
        observed_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS source_domains (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        domain TEXT UNIQUE,
        source_type TEXT,
        credibility_score REAL,
        pages_seen INTEGER DEFAULT 0,
        products_found INTEGER DEFAULT 0,
        blocked_count INTEGER DEFAULT 0,
        last_seen_at TEXT,
        notes TEXT
    )
    """,
    # ---- full product dataframe (wide, denormalised snapshot) --------------
    """
    CREATE TABLE IF NOT EXISTS product_dataframe (
        product_id TEXT PRIMARY KEY,
        run_id TEXT,
        payload_json TEXT,
        eps REAL,
        vtm REAL,
        final_trend_status TEXT,
        updated_at TEXT
    )
    """,
    # ---- url discovery / events / heartbeat -------------------------------
    """
    CREATE TABLE IF NOT EXISTS url_discovery_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT,
        discovered_at TEXT,
        discovery_method TEXT,
        discovery_source TEXT,
        seed_query TEXT,
        seed_url TEXT,
        discovered_url TEXT,
        domain TEXT,
        source_type TEXT,
        mapped_vertical TEXT,
        mapped_category TEXT,
        relevance_score REAL,
        credibility_score REAL,
        priority_score REAL,
        selection_status TEXT,
        selection_reason TEXT,
        rejection_reason TEXT,
        duplicate_flag INTEGER,
        robots_status TEXT,
        crawl_allowed INTEGER,
        extraction_status TEXT,
        products_found INTEGER DEFAULT 0,
        events_found INTEGER DEFAULT 0,
        next_action TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS event_calendar (
        event_id TEXT PRIMARY KEY,
        run_id TEXT,
        event_name TEXT,
        event_type TEXT,
        event_date TEXT,
        event_start_date TEXT,
        event_end_date TEXT,
        event_status TEXT,
        days_until_event INTEGER,
        days_since_event INTEGER,
        affected_verticals TEXT,
        affected_categories TEXT,
        mapped_products TEXT,
        event_source_url TEXT,
        event_source_domain TEXT,
        event_confidence_score REAL,
        seasonality_score REAL,
        expected_product_impact_reason TEXT,
        created_at TEXT,
        updated_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS worker_heartbeat (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT,
        worker_status TEXT,
        current_loop_state TEXT,
        current_run_id TEXT,
        urls_discovered_this_cycle INTEGER,
        urls_crawled_this_cycle INTEGER,
        products_extracted_this_cycle INTEGER,
        events_extracted_this_cycle INTEGER,
        errors_this_cycle INTEGER,
        next_run_at TEXT
    )
    """,
    # ---- failure / self-healing memory ------------------------------------
    """
    CREATE TABLE IF NOT EXISTS agent_failures (
        failure_id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT,
        agent_name TEXT,
        url TEXT,
        domain TEXT,
        failure_type TEXT,
        failure_reason TEXT,
        retry_count INTEGER DEFAULT 0,
        recovery_action TEXT,
        decay_applied REAL DEFAULT 0,
        next_retry_at TEXT,
        suppress_until TEXT,
        final_status TEXT,
        created_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS source_quality_memory (
        domain TEXT PRIMARY KEY,
        source_quality_score REAL DEFAULT 0.5,
        source_fatigue_score REAL DEFAULT 1.0,
        recovery_boost REAL DEFAULT 1.0,
        success_count INTEGER DEFAULT 0,
        failure_count INTEGER DEFAULT 0,
        blocked_count INTEGER DEFAULT 0,
        duplicate_count INTEGER DEFAULT 0,
        false_positive_count INTEGER DEFAULT 0,
        products_found INTEGER DEFAULT 0,
        validated_found INTEGER DEFAULT 0,
        avg_eps REAL DEFAULT 0,
        avg_vtm REAL DEFAULT 0,
        recent_crawl_count INTEGER DEFAULT 0,
        last_updated_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_residue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT,
        agent_name TEXT,
        total_outputs INTEGER DEFAULT 0,
        useless_outputs INTEGER DEFAULT 0,
        residue_score REAL DEFAULT 0,
        success_count INTEGER DEFAULT 0,
        failure_count INTEGER DEFAULT 0,
        avg_processing_time REAL DEFAULT 0,
        last_failure TEXT,
        recovery_actions_taken INTEGER DEFAULT 0,
        updated_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS extraction_rule_performance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rule_name TEXT,
        source_type TEXT,
        success_count INTEGER DEFAULT 0,
        failure_count INTEGER DEFAULT 0,
        avg_products REAL DEFAULT 0,
        updated_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS prompt_performance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        prompt_name TEXT,
        prompt_version TEXT,
        success_count INTEGER DEFAULT 0,
        failure_count INTEGER DEFAULT 0,
        updated_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS event_mapping_performance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mapping_rule TEXT,
        success_count INTEGER DEFAULT 0,
        failure_count INTEGER DEFAULT 0,
        updated_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS false_positive_memory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id TEXT,
        canonical_product_name TEXT,
        domain TEXT,
        reason TEXT,
        created_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS crawl_priority_memory (
        domain TEXT PRIMARY KEY,
        base_priority REAL DEFAULT 0.5,
        priority_adjusted REAL DEFAULT 0.5,
        failure_count INTEGER DEFAULT 0,
        updated_at TEXT
    )
    """,
    # ---- self-serve recovery / strategy / tools ---------------------------
    """
    CREATE TABLE IF NOT EXISTS scraping_failures (
        failure_id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT,
        url TEXT,
        domain TEXT,
        source_type TEXT,
        failure_type TEXT,
        failure_reason TEXT,
        failed_strategy TEXT,
        retry_count INTEGER DEFAULT 0,
        timestamp TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS recovery_attempts (
        recovery_id INTEGER PRIMARY KEY AUTOINCREMENT,
        failure_id INTEGER,
        run_id TEXT,
        url TEXT,
        domain TEXT,
        failure_type TEXT,
        original_strategy TEXT,
        attempted_strategy TEXT,
        attempted_tool TEXT,
        result TEXT,
        products_extracted INTEGER DEFAULT 0,
        extraction_confidence REAL DEFAULT 0,
        runtime_seconds REAL DEFAULT 0,
        notes TEXT,
        timestamp TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS strategy_registry (
        strategy_id INTEGER PRIMARY KEY AUTOINCREMENT,
        domain TEXT,
        source_type TEXT,
        strategy_name TEXT,
        success_count INTEGER DEFAULT 0,
        failure_count INTEGER DEFAULT 0,
        success_rate REAL DEFAULT 0,
        avg_products_extracted REAL DEFAULT 0,
        avg_confidence REAL DEFAULT 0,
        avg_runtime_seconds REAL DEFAULT 0,
        last_success_at TEXT,
        last_failure_at TEXT,
        priority_rank INTEGER DEFAULT 100,
        active_flag INTEGER DEFAULT 1,
        best_for_failure_types TEXT,
        notes TEXT,
        UNIQUE(domain, strategy_name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tool_candidates (
        tool_id INTEGER PRIMARY KEY AUTOINCREMENT,
        tool_name TEXT,
        source_url TEXT,
        source_type TEXT,
        tool_candidate_score REAL,
        maintenance_score REAL,
        security_score REAL,
        compatibility_score REAL,
        license_score REAL,
        documentation_score REAL,
        popularity_score REAL,
        test_coverage_score REAL,
        usefulness_score REAL,
        evaluation_status TEXT,
        recommendation_reason TEXT,
        install_status TEXT,
        test_status TEXT,
        promoted_flag INTEGER DEFAULT 0,
        failure_class_targeted TEXT,
        created_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS installed_tools (
        installed_tool_id INTEGER PRIMARY KEY AUTOINCREMENT,
        tool_name TEXT,
        version_or_commit_sha TEXT,
        source_url TEXT,
        install_path TEXT,
        installed_at TEXT,
        install_mode TEXT,
        sandbox_test_status TEXT,
        active_status TEXT,
        rollback_status TEXT,
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS domain_learning_memory (
        domain TEXT PRIMARY KEY,
        best_strategy TEXT,
        second_best_strategy TEXT,
        blocked_flag INTEGER DEFAULT 0,
        alternative_source_needed INTEGER DEFAULT 0,
        source_quality_score REAL DEFAULT 0.5,
        extraction_success_rate REAL DEFAULT 0,
        validation_success_rate REAL DEFAULT 0,
        false_positive_rate REAL DEFAULT 0,
        last_updated_at TEXT
    )
    """,
]

INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_products_run ON products(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_urls_run ON crawl_urls(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_urldisc_run ON url_discovery_log(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_news_run ON news_signals(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_prodsrc_pid ON product_sources(product_id)",
    "CREATE INDEX IF NOT EXISTS idx_heartbeat_ts ON worker_heartbeat(timestamp)",
]


def migrate(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    for stmt in SCHEMA:
        cur.execute(stmt)
    for stmt in INDEXES:
        cur.execute(stmt)
    conn.commit()
