"""Dashboard theme — McKinsey-style executive aesthetic with eBay accent colors.

Clean, high data-ink, generous whitespace, thin rules, a single deep-navy
structural color, and the required eBay palette used only for data accents.
"""
from __future__ import annotations

# eBay-inspired accent palette (data accents only)
COLORS = {
    "red": "#E53238",
    "blue": "#0064D2",
    "yellow": "#F5AF02",
    "green": "#86B817",
    "dark_text": "#111827",
    "light_bg": "#F9FAFB",
    # McKinsey-style structural neutrals
    "navy": "#0B1F3A",
    "slate": "#334155",
    "muted": "#64748B",
    "rule": "#E5E7EB",
    "panel": "#FFFFFF",
}

# Ordered categorical palette for charts (colour-blind-safe-ish, professional)
SEQ = ["#0064D2", "#86B817", "#F5AF02", "#E53238", "#0B1F3A", "#64748B",
       "#0EA5E9", "#7C3AED"]

STATUS_COLORS = {
    "Strong Emerging": COLORS["green"],
    "Validated Momentum": COLORS["blue"],
    "Emerging but Needs Validation": COLORS["yellow"],
    "Watchlist": "#CA8A04",
    "Potential False Positive": COLORS["red"],
    "Low Confidence": "#9CA3AF",
}

VALIDATION_COLORS = {
    "strong": COLORS["green"],
    "moderate": COLORS["blue"],
    "weak": COLORS["yellow"],
    "unvalidated": "#9CA3AF",
    "likely false positive": COLORS["red"],
    "blocked / insufficient evidence": "#6B7280",
}


def inject_css() -> str:
    c = COLORS
    return f"""
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
      .stApp {{ background-color: {c['light_bg']}; }}
      html, body, [class*="css"] {{ font-family: 'Inter', -apple-system, sans-serif; }}
      h1, h2, h3, h4 {{ color: {c['navy']}; letter-spacing: -0.01em; }}

      /* Executive banner — restrained navy with a thin accent rule */
      .ecia-header {{
        background: {c['navy']};
        padding: 22px 26px; border-radius: 4px; color: white; margin-bottom: 6px;
        border-left: 5px solid {c['blue']};
      }}
      .ecia-header h1 {{ color: white; margin: 0; font-size: 25px; font-weight: 700; }}
      .ecia-header p {{ color: #C7D2E0; margin: 6px 0 0 0; font-size: 12.5px; letter-spacing: .02em; }}
      .ecia-accentbar {{ height:4px; border-radius:2px; margin: 0 0 14px 0;
        background: linear-gradient(90deg, {c['red']} 0%, {c['blue']} 40%, {c['yellow']} 70%, {c['green']} 100%); }}

      /* Section header with McKinsey-style kicker + rule */
      .ecia-kicker {{ text-transform: uppercase; letter-spacing: .14em; font-size: 11px;
        color: {c['muted']}; font-weight: 600; margin-bottom: 2px; }}
      .ecia-section {{ font-size: 20px; font-weight: 700; color: {c['navy']};
        border-bottom: 2px solid {c['navy']}; padding-bottom: 6px; margin: 6px 0 14px 0; }}

      /* Insight callout */
      .ecia-insight {{ background: #F1F5F9; border-left: 4px solid {c['blue']};
        padding: 12px 16px; border-radius: 3px; color: {c['slate']}; font-size: 14px;
        margin: 6px 0 16px 0; }}
      .ecia-insight b {{ color: {c['navy']}; }}

      /* Metric cards */
      div[data-testid="stMetric"] {{
        background: white; border: 1px solid {c['rule']}; border-radius: 6px;
        padding: 14px 16px; box-shadow: 0 1px 2px rgba(15,23,42,0.04);
      }}
      div[data-testid="stMetricValue"] {{ color: {c['navy']}; font-weight: 700; }}
      div[data-testid="stMetricLabel"] {{ color: {c['muted']}; text-transform: uppercase;
        letter-spacing: .06em; font-size: 11px; }}

      .ecia-badge {{ display:inline-block; padding:2px 10px; border-radius:3px;
        color:white; font-size:12px; font-weight:600; }}
      .ecia-pill {{ display:inline-block; padding:2px 9px; border-radius:12px;
        font-size:11px; font-weight:600; margin:2px 4px 2px 0; border:1px solid {c['rule']};
        background:white; color:{c['slate']}; }}

      .stTabs [data-baseweb="tab-list"] {{ gap: 2px; border-bottom: 1px solid {c['rule']}; }}
      .stTabs [data-baseweb="tab"] {{ background: transparent; border-radius: 0;
        padding: 8px 14px; color: {c['muted']}; font-weight: 500; }}
      .stTabs [aria-selected="true"] {{ color: {c['navy']}; border-bottom: 2px solid {c['blue']}; }}

      .worker-live {{ color: {c['green']}; font-weight:700; }}
      .worker-stale {{ color: {c['red']}; font-weight:700; }}
      section[data-testid="stSidebar"] {{ background: {c['panel']}; border-right: 1px solid {c['rule']}; }}
    </style>
    """


def accent_bar() -> str:
    return '<div class="ecia-accentbar"></div>'


def section(kicker: str, title: str) -> str:
    return (f'<div class="ecia-kicker">{kicker}</div>'
            f'<div class="ecia-section">{title}</div>')


def insight(text: str) -> str:
    return f'<div class="ecia-insight">{text}</div>'


def status_badge(status: str) -> str:
    color = STATUS_COLORS.get(status, "#9CA3AF")
    return f'<span class="ecia-badge" style="background:{color}">{status}</span>'
