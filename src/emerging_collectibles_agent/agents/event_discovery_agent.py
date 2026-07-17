"""Event Discovery + Upcoming Event & Seasonality Agent.

Finds events (past/live/upcoming) that may explain or predict collectible
demand, extracts dates where source evidence exists (never hallucinated), maps
events to verticals/products, and computes proximity & seasonality scores used
inside EPS/VTM.

EventProximityScore = exp(-abs(days_to_or_since_event) / event_half_life_days)
"""
from __future__ import annotations

import math
import re
from typing import Optional

from dateutil import parser as dateparser

from ..config import Config
from ..logging_config import get_logger
from ..models import EventRecord, FetchedPage, NewsSignal
from ..scoring.eps import event_proximity_score
from ..taxonomy import detect_event_type, map_vertical
from ..util import now_utc, sha1, utc_iso

log = get_logger("agent.event")

_DATE_PATTERNS = [
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},?\s+\d{4}\b",
    r"\b\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{4}\b",
    r"\b\d{4}-\d{2}-\d{2}\b",
]
_DATE_RE = re.compile("|".join(_DATE_PATTERNS))

# recurring seasonal catalysts (name -> approx month, affected verticals)
SEASONAL_EVENTS = {
    "Holiday shopping season": (12, ["Collectibles", "Toys and Hobbies", "Trading Cards"]),
    "Black Friday / Cyber Monday": (11, ["Collectibles", "Toys and Hobbies"]),
    "Record Store Day": (4, ["Vinyl Records"]),
    "San Diego Comic-Con": (7, ["Comic Books and Memorabilia", "Entertainment Memorabilia", "Toys and Hobbies"]),
    "Super Bowl": (2, ["Trading Cards", "Sports Memorabilia"]),
    "NBA Finals": (6, ["Trading Cards", "Sports Memorabilia"]),
    "Back-to-school collecting": (9, ["Trading Cards", "Toys and Hobbies"]),
}

_HALF_LIFE_MAP = {
    "sports milestone": "sports_news", "championship": "sports_news",
    "tournament": "sports_news", "award": "sports_news",
    "player debut": "sports_news", "player retirement": "sports_news",
    "record-breaking performance": "sports_news",
    "product release": "product_release", "preorder launch": "product_release",
    "official announcement": "product_release", "limited drop": "product_release",
    "movie release": "entertainment", "trailer release": "entertainment",
    "TV show release": "entertainment", "comic release": "entertainment",
    "anniversary": "entertainment", "convention/event appearance": "entertainment",
    "auction result": "auction_market", "sold-out event": "auction_market",
    "price spike": "auction_market", "manufacturer discontinuation": "long_cycle",
}


class EventDiscoveryAgent:
    def __init__(self, config: Config):
        self.config = config
        ei = config.get("event_intelligence", {})
        self.enabled = bool(ei.get("enabled", True))
        self.upcoming_window = int(ei.get("upcoming_event_window_days", 180))
        self.recent_window = int(ei.get("recent_event_window_days", 30))
        self.require_evidence = bool(ei.get("require_event_source_evidence", True))
        self.half_lives = ei.get("event_half_life_days", {})
        self.verticals = config.verticals

    def _half_life_for(self, event_type: str, vertical: str) -> float:
        if vertical in ("Watches", "Coins", "Vinyl Records"):
            key = "long_cycle"
        else:
            key = _HALF_LIFE_MAP.get(event_type, "sports_news")
        defaults = {"sports_news": 14, "product_release": 30, "entertainment": 45,
                    "auction_market": 21, "long_cycle": 90}
        return float(self.half_lives.get(key, defaults.get(key, 30)))

    def _parse_date(self, text: str) -> Optional[str]:
        m = _DATE_RE.search(text)
        if not m:
            return None
        try:
            dt = dateparser.parse(m.group(0))
            return dt.date().isoformat()
        except Exception:
            return None

    def from_page(self, page: FetchedPage) -> Optional[EventRecord]:
        if not self.enabled or page.status != "ok" or not page.text:
            return None
        text = page.title + ". " + page.text[:2000]
        event_type, terms = detect_event_type(text)
        if not event_type:
            return None
        vertical, _, _ = map_vertical(text, self.verticals)
        date_iso = self._parse_date(text)
        if self.require_evidence and not date_iso and event_type not in (
                "cultural trend", "price spike", "sold-out event"):
            # keep event but mark date unknown
            pass

        status, days_until, days_since = self._status_from_date(date_iso)
        half_life = self._half_life_for(event_type, vertical)
        prox_days = days_until if days_until is not None else days_since
        proximity = event_proximity_score(prox_days, half_life) if prox_days is not None else 0.3

        ev = EventRecord(
            event_id=sha1(f"{event_type}|{page.domain}|{date_iso}|{page.title[:60]}")[:16],
            event_name=page.title[:160] or event_type,
            event_type=event_type,
            event_date=date_iso or "",
            event_status=status,
            days_until_event=days_until,
            days_since_event=days_since,
            affected_verticals=[vertical] if vertical else [],
            event_source_url=page.url,
            event_source_domain=page.domain,
            event_confidence_score=round(0.4 + 0.4 * (1 if date_iso else 0) + 0.2 * proximity, 3),
            seasonality_score=round(100 * proximity, 1),
            expected_product_impact_reason=(
                f"{event_type} detected ({', '.join(terms[:3])}); "
                f"proximity={proximity:.2f} may drive demand for {vertical or 'related'} products."
            ),
        )
        return ev

    def _status_from_date(self, date_iso: Optional[str]):
        if not date_iso:
            return "unknown", None, None
        try:
            dt = dateparser.parse(date_iso)
        except Exception:
            return "unknown", None, None
        delta = (dt.date() - now_utc().date()).days
        if delta > 0:
            return "upcoming", delta, None
        if delta == 0:
            return "live", 0, 0
        return "recently_happened" if -delta <= self.recent_window else "historical", None, -delta

    def seasonal_events(self) -> list[EventRecord]:
        """Calendar-driven seasonal catalysts (evidence = known recurring dates)."""
        if not self.config.get("event_intelligence.seasonality_enabled", True):
            return []
        out = []
        today = now_utc().date()
        for name, (month, verticals) in SEASONAL_EVENTS.items():
            # compute next occurrence of that month
            year = today.year if month >= today.month else today.year + 1
            try:
                event_date = today.replace(year=year, month=month, day=1)
            except Exception:
                continue
            days_until = (event_date - today).days
            if days_until > self.upcoming_window:
                continue
            half_life = 30.0
            proximity = math.exp(-abs(days_until) / half_life)
            out.append(EventRecord(
                event_id=sha1(f"seasonal|{name}|{year}")[:16],
                event_name=name,
                event_type="cultural trend",
                event_date=event_date.isoformat(),
                event_status="upcoming",
                days_until_event=days_until,
                affected_verticals=verticals,
                event_source_url="calendar://seasonality",
                event_source_domain="seasonality",
                event_confidence_score=0.5,
                seasonality_score=round(100 * proximity, 1),
                expected_product_impact_reason=(
                    f"{name} is a recurring seasonal catalyst (~{days_until} days away) "
                    f"that historically lifts demand for {', '.join(verticals)}."
                ),
            ))
        return out
