"""
Soumission semi-automatique - Prepare le dossier pour depot sur les plateformes.
Fonctionne sur Render (pas de Playwright) : preparation + guide manuel.
"""

import os
import json
import zipfile
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger("ao_hunter.soumission")

DASHBOARD_DIR = Path(__file__).parent
DOSSIERS_DIR = DASHBOARD_DIR / "dossiers_generes"
RESULTATS_DIR = DASHBOARD_DIR.parent / "resultats"
DOSSIER_PERMANENT = DASHBOARD_DIR.parent / "dossier_permanent"


# --- Mapping fichiers -> enveloppes ---

ENVELOPPE_CANDIDATURE_KEYWORDS = {
    "dc1": "DC1 - Lettre de candidature",
    "dc2": "DC2 - Declaration du candidat",
    "dume": "DUME",
    "lettre_candidature": "Lettre de candidature",
    "kbis": "Extrait Kbis",
    "urssaf": "Attestation URSSAF",
    "fiscale": "Attestation fiscale",
    "qualiopi": "Certificat Qualiopi",
    "rib": "RIB",
    "assurance": "Attestation RC Pro",
    "rc_pro": "Attestation RC Pro",
    "attestation": "Attestation",
    "acte_engagement": "Acte d'engagement",
}

ENVELOPPE_OFFRE_KEYWORDS = {
    "memoire_technique": "Memoire technique",
    "memoire": "Memoire technique",
    "bpu": "BPU / DPGF",
    "dpgf": "BPU / DPGF",
    "planning": "Planning previsionnel",
    "programme": "Programme de formation",
    "cv": "CV formateurs",
    "references": "References clients",
    "moyens_techniques": "Moyens techniques",
}


# --- Detection plateforme ---

PLATEFORMES = {
    "place": {
        "nom": "PLACE (Plateforme des Achats de l'Etat)",
        "url_base": "https://www.marches-publics.gouv.fr",
        "patterns": ["marches-publics.gouv.fr", "place.gouv", "place-marches"],
    },
    "aws": {
        "nom": "AWS (Achats Web Securises)",
        "url_base": "https://www.achats.defense.gouv.fr",
        "patterns": ["achats.defense.gouv", "aws-defense", "defense.gouv"],
    },
    "marches-securises": {
        "nom": "marches-securises.fr",
        "url_base": "https://www.marches-securises.fr",
        "patterns": ["marches-securises.fr", "marches-securises"],
    },
}


def identifier_plateforme(ao: dict) -> dict:
    """Identifie la plateforme de depot depuis les URLs de l'AO."""
    urls = []
    for champ in ["url", "url_profil_acheteur", "url_source"]:
        val = ao.get(champ, "")
        if val:
            urls.append(val.lower())

    source = ao.get("source", "").lower()

    for key, info in PLATEFORMES.items():
        for pattern in info["patterns"]:
            for url in urls:
                if pattern in url:
                    # Trouver l'URL de depot exacte
                    url_depot = next((u for u in [ao.get("url"), ao.get("url_profil_acheteur")] if u and pattern in u.lower()), info["url_base"])
                    return {"id": key, "nom": info["nom"], "url_depot": url_depot}

    # Fallback par source
    if "aws" in source or "defense" in source:
        return {"id": "aws", "nom": PLATEFORMES["aws"]["nom"], "url_depot": PLATEFORMES["aws"]["url_base"]}
    if "place" in source or "boamp" in source:
        return {"id": "place", "nom": PLATEFORMES["place"]["nom"], "url_depot": ao.get("url") or PLATEFORMES["place"]["url_base"]}
    if "msec" in source or "marches-securises" in source.replace("_", "-"):
        return {"id": "marches-securises", "nom": PLATEFORMES["marches-securises"]["nom"], "url_depot": ao.get("url") or PLATEFORMES["marches-securises"]["url_base"]}

    # Par defaut PLACE (la plus courante)
    return {"id": "place", "nom": PLATEFORMES["place"]["nom"], "url_depot": ao.get("url") or PLATEFORMES["place"]["url_base"]}


