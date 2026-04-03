"""
Detection de signaux faibles (AO fleches).
Analyse le DCE et les caracteristiques d'un AO pour detecter
si le marche semble oriente vers un candidat sortant.
"""

import re
import logging
from datetime import datetime

logger = logging.getLogger("ao_hunter.signaux_faibles")


# Signaux avec leur poids
SIGNAUX_CONFIG = {
    "delai_court": {
        "label": "Delai de reponse tres court (< 15 jours)",
        "poids": 20,
        "couleur": "#f59e0b",
    },
    "criteres_specifiques": {
        "label": "Criteres ultra-specifiques inhabituels",
        "poids": 25,
        "couleur": "#dc2626",
    },
    "reference_sortant": {
        "label": "Reference a un prestataire sortant",
        "poids": 30,
        "couleur": "#dc2626",
    },
    "localisation_restrictive": {
        "label": "Exigences de localisation tres restrictives",
        "poids": 15,
        "couleur": "#f59e0b",
    },
    "budget_bas": {
        "label": "Budget anormalement bas",
        "poids": 10,
        "couleur": "#f97316",
    },
    "experience_disproportionnee": {
        "label": "Annees d'experience exigees disproportionnees",
        "poids": 15,
        "couleur": "#f59e0b",
    },
    "allotissement_artificiel": {
        "label": "Allotissement artificiel",
        "poids": 10,
        "couleur": "#f97316",
    },
    "visite_obligatoire": {
        "label": "Visite obligatoire des locaux",
        "poids": 10,
        "couleur": "#f97316",
    },
}


def _detecter_delai_court(ao: dict) -> dict | None:
    """Verifie si le delai de reponse est inferieur a 15 jours."""
    date_limite = ao.get("date_limite", "")
    date_publication = ao.get("date_publication", ao.get("date_parution", ""))

    if not date_limite or not date_publication:
        return None

    try:
        dl = datetime.fromisoformat(date_limite.split("T")[0])
        dp = datetime.fromisoformat(date_publication.split("T")[0])
        delta = (dl - dp).days

        if delta < 15 and delta > 0:
            return {
                "id": "delai_court",
                "detail": f"Seulement {delta} jours entre publication et date limite",
                **SIGNAUX_CONFIG["delai_court"],
            }
    except (ValueError, TypeError):
        pass
    return None


def _detecter_criteres_specifiques(ao: dict, dce_texte: str) -> dict | None:
    """Detecte des criteres ultra-specifiques inhabituels."""
    texte = (dce_texte + " " + ao.get("description", "") + " " + ao.get("criteres_attribution", "")).lower()

    patterns_suspects = [
        r"certification\s+\w+\s+\w+\s+obligatoire",
        r"agrement\s+specifique",
        r"habilitation\s+\w+\s+obligatoire",
        r"norme\s+iso\s+\d+.*obligatoire",
        r"exclusivement\s+reserve",
        r"seul(s|es)?\s+(les?\s+)?candidat",
        r"technologie\s+proprietaire",
        r"outil\s+specifique\s+\w+",
        r"logiciel\s+\w+\s+obligatoire",
        r"marque\s+\w+\s+exige",
    ]

    matches = []
    for pattern in patterns_suspects:
        found = re.findall(pattern, texte)
        if found:
            matches.extend(found if isinstance(found[0], str) else [f[0] for f in found])

    if matches:
        return {
            "id": "criteres_specifiques",
            "detail": f"Criteres suspects detectes: {', '.join(str(m)[:40] for m in matches[:3])}",
            **SIGNAUX_CONFIG["criteres_specifiques"],
        }
    return None


def _detecter_reference_sortant(ao: dict, dce_texte: str) -> dict | None:
    """Detecte des references a un prestataire sortant."""
    texte = (dce_texte + " " + ao.get("description", "")).lower()

    patterns = [
        r"titulaire\s+actuel",
        r"prestataire\s+sortant",
        r"marche\s+reconduit",
        r"reconduction\s+du\s+marche",
        r"titulaire\s+en\s+place",
        r"prestataire\s+en\s+place",
        r"operateur\s+sortant",
        r"contrat\s+actuel",
        r"marche\s+en\s+cours.*renouvellement",
        r"succession\s+du\s+marche",
    ]

    for pattern in patterns:
        match = re.search(pattern, texte)
        if match:
            # Extraire le contexte autour du match
            start = max(0, match.start() - 30)
            end = min(len(texte), match.end() + 30)
            contexte = texte[start:end].strip()
            return {
                "id": "reference_sortant",
                "detail": f"Reference detectee: '...{contexte}...'",
                **SIGNAUX_CONFIG["reference_sortant"],
            }
    return None


def _detecter_localisation_restrictive(ao: dict, dce_texte: str) -> dict | None:
    """Detecte des exigences de localisation restrictives."""
    texte = (dce_texte + " " + ao.get("description", "")).lower()

    patterns = [
        r"implantation\s+locale\s+obligatoire",
        r"siege\s+social\s+dans\s+le\s+departement",
        r"presence\s+physique\s+permanente",
        r"agence\s+dans\s+(le|la)\s+(departement|commune|region)",
        r"proximite\s+geographique\s+exigee",
        r"exclusivement\s+local",
        r"rayon\s+de\s+\d+\s*km",
        r"implantation\s+dans\s+un\s+rayon",
    ]

    for pattern in patterns:
        match = re.search(pattern, texte)
        if match:
            return {
                "id": "localisation_restrictive",
                "detail": f"Exigence detectee: '{match.group()}'",
                **SIGNAUX_CONFIG["localisation_restrictive"],
            }
    return None


