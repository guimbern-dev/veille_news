# Veille quotidienne — plan de construction

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**But :** un site statique régénéré chaque matin qui présente les nouvelles de la veille, résumées en français par Claude et classées par rubrique.

**Architecture :** un paquet Python `veille` dont chaque module est une étape du pipeline (collecte, marchés, dédoublonnage, résumés, stockage, génération). Les données vivent dans `data/AAAA-MM-JJ.json`, versionnées. GitHub Actions lance le pipeline à 10 h UTC et déploie `site/` sur GitHub Pages.

**Outils :** Python 3.12, `feedparser`, `PyYAML`, `Jinja2`, `httpx`, `anthropic`, `pytest`, Pagefind, GitHub Actions, GitHub Pages.

**Spec :** `docs/superpowers/specs/2026-11-13-veille-quotidienne-design.md` (à lire avec ce plan).

## Contraintes globales

- Python 3.12 ; dépendances limitées à celles nommées ci-dessus.
- Interface, résumés, commentaires de code et messages de commit en français. Identifiants de code en anglais.
- Aucune clé dans le code ni dans Git : `ANTHROPIC_API_KEY`, `FRED_API_KEY` lues dans l'environnement ; `.env` ignoré par Git.
- Flux RSS et API officielles seulement. Jamais de téléchargement de la page d'un article. L'entrée de Claude est le titre et l'extrait du flux, rien d'autre.
- Chaque article affiché porte un lien vers sa source et le nom de celle-ci.
- Modèles : `claude-haiku-5-5` (articles), `claude-sonnet-5-5` (résumé du jour), lus dans `config.yaml`.
- Une panne d'une source, d'un symbole de marché ou de l'API Claude ne fait jamais échouer la page. Seule l'absence totale d'articles fait échouer la tâche.
- Les tests ne touchent ni le réseau ni l'API Claude ; tout accès externe passe par un paramètre injectable.
- Toutes les dates sont conscientes du fuseau, stockées en UTC ; le « jour » d'une édition est la date à `America/Toronto`.
- Gabarits Jinja2 avec échappement automatique activé.
- Pages : `<meta name="robots" content="noindex, nofollow">` et `robots.txt` avec `Disallow: /`.
- Chaque tâche se termine par un commit.

## Points de vigilance

1. **Entrée de flux sans date, ou avec une date illisible** : elle est gardée une seule fois (le jour où son URL apparaît pour la première fois), pas tous les jours. Test dans la tâche 4.
2. **HTML, entités et scripts dans les titres et extraits des flux** : réduits en texte brut à la collecte, échappés à l'affichage. Tests dans les tâches 3 et 7.
3. **Relance le même jour** (déclenchement manuel) : le fichier du jour est remplacé sans doublons, les articles déjà résumés ne sont pas renvoyés à Claude, et les articles du jour ne s'éliminent pas eux-mêmes au dédoublonnage. Test dans la tâche 8.
4. **Réponse inattendue de Claude** (refus, sortie invalide, importance hors de 1 à 5, résumé vide) : l'article garde son extrait, marqué comme tel ; l'importance est bornée. Test dans la tâche 6.
5. **Fin de semaine, jour férié, valeur manquante dans les séries de marché** : on prend les deux dernières observations valides, et la date de clôture est affichée. Test dans la tâche 5.

---

## Structure des fichiers

| Fichier | Rôle |
|---|---|
| `pyproject.toml`, `.gitignore`, `.env.example` | Projet, dépendances, secrets d'exemple |
| `config.yaml` | Rubriques, sources, marchés, modèles |
| `veille/models.py` | `Article`, `MarketQuote`, `DigestPoint`, `DayData` |
| `veille/config.py` | Lecture et validation de `config.yaml` |
| `veille/store.py` | Lecture et écriture de `data/*.json` |
| `veille/collect.py` | Flux RSS vers `Article` |
| `veille/dedupe.py` | URL et titres en double |
| `veille/markets.py` | Fournisseurs `valet`, `fred`, `stooq` |
| `veille/summarize.py` | Appels à Claude |
| `veille/build.py` | Gabarits vers `site/` |
| `veille/cli.py`, `veille/__main__.py` | Commande `python -m veille` |
| `templates/`, `static/` | `base.html`, `day.html`, `archive.html`, `style.css`, `theme.js` |
| `.github/workflows/daily.yml` | Planification et déploiement |
| `tests/` | Un fichier de tests par module, plus `tests/fixtures/` |

---

### Task 1: squelette du projet et configuration

**Fichiers :** créer `pyproject.toml`, `.gitignore`, `.env.example`, `config.yaml`, `veille/__init__.py`, `veille/config.py`, `tests/test_config.py`.

**Interfaces — produit :**

