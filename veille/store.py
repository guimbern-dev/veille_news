"""Stockage : un fichier JSON par jour dans data/, versionné dans Git.

Ces fichiers sont la seule base de données du projet. Ils sont écrits de
façon stable (clés triées, indentation) pour que les différences Git restent
lisibles d'un jour à l'autre.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path

from .models import Article, DayData, DigestPoint, MarketQuote

_DAY_FILE = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")


def _encode(value: object) -> str:
    """Les dates sont écrites au format ISO 8601."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"Type non sérialisable : {type(value)!r}")


def _datetime(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


def _date(text: str | None) -> date | None:
    return date.fromisoformat(text) if text else None


def save_day(data: DayData, data_dir: Path) -> Path:
    """Écrit (ou remplace) le fichier de l'édition et retourne son chemin."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / f"{data.day.isoformat()}.json"
    text = json.dumps(asdict(data), default=_encode, ensure_ascii=False, indent=2, sort_keys=True)
    path.write_text(text + "\n", encoding="utf-8")
    return path


def load_day(day: date, data_dir: Path) -> DayData | None:
    """Relit une édition ; None si elle n'existe pas."""
    path = Path(data_dir) / f"{day.isoformat()}.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return DayData(
        day=date.fromisoformat(raw["day"]),
        generated_at=datetime.fromisoformat(raw["generated_at"]),
        markets=[MarketQuote(**{**m, "as_of": _date(m.get("as_of"))}) for m in raw["markets"]],
        articles=[Article(**{**a, "published": _datetime(a.get("published"))}) for a in raw["articles"]],
        digest=None if raw.get("digest") is None else [DigestPoint(**p) for p in raw["digest"]],
        failed_sources=list(raw.get("failed_sources", [])),
    )


def list_days(data_dir: Path) -> list[date]:
    """Jours disponibles, du plus récent au plus ancien."""
    data_dir = Path(data_dir)
    if not data_dir.is_dir():
        return []
    names = [p.name for p in data_dir.iterdir() if _DAY_FILE.match(p.name)]
    return sorted((date.fromisoformat(n[:-5]) for n in names), reverse=True)


def load_previous(day: date, data_dir: Path, days: int) -> list[Article]:
    """Articles des `days` jours qui précèdent `day`, celui-ci exclu."""
    articles: list[Article] = []
    for offset in range(1, days + 1):
        data = load_day(day - timedelta(days=offset), data_dir)
        if data:
            articles.extend(data.articles)
    return articles
