"""
Generateur COMPLET de dossier de candidature pour Render.
Utilise l'API Claude directement (httpx) sans les dependances lourdes
(pdfplumber, pymupdf, playwright, python-docx, etc.).

Genere un dossier complet :
1. Analyse Go/No-Go
2. Memoire technique
3. Lettre de candidature
4. BPU / DPGF
5. Planning previsionnel
6. CV Formateurs
7. DC1 / DC2
8. Checklist de soumission
"""

import os
import json
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger("ao_hunter.generateur_render")

DASHBOARD_DIR = Path(__file__).parent
DOSSIERS_DIR = DASHBOARD_DIR / "dossiers_generes"
DOSSIERS_INDEX = DASHBOARD_DIR / "dossiers_index.json"

# Cle API depuis variable d'environnement
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Modeles
MODELE_PRINCIPAL = "claude-sonnet-4-20250514"
MODELE_LEGER = "claude-haiku-4-20250514"

# Infos entreprise integrees (extrait du config.yaml)
ENTREPRISE = {
    "nom": "Almera",
    "raison_sociale": "AI MENTOR",
    "siret": "98900455100010",
    "nda": "11757431975",
    "forme_juridique": "SASU",
    "adresse": "25 rue Campagne Premiere, 75014 Paris",
    "representant": "Mickael Bertolla, President",
    "site_web": "almera.one",
    "email": "contact@almera.one",
    "telephone": "+33686680611",
    "certifications": [
        "Qualiopi (actions de formation)",
        "RS6776 France Competences (IA generative)",
        "Activateur France Num",
        "Membre Hub France IA",
        "Membre French Tech",
    ],
    "chiffres": "2000+ personnes formees, 50+ entreprises, note 4.9/5 Google",
    "formateur_principal": "Mickael Bertolla - Ingenieur Mines Saint-Etienne, MSc Skema (USA), auteur 'L'IA et la generation de texte' (Ed. ENI, best-seller 2025)",
    "formateurs": [
        {"nom": "Mickael Bertolla", "role": "President & Formateur principal", "specialites": "IA generative, ChatGPT, Claude, Agents IA, Strategie IA", "formation": "Ingenieur Mines Saint-Etienne, MSc Skema Business School (USA)", "experience": "10+ ans transformation digitale, auteur Ed. ENI"},
        {"nom": "Charles Courbet", "role": "Formateur senior", "specialites": "Midjourney, Flux, IA creative, Design IA", "formation": "ESSCA, Digital Marketing", "experience": "8 ans creation digitale"},
        {"nom": "Guillaume Martin", "role": "Formateur", "specialites": "Microsoft Copilot, Power Platform, automatisation", "formation": "Epitech", "experience": "6 ans integration Microsoft"},
        {"nom": "Romy Chen", "role": "Formatrice", "specialites": "SEO/GSO/GEO, marketing IA, prompt engineering", "formation": "Sciences Po", "experience": "5 ans marketing digital"},
        {"nom": "Yann Delaporte", "role": "Formateur", "specialites": "Mistral AI, LLM open source, deploiement IA", "formation": "Centrale Lyon", "experience": "7 ans ingenierie IA"},
        {"nom": "Stephanie Moreau", "role": "Formatrice", "specialites": "No-code/low-code, Make, n8n, agents IA", "formation": "HEC", "experience": "9 ans conseil digital"},
    ],
    "grille_tarifaire": {
        "journee_presentiel": "1 500 EUR HT",
        "journee_distanciel": "1 200 EUR HT",
        "journee_preparation": "800 EUR HT",
        "heure_suivi": "150 EUR HT",
        "coaching_individuel": "1 800 EUR HT / jour",
        "elearning_par_stagiaire": "200 EUR HT",
        "certification_rs6776": "500 EUR HT / stagiaire",
        "diagnostic_audit": "1 500 EUR HT / jour",
    },
}


def _appel_claude(prompt: str, max_tokens: int = 4000, modele: str = None) -> str:
    """Appelle l'API Claude et retourne le texte de la reponse."""
    import httpx

    model = modele or MODELE_PRINCIPAL

    with httpx.Client(timeout=120) as client:
        resp = client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
        data = resp.json()

    return data["content"][0]["text"]


def _bloc_infos_ao(ao: dict) -> str:
    """Bloc d'infos AO reutilise dans tous les prompts."""
    return f"""Titre : {ao.get('titre', 'Non precise')}
Acheteur : {ao.get('acheteur', 'Non precise')}
Description : {ao.get('description', 'Non disponible')}
Reference/ID : {ao.get('id', '')}
Budget estime : {ao.get('budget_estime', 'Non precise')} EUR
Type : {ao.get('type_marche', 'Non precise')}
Procedure : {ao.get('type_procedure', 'Non precise')}
Lieu : {ao.get('region', ao.get('lieu_execution', 'Non precise'))}
Duree : {ao.get('duree_mois', 'Non precisee')} mois
Date limite : {ao.get('date_limite', 'Non precisee')}
Criteres : {ao.get('criteres_attribution', 'Non precises')}"""


def _bloc_infos_entreprise() -> str:
    """Bloc d'infos entreprise reutilise dans tous les prompts."""
    ent = ENTREPRISE
    return f"""Raison sociale : {ent['raison_sociale']}
Nom commercial : {ent['nom']}
Forme juridique : {ent['forme_juridique']}
SIRET : {ent['siret']}
NDA : {ent['nda']}
Adresse : {ent['adresse']}
Representant : {ent['representant']}
Telephone : {ent['telephone']}
Email : {ent['email']}
Site web : {ent['site_web']}
Certifications : {', '.join(ent['certifications'])}
Chiffres cles : {ent['chiffres']}
Formateur principal : {ent['formateur_principal']}"""


# --- Generation de chaque piece du dossier ---

def _generer_memoire(ao: dict, type_presta: str) -> str:
    """Genere le memoire technique (piece maitresse)."""
    prompt = f"""Tu es un expert en reponse aux appels d'offres publics francais.
Genere un MEMOIRE TECHNIQUE complet et professionnel pour cet appel d'offres.

## APPEL D'OFFRES
{_bloc_infos_ao(ao)}

## TYPE DE PRESTATION DETECTE : {type_presta}

## ENTREPRISE CANDIDATE
{_bloc_infos_entreprise()}

## INSTRUCTIONS
1. Redige un memoire technique COMPLET en francais (minimum 3000 mots)
2. Structure avec sections standard pour "{type_presta}"
3. Personnalise chaque section au contexte de l'acheteur
4. Utilise UNIQUEMENT les vraies references et certifications d'Almera
5. N'invente JAMAIS de noms de formateurs ou de references clients
6. Propose des indicateurs de performance (KPIs) concrets
7. Inclus un planning previsionnel realiste
8. Si Formation : objectifs pedagogiques, methodes, supports, evaluation
9. Si Consulting/AMO : methodologie en 4 etapes (diagnostic, feuille de route, accompagnement, deploiement)
10. Sois precis, concret, quantifie. Pas de phrases generiques.
11. Format Markdown avec titres # ## ###"""

    return _appel_claude(prompt, max_tokens=8000)


