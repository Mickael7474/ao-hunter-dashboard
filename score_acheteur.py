"""
Score de compatibilite acheteur - Feature 8
Analyse les acheteurs recurrents et calcule un score de compatibilite
avec le profil Almera (formation IA, budget 2k-200k).
"""

import json
import logging
import re
from collections import Counter
from pathlib import Path

logger = logging.getLogger("ao_hunter.score_acheteur")

DASHBOARD_DIR = Path(__file__).parent
AO_CACHE = DASHBOARD_DIR / "ao_pertinents.json"
ATTRIBUTIONS_FILE = DASHBOARD_DIR / "attributions.json"

# Mots-cles indiquant un acheteur ideal pour Almera
MOTS_CLES_FORMATION_IA = [
    "formation", "ia", "intelligence artificielle", "numerique", "digital",
    "chatgpt", "copilot", "prompt", "automatisation", "acculturation",
    "accompagnement", "transformation", "competences", "generative",
]

# Types d'acheteurs ideaux pour Almera
ACHETEURS_IDEAUX = [
    "region", "departement", "communaute", "metropole", "mairie", "ville",
    "conseil", "agglomeration", "commune", "prefecture",
    "universite", "ecole", "lycee", "college", "education",
    "chambre", "cci", "cma",
    "opco", "atlas", "akto", "ocapiat", "constructys",
    "hopital", "chu", "aphp", "ars",
    "ministere", "dgfip", "direccte", "dreets",
]

# Budget ideal Almera : 2 000 - 200 000 EUR
BUDGET_MIN_IDEAL = 2_000
BUDGET_MAX_IDEAL = 200_000


