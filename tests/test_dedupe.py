"""Tests du dédoublonnage par URL et par titre."""
from datetime import datetime, timedelta, timezone

import pytest

from veille.collect import article_id
from veille.dedupe import dedupe, normalize_title, normalize_url, titles_match
from veille.models import Article

NOW = datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc)
STORY = "Même nouvelle importante du jour ici"


def art(source: str, title: str, url: str | None = None, published=NOW) -> Article:
    url = url or f"https://{source.lower()}.test/x"
    return Article(id=article_id(url), title=title, url=url, source=source, section="ia",
                   published=published, excerpt="")


@pytest.mark.parametrize("a,b", [
    ("https://www.Exemple.test/a/?utm_source=x&b=2&a=1#haut", "https://exemple.test/a?a=1&b=2"),
    ("http://exemple.test/a/", "http://exemple.test/a"),
    ("https://exemple.test/a?fbclid=abc&gclid=def", "https://exemple.test/a"),
])
def test_normalize_url(a, b):
    assert normalize_url(a) == normalize_url(b)


def test_different_articles_keep_different_urls():
    assert normalize_url("https://x.test/a?id=1") != normalize_url("https://x.test/a?id=2")
    assert normalize_url("https://x.test/a") != normalize_url("https://x.test/b")


def test_article_id_ignores_tracking_parameters():
    assert article_id("https://www.x.test/a?utm_medium=rss") == article_id("https://x.test/a")


def test_normalize_title():
    assert normalize_title("  L'État  québécois : « Oui » ! | La Presse ") == "l etat quebecois oui"


def test_titles_match():
    assert titles_match("La Banque du Canada abaisse son taux directeur à 2,25 %",
                        "La Banque du Canada abaisse son taux directeur à 2,25% | Les Affaires")
    assert titles_match("La Banque du Canada abaisse son taux directeur à 2,25 %",
                        "Banque du Canada abaisse son taux directeur à 2,25 %")
    assert not titles_match("OpenAI lance un nouveau modèle", "Google lance un nouveau modèle")
    assert not titles_match("Mise à jour", "Mise à jour de sécurité critique pour Windows Server")


def test_same_story_keeps_first_source_in_config_order():
    out = dedupe([art("B", STORY), art("A", STORY)], seen=[], recent=[], source_order=["A", "B"])
    assert [a.source for a in out] == ["A"]
    assert out[0].also_covered_by == [{"name": "B", "url": "https://b.test/x"}]


def test_same_url_from_two_feeds_is_one_article():
    url = "https://x.test/meme"
    out = dedupe([art("A", "Titre un", url), art("B", "Titre deux", url + "?utm_source=rss")],
                 seen=[], recent=[], source_order=["A", "B"])
    assert len(out) == 1 and out[0].source == "A"


def test_unrelated_articles_are_all_kept_in_input_order():
    arts = [art("A", "Premier sujet sans rapport aucun"), art("B", "Deuxième histoire complètement différente")]
    assert dedupe(arts, seen=[], recent=[], source_order=["B", "A"]) == arts


def test_already_seen_url_is_dropped():
    old = art("A", "Vieux titre", "https://a.test/vu", published=NOW - timedelta(days=20))
    new = art("A", "Titre remanié depuis", "https://a.test/vu")
    assert dedupe([new], seen=[old], recent=[], source_order=["A"]) == []


def test_title_seen_recently_from_another_source_is_dropped():
    yesterday = art("A", STORY, published=NOW - timedelta(days=1))
    assert dedupe([art("B", STORY)], seen=[yesterday], recent=[yesterday], source_order=["A", "B"]) == []


def test_title_seen_ten_days_ago_is_kept():
    old = art("A", STORY, published=NOW - timedelta(days=10))
    assert len(dedupe([art("B", STORY)], seen=[old], recent=[], source_order=["A", "B"])) == 1


def test_recurring_title_from_the_same_source_is_kept():
    """« Revue des marchés » revient chaque jour à une nouvelle adresse : ce n'est pas un doublon."""
    yesterday = art("A", "Revue quotidienne des marchés boursiers canadiens", "https://a.test/hier")
    today = art("A", "Revue quotidienne des marchés boursiers canadiens", "https://a.test/aujourdhui")
    assert dedupe([today], seen=[yesterday], recent=[yesterday], source_order=["A"]) == [today]


def test_undated_article_is_kept_once_then_dropped():
    undated = art("A", "Article sans date dans son flux", published=None)
    first = dedupe([undated], seen=[], recent=[], source_order=["A"])
    assert len(first) == 1
    assert dedupe([undated], seen=first, recent=first, source_order=["A"]) == []


def test_input_articles_are_not_modified():
    a, b = art("A", STORY), art("B", STORY)
    dedupe([a, b], seen=[], recent=[], source_order=["A", "B"])
    assert a.also_covered_by == []
