"""Génération du site statique à partir des fichiers data/*.json.

Produit dans le dossier de sortie : index.html (édition la plus récente),
jour/AAAA-MM-JJ.html (une page par édition), archive.html, recherche.html,
robots.txt, les fichiers statiques et l'index de recherche Pagefind.
Tous les liens sont relatifs, car le site vit sous
https://<compte>.github.io/<dépôt>/.
"""
from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import Config
from .models import Article, DayData, MarketQuote
from .store import list_days, load_day

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = ROOT / "templates"
STATIC_DIR = ROOT / "static"

NBSP = " "  # espace insécable : séparateur de milliers et espace avant « % »
WEEKDAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
          "septembre", "octobre", "novembre", "décembre"]
SHORT_MONTHS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juill.", "août",
                "sept.", "oct.", "nov.", "déc."]
EXCERPT_LIMIT = 300  # longueur de l'extrait affiché quand il n'y a pas de résumé


# --- Mise en forme française, sans dépendre de la locale du système -----------------

def french_date(day: date, weekday: bool = True) -> str:
    """« vendredi 13 novembre 2026 »."""
    number = "1er" if day.day == 1 else str(day.day)
    text = f"{number} {MONTHS[day.month - 1]} {day.year}"
    return f"{WEEKDAYS[day.weekday()]} {text}" if weekday else text


def french_number(value: float, decimals: int = 2) -> str:
    """« 25 104,32 » : virgule décimale, espace insécable pour les milliers."""
    return f"{value:,.{decimals}f}".replace(",", NBSP).replace(".", ",")


