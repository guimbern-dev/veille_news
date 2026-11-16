"""Tests de la lecture et de la validation de config.yaml."""
from pathlib import Path

import pytest

from veille.config import ConfigError, load_config

MODELES = """
modeles:
  articles: claude-haiku-5-5
  resume_du_jour: claude-sonnet-5-5
"""

VALID = MODELES + """
general:
  fuseau: America/Toronto
  fenetre_heures: 24
  plafond_resumes: 20
marches:
  - {nom: "S&P 500", fournisseur: fred, symbole: SP500, type: indice}
  - {nom: "Fed", fournisseur: fred, symbole: DFEDTARU, type: taux, symbole_bas: DFEDTARL}
rubriques:
  - nom: IA
    max_articles: 5
    sources:
      - {nom: Source A, url: "https://a.test/rss"}
      - {nom: Source B, url: "https://b.test/rss"}
  - nom: Finance et marchés
    sources:
      - {nom: Source C, url: "https://c.test/rss"}
"""

MINIMAL = MODELES + """
rubriques:
  - nom: IA
    sources:
      - {nom: Source A, url: "https://a.test/rss"}
"""

NO_SECTIONS = MODELES + "rubriques: []\n"
SECTION_WITHOUT_SOURCES = MODELES + "rubriques:\n  - nom: IA\n    sources: []\n"
SOURCE_WITHOUT_URL = MODELES + "rubriques:\n  - nom: IA\n    sources:\n      - {nom: Source A}\n"
UNKNOWN_PROVIDER = MINIMAL + "marches:\n  - {nom: X, fournisseur: yahoo, symbole: X, type: indice}\n"
DUPLICATE_SECTION_NAME = MODELES + """
rubriques:
  - nom: IA
    sources: [{nom: A, url: "https://a.test/rss"}]
  - nom: IA
    sources: [{nom: B, url: "https://b.test/rss"}]
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_sections_in_file_order(tmp_path):
    cfg = load_config(write(tmp_path, VALID))
    assert [s.name for s in cfg.sections] == ["IA", "Finance et marchés"]
    assert cfg.sections[1].slug == "finance-et-marches"


def test_loads_every_field(tmp_path):
    cfg = load_config(write(tmp_path, VALID))
    assert (cfg.timezone, cfg.window_hours, cfg.summary_cap) == ("America/Toronto", 24, 20)
    assert (cfg.model_articles, cfg.model_digest) == ("claude-haiku-5-5", "claude-sonnet-5-5")
    assert cfg.sections[0].max_articles == 5
    assert [s.name for s in cfg.sections[0].sources] == ["Source A", "Source B"]
    assert cfg.sections[0].sources[0].url == "https://a.test/rss"
    fed = cfg.markets[1]
    assert (fed.name, fed.provider, fed.symbol, fed.kind, fed.symbol_low) == (
        "Fed", "fred", "DFEDTARU", "taux", "DFEDTARL")
    assert cfg.markets[0].symbol_low is None


def test_defaults(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL))
    assert (cfg.window_hours, cfg.summary_cap, cfg.sections[0].max_articles) == (36, 40, 10)
    assert cfg.timezone == "America/Toronto"
    assert cfg.markets == ()


@pytest.mark.parametrize("broken", [NO_SECTIONS, SECTION_WITHOUT_SOURCES, SOURCE_WITHOUT_URL,
                                    UNKNOWN_PROVIDER, DUPLICATE_SECTION_NAME])
def test_invalid_config_raises_with_location(tmp_path, broken):
    with pytest.raises(ConfigError) as e:
        load_config(write(tmp_path, broken))
    assert "rubriques" in str(e.value) or "marches" in str(e.value)


def test_missing_file_raises_config_error(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "absent.yaml")


def test_real_config_loads():
    cfg = load_config(Path(__file__).parent.parent / "config.yaml")
    assert [s.name for s in cfg.sections] == [
        "Finance et marchés", "Intelligence artificielle",
        "Technologie et infrastructure TI", "Québec/Canada"]
    assert all(s.sources for s in cfg.sections)


# --- Lecture des images de partage : activable dans la configuration ---

def test_page_images_default_on_and_can_be_turned_off(tmp_path):
    base = "modeles: {articles: a, resume_du_jour: b}\nrubriques:\n  - nom: IA\n    sources: [{nom: S, url: \"https://s.test/rss\"}]\n"
    on = tmp_path / "on.yaml"; on.write_text(base, encoding="utf-8")
    off = tmp_path / "off.yaml"; off.write_text("general: {images_des_pages: false}\n" + base, encoding="utf-8")
    from veille.config import load_config
    assert load_config(on).page_images is True and load_config(off).page_images is False


def test_market_row_and_new_kinds_are_read(tmp_path):
    from veille.config import load_config
    text = ("modeles: {articles: a, resume_du_jour: b}\n"
            "marches:\n"
            "  - {nom: S, fournisseur: fred, symbole: SP500}\n"
            "  - {nom: XEQT, fournisseur: alphavantage, symbole: XEQT.TRT, ligne: 2}\n"
            "  - {nom: Bitcoin, fournisseur: twelvedata, symbole: BTC/USD, type: crypto, ligne: 2}\n"
            "rubriques:\n  - nom: IA\n    sources: [{nom: S, url: \"https://s.test/rss\"}]\n")
    path = tmp_path / "c.yaml"; path.write_text(text, encoding="utf-8")
    markets = load_config(path).markets
    assert [(m.row, m.kind, m.provider) for m in markets] == [(1, "indice", "fred"), (2, "indice", "alphavantage"), (2, "crypto", "twelvedata")]
