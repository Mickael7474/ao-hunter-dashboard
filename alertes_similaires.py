"""
Alerte AO similaires apres victoire - Feature 9
Quand un AO passe en statut "gagne", cherche les AO similaires
encore actifs pour capitaliser sur cette victoire.
"""

import re
import logging

logger = logging.getLogger("ao_hunter.alertes_similaires")

# Stopwords francais courants a exclure de la similarite
STOPWORDS = {
    "le", "la", "les", "de", "du", "des", "un", "une", "et", "en", "a", "au",
    "aux", "ce", "ces", "dans", "par", "pour", "sur", "avec", "que", "qui",
    "est", "sont", "pas", "ne", "ou", "se", "sa", "son", "ses", "nous",
    "vous", "ils", "elles", "leur", "leurs", "cette", "cet", "tout", "tous",
    "toute", "toutes", "plus", "moins", "tres", "bien", "peut", "fait",
    "etre", "avoir", "faire", "comme", "mais", "aussi", "dont", "entre",
    "sans", "sous", "vers", "chez", "d", "l", "n", "s", "qu", "c",
    "marche", "public", "appel", "offre", "offres", "avis", "lot", "lots",
    "objet", "relatif", "relative", "prestation", "prestations", "service",
    "services", "accord", "cadre", "procedure", "marches", "publics",
}


def _extraire_mots(texte: str) -> set[str]:
    """Extrait les mots significatifs d'un texte (stopwords exclus, > 2 chars)."""
    if not texte:
        return set()
    # Normaliser : minuscule, supprimer ponctuation
    texte = re.sub(r"[^\w\s]", " ", texte.lower())
    mots = texte.split()
    return {m for m in mots if len(m) > 2 and m not in STOPWORDS}


def _jaccard_similarity(set1: set, set2: set) -> float:
    """Calcule la similarite de Jaccard entre deux ensembles."""
    if not set1 or not set2:
        return 0.0
    intersection = set1 & set2
    union = set1 | set2
    if not union:
        return 0.0
    return len(intersection) / len(union)


def _budget_proche(budget1, budget2, tolerance: float = 0.5) -> float:
    """Score de proximite de budget (0-1). Tolerance = ecart relatif max."""
    if not budget1 or not budget2:
        return 0.0
    try:
        b1, b2 = float(budget1), float(budget2)
        if b1 <= 0 or b2 <= 0:
            return 0.0
        ratio = min(b1, b2) / max(b1, b2)
        if ratio >= (1 - tolerance):
            return ratio
        return 0.0
    except (ValueError, TypeError):
        return 0.0


def calculer_similarite(ao1: dict, ao2: dict) -> float:
    """Calcule un score de similarite entre deux AO (0-1).

    Criteres :
    - Mots-cles communs titre+description (Jaccard) : poids 60%
    - Meme type de prestation / marche : poids 20%
    - Budget proche : poids 20%
    """
    # Mots-cles titre + description
    texte1 = f"{ao1.get('titre', '')} {ao1.get('description', '')}"
    texte2 = f"{ao2.get('titre', '')} {ao2.get('description', '')}"
    mots1 = _extraire_mots(texte1)
    mots2 = _extraire_mots(texte2)
    sim_texte = _jaccard_similarity(mots1, mots2)

    # Type de marche
    type1 = (ao1.get("type_marche") or "").lower()
    type2 = (ao2.get("type_marche") or "").lower()
    sim_type = 1.0 if type1 and type2 and type1 == type2 else 0.0
    # Aussi comparer le type_procedure
    proc1 = (ao1.get("type_procedure") or "").lower()
    proc2 = (ao2.get("type_procedure") or "").lower()
    if proc1 and proc2 and proc1 == proc2:
        sim_type = max(sim_type, 0.7)

    # Budget
    sim_budget = _budget_proche(
        ao1.get("budget_estime"), ao2.get("budget_estime")
    )

    # Score pondere
    score = sim_texte * 0.60 + sim_type * 0.20 + sim_budget * 0.20
    return round(score, 3)


def trouver_similaires(ao_gagne: dict, tous_ao: list[dict], n: int = 5) -> list[dict]:
    """Trouve les N AO les plus similaires a un AO gagne.

    Ne retourne que les AO encore actifs (statut nouveau, analyse, candidature).

    Args:
        ao_gagne: l'AO qui vient d'etre gagne
        tous_ao: tous les AO de la base
        n: nombre de resultats max

    Returns:
        list de dicts avec {ao, similarite, mots_communs}
    """
    statuts_actifs = {"nouveau", "analyse", "candidature"}
    ao_gagne_id = ao_gagne.get("id", "")

    mots_gagne = _extraire_mots(
        f"{ao_gagne.get('titre', '')} {ao_gagne.get('description', '')}"
    )

    candidats = []
    for ao in tous_ao:
        # Exclure l'AO lui-meme et les AO non actifs
        if ao.get("id") == ao_gagne_id:
            continue
        if ao.get("statut", "nouveau") not in statuts_actifs:
            continue

        sim = calculer_similarite(ao_gagne, ao)
        if sim < 0.05:
            continue

        # Mots en commun (pour affichage)
        mots_ao = _extraire_mots(
            f"{ao.get('titre', '')} {ao.get('description', '')}"
        )
        mots_communs = sorted(mots_gagne & mots_ao)[:10]

        candidats.append({
            "ao": ao,
            "similarite": sim,
            "similarite_pct": int(sim * 100),
            "mots_communs": mots_communs,
        })

    # Trier par similarite decroissante
    candidats.sort(key=lambda x: x["similarite"], reverse=True)
    return candidats[:n]
