"""Tests de l'enchaînement complet (réseau et API Claude simulés)."""
import json
import os
import threading
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace

import pytest

from veille.cli import load_env, main, run_pipeline
from veille.config import Config, Section, Source
from veille.store import load_day

UTC = timezone.utc
NOW = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)
CFG = Config(timezone="America/Toronto", window_hours=36, summary_cap=40, model_articles="m-articles",
             model_digest="m-digest", markets=(),
             sections=(Section("IA", "ia", 10, (Source("Source A", "https://a.test/rss"),)),))

CONFIG_YAML = """
modeles: {articles: m-articles, resume_du_jour: m-digest}
rubriques:
  - nom: IA
    sources: [{nom: Source A, url: "https://a.test/rss"}]
"""


def feed(titles: list[str], when: datetime = NOW - timedelta(hours=2)) -> bytes:
    items = "".join(
        f"<item><title>{t}</title><link>https://a.test/{t.replace(' ', '-')}</link>"
        f"<pubDate>{format_datetime(when)}</pubDate><description>Extrait de {t}.</description></item>"
        for t in titles)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>A</title>{items}</channel></rss>'.encode()


THREE = ["Premier sujet du jour", "Deuxième nouvelle distincte", "Troisième histoire différente"]


class FakeClient:
    """Résume tout, et produit un résumé du jour qui cite les trois premiers articles reçus."""

    def __init__(self, broken: bool = False):
        self.calls, self.broken, self._lock = [], broken, threading.Lock()
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        with self._lock:
            self.calls.append(kwargs)
        if self.broken:
            raise RuntimeError("API en panne")
        if kwargs["model"] == "m-digest":
            import re
            ids = re.findall(r"\[(\w{12})\]", kwargs["messages"][0]["content"])
            body = {"points": [{"texte": f"Point {i}", "rubrique": "ia", "articles": [x]} for i, x in enumerate(ids[:3])]}
        else:
            body = {"resume": "Résumé produit.", "importance": 4}
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(body))],
                               usage=SimpleNamespace(input_tokens=10, output_tokens=5))

    def count(self, model: str) -> int:
        return sum(1 for c in self.calls if c["model"] == model)


def run(tmp_path, now=NOW, titles=THREE, client=None, **kw):
    return run_pipeline(CFG, tmp_path / "data", tmp_path / "site", now, lambda url: feed(titles), client, {},
                        search=False, **kw)


def test_full_run_writes_day_file_and_site(tmp_path):
    client = FakeClient()
    data = run(tmp_path, client=client)
    assert data.day == date(2026, 11, 13) and len(data.articles) == 3
    assert all(a.summary == "Résumé produit." and a.importance == 4 for a in data.articles)
    assert len(data.digest) == 3
    assert load_day(date(2026, 11, 13), tmp_path / "data") == data
    page = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert "Premier sujet du jour" in page and "Résumé produit." in page and "Point 0" in page


def test_rerun_same_day_does_not_duplicate_or_resummarize(tmp_path):
    client = FakeClient()
    run(tmp_path, client=client)
    again = run(tmp_path, now=NOW + timedelta(hours=1), client=client)
    assert len(again.articles) == len({a.id for a in again.articles}) == 3
    assert client.count("m-articles") == 3 and client.count("m-digest") == 2   # seul le résumé du jour est refait


def test_rerun_adds_only_the_new_articles(tmp_path):
    client = FakeClient()
    run(tmp_path, client=client)
    again = run(tmp_path, now=NOW + timedelta(hours=1), titles=THREE + ["Quatrième dépêche toute neuve"], client=client)
    assert len(again.articles) == 4 and client.count("m-articles") == 4


def test_next_day_drops_what_was_already_published(tmp_path):
    client = FakeClient()
    run(tmp_path, client=client)
    tomorrow = run(tmp_path, now=NOW + timedelta(days=1), titles=THREE + ["Nouvelle du lendemain seulement"], client=client)
    assert [a.title for a in tomorrow.articles] == ["Nouvelle du lendemain seulement"]
    assert tomorrow.day == date(2026, 11, 14)


def test_no_articles_exits_1_and_leaves_previous_site_untouched(tmp_path):
    run(tmp_path, client=FakeClient())
    before = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    with pytest.raises(SystemExit) as stop:
        run(tmp_path, now=NOW + timedelta(days=1), titles=[], client=FakeClient())
    assert stop.value.code == 1
    assert (tmp_path / "site" / "index.html").read_text(encoding="utf-8") == before
    assert load_day(date(2026, 11, 14), tmp_path / "data") is None