def relative_time(moment: datetime | None, now: datetime) -> str:
    """« il y a 5 h », « hier »… ; chaîne vide si la date est inconnue."""
    if moment is None:
        return ""
    minutes = int((now - moment).total_seconds() // 60)
    if minutes < 1:
        return "à l'instant"
    if minutes < 60:
        return f"il y a {minutes} min"
    if minutes < 24 * 60:
        return f"il y a {minutes // 60} h"
    if minutes < 48 * 60:
        return "hier"
    return f"il y a {minutes // (24 * 60)} j"


def format_quote(quote: MarketQuote) -> dict:
    """Prépare une cote pour l'affichage : valeur, variation et sens (hausse, baisse, stable)."""
    if quote.value is None:
        return {"name": quote.name, "value": "n. d.", "change": "", "direction": "absent"}
    if quote.kind == "taux":
        value = f"{french_number(quote.value)}{NBSP}%"
        if quote.value_low is not None:  # fourchette cible de la Fed
            value = f"{french_number(quote.value_low)}-{value}"
    else:
        value = french_number(quote.value, 4 if quote.kind == "devise" else 2)

    change, direction = "", "stable"
    if quote.change_pct is not None:
        decimals, unit = (2, "pt") if quote.kind == "taux" else (2 if quote.kind == "devise" else 1, "%")
        rounded = round(quote.change_pct, decimals)
        direction = "hausse" if rounded > 0 else "baisse" if rounded < 0 else "stable"
        if direction == "stable":
            change = "inchangé" if quote.kind == "taux" else f"={NBSP}{french_number(0, decimals)}{NBSP}{unit}"
        else:
            arrow = "▲" if rounded > 0 else "▼"
            change = f"{arrow}{NBSP}{french_number(abs(rounded), decimals)}{NBSP}{unit}"
    return {"name": quote.name, "value": value, "change": change, "direction": direction}


# --- Préparation d'une édition pour les gabarits ------------------------------------

def _article_view(article: Article, now: datetime) -> dict:
    excerpt = article.excerpt
    if len(excerpt) > EXCERPT_LIMIT:
        excerpt = excerpt[:EXCERPT_LIMIT].rsplit(" ", 1)[0].rstrip(" ,;:.") + "…"
    return {"id": article.id, "title": article.title, "url": article.url, "source": article.source,
            "summary": article.summary, "excerpt": excerpt, "importance": article.importance or 0,
            "when": relative_time(article.published, now), "also": article.also_covered_by,
            "image": article.image}


def day_view(data: DayData, config: Config) -> dict:
    """Transforme une édition en dictionnaire prêt à afficher."""
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    sections, shown_ids = [], set()
    for section in config.sections:
        articles = [a for a in data.articles if a.section == section.slug]
        # Les plus importants d'abord ; à importance égale, les plus récents.
        articles.sort(key=lambda a: (a.importance or 0, a.published or oldest), reverse=True)
        views = [_article_view(a, data.generated_at) for a in articles]
        shown_ids.update(a.id for a in articles)
        sections.append({"name": section.name, "slug": section.slug,
                         "visible": views[: section.max_articles],
                         "folded": views[section.max_articles:]})

    names = {s.slug: s.name for s in config.sections}
    images = {a.id: a.image for a in data.articles if a.image}
    digest = None
    if data.digest:
        digest = []
        for point in data.digest:
            ids = [i for i in point.article_ids if i in shown_ids]
            if ids:  # un point dont aucun article n'est affiché n'a plus de source à citer
                # Le point est illustré par son premier article qui a une image.
                illustrated = next((i for i in ids if i in images), None)
                digest.append({"text": point.text, "ids": ids, "section": names.get(point.section, ""),
                               "image": images.get(illustrated), "image_id": illustrated})
        digest = digest or None

    # La « clôture » affichée est celle des indices ; un taux peut porter une date plus
    # récente (série publiée 7 jours sur 7) sans qu'aucun marché n'ait rouvert.
    close = None
    for kind in ("indice", "devise", "taux"):
        dates = [q.as_of for q in data.markets if q.kind == kind and q.as_of]
        if dates:
            close = max(dates)
            break
    quotes = []
    for q in data.markets:
        view = format_quote(q)
        # Une cote plus ancienne que la clôture annoncée (jour férié d'une seule bourse) porte sa date.
        stale = q.kind != "taux" and q.as_of is not None and q.as_of != close
        view["date"] = f"{q.as_of.day} {SHORT_MONTHS[q.as_of.month - 1]}" if stale else ""
        quotes.append(view)
    return {
        "day": data.day, "title": french_date(data.day), "sections": sections, "digest": digest,
        "quotes": quotes,
        "close": french_date(close, weekday=False).rsplit(" ", 1)[0] if close else "",
        "count": len(shown_ids), "failed": len(data.failed_sources),
        "generated": data.generated_at,
    }


def _archive_view(days: list[dict]) -> list[dict]:
    """Regroupe les éditions par mois, du plus récent au plus ancien."""
    months: list[dict] = []
    for view in days:
        label = f"{MONTHS[view['day'].month - 1].capitalize()} {view['day'].year}"
        if not months or months[-1]["label"] != label:
            months.append({"label": label, "days": []})
        count = view["count"]
        months[-1]["days"].append({"href": f"jour/{view['day'].isoformat()}.html", "title": view["title"],
                                   "count": f"{count} article{'s' if count > 1 else ''}"})
    return months


# --- Écriture du site ----------------------------------------------------------------

def _run_pagefind(out_dir: Path) -> None:
    """Construit l'index de recherche ; une absence de Pagefind n'empêche pas la publication."""
    try:
        subprocess.run([sys.executable, "-m", "pagefind", "--site", str(out_dir)],
                       check=True, capture_output=True, text=True, timeout=300)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        log.warning("Index de recherche non construit : %s", detail.strip().splitlines()[-1] if detail.strip() else exc)


def build_site(data_dir: Path, out_dir: Path, config: Config, search: bool = True) -> None:
    """Génère tout le site dans out_dir à partir des éditions de data_dir."""
    data_dir, out_dir = Path(data_dir), Path(out_dir)
    days = list_days(data_dir)
    if not days:
        raise FileNotFoundError(f"Aucune édition dans {data_dir} : lancer d'abord la collecte")

    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=select_autoescape(["html"]),
                      trim_blocks=True, lstrip_blocks=True)
    # Empreinte des fichiers statiques : un changement de style n'est pas masqué par le cache du navigateur.
    env.globals["version"] = hashlib.sha1(b"".join(p.read_bytes() for p in sorted(STATIC_DIR.glob("*")) if p.is_file())).hexdigest()[:8]
    views = [day_view(load_day(day, data_dir), config) for day in days]  # du plus récent au plus ancien

    (out_dir / "jour").mkdir(parents=True, exist_ok=True)
    day_template = env.get_template("day.html")

    def render_day(view: dict, root: str, newer: dict | None, older: dict | None, indexed: bool) -> str:
        return day_template.render(root=root, view=view, newer=newer, older=older, indexed=indexed,
                                   page_title=f"Veille · {view['title']}")

    # Page d'accueil : l'édition la plus récente. Elle n'est pas indexée par la recherche,
    # car la même édition existe aussi sous jour/.
    (out_dir / "index.html").write_text(
        render_day(views[0], "", None, views[1] if len(views) > 1 else None, indexed=False), encoding="utf-8")

    wanted = set()
    for i, view in enumerate(views):
        name = f"{view['day'].isoformat()}.html"
        wanted.add(name)
        newer = views[i - 1] if i > 0 else None
        older = views[i + 1] if i + 1 < len(views) else None
        (out_dir / "jour" / name).write_text(render_day(view, "../", newer, older, indexed=True), encoding="utf-8")
    for stale in (out_dir / "jour").glob("*.html"):
        if stale.name not in wanted:
            stale.unlink()  # édition retirée de data/

    (out_dir / "archive.html").write_text(
        env.get_template("archive.html").render(root="", months=_archive_view(views), page_title="Veille · Archive"),
        encoding="utf-8")
    (out_dir / "recherche.html").write_text(
        env.get_template("search.html").render(root="", page_title="Veille · Recherche"), encoding="utf-8")
    # Site personnel : on demande aux moteurs de recherche de ne rien indexer.
    (out_dir / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    shutil.copytree(STATIC_DIR, out_dir / "static", dirs_exist_ok=True)

    if search:
        _run_pagefind(out_dir)
