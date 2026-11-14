# Veille quotidienne — document de conception

Date : 2026-11-13 · Statut : en attente d'approbation

## 1. Intention

Un tableau de bord personnel, consulté le matin sur ordinateur et cellulaire, qui regroupe
les nouvelles importantes de la veille dans quelques rubriques, résumées en français et
classées par importance. Le but est de remplacer la tournée manuelle d'une vingtaine de
sites par une seule page lue en cinq minutes.

Utilisateur unique : programmeur-analyste en infrastructure TI, francophone, au Québec.

## 2. Buts, non-buts, anti-buts

**Buts**

- Une page par jour, prête avant 6 h (heure de l'Est), sans intervention.
- Résumés de 2-3 phrases en français, avec lien vers la source, même si la source est en anglais.
- Un « Résumé du jour » de 5 à 7 points, toutes rubriques confondues.
- Un bandeau de chiffres de marché avec la variation de la dernière séance.
- Archive par date et recherche par mot-clé.
- Rubriques et sources modifiables dans `config.yaml`, sans toucher au code.
- Coût mensuel total de l'ordre de 1 à 2 $ US.

**Non-buts**

- Données de marché en direct ou intrajournalières.
- Comptes utilisateurs, personnalisation par lecteur, notifications.
- Lecture du texte complet des articles (on travaille à partir du titre et de l'extrait du flux).
- Interface d'administration : la configuration se fait dans un fichier.

**Anti-buts** (ce qui ferait du projet un échec même s'il fonctionne)

- Recopier des articles complets ou omettre le lien vers la source.
- Une clé API dans le code ou dans l'historique Git.
- Une page vide ou cassée le matin parce qu'une seule source ou l'API Claude a échoué.
- Un résumé qui affirme autre chose que ce que dit la source.

## 3. Contraintes

- Hébergement gratuit, aucun serveur à maintenir.
- Clés API uniquement dans des variables d'environnement (secrets GitHub en production, `.env` non versionné en local).
- Respect des conditions d'utilisation : flux RSS et API officielles seulement, pas de moissonnage de pages, source toujours citée et liée.
- Le site est public (GitHub Pages). Il ne contient donc aucune donnée personnelle, et il porte `noindex` ainsi qu'un `robots.txt` restrictif pour rester hors des moteurs de recherche.
- Code commenté, README couvrant l'installation et le déploiement.
- Interface et résumés en français.

## 4. Approches considérées

| Approche | Avantages | Inconvénients |
|---|---|---|
| **A. Site statique Python, GitHub Actions, GitHub Pages** (retenue) | 0 $ d'hébergement, rien à maintenir, l'historique Git sert d'archive | Recherche côté navigateur seulement ; planification en UTC |
| B. TypeScript + Astro, même pipeline | Node 24 déjà installé localement | Outils RSS et traitement de texte moins pratiques qu'en Python |
| C. App FastAPI + SQLite sur VPS ou serveur maison | Recherche plein texte côté serveur, pages dynamiques | Serveur à sécuriser et à mettre à jour, environ 5-7 $/mois |

**Choix de l'utilisateur** : approche A, avec GitHub Pages (site public accepté) plutôt que
Cloudflare Pages avec accès privé. Raison : la simplicité prime, et le contenu n'a rien de sensible.

## 5. Décisions prises par l'utilisateur

- Rubriques, dans l'ordre : Finance et marchés ; Intelligence artificielle ; Technologie et infrastructure TI ; Québec/Canada. D'autres s'ajouteront par la configuration.
- Hébergement : GitHub (dépôt, Actions, Pages).
- Modèles : `claude-haiku-5-5` pour les résumés d'articles, `claude-sonnet-5-5` pour le « Résumé du jour ».
- Volume : 8 à 10 articles affichés par rubrique, les plus importants d'abord.

## 6. Conception

### 6.1 Pipeline quotidien

```
config.yaml ──► collecte ──► dédoublonnage ──► résumés ──► stockage ──► génération ──► publication
                (RSS, API)                     (Claude)   (JSON/jour)   (HTML)        (Pages)
```

Chaque étape est un module Python indépendant, testable seul, avec une commande unique
(`python -m veille run`) qui les enchaîne et des sous-commandes pour lancer une étape à la fois.

1. **Collecte** (`collect.py`) : lit chaque flux de `config.yaml`, garde les éléments publiés
   depuis la dernière exécution (fenêtre de 36 h par défaut pour ne rien perdre). Une source
   en erreur est journalisée et ignorée ; elle ne fait pas échouer le reste.
2. **Marchés** (`markets.py`) : dernière clôture et variation pour chaque symbole configuré.
   Une valeur indisponible est affichée « n. d. » plutôt que de bloquer la page.
3. **Dédoublonnage** (`dedupe.py`) : d'abord par URL normalisée (sans paramètres de suivi),
   puis par similarité de titres normalisés, en comparant aussi aux articles des 3 derniers
   jours. Quand plusieurs sources couvrent la même nouvelle, on garde la première selon
   l'ordre des sources dans la configuration, et on liste les autres comme « aussi couvert par ».
   Limite connue : deux titres dans des langues différentes ne sont pas reconnus comme doublons.
4. **Résumés** (`summarize.py`) : pour chaque article retenu, un appel à Haiku qui retourne en
   sortie structurée : résumé français de 2-3 phrases et note d'importance de 1 à 5. L'entrée
   est le titre et l'extrait du flux, rien d'autre. Puis un appel à Sonnet, qui reçoit tous les
   résumés du jour et produit les 5 à 7 points clés, chacun relié aux articles concernés.
   Plafond de 40 articles résumés par rubrique et par jour pour borner le coût.
5. **Stockage** (`store.py`) : `data/AAAA-MM-JJ.json` contient les marchés, les articles
   (titre, source, URL, date, résumé, importance, rubrique) et le résumé du jour. Ces fichiers
   sont versionnés ; ils sont la seule base de données.
6. **Génération** (`build.py`) : gabarits Jinja2 vers `site/` (non versionné) : page du jour,
   une page par date archivée, page d'archive, puis index de recherche Pagefind.
7. **Publication** : le flux de travail GitHub Actions valide `data/` dans le dépôt, puis
   déploie `site/` sur GitHub Pages.

### 6.2 Comportement en cas de panne

- Source RSS en panne : ignorée, mentionnée dans le journal et dans un pied de page discret (« 2 sources indisponibles »).
- API Claude en panne pour un article : l'extrait du flux est affiché à la place, marqué « extrait de la source ».
- API Claude en panne pour le résumé du jour : la section est masquée, le reste s'affiche.
- Aucune nouvelle collectée du tout : la tâche échoue (GitHub envoie un courriel) et la page de la veille reste en ligne.

### 6.3 Page

```
┌──────────────────────────────────────────────────────────┐
│ Veille · vendredi 13 novembre 2026      [Archive] [🔍]   │
├──────────────────────────────────────────────────────────┤
│ TSX 25 104 ▲0,4 %  S&P 6 012 ▼0,2 %  Nasdaq …  CAD/USD … │
│ BdC 2,25 %   Fed 3,75-4,00 %        (clôture du 12 nov.) │
├──────────────────────────────────────────────────────────┤
│ RÉSUMÉ DU JOUR                                           │
│  • Point clé 1 … [Finance]                               │
│  • Point clé 2 … [IA]                                    │
├──────────────────────────────────────────────────────────┤
│ FINANCE ET MARCHÉS                                       │
│  Titre de l'article (lien)                               │
│  Résumé en 2-3 phrases.                                  │
│  Source · il y a 5 h · aussi couvert par : X             │
│  …                                                       │
│  ▸ Afficher 12 autres articles                           │
├──────────────────────────────────────────────────────────┤
│ INTELLIGENCE ARTIFICIELLE …                              │
└──────────────────────────────────────────────────────────┘
```

- Une colonne sur cellulaire ; le bandeau de marché défile horizontalement.
- Mode sombre automatique selon le réglage du système, avec un bouton pour forcer l'un ou l'autre.
- HTML et CSS écrits à la main, un peu de JavaScript seulement pour le thème et la recherche. Aucun cadriciel côté navigateur.
- Les `max_articles` plus importants sont visibles par rubrique ; les autres sont repliés.

### 6.4 Configuration

```yaml
general:
  fuseau: America/Toronto
  fenetre_heures: 36
modeles:
  articles: claude-haiku-5-5
  resume_du_jour: claude-sonnet-5-5
marches:
  - {nom: "S&P 500", fournisseur: fred, symbole: SP500}
  - {nom: "CAD/USD", fournisseur: valet, symbole: FXCADUSD}
rubriques:            # l'ordre de la liste est l'ordre d'affichage
  - nom: Finance et marchés
    max_articles: 10
    sources:          # l'ordre fixe la priorité lors du dédoublonnage
      - {nom: Banque du Canada, url: "https://…/rss"}
```

Ajouter, retirer ou réordonner une rubrique ou une source se fait uniquement ici.

### 6.5 Sources de départ

À vérifier une par une à l'étape de collecte ; une source sans flux utilisable est retirée et signalée.

- **Finance et marchés** : Banque du Canada, Réserve fédérale, Statistique Canada (Le Quotidien), Radio-Canada Économie, La Presse Affaires, Les Affaires, Financial Post, CNBC.
- **Intelligence artificielle** : Anthropic, OpenAI, Google DeepMind, Meta AI, Hugging Face, Simon Willison, The Verge (IA), Ars Technica (IA), MIT Technology Review.
- **Technologie et infrastructure TI** : BleepingComputer, The Hacker News, Krebs on Security, CISA, Centre canadien pour la cybersécurité, The Register, nouveautés AWS, Azure et Google Cloud, blogue Cloudflare.
- **Québec/Canada** : Radio-Canada (grands titres, politique), La Presse, Le Devoir, CBC.
- **Marchés** : API Valet de la Banque du Canada (taux directeur, CAD/USD), FRED (taux de la Fed, S&P 500, Nasdaq), et une troisième source gratuite pour le TSX (Stooq ou Twelve Data).

### 6.6 Planification et déploiement

- Flux de travail GitHub Actions déclenché à 10 h UTC tous les jours, soit 6 h l'été et 5 h l'hiver à Montréal : la page est donc toujours prête avant 6 h. Déclenchement manuel possible.
- Secrets GitHub : `ANTHROPIC_API_KEY`, `FRED_API_KEY`, et une clé pour le TSX si le fournisseur retenu en exige une.
- En local : `.env` (ignoré par Git) et `.env.example` versionné.

### 6.7 Tests

Tests `pytest` écrits avant le code de chaque étape : analyse de la configuration, fenêtre
de collecte, dédoublonnage (URL, titres proches, priorité des sources), comportement en cas
de panne, rendu des gabarits à partir d'un jeu de données fixe. Les appels à Claude et au
réseau sont simulés dans les tests ; chaque étape est aussi essayée une fois pour vrai avant
de passer à la suivante.

## 7. Mes choix (à corriger au besoin)

- **Dépôt GitHub public.** GitHub Pages l'exige sur un compte gratuit ; le code et les fichiers `data/` seront donc visibles. Les minutes Actions sont alors illimitées.
- **Python 3.12**, à installer localement (le 3.9 présent est trop vieux pour le SDK Anthropic). Dépendances : `feedparser`, `PyYAML`, `Jinja2`, `httpx`, `anthropic`, `pytest`.
- **Fichiers JSON plutôt que SQLite** : lisibles, faciles à comparer dans Git, suffisants à ce volume.
- **Appels directs à l'API plutôt que le mode lot** : au tarif de Haiku l'économie serait de quelques cents, et le mode lot ajoute une attente pouvant aller jusqu'à 24 h.
- **Pagefind pour la recherche** : l'index est découpé en morceaux, donc il reste rapide même après des années d'archive.
- **Importance notée par Haiku** (1 à 5) pour trier les articles ; à égalité, le plus récent d'abord.
- **Exécution à 10 h UTC toute l'année** plutôt que de suivre le changement d'heure.
- **Rubrique Québec/Canada** : sources généralistes ci-dessus, 8 articles affichés.

## 8. Laissé au constructeur

- Seuil exact de similarité des titres et méthode de normalisation.
- Fournisseur du TSX, selon ce qui fonctionne réellement sans clé payante.
- Contournement pour les labos sans flux RSS officiel (flux tiers fiable, ou retrait de la source).
- Formulation des instructions envoyées à Claude et niveau d'effort des modèles.
- Détails visuels (typographie, couleurs) dans l'esprit « sobre et lisible ».

## 9. Ordre de construction

Collecte, puis stockage, résumés, interface, planification, déploiement. Chaque étape est
testée et montrée avant de passer à la suivante.