```python
class ConfigError(ValueError): ...

@dataclass(frozen=True)
class Source:        name: str; url: str
@dataclass(frozen=True)
class Section:       name: str; slug: str; max_articles: int; sources: tuple[Source, ...]
@dataclass(frozen=True)
class MarketSymbol:  name: str; provider: str; symbol: str; kind: str; symbol_low: str | None
@dataclass(frozen=True)
class Config:
    timezone: str; window_hours: int; summary_cap: int
    model_articles: str; model_digest: str
    markets: tuple[MarketSymbol, ...]; sections: tuple[Section, ...]

def load_config(path: Path) -> Config
```

Clés YAML (en français, comme dans la spec) : `general.fuseau`, `general.fenetre_heures` (défaut 36), `general.plafond_resumes` (défaut 40), `modeles.articles`, `modeles.resume_du_jour`, `marches[].{nom, fournisseur, symbole, type, symbole_bas}`, `rubriques[].{nom, max_articles, sources[].{nom, url}}`. `type` vaut `indice`, `taux` ou `devise`. `slug` est dérivé du nom (minuscules, sans accents, tirets). `max_articles` vaut 10 par défaut.

- [ ] **Étape 1 :** vérifier `uv --version`. S'il manque, s'arrêter et demander à l'utilisateur de l'installer (`brew install uv`). Puis `git init`, `uv venv --python 3.12`, et `pyproject.toml` avec les dépendances des contraintes globales.
- [ ] **Étape 2 :** `.gitignore` contient `.env`, `.venv/`, `site/`, `__pycache__/`, `.pytest_cache/`. `.env.example` contient `ANTHROPIC_API_KEY=` et `FRED_API_KEY=`.
- [ ] **Étape 3 : tests qui échouent** dans `tests/test_config.py` :

```python
def test_loads_sections_in_file_order(tmp_path):
    cfg = load_config(write(tmp_path, VALID))      # VALID : 2 rubriques, « IA » puis « Finance et marchés »
    assert [s.name for s in cfg.sections] == ["IA", "Finance et marchés"]
    assert cfg.sections[1].slug == "finance-et-marches"

def test_defaults(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL))
    assert (cfg.window_hours, cfg.summary_cap, cfg.sections[0].max_articles) == (36, 40, 10)

@pytest.mark.parametrize("broken", [NO_SECTIONS, SECTION_WITHOUT_SOURCES, SOURCE_WITHOUT_URL,
                                    UNKNOWN_PROVIDER, DUPLICATE_SECTION_NAME])
def test_invalid_config_raises_with_location(tmp_path, broken):
    with pytest.raises(ConfigError) as e:
        load_config(write(tmp_path, broken))
    assert "rubriques" in str(e.value) or "marches" in str(e.value)
```

- [ ] **Étape 4 :** lancer `uv run pytest tests/test_config.py -v`. Attendu : échec, `load_config` n'existe pas.
- [ ] **Étape 5 :** écrire `veille/config.py`. Les messages de `ConfigError` sont en français et nomment la clé fautive.
- [ ] **Étape 6 :** écrire `config.yaml` avec les quatre rubriques et les sources de la section 6.5 de la spec (URL de flux au mieux de vos connaissances ; elles sont vérifiées à la tâche 3), et les marchés : TSX (`stooq`), S&P 500 (`fred`, `SP500`), Nasdaq (`fred`, `NASDAQCOM`), CAD/USD (`valet`, `FXCADUSD`), taux BdC (`valet`, `V39079`), taux Fed (`fred`, `DFEDTARU` avec `symbole_bas: DFEDTARL`).
- [ ] **Étape 7 :** `uv run pytest -v` passe, y compris `test_real_config_loads` qui charge le vrai `config.yaml`.
- [ ] **Étape 8 :** commit `feat: squelette du projet et configuration`.

---

### Task 2: modèles et stockage

**Fichiers :** créer `veille/models.py`, `veille/store.py`, `tests/test_store.py`.

**Interfaces — produit :**

```python
@dataclass
class Article:
    id: str                    # 12 premiers caractères du SHA-1 de l'URL normalisée
    title: str; url: str; source: str; section: str   # section = slug de la rubrique
    published: datetime | None # UTC ; None si le flux ne donne pas de date lisible
    excerpt: str               # texte brut, 1500 caractères au plus
    summary: str | None = None
    importance: int | None = None          # 1 à 5
    also_covered_by: list[dict] = field(default_factory=list)   # [{"name":…, "url":…}]

@dataclass
class MarketQuote:
    name: str; kind: str; value: float | None; value_low: float | None
    change_pct: float | None; as_of: date | None

@dataclass
class DigestPoint:  text: str; section: str; article_ids: list[str]

@dataclass
class DayData:
    day: date; generated_at: datetime
    markets: list[MarketQuote]; articles: list[Article]
    digest: list[DigestPoint] | None; failed_sources: list[str]

def save_day(data: DayData, data_dir: Path) -> Path      # écrit data_dir/AAAA-MM-JJ.json
def load_day(day: date, data_dir: Path) -> DayData | None
def list_days(data_dir: Path) -> list[date]              # du plus récent au plus ancien
def load_previous(day: date, data_dir: Path, days: int) -> list[Article]   # jours strictement avant `day`
```

