# AO Hunter - Guide pour Claude AI

## Règle de mise à jour de ce fichier

A la fin de chaque session, mets à jour uniquement la section "Bugs connus et comportements à éviter" si tu as rencontré un bug, un comportement inattendu ou résolu un problème. Ne touche pas aux autres sections sauf si une information est devenue fausse (ex : changement de modèle, nouvelle source de veille, restructuration du projet).

---

## Contexte projet

AO Hunter est un outil de veille et reponse automatique aux appels d'offres publics francais, developpe pour **Almera** (raison sociale: AI MENTOR, SASU), organisme de formation en Intelligence Artificielle certifie Qualiopi et RS6776 France Competences.

Le projet est utilise en production par Mickael Bertolla, President d'Almera. Ne jamais inventer de noms de formateurs, de references clients ou de certifications. Toutes les donnees reelles sont dans `config.yaml` et `catalogue_formations.yaml`.

## Architecture

```
ao_hunter/                        # Racine projet (PAS un repo git)
├── main.py                       # CLI: veille, generer, rapport, demo
├── config.yaml                   # Config entreprise, API keys, criteres
├── catalogue_formations.yaml     # 29 formations avec programmes detailles
├── veille.py                     # Veille multi-sources (BOAMP, PLACE, TED, AWS)
├── filtre.py                     # Scoring IA (Claude Haiku) + mots-cles
├── generateur.py                 # Generation dossier complet (Claude API + dependances lourdes)
├── dce_downloader.py             # Telechargement DCE (Playwright pour PLACE)
├── export_docx.py                # Conversion Markdown -> DOCX charte Almera
├── dc_pdf.py                     # Generation DC1/DC2 en PDF (pymupdf)
├── dc_word.py                    # Pre-remplissage formulaires Word DC1/DC2/DC4
├── notifier.py                   # Envoi emails recapitulatifs (Gmail SMTP)
├── auto_push.py                  # Git push auto vers GitHub (trigger Render)
├── dossier_permanent/            # Pieces admin signees (PDF)
├── formulaires/                  # Formulaires officiels DC1/DC2/DC4 (Word)
├── resultats/                    # Dossiers generes en local
│
└── dashboard/                    # Flask web app (REPO GIT SEPARE)
    ├── app.py                    # Flask + SocketIO + APScheduler
    ├── pipeline_auto.py          # Pipeline: veille -> Go/No-Go -> dossier -> brouillon Gmail
    ├── generateur_render.py      # Generateur complet leger (httpx, pas de dependances lourdes)
    ├── veille_render.py          # Veille legere (BOAMP+TED+RSS+scraping, scoring mots-cles)
    ├── analyse_dce.py            # Go/No-Go intelligent (6 criteres, score 0-100)
    ├── modeles_reponse.py        # Templates par type (Formation, Consulting, Dev, Mixte)
    ├── veille_concurrence.py     # Veille attributions BOAMP (concurrents)
    ├── rappels.py                # Rappels deadlines J-7/J-3/J-1 (email)
    ├── dce_auto.py               # Download DCE HTTP-only (pas de Playwright)
    ├── catalogue_formations.yaml # Copie du catalogue (necessaire sur Render)
    ├── requirements.txt          # Dependances Flask stack
    ├── templates/                # Jinja2 (base, index, ao_liste, ao_detail, kanban, roi, dossiers)
    ├── static/css/style.css      # Styles + dark mode
    ├── ao_pertinents.json        # Base de donnees AO (JSON)
    └── dossiers_generes/         # Dossiers generes sur Render
```

## Deux environnements distincts

### Local (Windows, tache planifiee 8h)
- `python main.py veille` : veille complete avec scoring IA (Claude Haiku)
- `python main.py generer <id>` : generation dossier complet avec toutes les dependances
- Dependances lourdes disponibles: pdfplumber, pymupdf, playwright, python-docx, anthropic SDK
- Genere les DOCX/PDF avec charte Almera (logo, couleurs, sommaire)

### Render (cloud, auto-deploy depuis GitHub)
- Dashboard web: https://ao-hunter-dashboard.onrender.com
- Repo: https://github.com/Mickael7474/ao-hunter-dashboard (branche master)
- Seul le contenu de `dashboard/` est deploye
- Pas de dependances lourdes → versions allGees (veille_render.py, generateur_render.py)
- APScheduler execute la veille toutes les 8h
- Variables d'environnement requises: `ANTHROPIC_API_KEY`, `SMTP_PASSWORD`, `RENDER=true`

## Pipeline automatique (sur Render)

```
Veille auto (8h) : BOAMP API + TED API v3 + Marches-securises RSS + AWS scraping
    ↓
Scoring mots-cles (36 keywords, pas d'IA pour economiser)
    ↓
Deduplication cross-sources (Jaccard sur titre+acheteur)
    ↓
Pipeline auto (si score >= 70%) :
    Go/No-Go (6 criteres, score 0-100)
    ↓ si GO (score >= 60)
    Generation dossier complet (15 pieces, 5 appels Claude API)
    ↓
    Brouillon Gmail (IMAP, jamais d'envoi auto)
    ↓
    Max 1 dossier/jour
```

## Dossier genere (15 pieces)

