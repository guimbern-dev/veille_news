"""Vérifie chaque source de config.yaml : nombre d'entrées et date la plus récente.

Usage : .venv/bin/python scripts/check_feeds.py [config.yaml]
À lancer après avoir ajouté ou modifié une source.
"""
import sys
from pathlib import Path

from veille.collect import http_fetch, parse_feed
from veille.config import load_config


def main() -> int:
    config = load_config(Path(sys.argv[1] if len(sys.argv) > 1 else "config.yaml"))
    broken = 0
    for section in config.sections:
        print(f"\n== {section.name}")
        for source in section.sources:
            try:
                articles = parse_feed(http_fetch(source.url), source, section.slug)
                dates = [a.published for a in articles if a.published]
                latest = max(dates).strftime("%Y-%m-%d %H:%M") if dates else "sans date"
                excerpts = sum(1 for a in articles if a.excerpt)
                status = "OK " if articles else "VIDE"
                broken += 0 if articles else 1
                print(f"  {status} {source.name:<40} {len(articles):>3} entrées, "
                      f"{excerpts:>3} avec extrait, plus récente : {latest}")
            except Exception as exc:
                broken += 1
                print(f"  ERR  {source.name:<40} {type(exc).__name__}: {str(exc)[:90]}")
    print(f"\n{broken} source(s) à corriger")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
