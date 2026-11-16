"""Structures de données partagées par toutes les étapes du pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class Article:
    """Une nouvelle tirée d'un flux, avant ou après son résumé."""
    id: str                      # 12 premiers caractères du SHA-1 de l'URL normalisée
    title: str
    url: str
    source: str                  # nom de la source, tel qu'écrit dans config.yaml
    section: str                 # slug de la rubrique
    published: datetime | None   # UTC ; None si le flux ne donne pas de date lisible
    excerpt: str                 # extrait du flux en texte brut, jamais l'article complet
    summary: str | None = None   # résumé français produit par Claude
    importance: int | None = None  # 1 (anecdotique) à 5 (majeure)
    also_covered_by: list[dict] = field(default_factory=list)  # [{"name": …, "url": …}]
    image: str | None = None     # adresse https de l'image (flux ou page de l'article) ; jamais copiée
    image_tried: bool = False    # l'image de partage de la page a déjà été cherchée
    merged_into: str | None = None    # identifiant du meneur quand l'article est regroupé sous un autre
    group_title: str | None = None    # titre de synthèse (meneur d'un groupe seulement)
    group_summary: str | None = None  # résumé de synthèse (meneur d'un groupe seulement)
    grouped: bool = False             # déjà soumis au regroupement par sujet


@dataclass
class MarketQuote:
    """Dernière valeur connue d'un indice, d'un taux ou d'une devise."""
    name: str
    kind: str                    # indice, taux ou devise
    value: float | None          # None : donnée indisponible, affichée « n. d. »
    value_low: float | None      # borne basse quand le taux est une fourchette (Fed)
    change_pct: float | None     # variation en % (indice, devise) ou en points (taux)
    as_of: date | None           # date de l'observation
    row: int = 1                 # ligne du bandeau (1 ou 2)


@dataclass
class DigestPoint:
    """Un point du « Résumé du jour »."""
    text: str
    section: str                 # slug de la rubrique
    article_ids: list[str]


@dataclass
class DayData:
    """Tout le contenu d'une édition quotidienne."""
    day: date
    generated_at: datetime
    markets: list[MarketQuote]
    articles: list[Article]
    digest: list[DigestPoint] | None
    failed_sources: list[str]