def _charger_ao() -> list[dict]:
    if not AO_CACHE.exists():
        return []
    try:
        return json.loads(AO_CACHE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _charger_attributions() -> list[dict]:
    if not ATTRIBUTIONS_FILE.exists():
        return []
    try:
        return json.loads(ATTRIBUTIONS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _normaliser_acheteur(nom: str) -> str:
    """Normalise le nom d'acheteur pour regroupement."""
    if not nom:
        return ""
    # Minuscule, supprimer ponctuation superflue
    n = nom.lower().strip()
    n = re.sub(r"\s+", " ", n)
    # Supprimer les suffixes type "(siret...)"
    n = re.sub(r"\(.*?\)", "", n).strip()
    return n


def _est_acheteur_ideal(nom: str) -> bool:
    """Verifie si l'acheteur correspond au profil ideal pour Almera."""
    nom_lower = nom.lower()
    return any(mot in nom_lower for mot in ACHETEURS_IDEAUX)


def _est_ao_formation_ia(ao: dict) -> bool:
    """Verifie si un AO concerne la formation ou l'IA."""
    texte = f"{ao.get('titre', '')} {ao.get('description', '')}".lower()
    hits = sum(1 for mot in MOTS_CLES_FORMATION_IA if mot in texte)
    return hits >= 2


def _analyser_historique_acheteurs(tous_ao: list[dict]) -> dict:
    """Analyse l'historique complet des AO par acheteur.

    Returns:
        dict[acheteur_normalise] -> {
            nom_original, nb_ao, nb_formation_ia, budgets,
            statuts, premier_vu, dernier_vu
        }
    """
    par_acheteur = {}

    for ao in tous_ao:
        acheteur = ao.get("acheteur", "")
        if not acheteur or len(acheteur) < 3:
            continue

        cle = _normaliser_acheteur(acheteur)
        if not cle:
            continue

        if cle not in par_acheteur:
            par_acheteur[cle] = {
                "nom_original": acheteur,
                "nb_ao": 0,
                "nb_formation_ia": 0,
                "budgets": [],
                "statuts": Counter(),
                "premier_vu": ao.get("date_publication", ""),
                "dernier_vu": ao.get("date_publication", ""),
                "ao_ids": [],
            }

        info = par_acheteur[cle]
        info["nb_ao"] += 1
        info["ao_ids"].append(ao.get("id", ""))

        if _est_ao_formation_ia(ao):
            info["nb_formation_ia"] += 1

        budget = ao.get("budget_estime")
        if budget and isinstance(budget, (int, float)) and budget > 0:
            info["budgets"].append(budget)

        statut = ao.get("statut", "nouveau")
        info["statuts"][statut] += 1

        date_pub = ao.get("date_publication", "")
        if date_pub:
            if not info["premier_vu"] or date_pub < info["premier_vu"]:
                info["premier_vu"] = date_pub
            if date_pub > info["dernier_vu"]:
                info["dernier_vu"] = date_pub

    return par_acheteur


def scorer_acheteur(ao: dict) -> dict:
    """Calcule le score de compatibilite d'un acheteur pour un AO donne.

    Args:
        ao: dict representant un AO

    Returns:
        dict avec {score, recurrence, historique, recommandation}
    """
    tous_ao = _charger_ao()
    attributions = _charger_attributions()
    historique_acheteurs = _analyser_historique_acheteurs(tous_ao)

    acheteur = ao.get("acheteur", "")
    cle = _normaliser_acheteur(acheteur)

    score = 0
    details = []

    # --- Recurrence : acheteur deja vu (+20) ---
    info = historique_acheteurs.get(cle, {})
    recurrence = info.get("nb_ao", 0)

    if recurrence > 1:
        score += 20
        details.append(f"Acheteur recurrent ({recurrence} AO)")
    elif recurrence == 1:
        score += 5
        details.append("Premier AO de cet acheteur")

    # --- AO formation/IA similaires passes (+15) ---
    nb_formation_ia = info.get("nb_formation_ia", 0)
    if nb_formation_ia >= 2:
        score += 15
        details.append(f"{nb_formation_ia} AO formation/IA passes")
    elif nb_formation_ia == 1:
        score += 7
        details.append("1 AO formation/IA passe")

    # --- Type d'acheteur ideal (+15) ---
    if _est_acheteur_ideal(acheteur):
        score += 15
        details.append("Type d'acheteur ideal (collectivite/OPCO/public)")

    # --- Budget dans la zone Almera (+10) ---
    budget = ao.get("budget_estime")
    if budget and isinstance(budget, (int, float)):
        if BUDGET_MIN_IDEAL <= budget <= BUDGET_MAX_IDEAL:
            score += 10
            details.append(f"Budget ideal ({budget:,.0f} EUR)")
        elif budget < BUDGET_MIN_IDEAL:
            score += 3
            details.append(f"Budget faible ({budget:,.0f} EUR)")
        else:
            score += 5
            details.append(f"Budget eleve ({budget:,.0f} EUR)")
    else:
        # Budget moyen de l'acheteur
        budgets = info.get("budgets", [])
        if budgets:
            moy = sum(budgets) / len(budgets)
            if BUDGET_MIN_IDEAL <= moy <= BUDGET_MAX_IDEAL:
                score += 8
                details.append(f"Budget moyen acheteur: {moy:,.0f} EUR (zone ideale)")

    # --- Historique de reponse : deja repondu (+10) ---
    statuts = info.get("statuts", Counter())
    if statuts.get("soumis", 0) > 0 or statuts.get("gagne", 0) > 0 or statuts.get("perdu", 0) > 0:
        score += 10
        details.append("Deja repondu a cet acheteur")

    # --- Deja gagne (+30) ---
    nb_gagnes = statuts.get("gagne", 0)
    # Aussi verifier dans les attributions
    for attr in attributions:
        if attr.get("notre_statut") == "gagne":
            attr_acheteur = _normaliser_acheteur(attr.get("acheteur", ""))
            if attr_acheteur == cle:
                nb_gagnes += 1

    if nb_gagnes > 0:
        score += 30
        details.append(f"Deja gagne chez cet acheteur ({nb_gagnes}x)")

    # Cap a 100
    score = min(score, 100)

    # Recommandation
    if score >= 70:
        recommandation = "Prioritaire - acheteur tres compatible avec Almera"
    elif score >= 50:
        recommandation = "Interessant - bon potentiel de conversion"
    elif score >= 30:
        recommandation = "A surveiller - acheteur potentiel"
    else:
        recommandation = "Faible compatibilite - premier contact"

    return {
        "score": score,
        "recurrence": recurrence,
        "historique": {
            "nb_ao": info.get("nb_ao", 0),
            "nb_formation_ia": nb_formation_ia,
            "budget_moyen": (
                round(sum(info["budgets"]) / len(info["budgets"]))
                if info.get("budgets") else None
            ),
            "nb_gagnes": nb_gagnes,
            "nb_soumis": statuts.get("soumis", 0) + statuts.get("gagne", 0) + statuts.get("perdu", 0),
            "premier_vu": info.get("premier_vu", ""),
            "dernier_vu": info.get("dernier_vu", ""),
        },
        "details": details,
        "recommandation": recommandation,
    }


def top_acheteurs(n: int = 10) -> list[dict]:
    """Retourne les N acheteurs les plus interessants pour Almera.

    Returns:
        list de dicts avec {nom, score, nb_ao, nb_formation_ia, budget_moyen, recommandation}
    """
    tous_ao = _charger_ao()
    attributions = _charger_attributions()
    historique = _analyser_historique_acheteurs(tous_ao)

    # Construire un AO fictif par acheteur pour le scorer
    resultats = []
    for cle, info in historique.items():
        if info["nb_ao"] < 1:
            continue

        # Creer un AO representatif pour scorer
        budget_moyen = (
            sum(info["budgets"]) / len(info["budgets"])
            if info["budgets"] else None
        )
        ao_fictif = {
            "acheteur": info["nom_original"],
            "budget_estime": budget_moyen,
            "titre": "",
            "description": "",
        }

        score_data = scorer_acheteur(ao_fictif)

        resultats.append({
            "nom": info["nom_original"],
            "score": score_data["score"],
            "nb_ao": info["nb_ao"],
            "nb_formation_ia": info["nb_formation_ia"],
            "budget_moyen": round(budget_moyen) if budget_moyen else None,
            "nb_gagnes": score_data["historique"]["nb_gagnes"],
            "nb_soumis": score_data["historique"]["nb_soumis"],
            "dernier_vu": info["dernier_vu"],
            "recommandation": score_data["recommandation"],
            "details": score_data["details"],
        })

    # Trier par score decroissant
    resultats.sort(key=lambda x: x["score"], reverse=True)
    return resultats[:n]