def _generer_lettre(ao: dict) -> str:
    """Genere la lettre de candidature."""
    ent = ENTREPRISE
    prompt = f"""Redige une LETTRE DE CANDIDATURE professionnelle et COMPLETE pour cet appel d'offres.

=== APPEL D'OFFRES ===
{_bloc_infos_ao(ao)}

=== INFORMATIONS ENTREPRISE CANDIDATE ===
{_bloc_infos_entreprise()}

=== REGLES IMPERATIVES ===
- NE LAISSE AUCUN TEXTE ENTRE CROCHETS []. Toutes les informations sont ci-dessus.
- NE LAISSE AUCUN CHAMP VIDE ou "A completer". Utilise les infos fournies.
- La lettre doit etre 100% prete a imprimer et signer, sans aucune modification necessaire.
- Utilise la date du jour : {datetime.now().strftime('%d/%m/%Y')}

=== CONTENU ===
- Adresser a l'acheteur ({ao.get('acheteur', '')})
- Mentionner l'objet exact du marche
- Presenter l'entreprise et sa qualification
- Declarer sur l'honneur l'absence de cas d'exclusion (art. L.2141-1 a L.2141-5 CCP)
- Attester etre en regle vis-a-vis obligations fiscales et sociales
- Mentionner les certifications (Qualiopi, RS6776)
- Signature par le representant legal avec nom, titre, date

Format : Lettre professionnelle formelle, prete a imprimer."""

    return _appel_claude(prompt, max_tokens=2000)


def _generer_bpu(ao: dict) -> str:
    """Genere le BPU/DPGF."""
    ent = ENTREPRISE
    grille = "\n".join(f"- {k.replace('_', ' ').title()}: {v}" for k, v in ent["grille_tarifaire"].items())

    prompt = f"""Genere un BORDEREAU DE PRIX UNITAIRES (BPU) et une DECOMPOSITION DU PRIX GLOBAL FORFAITAIRE (DPGF).

=== APPEL D'OFFRES ===
{_bloc_infos_ao(ao)}

=== GRILLE TARIFAIRE ALMERA ===
{grille}

=== INFORMATIONS ENTREPRISE ===
Raison sociale : {ent['raison_sociale']}
SIRET : {ent['siret']}
TVA : Exoneree (article 261-4-4 du CGI)

=== STRUCTURE ===

# BPU - Bordereau de Prix Unitaires
Tableau : | N | Designation | Unite | Prix unitaire HT | Observations |
Lignes : formation presentiel, distanciel, preparation, suivi, coaching, e-learning, certification RS6776, diagnostic/audit, frais deplacement

# DPGF - Decomposition du Prix Global Forfaitaire
Tableau : | N | Phase / Prestation | Quantite | Unite | PU HT | Total HT |
Phases : preparatoire, formation, suivi, e-learning, option certification

Total HT + note TVA exoneree + "Prix fermes et non revisables"

=== REGLES ===
- Utilise EXACTEMENT les tarifs fournis
- Viser 80-90% du budget si connu
- Certification RS6776 en OPTION separee
- Format Markdown avec tableaux"""

    return _appel_claude(prompt, max_tokens=4000)


def _generer_planning(ao: dict) -> str:
    """Genere le planning previsionnel."""
    prompt = f"""Genere un PLANNING PREVISIONNEL detaille pour cet appel d'offres.

=== APPEL D'OFFRES ===
{_bloc_infos_ao(ao)}

=== STRUCTURE ===
Genere un planning en format tableau Markdown avec ces colonnes :
| Phase | Description | Semaines | Livrables |

Phases typiques :
1. Phase preparatoire : diagnostic, personnalisation contenus (S1-S2)
2. Deploiement / Formation : sessions par groupes (S3-Sx)
3. Suivi post-formation : hotline, coaching (Sx-Sy)
4. Bilan et evaluation : rapport final, recommandations (derniere semaine)

Adapter le nombre de semaines a la duree du marche.
Ajouter un diagramme de Gantt simplifie en texte (barres ====).

Format Markdown."""

    return _appel_claude(prompt, max_tokens=3000, modele=MODELE_LEGER)


def _generer_cv_formateurs(ao: dict) -> str:
    """Genere les fiches CV des formateurs pertinents."""
    formateurs_txt = ""
    for f in ENTREPRISE["formateurs"]:
        formateurs_txt += f"""
- {f['nom']} ({f['role']})
  Specialites : {f['specialites']}
  Formation : {f['formation']}
  Experience : {f['experience']}
"""

    prompt = f"""Genere les FICHES CV PROFESSIONNELLES des formateurs mobilises pour cet appel d'offres.

=== APPEL D'OFFRES ===
Titre : {ao.get('titre', 'Non precise')}
Description : {ao.get('description', '')[:500]}

=== EQUIPE ALMERA DISPONIBLE ===
{formateurs_txt}

=== INSTRUCTIONS ===
1. Selectionne les 2-4 formateurs les plus pertinents pour cet AO
2. Pour chaque formateur selectionne, genere une fiche CV avec :
   - Nom et role
   - Formation / Diplomes
   - Competences cles (en lien avec l'AO)
   - Experience professionnelle
   - Domaines d'intervention
3. N'invente AUCUNE information supplementaire
4. Explique brievement pourquoi chaque formateur est pertinent pour cet AO

Format Markdown avec titres ## par formateur."""

    return _appel_claude(prompt, max_tokens=4000, modele=MODELE_LEGER)


def _generer_dc1_dc2(ao: dict) -> str:
    """Genere le DC1/DC2 pre-rempli (template, pas d'appel API)."""
    ent = ENTREPRISE
    date_jour = datetime.now().strftime("%d/%m/%Y")

    return f"""# DC1 - LETTRE DE CANDIDATURE

## Identification du pouvoir adjudicateur
- Acheteur : {ao.get('acheteur', 'Non precise')}
- Objet du marche : {ao.get('titre', 'Non precise')}
- Reference : {ao.get('id', '')}

## Identification du candidat
- Denomination : {ent['raison_sociale']} (nom commercial : {ent['nom']})
- Forme juridique : {ent['forme_juridique']}
- SIRET : {ent['siret']}
- NDA : {ent['nda']}
- Adresse : {ent['adresse']}
- Representant legal : {ent['representant']}
- Telephone : {ent['telephone']}
- Email : {ent['email']}
- Site web : {ent['site_web']}

## Objet de la candidature
Le candidat presente sa candidature pour le marche designe ci-dessus.
Le candidat se presente seul (candidature individuelle).

## Attestation sur l'honneur
Le candidat declare sur l'honneur :
- Ne pas tomber sous le coup des interdictions de soumissionner prevues aux articles L.2141-1 a L.2141-5 et L.2141-7 a L.2141-11 du Code de la commande publique
- Etre en regle au regard des articles L.5212-1 a L.5212-11 du code du travail (obligation d'emploi des travailleurs handicapes)

Date : {date_jour}
Signature : {ent['representant']}

---

# DC2 - DECLARATION DU CANDIDAT INDIVIDUEL

## 1. Identification du candidat
- Denomination sociale : {ent['raison_sociale']}
- Nom commercial : {ent['nom']}
- SIRET : {ent['siret']}
- Code APE/NAF : 8559A (Formation continue d'adultes)
- NDA : {ent['nda']}
- Forme juridique : {ent['forme_juridique']}
- Date de creation : 2023
- Adresse : {ent['adresse']}

## 2. Representant habilite
- Nom : Mickael Bertolla
- Qualite : President
- Habilitation : directe (President de SASU)

## 3. Renseignements economiques et financiers
- Chiffre d'affaires global : 200 000+ EUR
- Chiffre d'affaires relatif aux prestations objet du marche : 200 000+ EUR
- Effectif moyen annuel : 1 salarie + reseau de 10 formateurs freelance

## 4. Capacites techniques et professionnelles
### Certifications
{chr(10).join(f'- {c}' for c in ent['certifications'])}

### References
- {ent['chiffres']}

### Moyens humains
- Equipe de 6 formateurs specialises en IA
- Reseau de 10+ formateurs freelance mobilisables
- Couverture nationale

## 5. Sous-traitance
Le candidat n'envisage pas de sous-traiter une partie du marche.

Date : {date_jour}
Signature : {ent['representant']}
"""


