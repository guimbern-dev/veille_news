"""Tests de la collecte des flux RSS et Atom (aucun accès réseau)."""
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

from veille.collect import clean_text, collect, parse_feed
from veille.config import Config, Section, Source

UTC = timezone.utc
FIXTURES = Path(__file__).parent / "fixtures"
SRC = Source(name="Source test", url="https://exemple.test/rss")
NOW = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)


def read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def config(*sources: Source) -> Config:
    return Config(timezone="America/Toronto", window_hours=36, summary_cap=40,
                  model_articles="m1", model_digest="m2", markets=(),
                  sections=(Section(name="IA", slug="ia", max_articles=10, sources=sources),))


def feed_with_ages() -> bytes:
    """Flux dont les entrées ont 2 h, 35 h et 37 h, plus une sans date."""
    def item(title: str, hours: int | None) -> str:
        date = "" if hours is None else f"<pubDate>{format_datetime(NOW - timedelta(hours=hours))}</pubDate>"
        return f"<item><title>{title}</title><link>https://exemple.test/{title.replace(' ', '-')}</link>{date}</item>"
    items = item("2h", 2) + item("35h", 35) + item("37h", 37) + item("sans date", None)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>{items}</channel></rss>'.encode()


def fails_for(bad_url: str):
    def fetch(url: str) -> bytes:
        if url == bad_url:
            raise ConnectionError("hôte injoignable")
        return read("rss_simple.xml")
    return fetch


def test_parses_rss_and_atom():
    for fixture in ("rss_simple.xml", "atom_simple.xml"):
        arts = parse_feed(read(fixture), SRC, "ia")
        assert arts[0].title == "Premier titre"
        assert arts[0].url == "https://exemple.test/a1"
        assert arts[0].published == datetime(2026, 11, 12, 14, 0, tzinfo=UTC)
        assert (arts[0].source, arts[0].section) == ("Source test", "ia")
        assert arts[0].excerpt == "Premier extrait."
        assert arts[0].summary is None and arts[0].importance is None


def test_ids_are_stable_and_distinct():
    first, second = parse_feed(read("rss_simple.xml"), SRC, "ia")
    assert len(first.id) == 12 and first.id != second.id
    assert parse_feed(read("rss_simple.xml"), SRC, "ia")[0].id == first.id


def test_dirty_feed_is_reduced_to_plain_text():
    a = parse_feed(read("rss_sale.xml"), SRC, "ia")[0]
    assert a.title == "Café & thé"
    assert "<" not in a.excerpt and "alert" not in a.excerpt and "color" not in a.excerpt
    assert a.excerpt.startswith("Texte utile du flux.")
    assert len(a.excerpt) <= 1500


def test_entry_without_link_or_title_is_skipped():
    titles = [a.title for a in parse_feed(read("rss_sale.xml"), SRC, "ia")]
    assert titles == ["Café & thé", "Date illisible"]


def test_unreadable_date_gives_none():
    assert parse_feed(read("rss_sale.xml"), SRC, "ia")[1].published is None


def test_clean_text_collapses_whitespace_and_cuts_on_a_word():
    assert clean_text("<p>Un&nbsp;deux</p>\n\n<p>trois</p>") == "Un deux trois"
    cut = clean_text("mot " * 100, limit=50)
    assert len(cut) <= 50 and cut.endswith("…") and "mo…" not in cut


def test_window_keeps_recent_and_undated_only():
    arts, failed = collect(config(SRC), now=NOW, fetch=lambda url: feed_with_ages())
    assert sorted(a.title for a in arts) == ["2h", "35h", "sans date"]
    assert failed == []


def test_one_failing_source_does_not_stop_the_others():
    bad = Source(name="Source en panne", url="https://panne.test/rss")
    arts, failed = collect(config(bad, SRC), NOW, fetch=fails_for("https://panne.test/rss"))
    assert failed == ["Source en panne"] and len(arts) == 2


def test_garbage_bytes_count_as_a_failed_source():
    arts, failed = collect(config(SRC), NOW, fetch=lambda url: b"<html>404</html>")
    assert failed == ["Source test"] and arts == []


def test_articles_keep_the_order_of_the_configuration():
    second = Source(name="Deuxième", url="https://deux.test/rss")
    arts, _ = collect(config(SRC, second), NOW, fetch=lambda url: read("rss_simple.xml"))
    assert [a.source for a in arts] == ["Source test", "Source test", "Deuxième", "Deuxième"]


def test_entries_dated_far_in_the_future_are_ignored():
    """Certains flux annoncent des événements à venir, datés dans plusieurs mois."""
    def item(title, delta):
        return (f"<item><title>{title}</title><link>https://exemple.test/{title}</link>"
                f"<pubDate>{format_datetime(NOW + delta)}</pubDate></item>")
    feed = ('<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>'
            + item("dans-3-mois", timedelta(days=90)) + item("dans-2-heures", timedelta(hours=2))
            + "</channel></rss>").encode()
    arts, _ = collect(config(SRC), NOW, fetch=lambda url: feed)
    assert [a.title for a in arts] == ["dans-2-heures"]
