"""
Generateur leger de memoire technique pour Render.
Utilise l'API Claude directement pour generer un memoire technique
sans les dependances lourdes (pdfplumber, playwright, etc.).

Genere : memoire_technique.md + version .txt
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
}


def generer_memoire_technique(ao: dict, type_presta: str = "Formation") -> dict:
    """Genere un memoire technique via l'API Claude.

    Returns:
        dict avec: success, dossier_nom, fichiers, erreur
    """
    if not API_KEY:
        return {"success": False, "erreur": "ANTHROPIC_API_KEY non configuree sur Render"}

    try:
        import httpx
    except ImportError:
        return {"success": False, "erreur": "httpx non installe"}

    # Construire le prompt
    prompt = _construire_prompt(ao, type_presta)

    # Appeler Claude
    try:
        with httpx.Client(timeout=120) as client:
            resp = client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 8000,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            resp.raise_for_status()
            data = resp.json()

        contenu = data["content"][0]["text"]

    except Exception as e:
        logger.error(f"Erreur API Claude: {e}")
        return {"success": False, "erreur": f"Erreur API: {str(e)}"}

    # Sauvegarder
    clean_id = ao.get("id", "inconnu").replace("/", "_")
    dossier_nom = f"AO_{clean_id}_{datetime.now():%Y%m%d}"
    dossier_path = DOSSIERS_DIR / dossier_nom
    dossier_path.mkdir(parents=True, exist_ok=True)

    # Memoire technique en markdown
    md_path = dossier_path / "memoire_technique.md"
    md_path.write_text(contenu, encoding="utf-8")

    # Version texte
    txt_path = dossier_path / "memoire_technique.txt"
    txt_path.write_text(contenu, encoding="utf-8")

    # Fiche AO en JSON
    fiche_path = dossier_path / "fiche_ao.json"
    fiche_path.write_text(json.dumps(ao, ensure_ascii=False, indent=2), encoding="utf-8")

    # Mettre a jour l'index
    _maj_index(dossier_nom, ao, dossier_path)

    fichiers = [f.name for f in dossier_path.glob("*") if f.is_file()]
    return {
        "success": True,
        "dossier_nom": dossier_nom,
        "fichiers": fichiers,
        "nb_mots": len(contenu.split()),
    }


def _construire_prompt(ao: dict, type_presta: str) -> str:
    """Construit le prompt pour generer le memoire technique."""
    ent = ENTREPRISE

    return f"""Tu es un expert en reponse aux appels d'offres publics francais.
Genere un MEMOIRE TECHNIQUE complet et professionnel pour cet appel d'offres.

## APPEL D'OFFRES
- Titre : {ao.get('titre', 'Non precise')}
- Acheteur : {ao.get('acheteur', 'Non precise')}
- Description : {ao.get('description', 'Non disponible')}
- Budget estime : {ao.get('budget_estime', 'Non precise')} EUR
- Type : {ao.get('type_marche', 'Non precise')}
- Procedure : {ao.get('type_procedure', 'Non precise')}
- Lieu : {ao.get('region', ao.get('lieu_execution', 'Non precise'))}
- Duree : {ao.get('duree_mois', 'Non precisee')} mois
- Criteres : {ao.get('criteres_attribution', 'Non precises')}

## TYPE DE PRESTATION DETECTE : {type_presta}

## ENTREPRISE CANDIDATE
- {ent['nom']} ({ent['raison_sociale']}) - {ent['forme_juridique']}
- SIRET : {ent['siret']} | NDA : {ent['nda']}
- Adresse : {ent['adresse']}
- Representant : {ent['representant']}
- Certifications : {', '.join(ent['certifications'])}
- Chiffres cles : {ent['chiffres']}
- Formateur principal : {ent['formateur_principal']}

## INSTRUCTIONS
1. Redige un memoire technique COMPLET en francais
2. Structure le memoire selon les sections standard pour une prestation de type "{type_presta}"
3. Personnalise chaque section au contexte de l'acheteur
4. Utilise UNIQUEMENT les vraies references et certifications d'Almera
5. N'invente JAMAIS de noms de formateurs ou de references clients
6. Propose des indicateurs de performance (KPIs) concrets
7. Inclus un planning previsionnel realiste
8. Si le type est "Formation", detaille les objectifs pedagogiques, les methodes, les supports
9. Si le type est "Consulting/AMO", detaille la methodologie en 4 etapes
10. Sois precis, concret, quantifie. Pas de phrases generiques.
11. Format Markdown avec titres # ## ###
"""


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
    }

    # Eviter les doublons
    index = [d for d in index if d.get("nom") != dossier_nom]
    index.insert(0, entry)

    DOSSIERS_INDEX.write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