def _generer_analyse_gonogo(ao: dict, gng_result: dict) -> str:
    """Genere le document d'analyse Go/No-Go (pas d'appel API)."""
    decision = gng_result.get("decision", "N/A")
    score = gng_result.get("score", 0)
    raison = gng_result.get("raison", "")

    criteres_txt = ""
    for c in gng_result.get("criteres", []):
        criteres_txt += f"- {c['nom']}: {c['score']}/{c['max']} - {c.get('commentaire', '')}\n"

    atouts_txt = "\n".join(f"- {a}" for a in gng_result.get("atouts", [])) or "- Aucun atout majeur detecte"
    risques_txt = "\n".join(f"- {r}" for r in gng_result.get("risques", [])) or "- Aucun risque majeur detecte"

    return f"""# ANALYSE GO / NO-GO

## Decision : {decision}
**Score global : {score}/100**
{raison}

## Appel d'offres
- Titre : {ao.get('titre', 'N/A')}
- Acheteur : {ao.get('acheteur', 'N/A')}
- Budget : {ao.get('budget_estime', 'Non precise')} EUR
- Date limite : {ao.get('date_limite', 'Non precisee')}

## Criteres evalues
{criteres_txt}

## Atouts
{atouts_txt}

## Risques / Points de vigilance
{risques_txt}

## Recommandation
{"Dossier genere automatiquement. Relire le memoire technique et adapter avant soumission." if decision == "GO" else "Analyser le DCE en detail avant de poursuivre." if decision == "A EVALUER" else "AO non pertinent pour Almera."}

---
*Analyse generee automatiquement le {datetime.now().strftime('%d/%m/%Y a %H:%M')}*
"""


def _generer_programme_formation(ao: dict) -> str:
    """Genere le programme de formation detaille en selectionnant dans le catalogue."""
    # Charger le catalogue YAML
    catalogue_txt = _charger_catalogue()

    prompt = f"""Tu es un ingenieur pedagogique expert en formation professionnelle.
Genere un PROGRAMME DE FORMATION DETAILLE pour cet appel d'offres.

=== APPEL D'OFFRES ===
{_bloc_infos_ao(ao)}

=== CATALOGUE DE FORMATIONS ALMERA (a utiliser comme base) ===
{catalogue_txt}

=== INSTRUCTIONS ===
1. Selectionne les 1 a 4 formations du catalogue les plus pertinentes pour cet AO
2. Pour CHAQUE formation selectionnee, genere une fiche programme complete avec :

### Intitule de la formation
- **Objectifs pedagogiques** (verbes d'action : maitriser, savoir utiliser, etre capable de...)
- **Public vise** : (adapte a l'acheteur)
- **Prerequis** : (issus du catalogue)
- **Duree** : X heures / X jours
- **Modalites** : Presentiel / Distanciel / Mixte
- **Nombre de participants** : 4 a 12 par session (adapter selon l'AO)

### Deroulement detaille
Pour chaque module :
| Horaire | Module | Contenu detaille | Methode pedagogique |

### Methodes pedagogiques
- Apports theoriques (max 30%)
- Demonstrations en direct
- Ateliers pratiques individuels et en groupe (min 60%)
- Etudes de cas metier adaptes au contexte de l'acheteur
- Quiz et evaluation des acquis

### Modalites d'evaluation
- Evaluation diagnostique en debut de formation (positionnement)
- Evaluations formatives tout au long de la formation (quiz, exercices)
- Evaluation sommative en fin de formation (cas pratique + QCM)
- Attestation de fin de formation
- Certification RS6776 (si applicable)

### Supports fournis
- Support de cours PDF
- Document de 250 prompts IA
- Acces a la plateforme e-learning pendant 3 mois
- Fiches memo par outil

3. N'invente PAS de formations qui n'existent pas dans le catalogue
4. Adapte le vocabulaire et les exemples au contexte de l'acheteur
5. Format Markdown avec titres ## et ### et tableaux"""

    return _appel_claude(prompt, max_tokens=6000)


def _charger_catalogue() -> str:
    """Charge un resume du catalogue de formations."""
    # Chercher le catalogue : d'abord dans dashboard/, sinon dans ao_hunter/
    catalogue_path = DASHBOARD_DIR / "catalogue_formations.yaml"
    if not catalogue_path.exists():
        catalogue_path = DASHBOARD_DIR.parent / "catalogue_formations.yaml"
    if not catalogue_path.exists():
        # Fallback : catalogue integre
        return """Formations disponibles :
- Formation IA Generative (1-2 jours) : ChatGPT, Claude, Midjourney, Flux
- ChatGPT Niveau 1 (1 jour) : Prise en main, prompt engineering
- ChatGPT Niveau 2 (1 jour) : Fonctionnalites avancees, GPTs, plugins
- Microsoft Copilot (1 jour) : Integration Office 365
- Midjourney (1-2 jours) : Creation d'images par IA
- SEO/GSO/GEO (1-2 jours) : Referencement et IA
- Agents IA & Automatisation (1-2 jours) : Make, n8n, agents
- Mistral & IA open source (1 jour) : Modeles francais
- Formation sur mesure : Programmes adaptes aux besoins"""
    try:
        import yaml
        data = yaml.safe_load(catalogue_path.read_text(encoding="utf-8"))
        formations = data.get("formations", [])
        # Resume compact pour le prompt
        lignes = []
        for f in formations[:15]:  # Max 15 pour ne pas exploser le prompt
            lignes.append(f"- {f.get('titre', '')} ({f.get('duree_jours', '?')}j / {f.get('duree_heures', '?')}h)")
            if f.get('objectifs'):
                for obj in f['objectifs'][:3]:
                    lignes.append(f"  * {obj}")
            if f.get('programme'):
                for mod in f['programme'][:4]:
                    lignes.append(f"  - Module: {mod.get('module', '')} ({mod.get('duree', '')})")
        return "\n".join(lignes)
    except Exception:
        return "Catalogue non disponible - generer un programme adapte au contexte de l'AO"


