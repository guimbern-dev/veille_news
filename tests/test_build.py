"""Tests de la génération du site : on construit un petit site et on lit le HTML produit."""
import importlib.util
import re
from datetime import date, datetime, timedelta, timezone
from html import unescape
from pathlib import Path

import pytest

from veille.build import build_site, format_quote, french_date, relative_time
from veille.config import Config, Section, Source
from veille.models import Article, DayData, DigestPoint, MarketQuote
from veille.store import save_day

UTC = timezone.utc
GENERATED = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)
SRC = (Source(name="S", url="https://s.test/rss"),)
CONFIG = Config(
    timezone="America/Toronto", window_hours=36, summary_cap=40, model_articles="m1", model_digest="m2",
    markets=(),
    sections=(Section("Finance et marchés", "finance", 10, SRC), Section("Intelligence artificielle", "ia", 2, SRC),
              Section("Technologie et infrastructure TI", "tech", 10, SRC), Section("Québec/Canada", "quebec", 8, SRC)),
)


def art(ident, section, title, hours_ago=2, summary="Résumé de l'article.", importance=3, **kw) -> Article:
    return Article(id=ident, title=title, url=f"https://source.test/{ident}", source="Source test",
                   section=section, published=GENERATED - timedelta(hours=hours_ago),
                   excerpt="Extrait du flux. " * 30, summary=summary, importance=importance, **kw)


def quote(name, kind, value, change, low=None, as_of=date(2026, 11, 12)) -> MarketQuote:
    return MarketQuote(name=name, kind=kind, value=value, value_low=low, change_pct=change,
                       as_of=None if value is None else as_of)


DAY_13 = DayData(
    day=date(2026, 11, 13), generated_at=GENERATED,
    markets=[quote("TSX", "indice", 25104.32, 0.4), quote("S&P 500", "indice", 6012.0, -0.2),
             quote("Nasdaq", "indice", None, None), quote("CAD/USD", "devise", 0.7015, -0.52),
             quote("Taux BdC", "taux", 2.5, 0.0), quote("Taux Fed", "taux", 4.0, -0.25, low=3.75)],
    articles=[
        art("i3", "ia", "Importance 3 ancien", hours_ago=20),
        art("i2", "ia", "Importance 3 récent", hours_ago=1),
        art("i1", "ia", "Importance 5", hours_ago=30, importance=5),
        art("f1", "finance", "Article de finance"),
        art("f2", "finance", "Article sans résumé", summary=None, importance=2),
        art("f3", "finance", "<script>alert(1)</script>"),
        art("q1", "quebec", "Nouvelle du Québec",
            also_covered_by=[{"name": "Autre source", "url": "https://autre.test/q1"}]),
        art("s1", "sports", "Rubrique retirée de la configuration"),
    ],
    digest=[DigestPoint("Point clé sur l'IA", "ia", ["i1"]),
            DigestPoint("Point clé de finance", "finance", ["f1", "s1"]),
            DigestPoint("Point fantôme", "sports", ["s1"])],
    failed_sources=["Source X", "Source Y"],
)
DAY_12 = DayData(day=date(2026, 11, 12), generated_at=GENERATED - timedelta(days=1), markets=[],
                 articles=[art("v1", "ia", "Nouvelle de la veille")], digest=None, failed_sources=[])
DAY_OCT = DayData(day=date(2026, 10, 31), generated_at=GENERATED - timedelta(days=13), markets=[],
                  articles=[art("o1", "ia", "Octobre un"), art("o2", "tech", "Octobre deux")],
                  digest=None, failed_sources=[])


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("veille")
    for day in (DAY_13, DAY_12, DAY_OCT):
        save_day(day, root / "data")
    build_site(root / "data", root / "site", CONFIG, search=False)
    return root / "site"


