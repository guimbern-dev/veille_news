# Veille quotidienne

Un tableau de bord personnel qui regroupe chaque matin les nouvelles de la veille, résumées
en français et classées par rubrique : finance et marchés, intelligence artificielle,
technologie et infrastructure TI, Québec/Canada.

- **Résumé du jour** : 5 à 7 points clés illustrés, toutes rubriques confondues.
- **Bandeau de marché** : actions canadiennes (FNB EWC), S&P 500, Nasdaq, CAD/USD, taux de la Banque du Canada et de la Fed.
- **Par article** : un résumé de 2-3 phrases, le nom de la source, le lien vers l'original et,
  quand la source en propose une, une image d'illustration.
- **Archive par date** et **recherche par mot-clé**.
- **Mode sombre**, lisible sur ordinateur comme sur cellulaire.

## Fonctionnement

```
config.yaml ─► collecte ─► dédoublonnage ─► résumés ─► stockage ─► génération ─► publication
               (RSS, API)                   (Claude)   (data/*.json) (site/)      (GitHub Pages)
```

Le site est **statique**. Une tâche GitHub Actions le régénère chaque jour à 10 h UTC
(6 h l'été, 5 h l'hiver à Montréal), enregistre l'édition dans `data/` et publie `site/`
sur GitHub Pages. Il n'y a ni serveur ni base de données à maintenir.

| Module | Rôle |
|---|---|
| `veille/config.py` | lit et valide `config.yaml` |
| `veille/collect.py` | lit les flux RSS et Atom |
| `veille/dedupe.py` | retire les doublons (URL et titres) |
| `veille/images.py` | image de partage des articles sans image dans leur flux |
| `veille/markets.py` | chiffres de marché |
| `veille/summarize.py` | résumés par l'API Claude |
| `veille/store.py` | lit et écrit `data/AAAA-MM-JJ.json` |
| `veille/build.py` | génère le site à partir de `templates/` et `static/` |
| `veille/cli.py` | commande `python -m veille` |

## Installation locale

Il faut Python 3.12 ou plus récent.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.example .env
```

Remplir ensuite `.env`. Ce fichier est ignoré par Git : **aucune clé ne doit se retrouver
dans le code ni dans le dépôt.**

| Variable | Sert à | Où l'obtenir | Coût |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | résumés | <https://platform.claude.com/> | à l'usage (voir plus bas) |
| `FRED_API_KEY` | S&P 500, Nasdaq, taux de la Fed | <https://fred.stlouisfed.org/docs/api/api_key.html> | gratuit |
| `TWELVEDATA_API_KEY` | actions canadiennes (FNB EWC) | <https://twelvedata.com/> | gratuit |

Aucune clé n'est obligatoire. Sans `ANTHROPIC_API_KEY`, les articles affichent l'extrait
de la source au lieu d'un résumé ; sans clé de marché, la case correspondante affiche « n. d. ».
Le taux de change et le taux de la Banque du Canada viennent de l'API Valet, qui ne demande pas de clé.

## Commandes

```bash
.venv/bin/python -m veille run        # tout : collecte, résumés, stockage, site
.venv/bin/python -m veille collect    # tout sauf la génération du site
.venv/bin/python -m veille build      # régénère le site à partir de data/
.venv/bin/python -m http.server 8000 --directory site   # consulter le site en local
.venv/bin/python scripts/check_feeds.py                 # vérifier chaque source de config.yaml
.venv/bin/pytest                      # tests (sans réseau ni appel à Claude)
```

Options utiles : `--sans-resumes` (aucun appel à Claude), `--sans-recherche` (pas d'index
de recherche), `--config`, `--data`, `--out`.

Relancer `run` le même jour est sans danger : l'édition est complétée, les articles déjà
résumés ne sont pas renvoyés à Claude.

## Modifier les rubriques et les sources

Tout se fait dans [`config.yaml`](config.yaml), sans toucher au code.

**Ajouter une source** à une rubrique : une ligne de plus sous `sources`.

```yaml
      - {nom: Le Soleil, url: "https://www.lesoleil.com/arc/outboundfeeds/rss/?outputType=xml"}
```

**Ajouter une rubrique** : un bloc de plus sous `rubriques`.

```yaml
  - nom: Jeux de société
    max_articles: 5          # articles visibles ; les autres sont repliés
    sources:
      - {nom: BoardGameGeek, url: "https://boardgamegeek.com/rss/blog/1"}