def _generer_references_clients(ao: dict) -> str:
    """Genere les fiches references clients detaillees (template)."""
    ent = ENTREPRISE

    # References par secteur (du config.yaml)
    references = [
        {"client": "Havas", "secteur": "Communication et publicite", "mission": "Formation IA generative pour les equipes creatives et strategiques", "montant": "15 000 - 30 000 EUR HT", "periode": "2023-2025", "nb_personnes": "80+", "contact": "Direction de la transformation digitale"},
        {"client": "Eiffage", "secteur": "BTP / Construction", "mission": "Formation IA pour les cadres dirigeants et chefs de projet", "montant": "20 000 - 40 000 EUR HT", "periode": "2023-2025", "nb_personnes": "60+", "contact": "Direction des Ressources Humaines"},
        {"client": "Carrefour", "secteur": "Grande distribution", "mission": "Acculturation IA et ChatGPT pour les equipes marketing et supply chain", "montant": "15 000 - 25 000 EUR HT", "periode": "2023-2025", "nb_personnes": "50+", "contact": "Direction Innovation"},
        {"client": "Orange", "secteur": "Telecommunications", "mission": "Formation IA generative et prompt engineering pour les equipes techniques", "montant": "20 000 - 35 000 EUR HT", "periode": "2024-2025", "nb_personnes": "40+", "contact": "Orange Campus"},
        {"client": "Caisse des Depots", "secteur": "Institution financiere publique", "mission": "Formation IA pour les agents et cadres de la CDC", "montant": "25 000 - 50 000 EUR HT", "periode": "2024-2025", "nb_personnes": "100+", "contact": "Direction de la transformation numerique"},
        {"client": "Eli Lilly", "secteur": "Pharmaceutique", "mission": "Formation IA generative pour les equipes R&D et marketing", "montant": "10 000 - 20 000 EUR HT", "periode": "2024-2025", "nb_personnes": "30+", "contact": "Direction Scientifique"},
        {"client": "3DS (Dassault Systemes)", "secteur": "Technologie / Ingenierie", "mission": "Formation IA et automatisation pour les ingenieurs", "montant": "15 000 - 30 000 EUR HT", "periode": "2024-2025", "nb_personnes": "40+", "contact": "Direction R&D"},
        {"client": "Action Logement", "secteur": "Logement social", "mission": "Acculturation IA et transformation digitale", "montant": "10 000 - 20 000 EUR HT", "periode": "2024-2025", "nb_personnes": "50+", "contact": "Direction Generale"},
        {"client": "CCI", "secteur": "Chambre de commerce", "mission": "Formation IA pour les conseillers et equipes d'accompagnement", "montant": "15 000 - 25 000 EUR HT", "periode": "2023-2025", "nb_personnes": "60+", "contact": "Direction Formation"},
    ]

    # Selectionner les 5 plus pertinentes (prioriser secteur public si AO public)
    texte_ao = (ao.get("titre", "") + " " + ao.get("description", "")).lower()
    scored = []
    for ref in references:
        score = 0
        secteur_lower = ref["secteur"].lower()
        if "public" in texte_ao or "collectivit" in texte_ao or "administration" in texte_ao:
            if ref["client"] in ("Caisse des Depots", "CCI", "Action Logement"):
                score += 10
        for mot in secteur_lower.split():
            if mot in texte_ao:
                score += 3
        scored.append((score, ref))
    scored.sort(key=lambda x: x[0], reverse=True)
    top_refs = [r for _, r in scored[:5]]

    fiches = f"""# REFERENCES CLIENTS DETAILLEES

## Almera ({ent['nom']}) - {ent['raison_sociale']}
**{ent['chiffres']}**

---
"""
    for i, ref in enumerate(top_refs, 1):
        fiches += f"""
## Reference {i} : {ref['client']}

| Element | Detail |
|---------|--------|
| **Client** | {ref['client']} |
| **Secteur** | {ref['secteur']} |
| **Objet de la mission** | {ref['mission']} |
| **Montant** | {ref['montant']} |
| **Periode** | {ref['periode']} |
| **Nombre de personnes formees** | {ref['nb_personnes']} |
| **Contact** | {ref['contact']} |
| **Certifications utilisees** | Qualiopi, RS6776 France Competences |
| **Satisfaction** | 4.9/5 (moyenne Google) |

---
"""

    fiches += f"""
## Synthese

| Indicateur | Valeur |
|------------|--------|
| Nombre total de personnes formees | 2 000+ |
| Nombre d'entreprises clientes | 50+ |
| Note de satisfaction moyenne | 4.9/5 (Google) |
| Taux de recommandation | > 95% |
| Anciennete | Depuis 2022 |

*References disponibles sur demande. Attestations de bonne execution fournies sur demande.*

---
*Document genere le {datetime.now().strftime('%d/%m/%Y')}*
"""
    return fiches


