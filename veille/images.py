"""Image de partage d'un article, pour ceux dont le flux ne fournit pas d'image.

Les sites publient dans l'en-tête de leurs pages une image destinée aux aperçus de
liens (balise og:image). On la lit ici, avec des garde-fous stricts :

- seulement pour les articles sans image dans leur flux ;
- le fichier robots.txt de chaque site est respecté ; s'il est illisible, le site est laissé de côté ;
- seul l'en-tête de la page est lu, jamais le texte de l'article ;
- un refus du site (403, délai dépassé…) n'est ni contourné ni retenté ;
- chaque tentative est mémorisée : une page n'est demandée qu'une fois.

Comme pour les images des flux, seule l'adresse est gardée : l'image reste chez la source.
"""
from __future__ import annotations

import html
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Callable
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from .collect import USER_AGENT, _safe_image
from .models import Article

log = logging.getLogger(__name__)

PageFetch = Callable[[str], bytes]    # retourne le début d'une page ; lève une exception en cas de refus
RobotsFetch = Callable[[str], str]    # retourne le texte de robots.txt (« "" » s'il n'existe pas)

HEAD_LIMIT = 300_000   # octets lus au plus par page
MAX_PARALLEL = 4
_META = re.compile(rb"<meta\b[^>]*>", re.IGNORECASE)
_ATTR = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_HEAD_END = re.compile(rb"</head\s*>", re.IGNORECASE)
_KEYS = ("og:image", "og:image:secure_url", "og:image:url", "twitter:image", "twitter:image:src")


def http_page_head(url: str) -> bytes:
    """Lit une page jusqu'à la fin de son en-tête (ou HEAD_LIMIT octets), puis coupe la connexion."""
    with httpx.stream("GET", url, timeout=10, follow_redirects=True,
                      headers={"User-Agent": USER_AGENT, "Accept": "text/html"}) as response:
        response.raise_for_status()
        data = b""
        for chunk in response.iter_bytes():
            data += chunk
            if len(data) >= HEAD_LIMIT or _HEAD_END.search(data):
                break
        return data[:HEAD_LIMIT]


def http_robots(url: str) -> str:
    """Texte de robots.txt. Absent (404…) : chaîne vide. Accès refusé ou panne : exception."""
    response = httpx.get(url, timeout=10, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    if response.status_code in (401, 403) or response.status_code >= 500:
        response.raise_for_status()
    return response.text if response.status_code == 200 else ""


def share_image(page: bytes, page_url: str) -> str | None:
    """Adresse https de l'image de partage déclarée dans l'en-tête de la page, ou None."""
    end = _HEAD_END.search(page)
    head = page[: end.start()] if end else page
    found: dict[str, str] = {}
    for tag in _META.findall(head):
        attrs = {m[0].lower(): (m[1] or m[2]) for m in _ATTR.findall(tag.decode("utf-8", "replace"))}
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        if key in _KEYS and attrs.get("content") and key not in found:
            found[key] = attrs["content"]
    for key in _KEYS:
        if key in found:
            absolute = urljoin(page_url, html.unescape(found[key]).strip())
            if safe := _safe_image(absolute):
                return safe
    return None


def fill_images(articles: list[Article], page_fetch: PageFetch = http_page_head,
                robots_fetch: RobotsFetch = http_robots, limit: int = 150,
                budget_seconds: float = 120) -> list[Article]:
    """Cherche une image de partage pour les articles qui n'ont pas d'image ; conserve l'ordre.

    Au plus `limit` pages sont lues par exécution, pendant `budget_seconds` au plus.
    Un article tenté porte image_tried=True, qu'une image ait été trouvée ou non.
    """
    rules: dict[str, RobotFileParser | None] = {}

    def allowed(url: str) -> bool:
        parts = urlsplit(url)
        site = f"{parts.scheme}://{parts.netloc}"
        if site not in rules:
            try:
                parser = RobotFileParser()
                parser.parse(robots_fetch(f"{site}/robots.txt").splitlines())
                rules[site] = parser
            except Exception as exc:
                log.warning("Images : robots.txt illisible pour %s (%s), site laissé de côté", parts.netloc, type(exc).__name__)
                rules[site] = None
        return rules[site] is not None and rules[site].can_fetch(USER_AGENT, url)

    refused: set[str] = set()
    to_read: list[Article] = []
    for article in articles:
        if article.image or article.image_tried:
            continue
        if not allowed(article.url):
            refused.add(article.id)
        elif len(to_read) < limit:
            to_read.append(article)

    deadline = time.monotonic() + budget_seconds

    def read(article: Article) -> tuple[str, str | None]:
        if time.monotonic() >= deadline:
            return article.id, None
        try:
            return article.id, share_image(page_fetch(article.url), article.url)
        except Exception as exc:  # refus, délai dépassé, page illisible : on n'insiste pas
            log.info("Images : page non lue pour « %s » (%s)", article.title[:50], type(exc).__name__)
            return article.id, None

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        results = dict(pool.map(read, to_read))

    out = []
    for article in articles:
        if article.id in results:
            out.append(replace(article, image=results[article.id], image_tried=True))
        elif article.id in refused:
            out.append(replace(article, image_tried=True))
        else:
            out.append(article)
    log.info("Images : %d pages lues, %d images trouvées, %d articles écartés par robots.txt",
             len(results), sum(1 for v in results.values() if v), len(refused))
    return out
