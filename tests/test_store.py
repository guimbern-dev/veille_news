"""Tests du stockage : un fichier JSON par jour."""
from datetime import date, datetime, timezone

from veille.models import Article, DayData, DigestPoint, MarketQuote
from veille.store import list_days, load_day, load_previous, save_day

UTC = timezone.utc


def article(ident: str, **kw) -> Article:
    base = dict(id=ident, title=f"Titre {ident}", url=f"https://x.test/{ident}", source="Source",
                section="ia", published=datetime(2026, 11, 12, 14, 0, tzinfo=UTC), excerpt="Extrait")
    base.update(kw)
    return Article(**base)


def day_data(day: date, articles: list[Article]) -> DayData:
    return DayData(day=day, generated_at=datetime(2026, 11, 13, 10, 0, tzinfo=UTC),
                   markets=[], articles=articles, digest=None, failed_sources=[])


SAMPLE = DayData(
    day=date(2026, 11, 13),
    generated_at=datetime(2026, 11, 13, 10, 2, 3, tzinfo=UTC),
    markets=[
        MarketQuote(name="S&P 500", kind="indice", value=6012.5, value_low=None,
                    change_pct=-0.2, as_of=date(2026, 11, 12)),
        MarketQuote(name="TSX", kind="indice", value=None, value_low=None, change_pct=None, as_of=None),
    ],
    articles=[
        article("a1", title="Le Québec adopte une loi", summary="Résumé en français.", importance=4,
                also_covered_by=[{"name": "Autre", "url": "https://autre.test/x"}]),
        article("a2", published=None),
    ],
    digest=[DigestPoint(text="Point clé", section="ia", article_ids=["a1"])],
    failed_sources=["Source en panne"],
)


def test_round_trip_preserves_everything(tmp_path):
    save_day(SAMPLE, tmp_path)
    assert load_day(SAMPLE.day, tmp_path) == SAMPLE


def test_round_trip_without_digest(tmp_path):
    data = day_data(date(2026, 11, 12), [article("a1")])
    save_day(data, tmp_path)
    assert load_day(data.day, tmp_path) == data


def test_file_is_readable_utf8_json(tmp_path):
    path = save_day(SAMPLE, tmp_path)
    assert path.name == "2026-11-13.json"
    text = path.read_text(encoding="utf-8")
    assert "Québec" in text and text.endswith("\n")
    assert "\n  " in text  # indenté, pour des différences Git lisibles


def test_save_creates_the_directory(tmp_path):
    assert save_day(SAMPLE, tmp_path / "data").exists()


def test_load_missing_day_returns_none(tmp_path):
    assert load_day(date(2026, 1, 1), tmp_path) is None


def test_list_days_ignores_other_files(tmp_path):
    for d in (date(2026, 11, 12), date(2026, 11, 13)):
        save_day(day_data(d, []), tmp_path)
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "brouillon.json").write_text("{}")
    assert list_days(tmp_path) == [date(2026, 11, 13), date(2026, 11, 12)]


def test_list_days_of_missing_directory_is_empty(tmp_path):
    assert list_days(tmp_path / "absent") == []


def test_load_previous_excludes_the_day_itself(tmp_path):
    for n in (10, 11, 12, 13):
        save_day(day_data(date(2026, 11, n), [article(f"id-{n}")]), tmp_path)
    ids = {a.id for a in load_previous(date(2026, 11, 13), tmp_path, days=2)}
    assert ids == {"id-11", "id-12"}
