"""Lecture et validation de config.yaml.

Tout ce qui se règle sans toucher au code vit dans ce fichier : rubriques,
sources, marchés et modèles. Les clés YAML sont en français ; les objets
Python qui en sortent ont des noms anglais.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import yaml

PROVIDERS = ("valet", "fred", "twelvedata", "alphavantage")
KINDS = ("indice", "taux", "devise", "crypto")


class ConfigError(ValueError):
    """Erreur de configuration, avec l'emplacement de la clé fautive."""


@dataclass(frozen=True)
class Source:
    name: str
    url: str


@dataclass(frozen=True)
class Section:
    name: str
    slug: str
    max_articles: int
    sources: tuple[Source, ...]


@dataclass(frozen=True)
class MarketSymbol:
    name: str
    provider: str
    symbol: str
    kind: str
    symbol_low: str | None = None
    row: int = 1   # ligne du bandeau (1 ou 2)


@dataclass(frozen=True)
class Config:
    timezone: str
    window_hours: int
    summary_cap: int
    model_articles: str
    model_digest: str
    markets: tuple[MarketSymbol, ...]
    sections: tuple[Section, ...]
    page_images: bool = True   # lire l'image de partage des articles sans image dans leur flux

    def source_order(self) -> list[str]:
        """Noms des sources dans l'ordre du fichier : c'est la priorité au dédoublonnage."""
        return [src.name for section in self.sections for src in section.sources]


def slugify(name: str) -> str:
    """« Finance et marchés » -> « finance-et-marches »."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")


def _require(mapping: object, key: str, where: str) -> object:
    """Retourne mapping[key] ou lève une ConfigError qui dit où est le problème."""
    if not isinstance(mapping, dict) or mapping.get(key) in (None, ""):
        raise ConfigError(f"{where} : la clé « {key} » est obligatoire")
    return mapping[key]


def _parse_markets(raw: object) -> tuple[MarketSymbol, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConfigError("marches : une liste est attendue")
    markets = []
    for i, item in enumerate(raw):
        where = f"marches[{i}]"
        provider = str(_require(item, "fournisseur", where))
        if provider not in PROVIDERS:
            raise ConfigError(f"{where} : fournisseur « {provider} » inconnu ({', '.join(PROVIDERS)})")
        kind = str(item.get("type", "indice"))
        if kind not in KINDS:
            raise ConfigError(f"{where} : type « {kind} » inconnu ({', '.join(KINDS)})")
        markets.append(MarketSymbol(
            name=str(_require(item, "nom", where)),
            provider=provider,
            symbol=str(_require(item, "symbole", where)),
            kind=kind,
            symbol_low=str(item["symbole_bas"]) if item.get("symbole_bas") else None,
            row=2 if str(item.get("ligne", 1)) == "2" else 1,
        ))
    return tuple(markets)


def _parse_sections(raw: object) -> tuple[Section, ...]:
    if not isinstance(raw, list) or not raw:
        raise ConfigError("rubriques : au moins une rubrique est obligatoire")
    sections, seen = [], set()
    for i, item in enumerate(raw):
        where = f"rubriques[{i}]"
        name = str(_require(item, "nom", where))
        slug = slugify(name)
        if slug in seen:
            raise ConfigError(f"{where} : la rubrique « {name} » est définie deux fois")
        seen.add(slug)
        raw_sources = item.get("sources")
        if not isinstance(raw_sources, list) or not raw_sources:
            raise ConfigError(f"{where} (rubriques « {name} ») : au moins une source est obligatoire")
        sources = tuple(
            Source(name=str(_require(src, "nom", f"{where}.sources[{j}]")),
                   url=str(_require(src, "url", f"{where}.sources[{j}]")))
            for j, src in enumerate(raw_sources)
        )
        sections.append(Section(name=name, slug=slug,
                                max_articles=int(item.get("max_articles", 10)),
                                sources=sources))
    return tuple(sections)


def load_config(path: Path) -> Config:
    """Charge et valide le fichier de configuration."""
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"Impossible de lire {path} : {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} n'est pas du YAML valide : {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} : un dictionnaire est attendu à la racine (rubriques, marches…)")

    general = raw.get("general") or {}
    models = raw.get("modeles")
    return Config(
        timezone=str(general.get("fuseau", "America/Toronto")),
        window_hours=int(general.get("fenetre_heures", 36)),
        summary_cap=int(general.get("plafond_resumes", 40)),
        page_images=bool(general.get("images_des_pages", True)),
        model_articles=str(_require(models, "articles", "modeles")),
        model_digest=str(_require(models, "resume_du_jour", "modeles")),
        markets=_parse_markets(raw.get("marches")),
        sections=_parse_sections(raw.get("rubriques")),
    )
