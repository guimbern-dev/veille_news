"""Commande `python -m veille` : enchaîne les étapes du pipeline.

    python -m veille run       collecte, résumés, stockage et génération du site
    python -m veille collect   tout sauf la génération du site
    python -m veille build     génération du site à partir des données existantes
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .build import build_site
from .collect import Fetch, collect, http_fetch
from .config import Config, ConfigError, load_config
from .dedupe import dedupe
from .markets import fetch_quotes
from .models import DayData
from .store import load_day, load_previous, save_day
from .summarize import daily_digest, summarize_articles

log = logging.getLogger("veille")

SEEN_DAYS = 30    # une URL déjà publiée dans cette période n'est pas republiée
RECENT_DAYS = 3   # période de comparaison des titres entre sources
SUMMARY_BUDGET = 600   # secondes accordées aux résumés ; ensuite, les extraits sont affichés
API_TIMEOUT = 60       # secondes par appel à l'API Claude


def load_env(path: Path) -> None:
    """Charge un fichier .env (CLÉ=valeur) sans écraser les variables déjà définies."""
    path = Path(path)
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip("\"'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def run_pipeline(config: Config, data_dir: Path, out_dir: Path, now: datetime, fetch: Fetch,
                 client, keys: dict[str, str | None], build: bool = True, search: bool = True) -> DayData:
    """Exécute le pipeline pour l'édition du jour et retourne ce qui a été enregistré.

    client : client de l'API Claude, ou None pour sauter les résumés.
    keys   : clés des fournisseurs de données de marché.
    Lève SystemExit(1) si aucune nouvelle n'a pu être collectée : rien n'est alors
    écrit, et le site déjà en ligne reste tel quel.
    """
    day = now.astimezone(ZoneInfo(config.timezone)).date()
    log.info("Édition du %s", day.isoformat())

    collected, failed = collect(config, now, fetch)
    sources = sum(len(s.sources) for s in config.sections)
    log.info("Collecte : %d sources lues, %d en panne, %d articles dans la fenêtre",
             sources - len(failed), len(failed), len(collected))

    fresh = dedupe(collected,
                   seen=load_previous(day, data_dir, SEEN_DAYS),
                   recent=load_previous(day, data_dir, RECENT_DAYS),
                   source_order=config.source_order())

    # Relance le même jour : on repart de l'édition déjà enregistrée, dont les articles
    # gardent leur résumé, et on n'y ajoute que ce qui est nouveau.
    existing = load_day(day, data_dir)
    articles = list(existing.articles) if existing else []
    known = {a.id for a in articles}
    articles.extend(a for a in fresh if a.id not in known)
    log.info("Dédoublonnage : %d articles retenus (%d nouveaux)", len(articles), len(articles) - len(known))

    if not articles:
        log.error("Aucune nouvelle collectée : rien n'est écrit, le site en ligne reste inchangé")
        raise SystemExit(1)

    digest = None
    if client is None:
        log.warning("Résumés sautés : pas de client pour l'API Claude (clé absente ou --sans-resumes)")
    else:
        usage: dict[str, int] = {}
        waiting = {a.id for a in articles if a.summary is None}
        articles = summarize_articles(articles, client, config.model_articles, config.summary_cap,
                                      usage=usage, budget_seconds=SUMMARY_BUDGET)
        if waiting and not any(a.summary for a in articles if a.id in waiting):
            # Panne totale (clé invalide, crédit épuisé, API hors service) : la page sort quand
            # même avec les extraits, mais GitHub Actions affiche un avertissement sur l'exécution.
            log.error("Aucun des %d articles en attente n'a pu être résumé : voir les avertissements ci-dessus", len(waiting))
            print("::warning title=Veille quotidienne::Aucun résumé produit par l'API Claude ; "
                  "la page affiche les extraits des sources. Voir le journal de l'exécution.", flush=True)
        digest = daily_digest(articles, config.sections, client, config.model_digest, usage=usage)
        log.info("Résumés : %d articles résumés, %d sans résumé, résumé du jour %s ; %d jetons en entrée, %d en sortie",
                 sum(1 for a in articles if a.summary), sum(1 for a in articles if not a.summary),
                 "produit" if digest else "absent", usage.get("input_tokens", 0), usage.get("output_tokens", 0))

    markets = fetch_quotes(config.markets, fetch, keys)
    log.info("Marchés : %d valeurs sur %d", sum(1 for q in markets if q.value is not None), len(markets))

    data = DayData(day=day, generated_at=now, markets=markets, articles=articles, digest=digest,
                   failed_sources=failed)
    log.info("Enregistré : %s", save_day(data, data_dir))
    if build:
        build_site(data_dir, out_dir, config, search=search)
        log.info("Site généré dans %s", out_dir)
    return data


def _make_client():
    """Client de l'API Claude si une clé est disponible, sinon None."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    import anthropic  # importé ici pour que `build` fonctionne sans le SDK
    # Délai court et une seule relance : une API lente ne doit pas bloquer l'édition.
    return anthropic.Anthropic(timeout=API_TIMEOUT, max_retries=1)  # clé lue dans ANTHROPIC_API_KEY


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="veille", description="Tableau de bord de veille quotidienne")
    parser.add_argument("command", choices=["run", "collect", "build"],
                        help="run : tout ; collect : sans générer le site ; build : générer le site seulement")
    parser.add_argument("--config", default="config.yaml", help="fichier de configuration")
    parser.add_argument("--data", default="data", help="dossier des éditions (JSON)")
    parser.add_argument("--out", default="site", help="dossier du site généré")
    parser.add_argument("--sans-resumes", action="store_true", help="ne pas appeler l'API Claude")
    parser.add_argument("--sans-recherche", action="store_true", help="ne pas construire l'index de recherche")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    load_env(Path(".env"))
    try:
        config = load_config(Path(args.config))
    except ConfigError as exc:
        print(f"Configuration invalide : {exc}", file=sys.stderr)
        return 2

    search = not args.sans_recherche
    if args.command == "build":
        build_site(Path(args.data), Path(args.out), config, search=search)
        log.info("Site généré dans %s", args.out)
        return 0

    keys = {"fred": os.environ.get("FRED_API_KEY"), "twelvedata": os.environ.get("TWELVEDATA_API_KEY")}
    client = None if args.sans_resumes else _make_client()
    run_pipeline(config, Path(args.data), Path(args.out), datetime.now(timezone.utc), http_fetch,
                 client, keys, build=args.command == "run", search=search)
    return 0
