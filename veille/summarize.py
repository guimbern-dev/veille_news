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
import time
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
DIGEST_ATTEMPTS = 2        # une réponse inutilisable est retentée une fois

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

Classe les points du plus important au moins important.

Les articles proviennent des flux RSS de médias reconnus, collectés aujourd'hui. Ils portent sur \
des événements postérieurs à tes connaissances : ne juge pas de leur vraisemblance, rapporte ce \
que disent les résumés. Réponds toujours par de vrais points, jamais par des valeurs de remplissage."""

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


def _summarize_one(article: Article, client, model: str, usage: dict | None,
                   deadline: float | None = None) -> Article:
    if deadline is not None and time.monotonic() >= deadline:
        # Budget de temps épuisé (API lente) : on n'appelle plus, l'extrait sera affiché.
        return replace(article, summary=None, importance=2)
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
        # Le message est journalisé : c'est lui qui révèle une clé invalide ou un crédit épuisé.
        log.warning("Résumé impossible pour « %s » (%s : %s)", article.title[:60],
                    type(exc).__name__, str(exc)[:200])
        return replace(article, summary=None, importance=2)


def summarize_articles(articles: list[Article], client, model: str, cap_per_section: int,
                       usage: dict | None = None, budget_seconds: float | None = None) -> list[Article]:
    """Résume les articles qui ne le sont pas encore ; conserve l'ordre d'entrée.

    Au plus `cap_per_section` articles sont envoyés par rubrique. Le plafond est réparti
    entre les sources, tour à tour (dans chaque source, les plus récents d'abord), pour
    qu'une source très active la nuit ne prive pas de résumé les nouvelles de la journée.
    Les articles au-delà du plafond gardent leur extrait, avec l'importance minimale.
    `usage`, s'il est fourni, cumule les jetons consommés.
    `budget_seconds` borne la durée totale : passé ce délai, plus aucun appel n'est fait.
    """
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    pending: dict[str, list[Article]] = {}
    for article in articles:
        if article.summary is None:
            pending.setdefault(article.section, []).append(article)

    to_send: list[Article] = []
    skipped: set[str] = set()
    for group in pending.values():
        by_source: dict[str, list[Article]] = {}
        for article in group:
            by_source.setdefault(article.source, []).append(article)
        queues = [sorted(q, key=lambda a: a.published or oldest, reverse=True) for q in by_source.values()]
        chosen: list[Article] = []
        turn = 0
        while len(chosen) < cap_per_section and any(len(q) > turn for q in queues):
            for queue in queues:  # un article par source à chaque tour
                if len(queue) > turn and len(chosen) < cap_per_section:
                    chosen.append(queue[turn])
            turn += 1
        chosen_ids = {a.id for a in chosen}
        to_send.extend(chosen)
        skipped.update(a.id for a in group if a.id not in chosen_ids)

    deadline = None if budget_seconds is None else time.monotonic() + budget_seconds
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        done = pool.map(lambda a: _summarize_one(a, client, model, usage, deadline), to_send)
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
    for attempt in range(1, DIGEST_ATTEMPTS + 1):
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
            log.warning("Résumé du jour indisponible, essai %d sur %d (%s : %s)", attempt, DIGEST_ATTEMPTS,
                        type(exc).__name__, str(exc)[:200])
    return None