def _detecter_budget_bas(ao: dict) -> dict | None:
    """Detecte un budget anormalement bas."""
    budget = ao.get("budget_estime")
    if not budget:
        return None

    try:
        budget = float(budget)
    except (ValueError, TypeError):
        return None

    # Pour de la formation, un budget < 5000 EUR pour un marche public est suspect
    description = ao.get("description", "").lower()
    is_formation = any(m in description for m in ["formation", "stage", "apprentissage", "pedagogique"])

    if is_formation and budget < 5000:
        return {
            "id": "budget_bas",
            "detail": f"Budget de {budget:.0f} EUR anormalement bas pour un marche de formation",
            **SIGNAUX_CONFIG["budget_bas"],
        }
    elif budget < 3000:
        return {
            "id": "budget_bas",
            "detail": f"Budget de {budget:.0f} EUR tres bas pour un marche public",
            **SIGNAUX_CONFIG["budget_bas"],
        }
    return None


def _detecter_experience_disproportionnee(ao: dict, dce_texte: str) -> dict | None:
    """Detecte des exigences d'experience disproportionnees."""
    texte = (dce_texte + " " + ao.get("description", "") + " " + ao.get("criteres_attribution", "")).lower()

    # Chercher des exigences d'annees d'experience elevees
    patterns = [
        r"(\d+)\s*ans?\s+d.experience\s+(minimum|obligatoire|exige|requis)",
        r"experience\s+de\s+(\d+)\s*ans?\s+(minimum|au\s+moins)",
        r"justifier\s+de\s+(\d+)\s*ans",
        r"anciennete\s+de\s+(\d+)\s*ans",
    ]

    for pattern in patterns:
        match = re.search(pattern, texte)
        if match:
            try:
                annees = int(match.group(1))
                if annees >= 8:
                    return {
                        "id": "experience_disproportionnee",
                        "detail": f"{annees} ans d'experience exiges - disproportionne pour le marche",
                        **SIGNAUX_CONFIG["experience_disproportionnee"],
                    }
            except (ValueError, IndexError):
                pass
    return None


def _detecter_allotissement_artificiel(ao: dict, dce_texte: str) -> dict | None:
    """Detecte un allotissement potentiellement artificiel."""
    texte = (dce_texte + " " + ao.get("description", "")).lower()

    patterns = [
        r"lot\s+unique\s+sans\s+allotissement",
        r"marche\s+global\s+sans\s+allotissement",
        r"absence\s+d.allotissement",
        r"lot\s+unique.*raisons\s+techniques",
    ]

    for pattern in patterns:
        match = re.search(pattern, texte)
        if match:
            return {
                "id": "allotissement_artificiel",
                "detail": "Lot unique sans allotissement - peut limiter la concurrence",
                **SIGNAUX_CONFIG["allotissement_artificiel"],
            }
    return None


def _detecter_visite_obligatoire(ao: dict, dce_texte: str) -> dict | None:
    """Detecte une visite obligatoire des locaux."""
    texte = (dce_texte + " " + ao.get("description", "")).lower()

    patterns = [
        r"visite\s+(obligatoire|imperative)\s+(des\s+)?(locaux|sites|lieux)",
        r"visite\s+sur\s+site\s+obligatoire",
        r"visite\s+prealable\s+obligatoire",
        r"la\s+visite\s+est\s+obligatoire",
    ]

    for pattern in patterns:
        match = re.search(pattern, texte)
        if match:
            return {
                "id": "visite_obligatoire",
                "detail": "Visite obligatoire des locaux - avantage le prestataire en place",
                **SIGNAUX_CONFIG["visite_obligatoire"],
            }
    return None


def detecter_signaux(ao: dict, dce_texte: str = "") -> dict:
    """Analyse un AO pour detecter les signaux faibles de marche fleche.

    Args:
        ao: dict de l'appel d'offres
        dce_texte: texte du DCE si disponible

    Returns:
        dict avec score_risque, signaux, recommandation, explication
    """
    signaux = []

    # Lancer toutes les detections
    detecteurs = [
        _detecter_delai_court(ao),
        _detecter_criteres_specifiques(ao, dce_texte),
        _detecter_reference_sortant(ao, dce_texte),
        _detecter_localisation_restrictive(ao, dce_texte),
        _detecter_budget_bas(ao),
        _detecter_experience_disproportionnee(ao, dce_texte),
        _detecter_allotissement_artificiel(ao, dce_texte),
        _detecter_visite_obligatoire(ao, dce_texte),
    ]

    for signal in detecteurs:
        if signal is not None:
            signaux.append(signal)

    # Calculer le score de risque
    score_risque = sum(s["poids"] for s in signaux)
    score_risque = min(100, score_risque)

    # Recommandation
    if score_risque >= 60:
        recommandation = "Eviter - probable marche fleche"
    elif score_risque > 30:
        recommandation = "Candidater avec prudence"
    else:
        recommandation = "Candidater"

    # Explication
    if signaux:
        details = [s["label"] for s in signaux]
        explication = (
            f"{len(signaux)} signal(aux) detecte(s) sur cet AO : "
            + ", ".join(details) + ". "
        )
        if score_risque >= 60:
            explication += "Forte probabilite que ce marche soit oriente vers un candidat sortant ou specifique."
        elif score_risque > 30:
            explication += "Quelques elements suspects, mais le marche reste potentiellement ouvert."
        else:
            explication += "Peu de signaux suspects, le marche semble ouvert."
    else:
        explication = "Aucun signal suspect detecte. Le marche semble ouvert a la concurrence."

    return {
        "score_risque": score_risque,
        "signaux": signaux,
        "recommandation": recommandation,
        "explication": explication,
    }
