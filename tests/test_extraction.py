from emerging_collectibles_agent.agents.product_extraction_agent import ProductExtractionAgent
from emerging_collectibles_agent.agents.news_intelligence_agent import NewsIntelligenceAgent
from emerging_collectibles_agent.scraping.parsers import parse_page
from emerging_collectibles_agent.models import FetchedPage
from emerging_collectibles_agent.util import sha1

from conftest import TRADING_CARD_HTML, NEWS_ARTICLE_HTML


def _page_from_html(html, url):
    parsed = parse_page(html, url)
    return FetchedPage(
        url=url, domain=url.split("/")[2], status="ok", title=parsed["title"],
        text=parsed["text"], extraction_method=parsed["extraction_method"],
        jsonld=parsed["jsonld"], links=parsed["links"], text_hash=sha1(parsed["text"]),
        published_at=parsed["published_at"] or None)


def test_extract_trading_card(config):
    page = _page_from_html(TRADING_CARD_HTML, "https://www.cardboardconnection.com/wemby")
    agent = ProductExtractionAgent(config, llm=None)
    cands = agent.extract(page)
    assert len(cands) >= 1
    names = " ".join(c.product_name.lower() for c in cands)
    assert "wembanyama" in names or "topps" in names
    # trading-card specific flags detected
    flagged = [c for c in cands if c.rookie_card_flag or c.autograph_flag or c.serial_numbered_flag]
    assert flagged, "expected rookie/auto/serial flags on at least one candidate"
    assert any(c.vertical == "Trading Cards" for c in cands)


def test_extract_news_article(config):
    page = _page_from_html(NEWS_ARTICLE_HTML, "https://www.pokemon.com/news/151")
    agent = ProductExtractionAgent(config, llm=None)
    cands = agent.extract(page)
    assert len(cands) >= 1
    news = NewsIntelligenceAgent(config, llm=None)
    signal = news.analyze(page, source_credibility=0.9)
    assert signal is not None
    assert signal.news_signal_score > 0
    assert signal.mapped_product_candidates


def test_no_llm_fallback_does_not_crash(config):
    # llm=None must never raise
    page = _page_from_html(TRADING_CARD_HTML, "https://x.com/a")
    agent = ProductExtractionAgent(config, llm=None)
    assert isinstance(agent.extract(page), list)


def test_blocked_page_yields_no_products(config):
    page = FetchedPage(url="https://blocked.com", domain="blocked.com", status="blocked",
                       error_message="HTTP 429 rate limited")
    agent = ProductExtractionAgent(config, llm=None)
    assert agent.extract(page) == []
