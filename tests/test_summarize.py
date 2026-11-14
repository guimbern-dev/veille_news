"""Tests des résumés : l'API Claude est remplacée par un faux client."""
import json
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from veille.config import Section
from veille.models import Article
from veille.summarize import daily_digest, summarize_articles

NOW = datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc)
SECTIONS = (Section(name="Intelligence artificielle", slug="ia", max_articles=10, sources=()),
            Section(name="Finance et marchés", slug="finance", max_articles=10, sources=()))


def art(ident: str, section: str = "ia", hours_ago: int = 1, **kw) -> Article:
    base = dict(id=ident, title=f"Titre {ident}", url=f"https://x.test/{ident}", source="Source",
                section=section, published=NOW - timedelta(hours=hours_ago),
                excerpt=f"Extrait unique de {ident}.")
    base.update(kw)
    return Article(**base)


def message(text: str, stop_reason: str = "end_turn"):
    """Imite la forme d'une réponse de l'API : blocs de contenu, raison d'arrêt, consommation."""
    return SimpleNamespace(stop_reason=stop_reason,
                           content=[SimpleNamespace(type="thinking", thinking=""),
                                    SimpleNamespace(type="text", text=text)],
                           usage=SimpleNamespace(input_tokens=100, output_tokens=20))


def ok(resume: str, importance: int):
    return message(json.dumps({"resume": resume, "importance": importance}))


def refusal():
    return SimpleNamespace(stop_reason="refusal", content=[], usage=SimpleNamespace(input_tokens=5, output_tokens=0))


def digest_reply(*points: tuple[str, str, list[str]]):
    return message(json.dumps({"points": [{"texte": t, "rubrique": r, "articles": ids} for t, r, ids in points]}))


class FakeClient:
    """Rejoue une réponse choisie selon un marqueur présent dans le texte envoyé.

    Les appels partent en parallèle : on ne peut donc pas se fier à leur ordre.
    """

    def __init__(self, replies: dict, default=None):
        self.replies, self.default = replies, default
        self.calls: list[dict] = []
        self._lock = threading.Lock()
        self.messages = SimpleNamespace(create=self._create)

    def sent_text(self, index: int = 0) -> str:
        call = self.calls[index]
        return str(call.get("system", "")) + json.dumps(call["messages"], ensure_ascii=False)

    def _create(self, **kwargs):
        with self._lock:
            self.calls.append(kwargs)
        text = json.dumps(kwargs["messages"], ensure_ascii=False)
        reply = next((r for marker, r in self.replies.items() if marker in text), self.default)
        if reply is None:
            raise AssertionError("appel inattendu à l'API")
        if isinstance(reply, Exception):
            raise reply
        return reply


def test_summary_and_importance_are_stored():
    out = summarize_articles([art("a1")], FakeClient({"Titre a1": ok("Résumé.", 4)}), "claude-haiku-5-5", 40)
    assert (out[0].summary, out[0].importance) == ("Résumé.", 4)


def test_only_title_and_excerpt_are_sent_with_the_configured_model():
    a1 = art("a1")
    client = FakeClient({}, default=ok("R.", 3))
    summarize_articles([a1], client, "modele-x", 40)
    assert a1.title in client.sent_text() and a1.excerpt in client.sent_text()
    assert client.calls[0]["model"] == "modele-x"
    assert "français" in client.sent_text()


def test_already_summarized_articles_are_not_sent_again():
    done = art("a1", summary="Déjà fait.", importance=5)
    client = FakeClient({})
    assert summarize_articles([done], client, "m", 40) == [done]
    assert client.calls == []


@pytest.mark.parametrize("reply", [
    refusal(), RuntimeError("503"), ok("", 3), ok("   ", 3), message("ceci n'est pas du JSON"),
    message(json.dumps({"importance": 3})), message('{"resume": "Coupé en plein', stop_reason="max_tokens"),
])
def test_bad_reply_falls_back_to_excerpt(reply):
    client = FakeClient({"Titre a1": reply, "Titre a2": ok("R2.", 3)})
    out = summarize_articles([art("a1"), art("a2")], client, "m", 40)
    assert (out[0].summary, out[0].importance) == (None, 2)
    assert (out[1].summary, out[1].importance) == ("R2.", 3)