def html_of(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def text(path: Path) -> str:
    """Texte visible d'une page, espaces insécables ramenés à des espaces simples."""
    body = re.sub(r"<(script|style)\b.*?</\1>", " ", html_of(path), flags=re.S)
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", body)).replace("\xa0", " ").replace(" ", " "))


def section_html(page: str, slug: str) -> str:
    return re.search(rf'<section class="rubrique" id="r-{slug}".*?</section>', page, flags=re.S).group(0)


def titles(fragment: str) -> list[str]:
    return [unescape(t) for t in re.findall(r'<h3[^>]*>\s*<a [^>]*>(.*?)</a>', fragment, flags=re.S)]


def test_index_shows_latest_day_and_sections_in_config_order(site):
    page = html_of(site / "index.html")
    assert re.findall(r'<section class="rubrique" id="r-([a-z]+)"', page) == ["finance", "ia", "tech", "quebec"]
    assert "vendredi 13 novembre 2026" in text(site / "index.html")
    assert '<html lang="fr"' in page


def test_articles_sorted_by_importance_then_recency_and_overflow_is_folded(site):
    ia = section_html(html_of(site / "index.html"), "ia")
    visible, folded = ia.split("<details", 1)
    assert titles(visible) == ["Importance 5", "Importance 3 récent"]
    assert titles(folded) == ["Importance 3 ancien"]
    assert "Afficher 1 autre article" in text(site / "index.html")


def test_every_article_links_to_its_source(site):
    page = html_of(site / "index.html")
    cards = re.findall(r'<article class="article[^"]*" id="a-(\w+)">(.*?)</article>', page, flags=re.S)
    assert [c[0] for c in cards if c[0] == "s1"] == []          # rubrique retirée : non affiché
    assert len(cards) == 7
    for ident, card in cards:
        assert f'href="https://source.test/{ident}"' in card
        assert 'rel="noopener noreferrer"' in card
        assert "Source test" in card


def test_also_covered_by_is_shown_with_links(site):
    quebec = section_html(html_of(site / "index.html"), "quebec")
    assert 'href="https://autre.test/q1"' in quebec and "Autre source" in quebec


def test_feed_content_is_escaped(site):
    page = html_of(site / "index.html")
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_article_without_summary_shows_marked_excerpt(site):
    card = re.search(r'<article class="article[^"]*" id="a-f2">(.*?)</article>', html_of(site / "index.html"), flags=re.S).group(1)
    assert "Extrait de la source" in card
    body = unescape(re.sub(r"<[^>]+>", "", re.search(r'<p class="resume[^"]*">(.*?)</p>', card, flags=re.S).group(1)))
    assert "Extrait du flux." in body and len(body) < 340          # tronqué à 300 caractères + mention


def test_empty_section_says_so(site):
    assert "Aucune nouvelle aujourd'hui" in unescape(section_html(html_of(site / "index.html"), "tech")).replace("’", "'")


def test_digest_points_link_to_displayed_articles_only(site):
    page = html_of(site / "index.html")
    digest = re.search(r'<section class="resume-du-jour".*?</section>', page, flags=re.S).group(0)
    assert "Point clé sur l'IA" in unescape(digest).replace("’", "'") and "Point clé de finance" in digest
    assert "Point fantôme" not in digest                      # aucun de ses articles n'est affiché
    assert re.findall(r'href="#a-(\w+)"', digest) == ["i1", "f1"]
    assert "Intelligence artificielle" in digest               # étiquette de rubrique


def test_digest_hidden_when_none(site):
    assert 'class="resume-du-jour"' not in html_of(site / "jour" / "2026-11-12.html")


def test_market_formatting(site):
    t = text(site / "index.html")
    assert "25 104,32" in t and "▲ 0,4 %" in t and "▼ 0,2 %" in t
    assert "n. d." in t and "0,7015" in t
    assert "2,50 %" in t and "inchangé" in t
    assert "3,75-4,00 %" in t and "▼ 0,25 pt" in t
    assert "Clôture du 12 novembre" in t


def test_market_strip_absent_without_quotes(site):
    assert 'class="marches"' not in html_of(site / "jour" / "2026-11-12.html")


def test_archive_lists_every_day_and_day_pages_exist(site):
    archive = html_of(site / "archive.html")
    assert re.findall(r'href="(jour/[\d-]+\.html)"', archive) == [
        "jour/2026-11-13.html", "jour/2026-11-12.html", "jour/2026-10-31.html"]
    t = text(site / "archive.html")
    assert t.index("Novembre 2026") < t.index("Octobre 2026")
    assert "7 articles" in t and "1 article" in t and "2 articles" in t
    for name in ("2026-11-13", "2026-11-12", "2026-10-31"):
        assert (site / "jour" / f"{name}.html").exists()


def test_day_page_has_same_articles_as_index_and_is_the_only_one_indexed(site):
    day = html_of(site / "jour" / "2026-11-13.html")
    assert titles(day) == titles(html_of(site / "index.html"))
    assert "data-pagefind-body" in day
    assert "data-pagefind-body" not in html_of(site / "index.html")
    assert "data-pagefind-body" not in html_of(site / "archive.html")


def test_day_pages_link_to_their_neighbours(site):
    middle = html_of(site / "jour" / "2026-11-12.html")
    assert 'href="2026-10-31.html"' in middle and 'href="2026-11-13.html"' in middle


def test_noindex_and_robots(site):
    for page in ("index.html", "archive.html", "recherche.html", "jour/2026-11-13.html"):
        assert 'content="noindex, nofollow"' in html_of(site / page)
    assert html_of(site / "robots.txt").strip().endswith("Disallow: /")


def test_all_internal_links_are_relative_and_resolve(site):
    for page in list(site.glob("*.html")) + list(site.glob("jour/*.html")):
        for target in re.findall(r'(?:href|src)="([^"]+)"', html_of(page)):
            if target.startswith(("http://", "https://", "#", "mailto:")) or "pagefind/" in target:
                continue
            assert not target.startswith("/"), f"{page.name} : lien absolu {target}"
            assert (page.parent / target.split("#")[0].split("?")[0]).exists(), f"{page.name} : lien cassé {target}"


def test_failed_sources_footer(site):
    assert "2 sources indisponibles" in text(site / "index.html")
    assert "indisponible" not in text(site / "jour" / "2026-11-12.html")


def test_stale_day_pages_are_removed(tmp_path):
    save_day(DAY_12, tmp_path / "data")
    (tmp_path / "site" / "jour").mkdir(parents=True)
    (tmp_path / "site" / "jour" / "2020-01-01.html").write_text("vieux")
    build_site(tmp_path / "data", tmp_path / "site", CONFIG, search=False)
    assert [p.name for p in (tmp_path / "site" / "jour").iterdir()] == ["2026-11-12.html"]


def test_build_without_any_data_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        build_site(tmp_path / "data", tmp_path / "site", CONFIG, search=False)


@pytest.mark.skipif(importlib.util.find_spec("pagefind") is None, reason="Pagefind non installé")
def test_search_index_is_built(tmp_path):
    save_day(DAY_13, tmp_path / "data")
    build_site(tmp_path / "data", tmp_path / "site", CONFIG, search=True)
    assert (tmp_path / "site" / "pagefind" / "pagefind.js").exists()
    assert "pagefind/pagefind-ui.js" in html_of(tmp_path / "site" / "recherche.html")


def test_french_date():
    assert french_date(date(2026, 11, 13)) == "vendredi 13 novembre 2026"
    assert french_date(date(2026, 8, 1)) == "samedi 1er août 2026"


@pytest.mark.parametrize("delta,expected", [
    (timedelta(minutes=20), "il y a 20 min"), (timedelta(hours=5, minutes=40), "il y a 5 h"),
    (timedelta(hours=30), "hier"), (timedelta(days=3), "il y a 3 j"), (timedelta(seconds=-600), "à l'instant"),
])
def test_relative_time(delta, expected):
    assert relative_time(GENERATED - delta, GENERATED) == expected


def test_relative_time_without_date_is_empty():
    assert relative_time(None, GENERATED) == ""


def test_format_quote_flat_and_small_moves():
    flat = format_quote(quote("X", "indice", 100.0, 0.004))
    assert (flat["direction"], flat["change"].replace("\xa0", " ")) == ("stable", "= 0,0 %")
    assert format_quote(quote("Y", "indice", 100.0, None))["change"] == ""


# --- Corrections issues de la revue finale (I2) ---

def test_close_date_comes_from_indices_not_from_rates():
    from veille.build import day_view
    data = DayData(day=date(2026, 11, 16), generated_at=GENERATED, articles=[], digest=None, failed_sources=[],
                   markets=[quote("TSX", "indice", 100.0, 0.1, as_of=date(2026, 11, 12)),
                            quote("S&P 500", "indice", 100.0, 0.1, as_of=date(2026, 11, 13)),
                            quote("CAD/USD", "devise", 0.7, 0.1, as_of=date(2026, 11, 13)),
                            quote("Taux Fed", "taux", 4.0, 0.0, as_of=date(2026, 11, 15))])
    view = day_view(data, CONFIG)
    assert view["close"] == "13 novembre"
    dates = {q["name"]: q["date"] for q in view["quotes"]}
    assert dates == {"TSX": "12 nov.", "S&P 500": "", "CAD/USD": "", "Taux Fed": ""}


# --- Image d'illustration ---

def build_with(tmp_path, *articles):
    save_day(DayData(day=date(2026, 11, 13), generated_at=GENERATED, markets=[], articles=list(articles),
                     digest=None, failed_sources=[]), tmp_path / "data")
    build_site(tmp_path / "data", tmp_path / "site", CONFIG, search=False)
    return (tmp_path / "site" / "index.html").read_text(encoding="utf-8")


def test_article_image_is_shown_lazily_and_links_to_the_source(tmp_path):
    page = build_with(tmp_path, art("i1", "ia", "Avec image", image="https://img.test/photo.jpg?w=600&h=400"))
    tag = re.search(r"<img[^>]*>", page).group(0)
    assert 'src="https://img.test/photo.jpg?w=600&amp;h=400"' in tag
    assert 'loading="lazy"' in tag and 'referrerpolicy="no-referrer"' in tag and 'alt=""' in tag
    assert re.search(r'<a class="vignette" href="https://source.test/i1"[^>]*>\s*<img', page)


def test_article_without_image_has_no_img_tag(tmp_path):
    assert "<img" not in build_with(tmp_path, art("i2", "ia", "Sans image"))


# --- Image de chaque point du Résumé du jour ---

def digest_html(tmp_path, articles, digest):
    save_day(DayData(day=date(2026, 11, 13), generated_at=GENERATED, markets=[], articles=articles,
                     digest=digest, failed_sources=[]), tmp_path / "data")
    build_site(tmp_path / "data", tmp_path / "site", CONFIG, search=False)
    page = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    return re.search(r'<section class="resume-du-jour".*?</section>', page, flags=re.S).group(0)


def test_digest_point_shows_the_image_of_its_first_illustrated_article(tmp_path):
    articles = [art("d1", "ia", "Sans image"), art("d2", "ia", "Avec image", image="https://img.test/deux.jpg"),
                art("d3", "ia", "Autre image", image="https://img.test/trois.jpg")]
    html = digest_html(tmp_path, articles, [DigestPoint("Point illustré", "ia", ["d1", "d2", "d3"])])
    assert re.findall(r'<img[^>]*src="([^"]+)"', html) == ["https://img.test/deux.jpg"]
    assert re.search(r'<a class="vignette" href="#a-d2"', html)
    assert 'loading="lazy"' in html and 'referrerpolicy="no-referrer"' in html


def test_digest_point_without_any_image_has_no_img(tmp_path):
    html = digest_html(tmp_path, [art("d1", "ia", "Sans image")], [DigestPoint("Point nu", "ia", ["d1"])])
    assert "<img" not in html and "Point nu" in html


def test_static_files_carry_a_fingerprint_against_stale_caches(site):
    assert re.search(r'href="static/style\.css\?v=[0-9a-f]{8}"', html_of(site / "index.html"))
    assert re.search(r'href="\.\./static/style\.css\?v=[0-9a-f]{8}"', html_of(site / "jour" / "2026-11-13.html"))