def _trouver_dossier(ao: dict) -> Path | None:
    """Trouve le dossier genere pour un AO."""
    ao_id = ao.get("id", "")
    clean_id = ao_id.replace("BOAMP-", "").replace("PLACE-", "").replace("TED-", "").replace("MSEC-", "").replace("AWS-", "")

    for base in [RESULTATS_DIR, DOSSIERS_DIR]:
        if not base.exists():
            continue
        for d in base.iterdir():
            if d.is_dir() and clean_id in d.name:
                return d
    return None


def _classer_fichier(nom_fichier: str) -> str:
    """Classe un fichier dans candidature ou offre."""
    nom_lower = nom_fichier.lower()
    for kw in ENVELOPPE_CANDIDATURE_KEYWORDS:
        if kw in nom_lower:
            return "candidature"
    for kw in ENVELOPPE_OFFRE_KEYWORDS:
        if kw in nom_lower:
            return "offre"
    # JSON de donnees -> ignorer
    if nom_lower.endswith(".json"):
        return "autre"
    # Go/No-Go -> autre
    if "go_no_go" in nom_lower or "analyse_go" in nom_lower:
        return "autre"
    # Par defaut -> offre
    return "offre"


def verifier_completude(fichiers_candidature: list, fichiers_offre: list) -> dict:
    """Verifie que toutes les pieces requises sont presentes."""
    pieces_requises_candidature = ["dc1", "dc2", "qualiopi"]
    pieces_requises_offre = ["memoire"]

    manquantes = []

    for piece in pieces_requises_candidature:
        found = any(piece in f.lower() for f in fichiers_candidature)
        if not found:
            label = ENVELOPPE_CANDIDATURE_KEYWORDS.get(piece, piece)
            manquantes.append(f"[Candidature] {label}")

    for piece in pieces_requises_offre:
        found = any(piece in f.lower() for f in fichiers_offre)
        if not found:
            label = ENVELOPPE_OFFRE_KEYWORDS.get(piece, piece)
            manquantes.append(f"[Offre] {label}")

    return {
        "complet": len(manquantes) == 0,
        "pieces_manquantes": manquantes,
    }