- [ ] **Étape 1 : tests qui échouent** :

```python
def test_round_trip_preserves_everything(tmp_path):
    save_day(SAMPLE, tmp_path)
    assert load_day(SAMPLE.day, tmp_path) == SAMPLE      # dates, None, accents compris

def test_file_is_readable_utf8_json(tmp_path):
    text = save_day(SAMPLE, tmp_path).read_text(encoding="utf-8")
    assert "Québec" in text and text.endswith("\n")       # ensure_ascii=False, indent=2, clés triées

def test_load_missing_day_returns_none(tmp_path):
    assert load_day(date(2026, 1, 1), tmp_path) is None

def test_list_days_ignores_other_files(tmp_path):
    # contient 2026-11-12.json, 2026-11-13.json, notes.txt, brouillon.json
    assert list_days(tmp_path) == [date(2026, 11, 13), date(2026, 11, 12)]

def test_load_previous_excludes_the_day_itself(tmp_path):
    # fichiers du 10, 11, 12 et 13 novembre, un article chacun
    ids = {a.id for a in load_previous(date(2026, 11, 13), tmp_path, days=2)}
    assert ids == {"id-11", "id-12"}
```

- [ ] **Étape 2 :** lancer, constater l'échec.
- [ ] **Étape 3 :** écrire `models.py` et `store.py`. JSON indenté, trié, UTF-8, pour des différences Git lisibles.
- [ ] **Étape 4 :** `uv run pytest -v` passe.
- [ ] **Étape 5 :** commit `feat: modèles de données et stockage JSON`.

---

### Task 3: collecte des flux

**Fichiers :** créer `veille/collect.py`, `tests/test_collect.py`, `tests/fixtures/rss_simple.xml`, `atom_simple.xml`, `rss_sale.xml`.

**Interfaces :**
- Consomme : `Config`, `Section`, `Source` (tâche 1) ; `Article` (tâche 2) ; `normalize_url` n'existe pas encore : `Article.id` est calculé ici par `article_id(url)`, que la tâche 4 réutilise.
- Produit :

```python
Fetch = Callable[[str], bytes]            # lève une exception en cas d'erreur réseau ou HTTP

def article_id(url: str) -> str
def clean_text(html: str, limit: int = 1500) -> str      # balises retirées, entités décodées, espaces réduits
def parse_feed(content: bytes, source: Source, section_slug: str) -> list[Article]
def collect(config: Config, now: datetime, fetch: Fetch) -> tuple[list[Article], list[str]]
def http_fetch(url: str) -> bytes         # httpx, délai de 20 s, redirections suivies, User-Agent "veille-quotidienne/1.0"
```

`collect` garde les articles dont `published` est dans les `window_hours` avant `now`, **et** ceux sans date (`published is None`) ; la tâche 4 élimine ces derniers s'ils ont déjà été vus. Le second élément retourné est la liste des noms de sources en panne.

- [ ] **Étape 1 : tests qui échouent** :

```python
def test_parses_rss_and_atom():
    for fixture in ("rss_simple.xml", "atom_simple.xml"):
        arts = parse_feed(read(fixture), SRC, "ia")
        assert arts[0].title == "Premier titre"
        assert arts[0].url == "https://exemple.test/a1"
        assert arts[0].published == datetime(2026, 11, 12, 14, 0, tzinfo=UTC)
        assert (arts[0].source, arts[0].section) == ("Source test", "ia")

def test_dirty_feed_is_reduced_to_plain_text():
    a = parse_feed(read("rss_sale.xml"), SRC, "ia")[0]
    # titre : « Caf&eacute; &amp; <b>th&eacute;</b> » ; description avec <script>alert(1)</script><p>Texte…</p>
    assert a.title == "Café & thé"
    assert "<" not in a.excerpt and "alert" not in a.excerpt
    assert len(a.excerpt) <= 1500

def test_entry_without_link_or_title_is_skipped(): ...       # rss_sale.xml en contient une de chaque
def test_unreadable_date_gives_none(): ...                   # pubDate « hier matin » -> published is None

def test_window_keeps_recent_and_undated_only():
    arts, _ = collect(CFG, now=datetime(2026, 11, 13, 10, tzinfo=UTC), fetch=fake_fetch)
    # le flux contient : il y a 2 h, il y a 35 h, il y a 37 h, sans date
    assert sorted(a.title for a in arts) == ["2h", "35h", "sans date"]

def test_one_failing_source_does_not_stop_the_others():
    arts, failed = collect(CFG_TWO_SOURCES, NOW, fetch=fails_for("https://panne.test/rss"))
    assert failed == ["Source en panne"] and len(arts) > 0

def test_garbage_bytes_count_as_a_failed_source():
    _, failed = collect(CFG, NOW, fetch=lambda url: b"<html>404</html>")
    assert failed == ["Source test"]
```

