"""Résumés par l'API Claude.

Deux usages :
- summarize_articles : un appel par article, qui retourne un résumé français
  de 2-3 phrases et une note d'importance de 1 à 5 ;
- daily_digest : un appel qui tire 5 à 7 points clés de tous les résumés du jour.

Claude ne reçoit que le titre et l'extrait fournis par le flux. Toute réponse
inattendue (refus, panne, sortie invalide) est absorbée ici : l'article garde
alors son extrait et la page se construit quand même.
"""
from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone

from .config import Section
from .models import Article, DigestPoint

log = logging.getLogger(__name__)
_usage_lock = threading.Lock()

MAX_PARALLEL = 5          # appels simultanés à l'API
DIGEST_MIN_IMPORTANCE = 3  # seuls les articles au moins « notables » nourrissent le résumé du jour
DIGEST_MIN_POINTS = 3      # en dessous, mieux vaut masquer la section

ARTICLE_SYSTEM = """\
Tu rédiges les résumés d'un tableau de bord de veille personnel, lu chaque matin \
par un programmeur-analyste en infrastructure TI au Québec, qui suit aussi la finance et l'IA.

On te donne le titre et l'extrait d'un article, tels que fournis par le flux RSS de la source. \
La source peut être en français ou en anglais.

Produis :
- « resume » : 2 à 3 phrases en français, claires et factuelles. Appuie-toi uniquement sur le \
titre et l'extrait fournis : n'ajoute aucun fait, chiffre, nom ou contexte qui n'y figure pas, \
même si tu crois le connaître. Si l'extrait est mince, fais plus court plutôt que de combler. \
N'écris pas « l'article dit que » : énonce directement l'information.
- « importance » : un entier de 1 à 5 pour ce lecteur. 1 = anecdotique ou promotionnel ; \
2 = mineur ; 3 = notable ; 4 = important ; 5 = majeur (décision de banque centrale, sortie d'un \
modèle phare, faille critique activement exploitée, événement politique de premier plan)."""

ARTICLE_SCHEMA = {
    "type": "object",
    "properties": {"resume": {"type": "string"}, "importance": {"type": "integer"}},
    "required": ["resume", "importance"],
    "additionalProperties": False,
}

DIGEST_SYSTEM = """\
Tu rédiges le « Résumé du jour » d'un tableau de bord de veille personnel, lu chaque matin par \
un programmeur-analyste en infrastructure TI au Québec, qui suit aussi la finance et l'IA.

On te donne les articles du jour, déjà résumés, avec leur identifiant, leur rubrique et leur \
note d'importance. Dégage les 5 à 7 points les plus importants de la journée, toutes rubriques \
confondues, en regroupant dans un même point les articles qui traitent du même sujet.

Pour chaque point :
- « texte » : une ou deux phrases en français, qui disent ce qui s'est passé et pourquoi cela \
compte. N'affirme rien qui ne figure pas dans les résumés fournis.
- « rubrique » : l'identifiant de la rubrique principale du point, choisi dans la liste fournie.
- « articles » : les identifiants des articles sur lesquels le point s'appuie (au moins un).

Classe les points du plus important au moins important."""

DIGEST_SCHEMA = {
    "type": "object",
    "properties": {"points": {"type": "array", "items": {
        "type": "object",
        "properties": {"texte": {"type": "string"}, "rubrique": {"type": "string"},
                       "articles": {"type": "array", "items": {"type": "string"}}},
        "required": ["texte", "rubrique", "articles"],
        "additionalProperties": False,
    }}},
    "required": ["points"],
    "additionalProperties": False,
}


def _ask(client, model: str, system: str, user: str, schema: dict, effort: str,
         max_tokens: int, usage: dict | None) -> dict:
    """Envoie une requête à sortie structurée et retourne le JSON décodé.

    Lève une exception pour tout ce qui n'est pas une réponse complète et valide.
    """
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
    )
    if usage is not None:
        with _usage_lock:  # les appels partent en parallèle
            for key in ("input_tokens", "output_tokens"):
                usage[key] = usage.get(key, 0) + (getattr(response.usage, key, 0) or 0)
    # Toujours vérifier la raison d'arrêt avant de lire le contenu (refus, coupure…).
    if response.stop_reason != "end_turn":
        raise ValueError(f"réponse incomplète (stop_reason={response.stop_reason})")
    text = next(block.text for block in response.content if block.type == "text")
    return json.loads(text)


