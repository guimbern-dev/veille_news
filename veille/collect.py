"""Collecte : lit les flux RSS et Atom de la configuration et en tire des articles.

On ne télécharge jamais la page d'un article : seuls le titre, le lien, la
date et l'extrait fournis par le flux sont utilisés.
"""
from __future__ import annotations

import calendar
import hashlib
import html
import logging
import re
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Callable

import feedparser
import httpx

from .config import Config, Source
from .dedupe import normalize_url
from .models import Article

log = logging.getLogger(__name__)
# httpx journalise chaque URL au niveau INFO ; certaines contiennent une clé d'API.
logging.getLogger("httpx").setLevel(logging.WARNING)

# Fonction qui télécharge une URL ; injectable pour tester sans réseau.
Fetch = Callable[[str], bytes]

USER_AGENT = "veille-quotidienne/1.0"


class FeedError(ValueError):
    """Le contenu reçu n'est pas un flux exploitable."""


def http_fetch(url: str) -> bytes:
    """Télécharge une URL ; lève une exception en cas d'erreur réseau ou HTTP."""
    response = httpx.get(url, timeout=20, follow_redirects=True,
                         headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return response.content


def article_id(url: str) -> str:
    """Identifiant court et stable d'un article, dérivé de son URL normalisée."""
    return hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest()[:12]


class _TextExtractor(HTMLParser):
    """Garde le texte d'un fragment HTML, sans les balises ni les scripts."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0  # profondeur à l'intérieur de <script> ou <style>

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        else:
            self.parts.append(" ")  # une balise sépare deux mots

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        else:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def clean_text(raw: str, limit: int = 1500) -> str:
    """Réduit du HTML en texte brut : balises retirées, entités décodées, espaces réduits."""
    parser = _TextExtractor()
    parser.feed(raw or "")
    parser.close()
    # Un second décodage traite les entités doublement encodées, courantes dans les flux.
    text = html.unescape("".join(parser.parts)).replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    # Coupe sur une frontière de mot et signale la coupure.
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:")
    return cut + "…"


def _published(entry) -> datetime | None:
    """Date de publication en UTC, ou None si le flux n'en donne pas de lisible."""
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)  # avis interne de feedparser
            parsed = entry.get(key)
        if parsed:
            try:
                return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
            except (ValueError, OverflowError):
                continue
    return None


def _excerpt(entry) -> str:
    """Extrait fourni par le flux (résumé, sinon début du contenu)."""
    raw = entry.get("summary") or ""
    if not raw and entry.get("content"):
        raw = entry["content"][0].get("value", "")
    return clean_text(raw)


_IMG_TAG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_ATTR = re.compile(r'''([a-zA-Z-]+)\s*=\s*["']([^"']*)["']''')


def _safe_image(url: object) -> str | None:
    """Garde une adresse d'image seulement si elle est en https et de longueur raisonnable."""
    url = html.unescape(str(url or "")).strip()
    return url if url.startswith("https://") and len(url) <= 1000 and not re.search(r"\s", url) else None


def _image(entry) -> str | None:
    """Image d'illustration proposée par le flux lui-même, ou None.

    Ordre de recherche : vignette, média, pièce jointe, puis première image du texte.
    Seule l'adresse est gardée : l'image reste hébergée par la source et n'est jamais copiée.
    """
    candidates = [m.get("url") for m in entry.get("media_thumbnail") or []]
    for media in entry.get("media_content") or []:
        kind, url = str(media.get("type", "")), str(media.get("url", ""))
        if media.get("medium") == "image" or kind.startswith("image/") or (
                not kind and re.search(r"\.(jpe?g|png|webp|avif)(\?|$)", url, re.IGNORECASE)):
            candidates.append(url)
    candidates += [link.get("href") for link in entry.get("enclosures") or []
                   if str(link.get("type", "")).startswith("image/")]
    for url in candidates:
        if safe := _safe_image(url):
            return safe

    raw = (entry.get("summary") or "") + "".join(c.get("value", "") for c in entry.get("content") or [])
    for tag in _IMG_TAG.findall(raw):
        attrs = {name.lower(): value for name, value in _ATTR.findall(tag)}
        if attrs.get("width") in ("0", "1") or attrs.get("height") in ("0", "1"):
            continue  # pixel de suivi, pas une illustration
        if safe := _safe_image(attrs.get("src")):
            return safe
    return None


def parse_feed(content: bytes, source: Source, section_slug: str) -> list[Article]:
    """Transforme le contenu d'un flux en articles. Lève FeedError si ce n'est pas un flux."""
    parsed = feedparser.parse(content)
    if not parsed.entries and (parsed.bozo or not parsed.get("version")):
        raise FeedError(f"{source.name} : contenu illisible comme flux RSS ou Atom")
    articles = []
    for entry in parsed.entries:
        title = clean_text(entry.get("title", ""), limit=300)
        url = (entry.get("link") or "").strip()
        if not title or not url.startswith(("http://", "https://")):
            continue  # sans titre ou sans lien, on ne peut ni afficher ni citer la source
        articles.append(Article(id=article_id(url), title=title, url=url, source=source.name,
                                section=section_slug, published=_published(entry),
                                excerpt=_excerpt(entry), image=_image(entry)))
    return articles


def collect(config: Config, now: datetime, fetch: Fetch = http_fetch) -> tuple[list[Article], list[str]]:
    """Lit toutes les sources. Retourne les articles récents et les noms des sources en panne.

    Sont gardés : les articles publiés dans la fenêtre configurée, et ceux sans
    date (le dédoublonnage écarte ensuite ceux qui ont déjà été vus).
    """
    oldest = now - timedelta(hours=config.window_hours)
    latest = now + timedelta(hours=24)
    jobs = [(section.slug, source) for section in config.sections for source in section.sources]

    def read_source(job: tuple[str, Source]) -> list[Article] | None:
        slug, source = job
        try:
            return parse_feed(fetch(source.url), source, slug)
        except Exception as exc:  # une source en panne ne doit jamais arrêter les autres
            log.warning("Source en panne : %s (%s)", source.name, exc)
            return None

    # Téléchargements en parallèle ; map() conserve l'ordre de la configuration.
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(read_source, jobs))

    articles: list[Article] = []
    failed: list[str] = []
    for (_, source), found in zip(jobs, results):
        if found is None:
            failed.append(source.name)
            continue
        # Une date trop loin dans le futur signale un événement annoncé, pas une nouvelle.
        articles.extend(a for a in found
                        if a.published is None or oldest <= a.published <= latest)
    return articles, failed