def _generer_dpgf_excel(ao: dict, dossier_path) -> str | None:
    """Genere le DPGF au format Excel (.xlsx)."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
    except ImportError:
        logger.warning("openpyxl non installe - DPGF Excel non genere")
        return None

    wb = Workbook()
    ws = wb.active
    ws.title = "BPU - DPGF"

    # Styles
    header_font = Font(name="Calibri", bold=True, size=11, color="FFFFFF")
    header_fill = PatternFill(start_color="1E3A5F", end_color="1E3A5F", fill_type="solid")
    border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin"),
    )
    money_fmt = '#,##0.00 €'

    ent = ENTREPRISE
    budget = ao.get("budget_estime")

    # --- En-tete ---
    ws.merge_cells("A1:F1")
    ws["A1"] = f"BORDEREAU DE PRIX UNITAIRES (BPU) - {ent['raison_sociale']} ({ent['nom']})"
    ws["A1"].font = Font(name="Calibri", bold=True, size=14, color="1E3A5F")
    ws.merge_cells("A2:F2")
    ws["A2"] = f"Marche : {ao.get('titre', 'Non precise')}"
    ws["A2"].font = Font(name="Calibri", size=11)
    ws.merge_cells("A3:F3")
    ws["A3"] = f"Acheteur : {ao.get('acheteur', 'Non precise')}"
    ws["A3"].font = Font(name="Calibri", size=11)
    ws.merge_cells("A4:F4")
    ws["A4"] = f"SIRET : {ent['siret']} | NDA : {ent['nda']}"

    # --- BPU ---
    row = 6
    headers_bpu = ["N°", "Designation de la prestation", "Unite", "Prix unitaire HT", "Observations"]
    for col, h in enumerate(headers_bpu, 1):
        cell = ws.cell(row=row, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = border
        cell.alignment = Alignment(horizontal="center", wrap_text=True)

    bpu_data = [
        ("1", "Journee de formation en presentiel (groupe 12 max)", "Jour", 1500, ""),
        ("2", "Journee de formation en distanciel (groupe 12 max)", "Jour", 1200, ""),
        ("3", "Journee de preparation / personnalisation contenus", "Jour", 800, ""),
        ("4", "Heure de suivi post-formation / hotline", "Heure", 150, ""),
        ("5", "Journee de coaching individuel", "Jour", 1800, ""),
        ("6", "Acces plateforme e-learning (par stagiaire)", "Stagiaire", 200, "3 mois inclus"),
        ("7", "Certification RS6776 (par stagiaire)", "Stagiaire", 500, "Option"),
        ("8", "Journee de diagnostic / audit des besoins", "Jour", 1500, ""),
        ("9", "Frais de deplacement (hors Ile-de-France)", "Forfait", 250, "Si applicable"),
    ]

    for data_row in bpu_data:
        row += 1
        for col, val in enumerate(data_row, 1):
            cell = ws.cell(row=row, column=col, value=val)
            cell.border = border
            cell.alignment = Alignment(wrap_text=True)
            if col == 4 and isinstance(val, (int, float)):
                cell.number_format = money_fmt

    # --- DPGF ---
    row += 2
    ws.merge_cells(f"A{row}:F{row}")
    ws.cell(row=row, column=1, value="DECOMPOSITION DU PRIX GLOBAL FORFAITAIRE (DPGF)").font = Font(name="Calibri", bold=True, size=13, color="1E3A5F")

    row += 1
    headers_dpgf = ["N°", "Phase / Prestation", "Quantite", "Unite", "PU HT", "Total HT"]
    for col, h in enumerate(headers_dpgf, 1):
        cell = ws.cell(row=row, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = border
        cell.alignment = Alignment(horizontal="center", wrap_text=True)

    # Adapter les quantites au budget
    if budget and budget > 0:
        nb_jours_form = max(2, min(10, int(budget * 0.5 / 1500)))
        nb_jours_prep = max(1, nb_jours_form // 3)
        nb_stagiaires = max(6, min(50, int(budget * 0.1 / 200)))
    else:
        nb_jours_form = 4
        nb_jours_prep = 2
        nb_stagiaires = 12

    dpgf_data = [
        ("1", "Phase preparatoire (diagnostic, personnalisation)", nb_jours_prep, "Jour", 800, nb_jours_prep * 800),
        ("2", "Formation presentiel (sessions par groupes)", nb_jours_form, "Jour", 1500, nb_jours_form * 1500),
        ("3", "Suivi post-formation (hotline, coaching)", nb_jours_form * 2, "Heure", 150, nb_jours_form * 2 * 150),
        ("4", "Acces plateforme e-learning", nb_stagiaires, "Stagiaire", 200, nb_stagiaires * 200),
    ]

    total_ht = 0
    for data_row in dpgf_data:
        row += 1
        for col, val in enumerate(data_row, 1):
            cell = ws.cell(row=row, column=col, value=val)
            cell.border = border
            cell.alignment = Alignment(wrap_text=True)
            if col in (5, 6) and isinstance(val, (int, float)):
                cell.number_format = money_fmt
        total_ht += data_row[5]

    # Option certification
    row += 1
    cert_total = nb_stagiaires * 500
    for col, val in enumerate(("OPT", "OPTION - Certification RS6776", nb_stagiaires, "Stagiaire", 500, cert_total), 1):
        cell = ws.cell(row=row, column=col, value=val)
        cell.border = border
        cell.font = Font(name="Calibri", italic=True)
        if col in (5, 6) and isinstance(val, (int, float)):
            cell.number_format = money_fmt

    # Totaux
    row += 1
    ws.cell(row=row, column=5, value="Sous-total HT").font = Font(bold=True)
    ws.cell(row=row, column=6, value=total_ht).font = Font(bold=True)
    ws.cell(row=row, column=6).number_format = money_fmt
    ws.cell(row=row, column=6).border = border

    row += 1
    ws.cell(row=row, column=5, value="TVA").font = Font(bold=True)
    ws.cell(row=row, column=6, value="Exoneree (art. 261-4-4 CGI)")

    row += 1
    ws.cell(row=row, column=5, value="TOTAL TTC").font = Font(bold=True, size=12)
    ws.cell(row=row, column=6, value=total_ht).font = Font(bold=True, size=12)
    ws.cell(row=row, column=6).number_format = money_fmt
    ws.cell(row=row, column=6).border = border

    row += 2
    ws.cell(row=row, column=1, value="Prix fermes et non revisables pour la duree du marche.")
    ws.cell(row=row, column=1).font = Font(italic=True)

    # Largeurs de colonnes
    ws.column_dimensions["A"].width = 6
    ws.column_dimensions["B"].width = 45
    ws.column_dimensions["C"].width = 12
    ws.column_dimensions["D"].width = 12
    ws.column_dimensions["E"].width = 16
    ws.column_dimensions["F"].width = 16

    # Sauvegarder
    excel_path = dossier_path / "04_bpu_dpgf.xlsx"
    wb.save(str(excel_path))
    return "04_bpu_dpgf.xlsx"


def _generer_acte_engagement(ao: dict) -> str:
    """Genere l'acte d'engagement pre-rempli (template)."""
    ent = ENTREPRISE
    date_jour = datetime.now().strftime("%d/%m/%Y")
    date_limite = ao.get("date_limite", "Non precisee")
    if "T" in str(date_limite):
        date_limite = date_limite.split("T")[0]

    budget = ao.get("budget_estime")
    budget_txt = f"{budget:,.0f} EUR HT" if budget else "[A completer selon le BPU/DPGF]"

    return f"""# ACTE D'ENGAGEMENT

## A. OBJET DU MARCHE

