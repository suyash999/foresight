from emerging_collectibles_agent.product_name import (clean_product_name,
                                                      is_product_name,
                                                      product_name_quality)

# Real product names (must be kept)
GOOD = [
    "2025-26 Panini Obsidian Soccer Set Review and Checklist",
    "2026 Panini Prizm FIFA World Cup Soccer Cards – Full Review &#038; Checklist",
    "2025-26 Panini Prizm FIFA Soccer Sealed Hobby Box",
    "Wembanyama's 1-of-1 numbered 2023-24 Panini Prizm Black parallel",
    "Cracked Ice Prizm", "Silver Prizm Parallels", "Pandora Prizm",
    "Purple Hyper Prizm", "Panini Logo Prizm", "Old Glory Prizm",
    "2024 Topps Chrome Victor Wembanyama Rookie Autograph",
]

# News headlines / prose / spec-sheet fragments (must be rejected)
BAD = [
    "J.J. Watt calls out Real Salt Lake for not acknowledging his wife - ESPN",
    "2020, helping her squad beat the Red Stars 2-0",
    "Comprised of Chandler", "Variety", "First 100 guests get comics",
    "Distribution Notes: One base parallel", "Exact pack odds for each parallel",
    "It suits team collectors, parallel hunters", "sets combine limited availability",
    "12 cards per pack", "144 cards total", "Blaster: 4 cards/pack, 6 packs/box",
    "Victor Wembanyama card sells for $5.11 Million in Private Sale",
    "Hobby boxes deliver consistent volume of mid-tier numbered cards",
    "Daniel Radcliffe to Narrate Comedian Kyle Gordon's Third Album (EXCLUSIVE)",
    "Features Panini branding on card face",
]


def test_good_names_kept():
    dropped = [n for n in GOOD if not is_product_name(n)]
    assert not dropped, f"real products wrongly dropped: {dropped}"


def test_bad_names_rejected():
    kept = [n for n in BAD if is_product_name(n)]
    assert not kept, f"junk wrongly kept: {kept}"


def test_clean_strips_article_suffix():
    assert clean_product_name(
        "2026 Panini Prizm FIFA World Cup Soccer Cards – Full Review &#038; Checklist"
    ) == "2026 Panini Prizm FIFA World Cup Soccer Cards"


def test_news_suffix_hard_reject():
    score, reason = product_name_quality("World Cup winners awarded rings - ESPN")
    assert score == 0.0
