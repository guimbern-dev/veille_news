"""Tests de l'image de partage lue sur la page d'un article (aucun accès réseau)."""
from datetime import datetime, timezone

from veille.images import fill_images, share_image
from veille.models import Article

NOW = datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc)


def art(ident: str, url: str = "", image: str | None = None, tried: bool = False) -> Article:
    return Article(id=ident, title=f"Titre {ident}", url=url or f"https://site.test/{ident}", source="S", section="ia",
                   published=NOW, excerpt="", image=image, image_tried=tried)


def page(meta: str) -> bytes:
    return f"<html><head><title>T</title>{meta}</head><body>corps</body></html>".encode()


OG = page('<meta property="og:image" content="https://img.test/partage.jpg">')
NO_RULES = lambda url: ""           # robots.txt absent : tout est permis


class Web:
    """Faux site : mémorise les pages demandées."""

    def __init__(self, pages: dict[str, bytes | Exception] | None = None, default: bytes = OG):
        self.pages, self.default, self.asked = pages or {}, default, []

    def __call__(self, url: str) -> bytes:
        self.asked.append(url)
        reply = self.pages.get(url, self.default)
        if isinstance(reply, Exception):
            raise reply
        return reply


# --- Lecture de la balise ---

def test_share_image_reads_og_image_in_either_attribute_order():
    assert share_image(OG, "https://site.test/a") == "https://img.test/partage.jpg"
    swapped = page("<meta content='https://img.test/b.png' property='og:image'/>")
    assert share_image(swapped, "https://site.test/a") == "https://img.test/b.png"


def test_share_image_falls_back_to_twitter_image():
    assert share_image(page('<meta name="twitter:image" content="https://img.test/t.jpg">'), "https://site.test/a") == "https://img.test/t.jpg"


def test_share_image_resolves_relative_and_decodes_entities():
    html = page('<meta property="og:image" content="/media/x.jpg?w=1&amp;h=2">')
    assert share_image(html, "https://site.test/section/a") == "https://site.test/media/x.jpg?w=1&h=2"


def test_share_image_rejects_missing_or_unsafe_values():
    assert share_image(page(""), "https://site.test/a") is None
    assert share_image(page('<meta property="og:image" content="http://img.test/a.jpg">'), "https://site.test/a") is None
    assert share_image(page('<meta property="og:image" content="javascript:alert(1)">'), "https://site.test/a") is None


def test_share_image_ignores_the_body():
    body_only = b'<html><head></head><body><meta property="og:image" content="https://img.test/corps.jpg"></body></html>'
    assert share_image(body_only, "https://site.test/a") is None


# --- Garde-fous ---

def test_only_articles_without_image_are_fetched():
    web = Web()
    out = fill_images([art("a", image="https://img.test/flux.jpg"), art("b")], web, NO_RULES)
    assert web.asked == ["https://site.test/b"]
    assert [a.image for a in out] == ["https://img.test/flux.jpg", "https://img.test/partage.jpg"]


def test_robots_txt_disallow_is_respected():
    web = Web()
    robots = lambda url: "User-agent: *\nDisallow: /prive/\n"
    out = fill_images([art("a", "https://site.test/prive/a"), art("b", "https://site.test/public/b")], web, robots)
    assert web.asked == ["https://site.test/public/b"]
    assert [a.image for a in out] == [None, "https://img.test/partage.jpg"]


def test_unreadable_robots_txt_means_no_fetch_for_that_site():
    def robots(url):
        if "ferme.test" in url:
            raise ConnectionError("x")
        return ""
    web = Web()
    out = fill_images([art("a", "https://ferme.test/a"), art("b")], web, robots)
    assert web.asked == ["https://site.test/b"] and out[0].image is None


def test_robots_txt_is_read_once_per_site():
    asked = []
    fill_images([art("a"), art("b"), art("c", "https://autre.test/c")], Web(), lambda url: asked.append(url) or "")
    assert sorted(asked) == ["https://autre.test/robots.txt", "https://site.test/robots.txt"]


def test_refused_page_is_not_retried_and_does_not_block_the_others():
    web = Web({"https://site.test/a": PermissionError("403")})
    out = fill_images([art("a"), art("b")], web, NO_RULES)
    assert web.asked.count("https://site.test/a") == 1
    assert [a.image for a in out] == [None, "https://img.test/partage.jpg"]


def test_every_attempt_is_remembered_so_a_rerun_asks_nothing():
    web = Web({"https://site.test/a": PermissionError("403")})
    first = fill_images([art("a"), art("b", "https://site.test/prive/b")], web, lambda url: "User-agent: *\nDisallow: /prive/\n")
    assert all(a.image_tried for a in first)
    again = Web()
    fill_images(first, again, NO_RULES)
    assert again.asked == []


def test_limit_caps_the_number_of_pages_read():
    web = Web()
    out = fill_images([art(f"n{i}") for i in range(5)], web, NO_RULES, limit=2)
    assert len(web.asked) == 2 and sum(1 for a in out if a.image) == 2
    assert sum(1 for a in out if a.image_tried) == 2      # les autres seront tentés à la prochaine exécution


def test_order_and_other_fields_are_kept():
    arts = [art("a"), art("b", image="https://img.test/flux.jpg"), art("c")]
    out = fill_images(arts, Web(), NO_RULES)
    assert [a.id for a in out] == ["a", "b", "c"] and out[0].title == "Titre a"
