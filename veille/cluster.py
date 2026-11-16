"""Regroupement des nouvelles qui portent sur un même sujet.

Un seul appel à Claude reçoit les articles déjà résumés et retourne les groupes,
chacun avec un titre et un résumé de synthèse en français. Dans un groupe, un article
« meneur » porte la carte affichée ; les autres pointent vers lui (merged_into) et
restent dans les données : leurs sources sont toutes citées et liées sous la carte.

À la relance, un groupe existant est présenté au modèle comme un seul article : une
nouvelle dépêche peut ainsi le rejoindre sans que le groupe change de meneur.
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import replace

from .models import Article
from .summarize import _ask

log = logging.getLogger(__name__)

GROUP_ATTEMPTS = 2

GROUP_SYSTEM = """\
Tu prépares un tableau de bord de veille personnel, lu chaque matin par un programmeur-analyste \
en infrastructure TI au Québec, qui suit aussi la finance et l'IA.

On te donne les articles du jour, déjà résumés, chacun avec son identifiant entre crochets. \
Plusieurs médias, en français ou en anglais, couvrent souvent le même sujet. Repère les articles \
qui portent sur un même sujet d'actualité : le même événement, la même affaire ou la même \
annonce, y compris ses suites et réactions directes.

Ne regroupe pas des articles qui partagent seulement un thème général (« l'IA », « l'économie », \
« la cybersécurité ») ou une même entreprise sans que les événements soient liés. Dans le doute, \
ne regroupe pas. Un article appartient à un seul groupe.

Pour chaque groupe d'au moins deux articles :
- « articles » : les identifiants des articles du groupe.
- « titre » : un titre en français, court et factuel, qui nomme le sujet commun.
- « resume » : 2 à 4 phrases en français qui font la synthèse de ce que rapportent les articles \
du groupe. N'affirme rien qui ne figure pas dans leurs résumés.

Les articles qui ne se regroupent avec aucun autre ne figurent dans aucun groupe. S'il n'y a \
aucun groupe à former, retourne une liste vide.

Les articles proviennent des flux RSS de médias reconnus, collectés aujourd'hui. Ils portent sur \
des événements postérieurs à tes connaissances : ne juge pas de leur vraisemblance, rapporte ce \
que disent les résumés."""

GROUP_SCHEMA = {
    "type": "object",
    "properties": {"groupes": {"type": "array", "items": {
        "type": "object",
        "properties": {"articles": {"type": "array", "items": {"type": "string"}},
                       "titre": {"type": "string"}, "resume": {"type": "string"}},
        "required": ["articles", "titre", "resume"],
        "additionalProperties": False,
    }}},
    "required": ["groupes"],
    "additionalProperties": False,
}


def _apply(articles: list[Article], groups: list[dict]) -> list[Article]:
    """Applique les groupes proposés : choisit un meneur, y rattache les autres articles."""
    by_id = {a.id: a for a in articles}
    members: dict[str, int] = {}   # meneur -> nombre d'articles déjà rattachés
    for a in articles:
        if a.merged_into:
            members[a.merged_into] = members.get(a.merged_into, 0) + 1

    taken: set[str] = set()
    for raw in groups:
        title, summary = str(raw.get("titre", "")).strip(), str(raw.get("resume", "")).strip()
        ids = []
        for i in raw.get("articles", []):   # identifiants inventés, déjà pris ou répétés : écartés
            if i in by_id and by_id[i].merged_into is None and i not in taken and i not in ids:
                ids.append(i)
        if len(ids) < 2 or not title or not summary:
            continue
        taken.update(ids)
        group = [by_id[i] for i in ids]
        # Meneur : d'abord un groupe déjà formé (le plus fourni) ; sinon un article de la rubrique
        # la plus représentée dans le groupe, pour que le sujet s'affiche au bon endroit ; puis le plus important.
        weight = Counter()
        for a in group:
            weight[a.section] += 1 + members.get(a.id, 0)
        lead = max(group, key=lambda a: (members.get(a.id, 0), weight[a.section], a.importance or 0))
        others = {a.id for a in group if a.id != lead.id}
        image = lead.image or next((a.image for a in group if a.image), None)
        by_id[lead.id] = replace(lead, group_title=title, group_summary=summary, image=image,
                                 importance=max(a.importance or 0 for a in group))
        for a in articles:
            if a.id in others or a.merged_into in others:
                by_id[a.id] = replace(by_id[a.id], merged_into=lead.id, group_title=None, group_summary=None)
        members[lead.id] = members.get(lead.id, 0) + sum(1 + members.get(i, 0) for i in others)
    return [by_id[a.id] for a in articles]


def group_articles(articles: list[Article], client, model: str, usage: dict | None = None) -> list[Article]:
    """Regroupe les articles d'un même sujet ; conserve l'ordre et tous les articles.

    Rien n'est demandé au modèle si aucun article n'est apparu depuis le dernier regroupement.
    En cas d'échec, les articles restent tels quels et seront reconsidérés à la prochaine exécution.
    """
    leads = [a for a in articles if a.merged_into is None and a.summary]
    if len(leads) < 2 or all(a.grouped for a in leads):
        return articles

    lines = ["Articles du jour :"]
    for a in leads:
        lines.append(f"\n[{a.id}] source={a.source} rubrique={a.section}\n"
                     f"Titre : {a.group_title or a.title}\nRésumé : {a.group_summary or a.summary}")
    for attempt in range(1, GROUP_ATTEMPTS + 1):
        try:
            data = _ask(client, model, GROUP_SYSTEM, "\n".join(lines), GROUP_SCHEMA,
                        effort="medium", max_tokens=16000, usage=usage)
            grouped = _apply(articles, list(data["groupes"]))
            lead_ids = {a.id for a in leads}
            done = [replace(a, grouped=True) if a.id in lead_ids else a for a in grouped]
            log.info("Regroupement : %d articles rattachés à un autre, %d groupes affichés",
                     sum(1 for a in done if a.merged_into), sum(1 for a in done if a.group_title))
            return done
        except Exception as exc:
            log.warning("Regroupement indisponible, essai %d sur %d (%s : %s)", attempt, GROUP_ATTEMPTS,
                        type(exc).__name__, str(exc)[:200])
    return articles