- [ ] **Étape 2 :** lancer, constater l'échec.
- [ ] **Étape 3 :** écrire `collect.py` avec `feedparser`. Une source dont l'analyse ne donne aucune entrée et signale une erreur (`bozo`) compte comme en panne.
- [ ] **Étape 4 :** `uv run pytest -v` passe.
- [ ] **Étape 5 : vérification réelle.** Écrire `scripts/check_feeds.py`, qui appelle `http_fetch` et `parse_feed` pour chaque source de `config.yaml` et imprime, par source : nombre d'entrées, date la plus récente, ou l'erreur. Le lancer. Pour chaque source en erreur ou vide : chercher l'URL de flux officielle correcte ; s'il n'en existe pas, retirer la source et la noter. Relancer jusqu'à ce que chaque source restante retourne des entrées. Rapporter à l'utilisateur la liste finale et les sources retirées.
- [ ] **Étape 6 :** commit `feat: collecte des flux RSS`.

---

### Task 4: dédoublonnage

**Fichiers :** créer `veille/dedupe.py`, `tests/test_dedupe.py` ; modifier `veille/collect.py` pour que `article_id` utilise `normalize_url`.

**Interfaces :**
- Consomme : `Article`, `load_previous` (tâche 2).
- Produit :

```python
def normalize_url(url: str) -> str
def normalize_title(title: str) -> str
def titles_match(a: str, b: str) -> bool
def dedupe(articles: list[Article], previous: list[Article], source_order: list[str]) -> list[Article]
```

Règles :
- `normalize_url` : schéma et hôte en minuscules, `www.` retiré, fragment retiré, barre oblique finale retirée, paramètres `utm_*`, `fbclid`, `gclid`, `ref`, `cmp`, `ocid` retirés, paramètres restants triés.
- `normalize_title` : minuscules, accents retirés, ponctuation retirée, suffixe de marque retiré (` | Source`, ` - Source`), espaces réduits.
- `titles_match` : titres normalisés égaux, ou rapport `difflib.SequenceMatcher` d'au moins 0,85 quand les deux titres ont au moins 5 mots.
- `dedupe` : `previous` reçoit les articles des 30 derniers jours pour la comparaison d'URL ; seuls ceux des 3 derniers jours (selon `published`) servent à la comparaison des titres. Un article déjà dans `previous` est retiré. Dans un groupe de doublons du jour, on garde celui dont la source vient en premier dans `source_order` ; les autres vont dans son `also_covered_by`. L'ordre de sortie suit l'ordre d'entrée.

- [ ] **Étape 1 : tests qui échouent** :

```python
@pytest.mark.parametrize("a,b", [
    ("https://www.Exemple.test/a/?utm_source=x&b=2&a=1#haut", "https://exemple.test/a?a=1&b=2"),
    ("http://exemple.test/a/", "http://exemple.test/a"),
])
def test_normalize_url(a, b): assert normalize_url(a) == normalize_url(b)

def test_different_articles_keep_different_urls():
    assert normalize_url("https://x.test/a?id=1") != normalize_url("https://x.test/a?id=2")

def test_titles_match():
    assert titles_match("La Banque du Canada abaisse son taux directeur à 2,25 %",
                        "La Banque du Canada abaisse son taux directeur à 2,25% | Les Affaires")
    assert not titles_match("OpenAI lance un nouveau modèle", "Google lance un nouveau modèle")
    assert not titles_match("Mise à jour", "Mise à jour de sécurité critique pour Windows Server")

def test_same_story_keeps_first_source_in_config_order():
    out = dedupe([art("B", "Même nouvelle importante du jour ici"), art("A", "Même nouvelle importante du jour ici")],
                 previous=[], source_order=["A", "B"])
    assert [a.source for a in out] == ["A"]
    assert out[0].also_covered_by == [{"name": "B", "url": "https://b.test/x"}]

def test_already_seen_url_is_dropped():           # previous contient la même URL, vieille de 20 jours
def test_title_seen_two_days_ago_is_dropped():
def test_title_seen_ten_days_ago_is_kept():       # au-delà de 3 jours, un titre semblable est une nouvelle nouvelle
def test_undated_article_is_kept_once_then_dropped():
    first = dedupe([undated], previous=[], source_order=["A"])
    assert len(first) == 1
    assert dedupe([undated], previous=first, source_order=["A"]) == []
```

