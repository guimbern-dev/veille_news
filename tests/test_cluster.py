"""Tests du regroupement des nouvelles portant sur un même sujet (API Claude simulée)."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

from veille.cluster import group_articles
from veille.models import Article

NOW = datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc)


def art(ident: str, importance: int = 3, source: str = "S", **kw) -> Article:
    base = dict(id=ident, title=f"Titre {ident}", url=f"https://s.test/{ident}", source=source, section="ia",
                published=NOW, excerpt="", summary=f"Résumé {ident}.", importance=importance)
    return Article(**{**base, **kw})


def reply(*groups: tuple[list[str], str, str]):
    body = {"groupes": [{"articles": ids, "titre": t, "resume": r} for ids, t, r in groups]}
    return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(body))],
                           usage=SimpleNamespace(input_tokens=50, output_tokens=10))


class Client:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        r = self.replies[min(len(self.calls), len(self.replies)) - 1]
        if isinstance(r, Exception):
            raise r
        return r

    def sent(self, i: int = 0) -> str:
        return json.dumps(self.calls[i]["messages"], ensure_ascii=False)


def by_id(articles):
    return {a.id: a for a in articles}


def test_same_subject_articles_are_merged_under_one_lead():
    arts = [art("a", 3), art("b", 5, image="https://img.test/b.jpg"), art("c", 2), art("d", 4)]
    out = by_id(group_articles(arts, Client(reply((["a", "b", "c"], "Titre commun", "Résumé commun."))), "m"))
    assert out["b"].merged_into is None                       # le plus important mène le groupe
    assert (out["b"].group_title, out["b"].group_summary) == ("Titre commun", "Résumé commun.")
    assert out["a"].merged_into == "b" and out["c"].merged_into == "b"
    assert out["d"].merged_into is None and out["d"].group_title is None
    assert [a.id for a in group_articles(arts, Client(reply()), "m")] == ["a", "b", "c", "d"]   # ordre conservé


def test_lead_takes_the_highest_importance_and_a_member_image_if_it_has_none():
    arts = [art("a", 4), art("b", 4, image="https://img.test/b.jpg"), art("c", 5, image=None)]
    out = by_id(group_articles(arts, Client(reply((["a", "b", "c"], "T", "R"))), "m"))
    assert out["c"].merged_into is None and out["c"].importance == 5 and out["c"].image == "https://img.test/b.jpg"


def test_invalid_groups_are_ignored():
    arts = [art("a"), art("b"), art("c"), art("d")]
    client = Client(reply((["a", "zzz"], "Un seul valide", "R"),          # un identifiant inventé : reste 1 article
                          (["b", "c"], "", "R"),                           # titre vide
                          (["c", "d"], "Bon", "Résumé."),
                          (["d", "a"], "Déjà pris", "R")))                 # d appartient déjà à un groupe
    out = by_id(group_articles(arts, client, "m"))
    assert out["d"].merged_into == "c" or out["c"].merged_into == "d"
    assert out["a"].merged_into is None and out["b"].merged_into is None and out["a"].group_title is None


def test_merged_and_unsummarized_articles_are_not_sent():
    arts = [art("a"), art("b", merged_into="a"), art("c", summary=None), art("d")]
    client = Client(reply())
    group_articles(arts, client, "m")
    assert "[a]" in client.sent() and "[d]" in client.sent() and "[b]" not in client.sent() and "[c]" not in client.sent()


def test_existing_group_absorbs_a_new_article_and_keeps_its_lead():
    arts = [art("a", 3, group_title="Ancien titre", group_summary="Ancien résumé.", grouped=True),
            art("b", 2, merged_into="a", grouped=True), art("n", 5)]
    client = Client(reply((["n", "a"], "Titre mis à jour", "Résumé mis à jour.")))
    out = by_id(group_articles(arts, client, "m"))
    assert "Ancien titre" in client.sent() and "Ancien résumé." in client.sent()   # le groupe est présenté tel quel
    assert out["a"].merged_into is None and out["n"].merged_into == "a" and out["b"].merged_into == "a"
    assert out["a"].group_title == "Titre mis à jour" and out["a"].importance == 5


def test_two_existing_groups_can_be_merged():
    arts = [art("a", 4, group_title="G1", group_summary="R1", grouped=True), art("a2", merged_into="a", grouped=True),
            art("b", 3, group_title="G2", group_summary="R2", grouped=True), art("b2", merged_into="b", grouped=True),
            art("n")]
    out = by_id(group_articles(arts, Client(reply((["a", "b"], "Fusion", "R"))), "m"))
    assert out["b"].merged_into == "a" and out["b2"].merged_into == "a" and out["a2"].merged_into == "a"
    assert out["b"].group_title is None


def test_no_call_when_nothing_new_since_the_last_grouping():
    client = Client(reply())
    arts = [art("a", grouped=True), art("b", grouped=True)]
    assert group_articles(arts, client, "m") == arts and client.calls == []


def test_all_leads_are_marked_after_a_successful_call_only():
    ok = group_articles([art("a"), art("b")], Client(reply()), "m")
    assert all(a.grouped for a in ok)
    failed_client = Client(RuntimeError("panne"))
    failed = group_articles([art("a"), art("b")], failed_client, "m")
    assert not any(a.grouped for a in failed) and len(failed_client.calls) == 2    # une nouvelle tentative, puis abandon


def test_fewer_than_two_candidates_means_no_call():
    client = Client(reply())
    group_articles([art("a")], client, "m")
    assert client.calls == []


def test_lead_comes_from_the_section_most_of_the_group_belongs_to():
    arts = [art("f", 5, section="finance"), art("q1", 3, section="quebec"), art("q2", 4, section="quebec")]
    out = by_id(group_articles(arts, Client(reply((["f", "q1", "q2"], "T", "R"))), "m"))
    assert out["q2"].merged_into is None and out["f"].merged_into == "q2" and out["q2"].importance == 5