def preparer_soumission(ao: dict, dossier_path: str = None) -> dict:
    """
    Prepare tout pour la soumission manuelle sur les plateformes.

    Returns:
        dict avec: plateforme, url_depot, zip_path, enveloppes, checklist_etapes,
                   pret, pieces_manquantes
    """
    # Identifier la plateforme
    plateforme_info = identifier_plateforme(ao)

    # Trouver le dossier
    if dossier_path:
        dossier = Path(dossier_path)
    else:
        dossier = _trouver_dossier(ao)

    if not dossier or not dossier.exists():
        return {
            "plateforme": plateforme_info["nom"],
            "plateforme_id": plateforme_info["id"],
            "url_depot": plateforme_info["url_depot"],
            "zip_path": None,
            "enveloppes": {"candidature": [], "offre": []},
            "checklist_etapes": generer_guide_soumission(plateforme_info["id"]),
            "pret": False,
            "pieces_manquantes": ["Dossier non genere - lancez d'abord la generation"],
            "erreur": "Aucun dossier genere trouve pour cet AO",
        }

    # Lister et classer les fichiers
    fichiers = [f for f in dossier.iterdir() if f.is_file()]
    candidature = []
    offre = []

    for f in fichiers:
        cat = _classer_fichier(f.name)
        if cat == "candidature":
            candidature.append(f.name)
        elif cat == "offre":
            offre.append(f.name)

    # Ajouter les pieces du dossier permanent (si disponible)
    pieces_permanentes = []
    if DOSSIER_PERMANENT.exists():
        for f in DOSSIER_PERMANENT.iterdir():
            if f.is_file() and f.suffix.lower() in (".pdf", ".jpg", ".png"):
                pieces_permanentes.append(f.name)
                cat = _classer_fichier(f.name)
                if cat == "candidature":
                    candidature.append(f"[permanent] {f.name}")

    # Verifier completude
    completude = verifier_completude(candidature, offre)

    # Generer le ZIP
    zip_path = None
    try:
        ao_id = ao.get("id", "inconnu").replace("/", "_").replace("\\", "_")
        zip_name = f"soumission_{ao_id}_{datetime.now():%Y%m%d}.zip"
        zip_full_path = dossier / zip_name

        with zipfile.ZipFile(zip_full_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # Enveloppe candidature
            for f in fichiers:
                if _classer_fichier(f.name) == "candidature":
                    zf.write(f, f"01_Candidature/{f.name}")
                elif _classer_fichier(f.name) == "offre":
                    zf.write(f, f"02_Offre/{f.name}")

            # Pieces du dossier permanent
            if DOSSIER_PERMANENT.exists():
                for f in DOSSIER_PERMANENT.iterdir():
                    if f.is_file() and f.suffix.lower() in (".pdf", ".jpg", ".png"):
                        zf.write(f, f"01_Candidature/{f.name}")

        zip_path = str(zip_full_path)
        logger.info(f"ZIP soumission cree: {zip_path}")

    except Exception as e:
        logger.error(f"Erreur creation ZIP: {e}")

    # Checklist etapes
    checklist = generer_guide_soumission(plateforme_info["id"])

    return {
        "plateforme": plateforme_info["nom"],
        "plateforme_id": plateforme_info["id"],
        "url_depot": plateforme_info["url_depot"],
        "zip_path": zip_path,
        "enveloppes": {
            "candidature": sorted(candidature),
            "offre": sorted(offre),
        },
        "checklist_etapes": checklist,
        "pret": completude["complet"],
        "pieces_manquantes": completude["pieces_manquantes"],
        "pieces_permanentes": pieces_permanentes,
        "nb_fichiers": len(fichiers),
    }


def generer_guide_soumission(plateforme: str) -> list[str]:
    """
    Genere les instructions pas-a-pas pour deposer sur une plateforme.
    Retourne une liste d'etapes textuelles.
    """
    guides = {
        "place": [
            "1. Connectez-vous sur PLACE (marches-publics.gouv.fr) avec votre certificat de signature ou identifiant",
            "2. Recherchez la consultation par son numero de reference ou titre",
            "3. Cliquez sur 'Deposer une offre' ou 'Repondre a la consultation'",
            "4. Remplissez le formulaire de candidature (DC1 pre-rempli dans le dossier)",
            "5. Deposez l'enveloppe CANDIDATURE : DC1, DC2/DUME, Qualiopi, Kbis, attestations URSSAF/fiscales, RIB",
            "6. Deposez l'enveloppe OFFRE : Memoire technique, BPU/DPGF, planning, programme, CV formateurs",
            "7. Signez electroniquement chaque enveloppe (certificat RGS** ou eIDAS qualifie)",
            "8. Verifiez le recapitulatif et confirmez le depot",
            "9. Conservez l'accuse de reception (horodatage du depot)",
            "10. Verifiez que le statut passe a 'Deposee' dans votre espace",
        ],
        "aws": [
            "1. Connectez-vous sur AWS (achats.defense.gouv.fr) avec votre certificat de signature",
            "2. Accedez a la consultation via le numero de reference",
            "3. Cliquez sur 'Deposer une reponse'",
            "4. Creez l'enveloppe de candidature et telechargez les pieces administratives",
            "5. Pieces candidature : DC1, DC2, DUME, attestations, Qualiopi, Kbis, RIB",
            "6. Creez l'enveloppe technique/offre et telechargez les documents",
            "7. Pieces offre : Memoire technique, BPU/DPGF, planning, programme formation, CV",
            "8. Signez electroniquement (certificat RGS** obligatoire pour Defense)",
            "9. Validez et soumettez la reponse",
            "10. Telechargez et conservez l'accuse de reception horodate",
        ],
        "marches-securises": [
            "1. Connectez-vous sur marches-securises.fr avec vos identifiants",
            "2. Recherchez la consultation (numero ou titre)",
            "3. Cliquez sur 'Repondre electroniquement'",
            "4. Telechargez l'enveloppe candidature : DC1, DC2, DUME, attestations, Qualiopi, RIB",
            "5. Telechargez l'enveloppe offre : Memoire technique, BPU/DPGF, planning, programme, CV",
            "6. Renseignez les informations complementaires si demande",
            "7. Signez electroniquement les enveloppes",
            "8. Confirmez le depot avant la date limite",
            "9. Conservez l'accuse de reception avec horodatage",
            "10. Verifiez la bonne reception dans votre espace 'Mes reponses'",
        ],
    }

    return guides.get(plateforme, guides["place"])