- [ ] **Étape 2 :** lancer, constater l'échec.
- [ ] **Étape 3 :** écrire `dedupe.py` ; brancher `normalize_url` dans `article_id`.
- [ ] **Étape 4 :** `uv run pytest -v` passe (tests de la tâche 3 compris).
- [ ] **Étape 5 :** commit `feat: dédoublonnage par URL et par titre`.

---

### Task 5: chiffres de marché

**Fichiers :** créer `veille/markets.py`, `tests/test_markets.py`, `tests/fixtures/valet_fxcadusd.json`, `fred_sp500.json`, `stooq_tsx.csv`.

**Interfaces :**
- Consomme : `MarketSymbol` (tâche 1), `MarketQuote` (tâche 2), `Fetch` (tâche 3).
- Produit :

```python
def last_two(observations: list[tuple[date, float | None]]) -> tuple[tuple[date, float], tuple[date, float] | None] | None
def fetch_quotes(markets: tuple[MarketSymbol, ...], fetch: Fetch, fred_key: str | None) -> list[MarketQuote]
```

Fournisseurs :
- `valet` : `https://www.banqueducanada.ca/valet/observations/{symbole}/json?recent=10`
- `fred` : `https://api.stlouisfed.org/fred/series/observations?series_id={symbole}&api_key={clé}&file_type=json&sort_order=desc&limit=10` ; une valeur `"."` est une observation manquante.
- `stooq` : `https://stooq.com/q/d/l/?s={symbole}&i=d` (CSV, colonne `Close`).

`change_pct` est la variation en pourcentage entre les deux dernières observations valides pour `indice` et `devise`. Pour `taux`, c'est l'écart en points de pourcentage (0 si inchangé). Avec `symbole_bas`, `value_low` reçoit la dernière valeur de cette seconde série.

- [ ] **Étape 1 : tests qui échouent** :

```python
def test_last_two_skips_missing_values():
    obs = [(d(13), None), (d(12), 6012.0), (d(11), None), (d(10), 6024.0)]
    assert last_two(obs) == ((d(12), 6012.0), (d(10), 6024.0))

def test_index_change_is_percent():
    q = fetch_quotes((SP500,), fixture_fetch, fred_key="k")[0]
    assert (q.value, q.as_of) == (6012.0, date(2026, 11, 12))
    assert q.change_pct == pytest.approx(-0.199, abs=0.001)

def test_rate_change_is_points_and_range_has_low():
    q = fetch_quotes((FED,), fixture_fetch, fred_key="k")[0]
    assert (q.value, q.value_low, q.change_pct) == (4.00, 3.75, 0.0)

def test_each_provider_parses_its_fixture(): ...          # valet, fred, stooq

def test_failing_symbol_gives_empty_quote_and_keeps_the_rest():
    quotes = fetch_quotes((SP500, TSX), fails_for("stooq.com"), fred_key="k")
    assert quotes[1] == MarketQuote(name="TSX", kind="indice", value=None, value_low=None, change_pct=None, as_of=None)
    assert quotes[0].value is not None

def test_missing_fred_key_gives_empty_quote_without_calling_network(): ...
def test_single_observation_gives_value_without_change(): ...
```

- [ ] **Étape 2 :** lancer, constater l'échec.
- [ ] **Étape 3 :** écrire `markets.py`.
- [ ] **Étape 4 :** `uv run pytest -v` passe.
- [ ] **Étape 5 : vérification réelle.** Lancer `fetch_quotes` sur le vrai `config.yaml` (la clé FRED vient de `.env` ; si elle manque, demander à l'utilisateur d'en créer une gratuite sur fred.stlouisfed.org). Chaque symbole doit retourner une valeur plausible. Si Stooq ne livre pas le TSX, ajouter un fournisseur `twelvedata` de la même forme et le dire à l'utilisateur. Corriger les identifiants de série qui ne répondent pas.
- [ ] **Étape 6 :** commit `feat: bandeau de chiffres de marché`.

---

### Task 6: résumés par Claude

**Fichiers :** créer `veille/summarize.py`, `tests/test_summarize.py`.

**Avant d'écrire le code :** invoquer la compétence `claude-api`, puis lire `python/claude-api/README.md` et `python/claude-api/tool-use.md` (sorties structurées, gestion de `stop_reason`, option `fallbacks` pour Sonnet 5.5). Ne deviner aucune signature du SDK.

**Interfaces :**
- Consomme : `Article`, `DigestPoint` (tâche 2), `Section` (tâche 1).
- Produit :

```python
def summarize_articles(articles: list[Article], client, model: str, cap_per_section: int) -> list[Article]
def daily_digest(articles: list[Article], sections: tuple[Section, ...], client, model: str) -> list[DigestPoint] | None
```

