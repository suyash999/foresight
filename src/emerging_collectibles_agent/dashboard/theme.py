"""eBay-inspired dashboard theme."""
from __future__ import annotations

COLORS = {
    "red": "#E53238",
    "blue": "#0064D2",
    "yellow": "#F5AF02",
    "green": "#86B817",
    "dark_text": "#111827",
    "light_bg": "#F9FAFB",
}

STATUS_COLORS = {
    "Strong Emerging": COLORS["green"],
    "Validated Momentum": COLORS["blue"],
    "Emerging but Needs Validation": COLORS["yellow"],
    "Watchlist": COLORS["yellow"],
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
      .stApp {{ background-color: {c['light_bg']}; }}
      h1, h2, h3, h4 {{ color: {c['dark_text']}; }}
      .ecia-header {{
        background: linear-gradient(90deg, {c['red']} 0%, {c['blue']} 45%,
                    {c['yellow']} 75%, {c['green']} 100%);
        padding: 18px 22px; border-radius: 10px; color: white; margin-bottom: 14px;
      }}
      .ecia-header h1 {{ color: white; margin: 0; font-size: 26px; }}
      .ecia-header p {{ color: #f3f4f6; margin: 4px 0 0 0; font-size: 13px; }}
      div[data-testid="stMetric"] {{
        background: white; border: 1px solid #E5E7EB; border-radius: 10px;
        padding: 12px 14px; box-shadow: 0 1px 2px rgba(0,0,0,0.04);
      }}
      div[data-testid="stMetricValue"] {{ color: {c['blue']}; }}
      .ecia-badge {{
        display:inline-block; padding:2px 10px; border-radius:12px;
        color:white; font-size:12px; font-weight:600;
      }}
      .stTabs [data-baseweb="tab-list"] {{ gap: 4px; }}
      .stTabs [data-baseweb="tab"] {{
        background: white; border-radius: 8px 8px 0 0; padding: 8px 14px;
      }}
      .worker-live {{ color: {c['green']}; font-weight:700; }}
      .worker-stale {{ color: {c['red']}; font-weight:700; }}
    </style>
    """


def status_badge(status: str) -> str:
    color = STATUS_COLORS.get(status, "#9CA3AF")
    return f'<span class="ecia-badge" style="background:{color}">{status}</span>'
