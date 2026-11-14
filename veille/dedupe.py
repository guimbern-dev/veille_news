"""Dédoublonnage : une même nouvelle reprise par plusieurs sources n'apparaît qu'une fois.

Deux articles sont le même s'ils ont la même URL normalisée ou des titres
quasi identiques. Limite connue : deux titres dans des langues différentes
ne sont pas reconnus comme doublons.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import replace
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import Article

# Paramètres de suivi publicitaire, sans effet sur le contenu de la page.
TRACKING_PARAMS = {"fbclid", "gclid", "ref", "cmp", "ocid"}
TITLE_THRESHOLD = 0.85   # similarité minimale pour que deux titres comptent comme identiques
MIN_WORDS_FUZZY = 5      # en dessous, seuls les titres strictement égaux comptent


def normalize_url(url: str) -> str:
    """Forme canonique d'une URL : sans suivi, sans fragment, hôte en minuscules."""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                   if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS)
    return urlunsplit((parts.scheme.lower(), host, parts.path.rstrip("/"), urlencode(query), ""))


def normalize_title(title: str) -> str:
    """Titre réduit à ses mots : minuscules, sans accents, ponctuation ni nom de marque final."""
    # « Titre | Source ». Un suffixe « - Source » n'est pas retiré ici : il peut faire partie
    # du titre (« Résultats du trimestre - Banque Nationale ») ; titles_match le tolère.
    title = title.split(" | ")[0]
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", ascii_title.lower()).strip()


def titles_match(a: str, b: str) -> bool:
    """Vrai si les deux titres désignent selon toute vraisemblance la même nouvelle."""
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    wa, wb = na.split(), nb.split()
    if min(len(wa), len(wb)) < MIN_WORDS_FUZZY:
        return False
    # Comparaison mot à mot. Des mots ajoutés ou retirés (un article, « - Le Devoir ») sont
    # tolérés ; un mot remplacé par un autre (« OpenAI » / « Google », « août » / « septembre »,
    # « M8g » / « C8g ») désigne une autre nouvelle, quelle que soit la longueur du titre.
    matcher = SequenceMatcher(None, wa, wb, autojunk=False)
    if any(tag == "replace" for tag, *_ in matcher.get_opcodes()):
        return False
    return matcher.ratio() >= TITLE_THRESHOLD


def dedupe(articles: list[Article], seen: list[Article], recent: list[Article],
           source_order: list[str]) -> list[Article]:
    """Retire les doublons et retourne de nouveaux objets Article, dans l'ordre d'entrée.

    seen   : articles des 30 derniers jours ; une URL déjà vue est écartée.
    recent : articles des 3 derniers jours ; un titre déjà publié par une *autre*
             source est écarté (une même source qui réutilise un titre à une
             nouvelle adresse, comme une chronique quotidienne, est gardée).
    source_order : noms des sources par priorité décroissante ; dans un groupe de
             doublons du jour, la première de la liste l'emporte et les autres
             sont citées dans `also_covered_by`.
    """
    seen_urls = {normalize_url(a.url) for a in seen}
    rank = {name: i for i, name in enumerate(source_order)}

    fresh = []
    for article in articles:
        if normalize_url(article.url) in seen_urls:
            continue
        if any(r.source != article.source and titles_match(article.title, r.title) for r in recent):
            continue
        fresh.append(article)

    # Regroupe les doublons du jour ; chaque groupe garde sa position de première apparition.
    groups: list[list[Article]] = []
    for article in fresh:
        key = normalize_url(article.url)
        for group in groups:
            # Deux articles d'une même source à des adresses différentes sont deux articles,
            # même si leurs titres se ressemblent (séries d'annonces, publications statistiques).
            if any(normalize_url(g.url) == key
                   or (g.source != article.source and titles_match(g.title, article.title))
                   for g in group):
                group.append(article)
                break
        else:
            groups.append([article])

    result = []
    for group in groups:
        ordered = sorted(group, key=lambda a: rank.get(a.source, len(rank)))  # tri stable
        winner, others = ordered[0], ordered[1:]
        also, names = [], {winner.source}
        for other in others:
            if other.source not in names:  # deux flux d'une même source ne se citent pas eux-mêmes
                names.add(other.source)
                also.append({"name": other.source, "url": other.url})
        result.append(replace(winner, also_covered_by=also))
    return result