Règles :
- Un appel par article sans `summary`. Les articles qui ont déjà un `summary` ne sont jamais renvoyés.
- Au-delà de `cap_per_section` articles par rubrique (les plus récents d'abord), les suivants gardent `summary=None`, `importance=1`.
- Sortie structurée attendue par article : `{"resume": str, "importance": int}`. Consigne : 2-3 phrases en français, uniquement à partir du titre et de l'extrait fournis, sans rien ajouter qui n'y figure pas ; importance de 1 (anecdotique) à 5 (majeure) pour un lecteur en TI au Québec qui suit la finance et l'IA.
- `importance` bornée à 1-5. Refus, exception, sortie invalide ou résumé vide : `summary=None`, `importance=2`, et on continue.
- `daily_digest` reçoit titre, résumé, rubrique et `id` de chaque article résumé d'importance 3 ou plus, et retourne 5 à 7 points `{"texte", "rubrique", "articles": [id…]}`. Les `id` inconnus sont retirés ; un point sans article valide est écarté. Moins de 3 points valides, ou toute erreur : retourne `None`.
- Appels aux articles en parallèle, 5 à la fois au plus.

- [ ] **Étape 1 : tests qui échouent**, avec un faux client qui enregistre les appels et rejoue des réponses :

```python
def test_summary_and_importance_are_stored():
    out = summarize_articles([a1], FakeClient([ok("Résumé.", 4)]), "claude-haiku-5-5", 40)
    assert (out[0].summary, out[0].importance) == ("Résumé.", 4)

def test_only_title_and_excerpt_are_sent():
    client = FakeClient([ok("R.", 3)]); summarize_articles([a1], client, "m", 40)
    assert a1.title in client.sent_text(0) and a1.excerpt in client.sent_text(0)
    assert client.calls[0]["model"] == "m"

def test_already_summarized_articles_are_not_sent_again():
    client = FakeClient([]); summarize_articles([already_done], client, "m", 40)
    assert client.calls == []

@pytest.mark.parametrize("reply", [refusal(), raises(RuntimeError("503")), ok("", 3), invalid_json()])
def test_bad_reply_falls_back_to_excerpt(reply):
    out = summarize_articles([a1, a2], FakeClient([reply, ok("R2.", 3)]), "m", 40)
    assert (out[0].summary, out[0].importance) == (None, 2)
    assert out[1].summary == "R2."

@pytest.mark.parametrize("given,stored", [(0, 1), (9, 5), (3, 3)])
def test_importance_is_clamped(given, stored): ...

def test_cap_per_section_limits_calls():
    client = FakeClient([ok("R.", 3)] * 2)
    out = summarize_articles(three_articles_same_section, client, "m", cap_per_section=2)
    assert len(client.calls) == 2 and out[2].summary is None     # out[2] est le plus ancien

def test_digest_drops_unknown_ids_and_empty_points(): ...
def test_digest_returns_none_on_error_or_too_few_points(): ...
```

- [ ] **Étape 2 :** lancer, constater l'échec.
- [ ] **Étape 3 :** écrire `summarize.py` selon les fichiers de la compétence lus plus haut.
- [ ] **Étape 4 :** `uv run pytest -v` passe.
- [ ] **Étape 5 : vérification réelle** (dépense quelques cents ; demander l'accord de l'utilisateur et sa clé dans `.env`). Résumer 5 vrais articles de la tâche 3, dont au moins 2 en anglais, puis produire un résumé du jour. Montrer le résultat à l'utilisateur et vérifier à la main que chaque résumé ne dit rien d'absent de son extrait.
- [ ] **Étape 6 :** commit `feat: résumés et résumé du jour par Claude`.

---

### Task 7: génération du site

**Fichiers :** créer `veille/build.py`, `templates/base.html`, `templates/day.html`, `templates/archive.html`, `static/style.css`, `static/theme.js`, `tests/test_build.py`.

**Interfaces :**
- Consomme : `DayData`, `list_days`, `load_day` (tâche 2), `Config` (tâche 1).
- Produit :

```python
def build_site(data_dir: Path, out_dir: Path, config: Config) -> None
```

Sorties : `index.html` (jour le plus récent), `jour/AAAA-MM-JJ.html` pour chaque jour, `archive.html` (liste des jours, groupés par mois, avec le nombre d'articles), `robots.txt`, `static/`. Liens tous relatifs, car le site vit sous `https://<compte>.github.io/<dépôt>/`.

Règles d'affichage (maquette en section 6.3 de la spec) :
- Rubriques dans l'ordre de `config.sections`. Une rubrique sans article affiche « Aucune nouvelle aujourd'hui ». Un article dont la rubrique n'existe plus dans la configuration n'est pas affiché.
- Dans une rubrique : tri par `importance` décroissante, puis `published` décroissant. Les `max_articles` premiers sont visibles ; les autres sont dans `<details>` intitulé « Afficher N autres articles ».
- Article : titre lié (`rel="noopener noreferrer"`), résumé, puis source, heure relative et « aussi couvert par ». Sans `summary` : l'extrait, tronqué à 300 caractères, précédé de la mention « Extrait de la source ».
- Bandeau : nombres au format français (`25 104,32`, `▲ 0,4 %`, `▼ 0,2 %`), « n. d. » si `value is None`, fourchette `3,75-4,00 %` si `value_low` existe, et la date de clôture la plus récente.
- Résumé du jour masqué si `digest is None`. Chaque point porte une étiquette de rubrique et des liens d'ancre vers ses articles (`#a-<id>`).
- Pied de page : heure de génération et « N sources indisponibles » si `failed_sources` n'est pas vide.
- Date en français écrite en toutes lettres (`vendredi 13 novembre 2026`) sans dépendre de la locale du système.
- Mode sombre : `prefers-color-scheme` par défaut, bouton à trois états (auto, clair, sombre) mémorisé dans `localStorage`, appliqué avant le premier affichage pour éviter un éclair blanc.
- Cellulaire : une colonne, texte de 16 px au moins, bandeau à défilement horizontal, aucune barre de défilement horizontale sur la page à 360 px de large.
- Recherche : après le rendu, lancer Pagefind sur `out_dir` (`python -m pagefind --site <out_dir>` via le paquet `pagefind[extended]`, ajouté aux dépendances). Seuls les articles sont indexés (`data-pagefind-body` sur le conteneur des articles des pages `jour/`, pas sur `index.html`, pour éviter les doublons). La recherche s'ouvre depuis l'en-tête. Si Pagefind est absent, `build_site` journalise un avertissement et termine quand même.

- [ ] **Étape 1 : tests qui échouent** (analyse du HTML produit avec `html.parser`) :

```python
def test_index_shows_latest_day_and_sections_in_config_order(site):
    assert headings(site / "index.html") == ["Finance et marchés", "Intelligence artificielle", …]
    assert "vendredi 13 novembre 2026" in text(site / "index.html")

def test_articles_sorted_by_importance_then_recency_and_overflow_is_folded(site):
    # rubrique à max_articles=2 avec 3 articles
    assert visible_titles(site, "ia") == ["Importance 5", "Importance 3 récent"]
    assert "Afficher 1 autre article" in text(site / "index.html")

def test_every_article_links_to_its_source(site):
    for card in article_cards(site / "index.html"):
        assert card.link.startswith("https://") and card.source_name

def test_feed_content_is_escaped(site):
    # un titre vaut « <script>alert(1)</script> »
    assert "<script>alert(1)</script>" not in (site / "index.html").read_text()

def test_article_without_summary_shows_marked_excerpt(site): ...
def test_empty_section_says_so(site): ...
def test_digest_hidden_when_none_and_anchors_resolve_otherwise(site): ...
def test_market_formatting(site):
    t = text(site / "index.html")
    assert "25 104,32" in t and "▼ 0,2 %" in t and "n. d." in t and "3,75-4,00 %" in t
def test_archive_lists_every_day_and_day_pages_exist(site): ...
def test_noindex_and_robots(site):
    assert 'content="noindex, nofollow"' in (site / "index.html").read_text()
    assert (site / "robots.txt").read_text().strip().endswith("Disallow: /")
def test_all_internal_links_are_relative_and_resolve(site): ...
def test_failed_sources_footer(site): ...
```

- [ ] **Étape 2 :** lancer, constater l'échec.
- [ ] **Étape 3 :** écrire `build.py`, les gabarits et les fichiers statiques. Style sobre : une police système, une couleur d'accent, bon contraste dans les deux thèmes.
- [ ] **Étape 4 :** `uv run pytest -v` passe.
- [ ] **Étape 5 : vérification visuelle.** Générer le site à partir d'un jeu de données réaliste, le servir en local, et le regarder dans le navigateur en clair et en sombre, en largeur d'ordinateur et à 375 px. Essayer la recherche, l'archive, le bouton de thème. Corriger ce qui cloche, puis montrer des captures à l'utilisateur.
- [ ] **Étape 6 :** commit `feat: génération du site statique`.

---

### Task 8: commande et enchaînement

**Fichiers :** créer `veille/cli.py`, `veille/__main__.py`, `tests/test_cli.py`.

**Interfaces :**
- Consomme : tout ce qui précède, avec les signatures données dans chaque tâche.
- Produit :

```python
def run_pipeline(config: Config, data_dir: Path, out_dir: Path, now: datetime,
                 fetch: Fetch, client, fred_key: str | None) -> DayData
def main(argv: list[str] | None = None) -> int
```

Sous-commandes : `run` (tout), `collect` (collecte, marchés, dédoublonnage, résumés, stockage ; pas de génération), `build` (génération seule), avec les options `--config`, `--data`, `--out`, `--sans-resumes`. `main` charge `.env` s'il existe, sans dépendance de plus.

Déroulement de `run_pipeline` :
1. `day` = date de `now` dans `config.timezone`.
2. `collect`, puis `dedupe` avec `load_previous(day, data_dir, 30)` et l'ordre des sources de la configuration.
3. Si `load_day(day)` existe : ses articles sont fusionnés avec les nouveaux par `id` ; ceux qui ont déjà un `summary` le gardent.
4. Aucun article au total : lève `SystemExit(1)` avec le message « Aucune nouvelle collectée », sans rien écrire.
5. `summarize_articles`, `daily_digest`, `fetch_quotes`, `save_day`, `build_site`.
6. Sans clé `ANTHROPIC_API_KEY`, ou avec `--sans-resumes` : les étapes Claude sont sautées, un avertissement est journalisé, le reste se déroule.

- [ ] **Étape 1 : tests qui échouent** (réseau et client simulés) :

```python
def test_full_run_writes_day_file_and_site(tmp_path): ...
def test_rerun_same_day_does_not_duplicate_or_resummarize(tmp_path):
    run_pipeline(CFG, data, out, NOW, fake_fetch, client, None)
    calls = len(client.calls)
    again = run_pipeline(CFG, data, out, NOW + timedelta(hours=1), fake_fetch, client, None)
    assert len(again.articles) == len({a.id for a in again.articles}) == 3
    assert len(client.calls) == calls + 1          # seul le résumé du jour est refait
def test_no_articles_exits_1_and_leaves_previous_site_untouched(tmp_path): ...
def test_runs_without_api_key(tmp_path): ...       # articles sans résumé, digest None, site produit
def test_claude_fully_down_still_builds(tmp_path): ...
def test_day_uses_toronto_date(tmp_path):
    data = run_pipeline(CFG, d, o, datetime(2026, 11, 14, 3, 0, tzinfo=UTC), fake_fetch, client, None)
    assert data.day == date(2026, 11, 13)
```

- [ ] **Étape 2 :** lancer, constater l'échec.
- [ ] **Étape 3 :** écrire `cli.py` et `__main__.py`. Journalisation en français sur la sortie standard : une ligne par étape avec les compteurs (sources lues, en panne, articles gardés, résumés, jetons consommés).
- [ ] **Étape 4 :** `uv run pytest -v` passe.
- [ ] **Étape 5 : vérification réelle.** `uv run python -m veille run` avec les vraies clés, puis ouvrir `site/index.html` servi en local. Relancer une seconde fois et vérifier dans le journal qu'aucun article n'est résumé de nouveau. Montrer la page à l'utilisateur.
- [ ] **Étape 6 :** commit `feat: commande veille et enchaînement du pipeline`.

---

### Task 9: planification, déploiement et README

**Fichiers :** créer `.github/workflows/daily.yml`, `README.md`.

**Flux de travail** `daily.yml` :
- Déclencheurs : `schedule: cron: "0 10 * * *"` et `workflow_dispatch`.
- `concurrency: veille`, sans annulation de l'exécution en cours.
- Permissions : `contents: write`, `pages: write`, `id-token: write`.
- Étapes : extraction du dépôt ; installation de `uv` et de Python 3.12 ; `uv sync` ; `uv run pytest -q` ; `uv run python -m veille run` avec `ANTHROPIC_API_KEY` et `FRED_API_KEY` tirées des secrets ; commit de `data/` (`chore: édition du AAAA-MM-JJ`, seulement s'il y a un changement) et push ; téléversement de `site/` et déploiement avec les actions officielles de Pages.
- Si `veille run` échoue, rien n'est déployé : la page de la veille reste en ligne.

**README** (en français) : ce que fait le projet ; installation locale ; clés à obtenir et où les mettre ; commandes ; comment ajouter, retirer ou réordonner une rubrique ou une source, avec exemples ; déploiement pas à pas ; coût attendu ; limites connues (doublons entre langues, résumés à partir de l'extrait, clôture de la veille).

- [ ] **Étape 1 :** écrire `daily.yml` et le valider (`actionlint` s'il est disponible, sinon relecture contre la documentation des actions utilisées).
- [ ] **Étape 2 :** écrire `README.md`.
- [ ] **Étape 3 :** commit `feat: planification quotidienne, déploiement et README`.
- [ ] **Étape 4 : avec l'utilisateur, qui fait lui-même ces gestes ou les autorise un à un :** créer le dépôt GitHub public ; pousser ; ajouter les deux secrets ; activer Pages avec la source « GitHub Actions ».
- [ ] **Étape 5 :** lancer le flux à la main, suivre l'exécution jusqu'au bout, ouvrir l'URL publique sur ordinateur et sur cellulaire, vérifier que le commit `data/` est arrivé.
- [ ] **Étape 6 :** le lendemain, confirmer que l'exécution planifiée a eu lieu et à quelle heure.