@pytest.mark.parametrize("given,stored", [(0, 1), (9, 5), (3, 3), (-4, 1)])
def test_importance_is_clamped(given, stored):
    out = summarize_articles([art("a1")], FakeClient({}, default=ok("R.", given)), "m", 40)
    assert out[0].importance == stored


def test_cap_per_section_limits_calls_and_skips_the_oldest():
    arts = [art("vieux", hours_ago=30), art("recent", hours_ago=1), art("moyen", hours_ago=10),
            art("autre", section="finance", hours_ago=40)]
    client = FakeClient({}, default=ok("R.", 3))
    out = summarize_articles(arts, client, "m", cap_per_section=2)
    assert len(client.calls) == 3                       # 2 en IA, 1 en finance
    assert [a.id for a in out] == ["vieux", "recent", "moyen", "autre"]   # ordre d'entrée conservé
    assert (out[0].summary, out[0].importance) == (None, 1)
    assert out[1].summary == out[2].summary == out[3].summary == "R."


def test_usage_is_accumulated():
    usage = {}
    summarize_articles([art("a1"), art("a2")], FakeClient({}, default=ok("R.", 3)), "m", 40, usage=usage)
    assert usage == {"input_tokens": 200, "output_tokens": 40}


def summarized() -> list[Article]:
    return [art("a1", summary="Résumé un.", importance=5), art("a2", summary="Résumé deux.", importance=3),
            art("f1", section="finance", summary="Résumé finance.", importance=4),
            art("mineur", summary="Peu important.", importance=1), art("brut")]


def test_digest_returns_points_linked_to_articles():
    client = FakeClient({}, default=digest_reply(("Point un", "ia", ["a1", "a2"]), ("Point deux", "finance", ["f1"]),
                                                  ("Point trois", "ia", ["a2"])))
    points = daily_digest(summarized(), SECTIONS, client, "claude-sonnet-5-5")
    assert [(p.text, p.section, p.article_ids) for p in points] == [
        ("Point un", "ia", ["a1", "a2"]), ("Point deux", "finance", ["f1"]), ("Point trois", "ia", ["a2"])]
    sent = client.sent_text()
    assert client.calls[0]["model"] == "claude-sonnet-5-5"
    assert "Résumé un." in sent and "Résumé finance." in sent
    assert "Peu important." not in sent and "Extrait unique de brut" not in sent


def test_digest_drops_unknown_ids_and_empty_points():
    client = FakeClient({}, default=digest_reply(
        ("Un", "ia", ["a1", "inconnu"]), ("Deux", "finance", ["f1"]), ("Trois", "ia", ["a2"]),
        ("Sans article valide", "ia", ["inconnu"]), ("   ", "ia", ["a1"])))
    points = daily_digest(summarized(), SECTIONS, client, "m")
    assert [(p.text, p.article_ids) for p in points] == [("Un", ["a1"]), ("Deux", ["f1"]), ("Trois", ["a2"])]


def test_digest_fixes_an_unknown_section_from_its_first_article():
    client = FakeClient({}, default=digest_reply(("Un", "Finance", ["f1"]), ("Deux", "ia", ["a1"]), ("Trois", "ia", ["a2"])))
    assert daily_digest(summarized(), SECTIONS, client, "m")[0].section == "finance"


@pytest.mark.parametrize("reply", [
    refusal(), RuntimeError("panne"), message("pas du JSON"),
    digest_reply(("Un", "ia", ["a1"]), ("Deux", "ia", ["a2"])),          # moins de 3 points valides
])
def test_digest_returns_none_on_error_or_too_few_points(reply):
    assert daily_digest(summarized(), SECTIONS, FakeClient({}, default=reply), "m") is None


def test_digest_without_enough_articles_does_not_call_the_api():
    client = FakeClient({})
    assert daily_digest([art("a1", summary="R.", importance=5)], SECTIONS, client, "m") is None
    assert client.calls == []
