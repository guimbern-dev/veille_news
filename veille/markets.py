"""Bandeau de marché : dernière clôture et variation pour chaque symbole configuré.

Fournisseurs (API officielles et gratuites) :
- valet      : Banque du Canada, sans clé ;
- fred       : Réserve fédérale de Saint-Louis, clé gratuite FRED_API_KEY ;
- twelvedata : Twelve Data, clé gratuite TWELVEDATA_API_KEY (utilisé pour le FNB d'actions canadiennes EWC).

Un symbole indisponible donne une cote vide, affichée « n. d. » : il ne bloque
jamais la page.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from urllib.parse import quote

from .collect import Fetch
from .config import MarketSymbol
from .models import MarketQuote

log = logging.getLogger(__name__)

Observation = tuple[date, float | None]


def last_two(observations: list[Observation]) -> tuple[tuple[date, float], tuple[date, float] | None] | None:
    """Les deux dernières observations valides (la plus récente d'abord).

    Les valeurs manquantes (fins de semaine, jours fériés) sont sautées.
    Retourne None s'il n'y a aucune valeur, et (dernière, None) s'il n'y en a qu'une.
    """
    valid = sorted(((d, v) for d, v in observations if v is not None), reverse=True)
    if not valid:
        return None
    return valid[0], (valid[1] if len(valid) > 1 else None)


def _number(text: object) -> float | None:
    """Convertit une valeur d'API en nombre ; « . » et le vide signifient « manquant »."""
    try:
        return float(str(text))
    except ValueError:
        return None


def _valet(symbol: str, fetch: Fetch, key: str | None) -> list[Observation]:
    url = f"https://www.banqueducanada.ca/valet/observations/{quote(symbol)}/json?recent=10"
    rows = json.loads(fetch(url))["observations"]
    return [(date.fromisoformat(r["d"]), _number(r[symbol]["v"])) for r in rows if symbol in r]


def _fred(symbol: str, fetch: Fetch, key: str | None) -> list[Observation]:
    url = ("https://api.stlouisfed.org/fred/series/observations"
           f"?series_id={quote(symbol)}&api_key={quote(key or '')}&file_type=json&sort_order=desc&limit=10")
    rows = json.loads(fetch(url))["observations"]
    return [(date.fromisoformat(r["date"]), _number(r["value"])) for r in rows]


def _twelvedata(symbol: str, fetch: Fetch, key: str | None) -> list[Observation]:
    url = ("https://api.twelvedata.com/time_series"
           f"?symbol={quote(symbol)}&interval=1day&outputsize=10&apikey={quote(key or '')}")
    payload = json.loads(fetch(url))
    if payload.get("status") != "ok":
        raise ValueError(f"réponse d'erreur (code {payload.get('code')})")
    return [(date.fromisoformat(r["datetime"][:10]), _number(r["close"])) for r in payload["values"]]


# fournisseur -> (fonction de lecture, une clé est-elle obligatoire ?)
_PROVIDERS = {"valet": (_valet, False), "fred": (_fred, True), "twelvedata": (_twelvedata, True)}


def _quote(market: MarketSymbol, fetch: Fetch, keys: dict[str, str | None]) -> MarketQuote:
    empty = MarketQuote(name=market.name, kind=market.kind, value=None, value_low=None,
                        change_pct=None, as_of=None)
    if market.provider not in _PROVIDERS:
        log.warning("Marché « %s » : fournisseur « %s » non pris en charge", market.name, market.provider)
        return empty
    read, needs_key = _PROVIDERS[market.provider]
    key = keys.get(market.provider)
    if needs_key and not key:
        log.warning("Marché « %s » : clé d'API absente pour %s", market.name, market.provider)
        return empty
    try:
        pair = last_two(read(market.symbol, fetch, key))
        if pair is None:
            return empty
        (as_of, value), previous = pair
        change = None
        if previous is not None:
            # Un taux varie en points de pourcentage ; un indice ou une devise, en pourcentage.
            change = value - previous[1] if market.kind == "taux" else (value / previous[1] - 1) * 100
        low = None
        if market.symbol_low:
            low_pair = last_two(read(market.symbol_low, fetch, key))
            low = low_pair[0][1] if low_pair else None
        return MarketQuote(name=market.name, kind=market.kind, value=value, value_low=low,
                           change_pct=round(change, 4) if change is not None else None, as_of=as_of)
    except Exception as exc:
        # On ne journalise que le type d'erreur : son message peut contenir l'URL, donc la clé.
        log.warning("Marché « %s » indisponible (%s)", market.name, type(exc).__name__)
        return empty


def fetch_quotes(markets: tuple[MarketSymbol, ...], fetch: Fetch,
                 keys: dict[str, str | None]) -> list[MarketQuote]:
    """Une cote par symbole configuré, dans l'ordre de la configuration.

    keys : clés d'API par fournisseur, par exemple {"fred": "…", "twelvedata": "…"}.
    """
    return [_quote(market, fetch, keys) for market in markets]