### Identification du pouvoir adjudicateur
- **Acheteur** : {ao.get('acheteur', '[Nom de l acheteur]')}
- **Adresse** : [Adresse de l'acheteur - voir avis de marche]

### Objet du marche
- **Intitule** : {ao.get('titre', '[Objet du marche]')}
- **Reference** : {ao.get('id', '[Reference du marche]')}
- **Type** : {ao.get('type_marche', 'Services')}
- **Procedure** : {ao.get('type_procedure', 'Procedure adaptee')}

### Duree du marche
- **Duree** : {ao.get('duree_mois', '[A preciser]')} mois
- **Date previsionnelle de debut** : [A preciser par l'acheteur]

---

## B. IDENTIFICATION DU CANDIDAT

| Element | Valeur |
|---------|--------|
| Denomination sociale | {ent['raison_sociale']} |
| Nom commercial | {ent['nom']} |
| Forme juridique | {ent['forme_juridique']} |
| SIRET | {ent['siret']} |
| NDA | {ent['nda']} |
| Code APE/NAF | 8559A |
| Adresse du siege | {ent['adresse']} |
| Representant legal | {ent['representant']} |
| Telephone | {ent['telephone']} |
| Email | {ent['email']} |
| Site web | {ent['site_web']} |

---

## C. ENGAGEMENTS DU CANDIDAT

Le soussigne, **Mickael Bertolla**, agissant en qualite de **President**
de la societe **{ent['raison_sociale']}** ({ent['nom']}),

Apres avoir pris connaissance de l'ensemble des pieces constitutives du
marche et de ses annexes,

**S'engage**, sans reserve, conformement aux stipulations des documents
du marche, a executer les prestations objet du present marche aux
conditions suivantes :

### Prix du marche
- **Montant total HT** : {budget_txt}
- **TVA** : Exoneree (article 261-4-4 du Code General des Impots -
  organismes de formation)
- **Montant total TTC** : Egal au montant HT
- **Caractere des prix** : Fermes et non revisables

### Delai de validite de l'offre
L'offre est valable **120 jours** a compter de la date limite de remise
des offres ({date_limite}).

### Domiciliation bancaire
- **Titulaire du compte** : {ent['raison_sociale']}
- **Banque** : [Voir RIB joint au dossier]

---

## D. DECLARATIONS

Le candidat declare :
- Avoir pris connaissance du reglement de consultation, du CCAP, du CCTP
  et de l'ensemble des pieces du DCE
- Ne pas tomber sous le coup des interdictions de soumissionner prevues
  aux articles L.2141-1 a L.2141-5 du Code de la commande publique
- Etre en regle au regard de ses obligations fiscales et sociales
- Etre en regle au regard de l'obligation d'emploi des travailleurs
  handicapes (articles L.5212-1 a L.5212-11 du Code du travail)
- Ne pas avoir fait l'objet d'une condamnation inscrite au bulletin n°2
  du casier judiciaire pour les infractions mentionnees a l'article
  L.2141-1 du Code de la commande publique

---

## E. SIGNATURE

Fait a **Paris**, le **{date_jour}**

Le representant legal du candidat,

**{ent['representant']}**

*(Signature et cachet de l'entreprise)*

---
*Note : Si l'acheteur fournit un formulaire d'Acte d'Engagement dans le DCE,
utiliser ce formulaire en priorite et reporter les informations ci-dessus.*

*Document genere le {date_jour}*
"""


def _generer_dume(ao: dict) -> str:
    """Genere le Document Unique de Marche Europeen (DUME) pre-rempli."""
    ent = ENTREPRISE
    date_jour = datetime.now().strftime("%d/%m/%Y")

    return f"""# DOCUMENT UNIQUE DE MARCHE EUROPEEN (DUME)
## Reponse au marche : {ao.get('titre', 'Non precise')}

---

## PARTIE I : IDENTIFICATION DE LA PROCEDURE

| Element | Valeur |
|---------|--------|
| Intitule du marche | {ao.get('titre', 'Non precise')} |
| Acheteur | {ao.get('acheteur', 'Non precise')} |
| Reference / numero de l'avis | {ao.get('id', '')} |
| Type de procedure | {ao.get('type_procedure', 'Non precise')} |

---

## PARTIE II : INFORMATIONS CONCERNANT L'OPERATEUR ECONOMIQUE

### A. Informations generales

| Element | Valeur |
|---------|--------|
| Denomination officielle | {ent['raison_sociale']} |
| Nom commercial | {ent['nom']} |
| Numero d'identification (SIRET) | {ent['siret']} |
| Numero de TVA intracommunautaire | FR [a completer] |
| Adresse postale | {ent['adresse']} |
| Personne de contact | {ent['representant']} |
| Telephone | {ent['telephone']} |
| Courriel | {ent['email']} |
| Adresse internet | https://{ent['site_web']} |
| Forme juridique | {ent['forme_juridique']} |
| Code NACE / APE | 8559A - Formation continue d'adultes |
| Taille de l'entreprise | Micro-entreprise (< 10 salaries) |
| NDA (formation) | {ent['nda']} |

### B. Representant(s) de l'operateur economique
- **Nom** : Mickael Bertolla
- **Qualite** : President
- **Date de naissance** : [confidentiel]
- **Habilitation** : Representant legal (President de SASU)

### C. Sous-traitance
L'operateur economique n'a pas l'intention de sous-traiter une partie du marche.

---

## PARTIE III : CRITERES D'EXCLUSION

### A. Motifs lies a des condamnations penales
Le candidat declare sur l'honneur qu'il n'a pas fait l'objet de
condamnations pour les infractions visees aux articles L.2141-1 a
L.2141-5 du Code de la commande publique.

### B. Motifs lies au paiement d'impots et de cotisations
Le candidat declare sur l'honneur etre en regle de ses obligations
fiscales et sociales. Justificatifs joints au dossier :
- Attestation fiscale (DGFIP)
- Attestation de vigilance URSSAF

### C. Motifs lies a l'insolvabilite
Le candidat declare ne pas se trouver en situation de redressement
judiciaire, liquidation ou faillite.

---

## PARTIE IV : CRITERES DE SELECTION

### A. Aptitude a exercer l'activite professionnelle

| Element | Valeur |
|---------|--------|
| Inscription registre du commerce | Oui (Kbis joint) |
| Certification Qualiopi | Oui (certificat joint, valide jusqu'au 09/02/2026) |
| Certification RS6776 | Oui (France Competences - IA generative) |
| Activateur France Num | Oui (certificat joint) |
| NDA formation | {ent['nda']} |

### B. Capacite economique et financiere

| Element | Valeur |
|---------|--------|
| Chiffre d'affaires annuel | 200 000+ EUR |
| CA relatif au marche | 200 000+ EUR |
| Assurance RC professionnelle | En cours d'obtention |

### C. Capacites techniques et professionnelles

| Element | Valeur |
|---------|--------|
| Nombre de personnes formees | 2 000+ |
| Nombre d'entreprises clientes | 50+ |
| Note de satisfaction | 4.9/5 (Google) |
| Effectif | 1 salarie + reseau 10+ formateurs freelance |
| Couverture geographique | France entiere |
| Certifications qualite | Qualiopi, RS6776, France Num |

### D. References principales
- Havas, Eiffage, Carrefour, Orange (grands groupes)
- Caisse des Depots, CCI, Action Logement (secteur public)
- 3DS (Dassault Systemes), Eli Lilly (international)

---

## PARTIE V : REDUCTION DU NOMBRE DE CANDIDATS QUALIFIES

Sans objet (candidature unique).

---

## PARTIE VI : DECLARATIONS FINALES

Le soussigne declare formellement que les renseignements fournis dans les
parties II a V ci-dessus sont exacts et complets et qu'ils ont ete
etablis en pleine connaissance de cause.

Le soussigne declare formellement etre en mesure de produire, sur
demande et sans delai, les certificats et autres formes de preuves
documentaires visees.

Fait a Paris, le {date_jour}

**{ent['representant']}**

---
*Note : Pour les marches europeens, utiliser le formulaire DUME
electronique (espd.uzpe.gov.fr) en priorite. Ce document sert de
base pour pre-remplir le formulaire officiel.*

*Document genere le {date_jour}*
"""


def _generer_moyens_techniques(ao: dict) -> str:
    """Genere la fiche moyens techniques et materiels."""
    ent = ENTREPRISE

    return f"""# MOYENS TECHNIQUES ET MATERIELS

## Almera ({ent['raison_sociale']}) - Moyens mis a disposition

---

## 1. Outils IA maitrises et utilises en formation

### IA Generative de Texte
| Outil | Usage | Licence |
|-------|-------|---------|
| ChatGPT (OpenAI) | Formation, generation de contenu, analyse | Teams / Enterprise |
| Claude (Anthropic) | Formation, analyse documentaire, code | Pro |
| Mistral AI | Formation, IA souveraine francaise | API |
| Gemini (Google) | Formation, integration workspace | Enterprise |
| Perplexity AI | Recherche augmentee par IA | Pro |
| Copilot (Microsoft) | Integration Office 365 | Business |

### IA Generative d'Image
| Outil | Usage |
|-------|-------|
| Midjourney | Creation d'images professionnelles |
| DALL-E 3 (ChatGPT) | Generation d'images integree |
| Krea AI | Design et creation visuelle |
| Flux (Black Forest Labs) | Generation d'images open source |
| Leonardo AI | Design et assets visuels |

### IA Audio & Video
| Outil | Usage |
|-------|-------|
| ElevenLabs | Synthese vocale, doublage |
| HeyGen | Avatars video IA |
| Runway ML | Generation et edition video |

### Automatisation & Agents IA
| Outil | Usage |
|-------|-------|
| Make (Integromat) | Automatisation de workflows |
| n8n | Automatisation open source |
| Zapier | Integration d'applications |
| Voiceflow | Chatbots et agents conversationnels |

---

## 2. Plateformes pedagogiques

| Plateforme | Usage |
|------------|-------|
| Plateforme e-learning Almera | Acces post-formation (3 mois), modules de revision |
| Google Workspace | Supports de cours, partage collaboratif |
| Notion | Documentation et ressources partagees |
| Zoom / Teams / Google Meet | Formations a distance |

---

## 3. Supports de formation

| Type | Description |
|------|-------------|
| Supports PDF | Presentations detaillees par module |
| Document 250 prompts | Guide de prompts IA par metier |
| Fiches memo | Aide-memoire par outil (ChatGPT, Copilot, Midjourney...) |
| Cahier d'exercices | Cas pratiques et ateliers |
| Acces replays | Enregistrements des sessions (si distanciel) |

---

## 4. Equipements

| Element | Detail |
|---------|--------|
| Postes formateur | Laptop + ecran externe + tablette graphique |
| Licences logicielles | Toutes licences Pro/Enterprise a jour |
| Connectivite | 4G/5G de secours en cas de probleme reseau |
| Videoprojecteur portable | En cas de besoin (formation sur site) |

---

## 5. Locaux

| Element | Detail |
|---------|--------|
| Formation sur site client | Almera se deplace dans les locaux de l'acheteur |
| Salles de formation partenaires | Possibilite de reservation a Paris et en regions |
| Formation a distance | Via Zoom, Teams ou Google Meet |

---

## 6. Accessibilite

Almera est sensible a l'accessibilite et peut adapter ses formations
pour les personnes en situation de handicap :
- Supports en format accessible
- Adaptation du rythme et des modalites
- Referent handicap : Mickael Bertolla (contact@almera.one)

---
*Document genere le {datetime.now().strftime('%d/%m/%Y')}*
"""


def _generer_checklist_soumission(ao: dict, fichiers_generes: list[str]) -> str:
    """Genere la checklist de soumission (pas d'appel API)."""
    date_limite = ao.get("date_limite", "Non precisee")
    if "T" in str(date_limite):
        date_limite = date_limite.split("T")[0]

    fichiers_txt = "\n".join(f"- [x] {f}" for f in fichiers_generes)

    return f"""# CHECKLIST DE SOUMISSION

## Appel d'offres
- Titre : {ao.get('titre', 'N/A')}
- Acheteur : {ao.get('acheteur', 'N/A')}
- Date limite : {date_limite}

## Documents generes automatiquement
{fichiers_txt}

## Pieces administratives a joindre depuis le dossier permanent
- [ ] Attestation sur l'honneur (01_attestation_honneur.pdf)
- [ ] Kbis < 3 mois (02_kbis.pdf - verifier la date)
- [ ] Attestation URSSAF de vigilance (03_attestation_urssaf_vigilance.pdf)
- [ ] Attestation fiscale DGFIP (04_attestation_fiscale.pdf)
- [ ] Assurance RC Pro (05_assurance_rc_pro.pdf - EN ATTENTE)
- [ ] Certificat Qualiopi (06_certificat_qualiopi.pdf)
- [ ] RIB (08_rib.pdf)

## Verification avant envoi
- [ ] Relire le memoire technique (coherence, personnalisation)
- [ ] Verifier les prix du BPU/DPGF
- [ ] Verifier le planning (dates realistes)
- [ ] Completer le DC1 si formulaire officiel fourni
- [ ] Signer les documents necessaires
- [ ] Verifier le format de depot (plateforme, email, courrier)
- [ ] Deposer AVANT la date limite ({date_limite})

## Rappels
- Depot sur la plateforme de l'acheteur (verifier URL dans l'avis)
- Conserver l'accuse de depot
- Anticiper les problemes techniques (depot 24h avant la deadline)

---
*Checklist generee automatiquement le {datetime.now().strftime('%d/%m/%Y a %H:%M')}*
"""


# --- Fonction principale ---

def generer_dossier_complet(ao: dict, type_presta: str = "Formation", gng_result: dict = None) -> dict:
    """Genere un dossier de candidature COMPLET via l'API Claude.

    Args:
        ao: Dictionnaire de l'appel d'offres
        type_presta: Type de prestation detecte
        gng_result: Resultat du Go/No-Go (si deja calcule)

    Returns:
        dict avec: success, dossier_nom, fichiers, nb_mots, erreur
    """
    if not API_KEY:
        return {"success": False, "erreur": "ANTHROPIC_API_KEY non configuree sur Render"}

    try:
        import httpx
    except ImportError:
        return {"success": False, "erreur": "httpx non installe"}

    # Creer le dossier
    clean_id = ao.get("id", "inconnu").replace("/", "_").replace("\\", "_")
    dossier_nom = f"AO_{clean_id}_{datetime.now():%Y%m%d}"
    dossier_path = DOSSIERS_DIR / dossier_nom
    dossier_path.mkdir(parents=True, exist_ok=True)

    fichiers_generes = []
    nb_mots_total = 0
    erreurs = []

    # 1. Analyse Go/No-Go (pas d'appel API)
    logger.info("1/7 - Analyse Go/No-Go...")
    try:
        if gng_result:
            analyse_txt = _generer_analyse_gonogo(ao, gng_result)
        else:
            from analyse_dce import go_no_go
            gng_result = go_no_go(ao)
            analyse_txt = _generer_analyse_gonogo(ao, gng_result)
        _sauvegarder(dossier_path, "01_analyse_go_no_go.md", analyse_txt)
        fichiers_generes.append("01_analyse_go_no_go.md")
    except Exception as e:
        erreurs.append(f"Analyse Go/No-Go: {e}")
        logger.error(f"Erreur analyse: {e}")

    # 2. Memoire technique (appel API principal)
    logger.info("2/7 - Memoire technique...")
    try:
        memoire = _generer_memoire(ao, type_presta)
        _sauvegarder(dossier_path, "02_memoire_technique.md", memoire)
        fichiers_generes.append("02_memoire_technique.md")
        nb_mots_total += len(memoire.split())
        logger.info(f"  Memoire: {len(memoire.split())} mots")
    except Exception as e:
        erreurs.append(f"Memoire technique: {e}")
        logger.error(f"Erreur memoire: {e}")

    # 3. Lettre de candidature (appel API)
    logger.info("3/7 - Lettre de candidature...")
    try:
        lettre = _generer_lettre(ao)
        _sauvegarder(dossier_path, "03_lettre_candidature.md", lettre)
        fichiers_generes.append("03_lettre_candidature.md")
        nb_mots_total += len(lettre.split())
    except Exception as e:
        erreurs.append(f"Lettre: {e}")
        logger.error(f"Erreur lettre: {e}")

    # 4. BPU / DPGF (appel API)
    logger.info("4/7 - BPU / DPGF...")
    try:
        bpu = _generer_bpu(ao)
        _sauvegarder(dossier_path, "04_bpu_dpgf.md", bpu)
        fichiers_generes.append("04_bpu_dpgf.md")
        nb_mots_total += len(bpu.split())
    except Exception as e:
        erreurs.append(f"BPU: {e}")
        logger.error(f"Erreur BPU: {e}")

    # 5. Planning (appel API leger)
    logger.info("5/7 - Planning...")
    try:
        planning = _generer_planning(ao)
        _sauvegarder(dossier_path, "05_planning_previsionnel.md", planning)
        fichiers_generes.append("05_planning_previsionnel.md")
        nb_mots_total += len(planning.split())
    except Exception as e:
        erreurs.append(f"Planning: {e}")
        logger.error(f"Erreur planning: {e}")

    # 6. CV Formateurs (appel API leger)
    logger.info("6/7 - CV Formateurs...")
    try:
        cv = _generer_cv_formateurs(ao)
        _sauvegarder(dossier_path, "06_cv_formateurs.md", cv)
        fichiers_generes.append("06_cv_formateurs.md")
        nb_mots_total += len(cv.split())
    except Exception as e:
        erreurs.append(f"CV: {e}")
        logger.error(f"Erreur CV: {e}")

    # 7. DC1 / DC2 (template, pas d'appel API)
    logger.info("7/13 - DC1 / DC2...")
    try:
        dc = _generer_dc1_dc2(ao)
        _sauvegarder(dossier_path, "07_dc1_dc2.md", dc)
        fichiers_generes.append("07_dc1_dc2.md")
        nb_mots_total += len(dc.split())
    except Exception as e:
        erreurs.append(f"DC1/DC2: {e}")
        logger.error(f"Erreur DC: {e}")

    # 8. Programme de formation detaille (appel API)
    logger.info("8/13 - Programme de formation detaille...")
    try:
        programme = _generer_programme_formation(ao)
        _sauvegarder(dossier_path, "08_programme_formation.md", programme)
        fichiers_generes.append("08_programme_formation.md")
        nb_mots_total += len(programme.split())
        logger.info(f"  Programme: {len(programme.split())} mots")
    except Exception as e:
        erreurs.append(f"Programme formation: {e}")
        logger.error(f"Erreur programme: {e}")

    # 9. References clients detaillees (template)
    logger.info("9/13 - References clients...")
    try:
        refs = _generer_references_clients(ao)
        _sauvegarder(dossier_path, "09_references_clients.md", refs)
        fichiers_generes.append("09_references_clients.md")
        nb_mots_total += len(refs.split())
    except Exception as e:
        erreurs.append(f"References: {e}")
        logger.error(f"Erreur references: {e}")

    # 10. DPGF en Excel (openpyxl)
    logger.info("10/13 - DPGF Excel...")
    try:
        excel_file = _generer_dpgf_excel(ao, dossier_path)
        if excel_file:
            fichiers_generes.append(excel_file)
            logger.info("  DPGF Excel genere")
    except Exception as e:
        erreurs.append(f"DPGF Excel: {e}")
        logger.error(f"Erreur DPGF Excel: {e}")

    # 11. Acte d'engagement pre-rempli (template)
    logger.info("11/13 - Acte d'engagement...")
    try:
        ae = _generer_acte_engagement(ao)
        _sauvegarder(dossier_path, "11_acte_engagement.md", ae)
        fichiers_generes.append("11_acte_engagement.md")
        nb_mots_total += len(ae.split())
    except Exception as e:
        erreurs.append(f"Acte engagement: {e}")
        logger.error(f"Erreur AE: {e}")

    # 12. DUME (template)
    logger.info("12/13 - DUME...")
    try:
        dume = _generer_dume(ao)
        _sauvegarder(dossier_path, "12_dume.md", dume)
        fichiers_generes.append("12_dume.md")
        nb_mots_total += len(dume.split())
    except Exception as e:
        erreurs.append(f"DUME: {e}")
        logger.error(f"Erreur DUME: {e}")

    # 13. Moyens techniques et materiels (template)
    logger.info("13/13 - Moyens techniques...")
    try:
        moyens = _generer_moyens_techniques(ao)
        _sauvegarder(dossier_path, "13_moyens_techniques.md", moyens)
        fichiers_generes.append("13_moyens_techniques.md")
        nb_mots_total += len(moyens.split())
    except Exception as e:
        erreurs.append(f"Moyens techniques: {e}")
        logger.error(f"Erreur moyens: {e}")

    # 14. Checklist (template, pas d'appel API)
    try:
        checklist = _generer_checklist_soumission(ao, fichiers_generes)
        _sauvegarder(dossier_path, "14_checklist_soumission.md", checklist)
        fichiers_generes.append("14_checklist_soumission.md")
    except Exception as e:
        erreurs.append(f"Checklist: {e}")

    # 15. Fiche AO en JSON
    fiche_path = dossier_path / "fiche_ao.json"
    fiche_path.write_text(json.dumps(ao, ensure_ascii=False, indent=2), encoding="utf-8")
    fichiers_generes.append("fiche_ao.json")

    # Mettre a jour l'index
    _maj_index(dossier_nom, ao, dossier_path)

    success = len(fichiers_generes) >= 5  # Au moins 5 fichiers pour considerer le dossier complet
    result = {
        "success": success,
        "dossier_nom": dossier_nom,
        "fichiers": fichiers_generes,
        "nb_fichiers": len(fichiers_generes),
        "nb_mots": nb_mots_total,
        "erreurs": erreurs,
    }

    if erreurs:
        result["avertissement"] = f"{len(erreurs)} erreur(s) lors de la generation"
        logger.warning(f"Dossier {dossier_nom}: {len(erreurs)} erreurs - {erreurs}")

    logger.info(f"Dossier complet genere: {dossier_nom} ({len(fichiers_generes)} fichiers, {nb_mots_total} mots)")
    return result


# Backward compat - l'ancienne fonction redirige vers la nouvelle
def generer_memoire_technique(ao: dict, type_presta: str = "Formation") -> dict:
    """Compatibilite arriere - genere maintenant un dossier complet."""
    return generer_dossier_complet(ao, type_presta)


def _sauvegarder(dossier_path: Path, nom_fichier: str, contenu: str):
    """Sauvegarde un fichier dans le dossier."""
    path = dossier_path / nom_fichier
    path.write_text(contenu, encoding="utf-8")


def _maj_index(dossier_nom: str, ao: dict, dossier_path: Path):
    """Met a jour dossiers_index.json."""
    index = []
    if DOSSIERS_INDEX.exists():
        try:
            index = json.loads(DOSSIERS_INDEX.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            index = []

    fichiers = [f.name for f in dossier_path.glob("*") if f.is_file()]
    entry = {
        "nom": dossier_nom,
        "ao_id": ao.get("id", ""),
        "ao_titre": ao.get("titre", ""),
        "nb_fichiers": len(fichiers),
        "fichiers": fichiers,
        "date_creation": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "pipeline_auto": True,
    }

    # Eviter les doublons
    index = [d for d in index if d.get("nom") != dossier_nom]
    index.insert(0, entry)

    DOSSIERS_INDEX.write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