```

**Réordonner** : l'ordre des rubriques dans le fichier est l'ordre d'affichage. L'ordre
des sources fixe la priorité au dédoublonnage : quand deux sources couvrent la même
nouvelle, la première de la liste est gardée et l'autre est citée sous « aussi couvert par ».

**Retirer** une rubrique ou une source : supprimer ses lignes. Les articles déjà archivés
d'une rubrique retirée ne sont plus affichés, mais restent dans `data/`.

Après un changement, lancer `scripts/check_feeds.py` pour confirmer que les flux répondent.

Autres réglages : `general.fenetre_heures` (âge maximal des nouvelles, 36 h),
`general.plafond_resumes` (articles résumés par rubrique à chaque exécution, répartis entre les sources ; 80),
`general.images_des_pages` (lecture de l'image de partage ; `true`),
`modeles` (modèles Claude utilisés) et `marches` (symboles du bandeau).

## Déploiement sur GitHub Pages

1. Créer un dépôt **public** sur GitHub (Pages l'exige sur un compte gratuit), puis y pousser le projet :
   ```bash
   git remote add origin https://github.com/<compte>/<depot>.git
   git push -u origin main
   ```
2. Dans *Settings → Secrets and variables → Actions*, ajouter les secrets
   `ANTHROPIC_API_KEY`, `FRED_API_KEY` et `TWELVEDATA_API_KEY`.
3. Dans *Settings → Pages*, choisir la source **GitHub Actions**.
4. Dans l'onglet *Actions*, ouvrir « Veille quotidienne » et cliquer **Run workflow** pour
   produire la première édition.
5. Le site est à `https://<compte>.github.io/<depot>/`.

La tâche tourne ensuite seule chaque jour. Si elle échoue, GitHub envoie un courriel et
l'édition de la veille reste en ligne.

Le site est public, mais il demande aux moteurs de recherche de ne pas l'indexer
(`noindex` et `robots.txt`). Le dépôt, lui, est visible : code et fichiers `data/`.

## Coût

| Poste | Coût mensuel |
|---|---|
| GitHub Actions et Pages (dépôt public) | 0 $ |
| API de marché (Valet, FRED, Twelve Data) | 0 $ |
| API Claude : Haiku 5.5 pour les articles, Sonnet 5.5 pour le résumé du jour | environ 2 à 3 $ US |

Le coût de Claude dépend du volume. Le plafond `plafond_resumes` le borne ; le nombre de
jetons consommés est écrit dans le journal de chaque exécution.

## Respect des sources

- Le contenu vient uniquement des flux RSS/Atom publics et d'API officielles ; le texte des articles n'est jamais téléchargé.
- **Une exception, pour les images** : quand un flux ne fournit pas d'image, l'en-tête de la page de l'article est lu
  pour y trouver l'image de partage (`og:image`), celle que les sites publient pour les aperçus de liens. Garde-fous :
  `robots.txt` est respecté (illisible = site laissé de côté), seul l'en-tête est lu, un refus n'est ni contourné ni
  retenté, et chaque page n'est demandée qu'une fois. Pour désactiver : `general.images_des_pages: false`.
- Claude reçoit uniquement le titre et l'extrait fournis par le flux.
- Chaque article affiche le nom de sa source et un lien vers l'original.
- Les images sont celles que les sources proposent elles-mêmes (flux ou image de partage). Seule leur adresse est enregistrée : elles restent
  hébergées par la source, ne sont jamais copiées dans le dépôt, et mènent à l'article d'origine.
- Une source qui bloque les lecteurs automatisés n'est pas contournée : elle est retirée de la configuration.

## Limites connues

- **Doublons entre langues** : une même nouvelle couverte en français et en anglais n'est pas fusionnée.
- **Résumés à partir de l'extrait** : si le flux ne donne qu'un titre, le résumé est court.
- **Marchés** : le bandeau montre la dernière clôture connue, pas des données en direct.
- **Pas d'indice TSX** : aucune source gratuite ne le fournit chaque jour. La case « Canada (EWC) » montre le
  prix d'un FNB d'actions canadiennes coté à New York, en dollars américains ; sa variation inclut l'effet du taux de change.
- **Recherche** : un résultat correspond à une édition ; au plus trois articles correspondants y sont listés.
- **Articles sans image** : quelques sources n'offrent d'image ni dans leur flux ni sur leur page, ou refusent les
  lecteurs automatisés (OpenAI) ; leurs articles s'affichent sans vignette. Certains sites institutionnels donnent
  leur logo comme image de partage.
- **Sources sans flux** : Anthropic et Meta AI n'offrent pas de flux RSS officiel ; leurs annonces
  arrivent par les médias spécialisés de la rubrique.