def test_runs_without_client(tmp_path):
    data = run(tmp_path, client=None)
    assert all(a.summary is None for a in data.articles) and data.digest is None
    assert "Extrait de la source" in (tmp_path / "site" / "index.html").read_text(encoding="utf-8")


def test_claude_fully_down_still_builds(tmp_path):
    data = run(tmp_path, client=FakeClient(broken=True))
    assert all((a.summary, a.importance) == (None, 2) for a in data.articles) and data.digest is None
    assert (tmp_path / "site" / "index.html").exists()


def test_failed_articles_are_retried_on_rerun(tmp_path):
    run(tmp_path, client=FakeClient(broken=True))
    again = run(tmp_path, now=NOW + timedelta(hours=1), client=FakeClient())
    assert all(a.summary == "Résumé produit." for a in again.articles)


def test_day_uses_toronto_date(tmp_path):
    data = run(tmp_path, now=datetime(2026, 11, 14, 3, 0, tzinfo=UTC), client=None)
    assert data.day == date(2026, 11, 13)


def test_failed_sources_are_recorded(tmp_path):
    cfg = Config(**{**CFG.__dict__, "sections": (Section("IA", "ia", 10, (
        Source("Source A", "https://a.test/rss"), Source("En panne", "https://panne.test/rss"))),)})

    def fetch(url):
        if "panne" in url:
            raise ConnectionError("x")
        return feed(THREE)
    data = run_pipeline(cfg, tmp_path / "data", tmp_path / "site", NOW, fetch, None, {}, search=False)
    assert data.failed_sources == ["En panne"]


def test_build_false_skips_the_site(tmp_path):
    run(tmp_path, client=None, build=False)
    assert not (tmp_path / "site").exists() and load_day(date(2026, 11, 13), tmp_path / "data")


def test_load_env_reads_missing_variables_only(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('# commentaire\nVEILLE_TEST_A=valeur a\nVEILLE_TEST_B="entre guillemets"\nVEILLE_TEST_C=\n\nligne sans égal\n')
    monkeypatch.setenv("VEILLE_TEST_A", "déjà définie")
    monkeypatch.delenv("VEILLE_TEST_B", raising=False)
    monkeypatch.delenv("VEILLE_TEST_C", raising=False)
    load_env(env)
    assert os.environ["VEILLE_TEST_A"] == "déjà définie"
    assert os.environ["VEILLE_TEST_B"] == "entre guillemets"
    assert "VEILLE_TEST_C" not in os.environ
    load_env(tmp_path / "absent.env")   # un fichier absent n'est pas une erreur


def test_main_build_and_bad_config(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text(CONFIG_YAML, encoding="utf-8")
    run(tmp_path, client=None, build=False)
    assert main(["build", "--sans-recherche"]) == 0
    assert (tmp_path / "site" / "index.html").exists()
    (tmp_path / "config.yaml").write_text("rubriques: []\n", encoding="utf-8")
    assert main(["build"]) == 2
    assert "modeles" in capsys.readouterr().err


# --- Corrections issues de la revue finale (I4) ---

def test_total_failure_of_claude_is_flagged_for_github(tmp_path, capsys):
    run(tmp_path, client=FakeClient(broken=True))
    assert "::warning" in capsys.readouterr().out


def test_no_flag_when_summaries_work(tmp_path, capsys):
    run(tmp_path, client=FakeClient())
    assert "::warning" not in capsys.readouterr().out


# --- Relance : le résumé du jour déjà produit n'est pas perdu ---

def test_rerun_without_claude_keeps_the_digest(tmp_path):
    first = run(tmp_path, client=FakeClient())
    again = run(tmp_path, now=NOW + timedelta(hours=1), client=None)
    assert again.digest == first.digest and len(again.digest) == 3


def test_rerun_with_failing_digest_keeps_the_previous_one(tmp_path):
    first = run(tmp_path, client=FakeClient())
    again = run(tmp_path, now=NOW + timedelta(hours=1), client=FakeClient(broken=True))
    assert again.digest == first.digest


# --- Relance : une image apparue dans le flux complète l'article déjà enregistré ---

def test_rerun_fills_in_missing_images(tmp_path):
    run(tmp_path, client=None)
    def with_images(url):
        return feed(THREE).replace(b"</item>", b'<enclosure url="https://img.test/x.jpg" type="image/jpeg" length="1"/></item>')
    again = run_pipeline(CFG, tmp_path / "data", tmp_path / "site", NOW + timedelta(hours=1), with_images, None, {}, search=False)
    assert [a.image for a in again.articles] == ["https://img.test/x.jpg"] * 3