| # | Fichier | Methode |
|---|---------|---------|
| 01 | analyse_go_no_go.md | Template + scoring 6 criteres |
| 02 | memoire_technique.md | Claude Sonnet (3000+ mots) |
| 03 | lettre_candidature.md | Claude Sonnet (prete a signer) |
| 04 | bpu_dpgf.md + .xlsx | Claude Sonnet + openpyxl |
| 05 | planning_previsionnel.md | Claude Haiku |
| 06 | cv_formateurs.md | Claude Haiku (selection pertinente) |
| 07 | dc1_dc2.md | Template pre-rempli |
| 08 | programme_formation.md | Claude Sonnet + catalogue YAML |
| 09 | references_clients.md | Template (5 fiches adaptees) |
| 11 | acte_engagement.md | Template pre-rempli |
| 12 | dume.md | Template pre-rempli |
| 13 | moyens_techniques.md | Template |
| 14 | checklist_soumission.md | Template recap |
| - | fiche_ao.json | Donnees brutes AO |

## Commandes CLI (local)

```bash
python main.py veille              # Veille complete (BOAMP+PLACE+TED+RSS+AWS)
python main.py veille --auto       # Mode continu
python main.py generer <id>        # Generer dossier pour un AO
python main.py generer --all       # Generer pour tous les AO pertinents (score > seuil)
python main.py rapport             # Rapport des AO detectes
python main.py demo                # Demo avec AO fictif
python auto_push.py                # Push ao_pertinents.json vers GitHub
```

## API REST (dashboard)

```
GET  /api/ao                      # Liste AO avec filtres (?source=BOAMP&score_min=0.5)
GET  /api/ao/<id>                 # Detail AO
PATCH /api/ao/<id>                # Modifier statut, notes
POST /api/ao/<id>/generer         # Lancer generation dossier
GET  /api/stats                   # Statistiques globales
GET  /api/urgents                 # AO avec deadline < 3 jours
GET  /api/pipeline                # Statut pipeline auto
GET  /api/concurrence             # Top concurrents (attributions)
GET  /api/historique-veille       # Tendances veille
GET  /api/deadlines               # Rappels deadlines
POST /api/veille                  # Lancer veille manuelle
GET  /api/veille/status           # Statut scheduler
```

## Donnees entreprise

Toutes les infos Almera sont dans `config.yaml` :
- **SIRET**: 98900455100010
- **NDA**: 11757431975
- **6 formateurs** avec CV detailles, specialites, references
- **23+ references clients** (Havas, Eiffage, Carrefour, Orange, Caisse des Depots, etc.)
- **29 formations** dans `catalogue_formations.yaml` avec programmes heure par heure
- **Certifications**: Qualiopi, RS6776, France Num, Hub France IA, French Tech

## Regles importantes

1. **Ne jamais inventer de noms** de formateurs, references ou certifications
2. **Ne jamais envoyer d'email automatiquement** → uniquement des brouillons Gmail
3. **Max 1 dossier auto/jour** (configurable dans pipeline_auto.py)
4. **TVA exoneree** (art. 261-4-4 CGI) pour les prestations de formation
5. Les fichiers du `dossier_permanent/` sont des PDF signes, ne pas les regenerer
6. Le dashboard est un repo Git separe (`dashboard/`), seul ce dossier va sur Render
7. Preferer les edits aux regenerations completes (ne pas tout recasser)
8. Les pieces admin manquantes : RC Pro (en attente devis) et RS6776 (en attente ~2 mois)

## Stack technique

- **Backend local**: Python 3.14, anthropic SDK, pdfplumber, pymupdf, python-docx, playwright
- **Dashboard**: Flask 3, Flask-SocketIO, Gunicorn + gevent-websocket
- **Scheduler**: APScheduler (BackgroundScheduler, toutes les 8h)
- **API IA**: Claude Sonnet 4 (`claude-sonnet-4-5-20251001`) pour mémoire, lettre, BPU, programme ; Claude Haiku 3.5 (`claude-haiku-3-5-20251001`) pour planning, CV, scoring local
- **Sources veille**: BOAMP API open data, TED API v3, marches-securises.fr RSS, AWS Defense scraping
- **Email**: Gmail SMTP (envoi) + IMAP (brouillons)
- **Excel**: openpyxl (DPGF)
- **Deploy**: Render (auto-deploy on git push), GitHub

## Fichiers de donnees (dashboard/)

| Fichier | Contenu |
|---------|---------|
| ao_pertinents.json | Base AO (id, titre, acheteur, score, statut, dates, source) |
| ao_notes.json | Notes/commentaires par AO |
| dossiers_index.json | Index des dossiers generes |
| reviews.json | Relecture collaborative (statut, commentaires) |
| checklist_etat.json | Etat des checklists de soumission par AO |
| commentaires.json | Commentaires par AO |
| concurrence.json | Attributions BOAMP (concurrents) |
| historique_veille.json | Historique des cycles de veille (90 jours) |
| rappels_envoyes.json | Anti-doublon rappels email |
| pipeline_auto_log.json | Log du pipeline automatique |

## Bugs connus et comportements à éviter

| Date | Fichier concerné | Description | Statut |
|---|---|---|---|
| 2026-04-04 | crm_acheteurs.py, app.py | CRM fiches acheteurs ne s'ouvraient pas au clic : double normalisation de la cle dans get_fiche(). Ajout param is_cle=True. | Resolu |
| 2026-04-04 | estimation_marche.py | Internal Server Error sur detail AO : import top-level de decp_data crashait si module indisponible + structure retournee (fourchette_basse/haute, nb_concurrents_estime, facteurs_hausse) ne matchait pas le template (fourchette[], nb_candidats_estime, facteurs). Normalise les deux chemins DECP et heuristique. | Resolu |

> Cette section est maintenue par Claude au fil des sessions. Ajouter une ligne à chaque bug résolu ou comportement inattendu découvert en prod ou en développement.