def _summarize_one(article: Article, client, model: str, usage: dict | None) -> Article:
    user = f"Source : {article.source}\nTitre : {article.title}\nExtrait : {article.excerpt or '(aucun extrait)'}"
    try:
        data = _ask(client, model, ARTICLE_SYSTEM, user, ARTICLE_SCHEMA,
                    effort="low", max_tokens=4000, usage=usage)
        summary = str(data["resume"]).strip()
        if not summary:
            raise ValueError("résumé vide")
        importance = max(1, min(5, int(data["importance"])))
        return replace(article, summary=summary, importance=importance)
    except Exception as exc:
        # L'article reste affiché avec son extrait, marqué comme tel.
        log.warning("Résumé impossible pour « %s » (%s)", article.title[:60], type(exc).__name__)
        return replace(article, summary=None, importance=2)


def summarize_articles(articles: list[Article], client, model: str, cap_per_section: int,
                       usage: dict | None = None) -> list[Article]:
    """Résume les articles qui ne le sont pas encore ; conserve l'ordre d'entrée.

    Au plus `cap_per_section` articles sont envoyés par rubrique (les plus récents
    d'abord) ; les autres gardent leur extrait, avec l'importance minimale.
    `usage`, s'il est fourni, cumule les jetons consommés.
    """
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    pending: dict[str, list[Article]] = {}
    for article in articles:
        if article.summary is None:
            pending.setdefault(article.section, []).append(article)

    to_send: list[Article] = []
    skipped: set[str] = set()
    for group in pending.values():
        group.sort(key=lambda a: a.published or oldest, reverse=True)
        to_send.extend(group[:cap_per_section])
        skipped.update(a.id for a in group[cap_per_section:])

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        done = pool.map(lambda a: _summarize_one(a, client, model, usage), to_send)
        results = {a.id: a for a in done}

    out = []
    for article in articles:
        if article.id in results:
            out.append(results[article.id])
        elif article.id in skipped:
            out.append(replace(article, summary=None, importance=1))
        else:
            out.append(article)
    return out


def daily_digest(articles: list[Article], sections: tuple[Section, ...], client, model: str,
                 usage: dict | None = None) -> list[DigestPoint] | None:
    """Les 5 à 7 points clés du jour, ou None si on ne peut pas en produire de fiables."""
    names = {s.slug: s.name for s in sections}
    candidates = [a for a in articles
                  if a.summary and (a.importance or 0) >= DIGEST_MIN_IMPORTANCE and a.section in names]
    if len(candidates) < DIGEST_MIN_POINTS:
        return None

    by_id = {a.id: a for a in candidates}
    lines = ["Rubriques (identifiant : nom) :"]
    lines += [f"- {slug} : {name}" for slug, name in names.items()]
    lines.append("\nArticles du jour :")
    for a in candidates:
        lines.append(f"\n[{a.id}] rubrique={a.section} importance={a.importance} source={a.source}\n"
                     f"Titre : {a.title}\nRésumé : {a.summary}")
    try:
        data = _ask(client, model, DIGEST_SYSTEM, "\n".join(lines), DIGEST_SCHEMA,
                    effort="medium", max_tokens=16000, usage=usage)
        points = []
        for raw in data["points"]:
            text = str(raw["texte"]).strip()
            ids = [i for i in raw["articles"] if i in by_id]   # identifiants inventés écartés
            if not text or not ids:
                continue
            section = raw["rubrique"] if raw["rubrique"] in names else by_id[ids[0]].section
            points.append(DigestPoint(text=text, section=section, article_ids=ids))
        if len(points) < DIGEST_MIN_POINTS:
            raise ValueError(f"seulement {len(points)} point(s) valide(s)")
        return points[:7]
    except Exception as exc:
        log.warning("Résumé du jour indisponible (%s)", type(exc).__name__)
        return None
