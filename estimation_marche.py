"""
Estimation du montant et de la concurrence d'un AO.

Meme quand le budget n'est pas precise dans l'avis, on peut l'estimer
a partir de : type de procedure, nombre de personnes, duree, type d'acheteur,
mots-cles, et historique des attributions similaires.

Le score d'accessibilite combine budget + concurrence pour prioriser :
- Phase 1 (debut) : petits AO, peu de concurrence -> construire les refs
- Phase 2 (croissance) : AO moyens, concurrence moderee -> monter en CA
- Phase 3 (maturite) : gros AO, forte concurrence -> exploser le CA

Usage:
    from estimation_marche import estimer_marche
    estimation = estimer_marche(ao)
    # estimation = {
    #     "budget_estime": 25000,
    #     "budget_fourchette": [15000, 35000],
    #     "budget_confiance": "moyen",
    #     "concurrence_niveau": "faible",
    #     "concurrence_score": 25,
    #     "concurrence_facteurs": [...],
    #     "accessibilite": 82,
    #     "phase_recommandee": "Phase 1 - Construction references",
    #     "recommandation": "AO ideal pour demarrer...",
    # }
"""

import re
import json
import logging
from pathlib import Path
from datetime import datetime

from decp_data import rechercher_marches_similaires

logger = logging.getLogger("ao_hunter.estimation_marche")

DASHBOARD_DIR = Path(__file__).parent
CONCURRENCE_FILE = DASHBOARD_DIR / "concurrence.json"


# ============================================================
# ESTIMATION DU BUDGET
# ============================================================

# Fourchettes par type de procedure
BUDGET_PAR_PROCEDURE = {
    "mapa": (5_000, 40_000),           # Marche a procedure adaptee
    "adapte": (5_000, 40_000),
    "procedure adaptee": (5_000, 40_000),
    "appel d'offres ouvert": (40_000, 200_000),
    "appel d'offres restreint": (90_000, 500_000),
    "dialogue competitif": (100_000, 500_000),
    "negociee": (20_000, 100_000),
    "procedure negociee": (20_000, 100_000),
    "concours": (50_000, 300_000),
    "accord-cadre": (50_000, 300_000),
}

# Fourchettes par type d'acheteur
BUDGET_PAR_ACHETEUR = {
    # Petits acheteurs -> petits budgets
    "commune": (3_000, 25_000),
    "mairie": (3_000, 25_000),
    "communaute de communes": (5_000, 40_000),
    "intercommunal": (5_000, 40_000),
    "syndicat": (5_000, 30_000),
    "association": (3_000, 20_000),
    "lycee": (3_000, 15_000),
    "college": (3_000, 15_000),
    "universite": (10_000, 60_000),
    "ecole": (3_000, 15_000),
    # Acheteurs moyens
    "departement": (10_000, 80_000),
    "conseil departemental": (10_000, 80_000),
    "cci": (5_000, 50_000),
    "chambre": (5_000, 50_000),
    "opco": (10_000, 100_000),
    "hopital": (10_000, 60_000),
    "centre hospitalier": (10_000, 60_000),
    # Gros acheteurs
    "region": (20_000, 200_000),
    "conseil regional": (20_000, 200_000),
    "metropole": (15_000, 150_000),
    "ministere": (30_000, 300_000),
    "etat": (30_000, 300_000),
    "prefecture": (10_000, 80_000),
    "agence": (15_000, 100_000),
    "caisse": (15_000, 150_000),
    "etablissement public": (10_000, 100_000),
}

# Indicateurs de volume dans le texte
INDICATEURS_VOLUME = [
    # (pattern, multiplicateur_budget_base)
    (r"(\d+)\s*(?:agents?|personnes?|participants?|stagiaires?|collaborateurs?|salaries?)", "nb_personnes"),
    (r"(\d+)\s*(?:sessions?|groupes?)", "nb_sessions"),
    (r"(\d+)\s*(?:jours?|journees?)\s*(?:de\s*)?formation", "nb_jours"),
    (r"(\d+)\s*(?:heures?|h)\s*(?:de\s*)?formation", "nb_heures"),
    (r"(\d+)\s*(?:modules?|formations?)", "nb_modules"),
]

# Prix unitaires de reference (marche public formation IA)
PRIX_REFERENCE = {
    "prix_jour_formation": 1_200,          # Prix moyen journee formation (marche public)
    "prix_heure_formation": 170,
    "prix_par_stagiaire_jour": 200,        # Cout par stagiaire/jour
    "prix_preparation_ratio": 0.3,         # 30% du temps formation en preparation
    "prix_suivi_ratio": 0.15,              # 15% en suivi post-formation
}


def estimer_budget(ao: dict) -> dict:
    """Estime le budget d'un AO quand il n'est pas precise.

    Returns:
        dict avec: montant, fourchette_basse, fourchette_haute, confiance, methode, details
    """
    budget_declare = ao.get("budget_estime")
    if budget_declare and budget_declare > 0:
        return {
            "montant": budget_declare,
            "fourchette_basse": int(budget_declare * 0.8),
            "fourchette_haute": int(budget_declare * 1.2),
            "confiance": "declare",
            "methode": "Budget declare dans l'avis",
            "details": [],
        }

    estimations = []
    details = []
    titre = (ao.get("titre") or "").lower()
    description = (ao.get("description") or "").lower()
    acheteur = (ao.get("acheteur") or "").lower()
    texte = f"{titre} {description}"
    procedure = (ao.get("type_procedure") or "").lower()

    # 1. Estimation par type de procedure
    for proc_key, (low, high) in BUDGET_PAR_PROCEDURE.items():
        if proc_key in procedure:
            mid = (low + high) // 2
            estimations.append(mid)
            details.append(f"Procedure '{proc_key}': {low:,}-{high:,} EUR")
            break

    # 2. Estimation par type d'acheteur
    for ach_key, (low, high) in BUDGET_PAR_ACHETEUR.items():
        if ach_key in acheteur:
            mid = (low + high) // 2
            estimations.append(mid)
            details.append(f"Acheteur type '{ach_key}': {low:,}-{high:,} EUR")
            break

    # 3. Estimation par volume (nb personnes, jours, sessions)
    for pattern, indicateur in INDICATEURS_VOLUME:
        match = re.search(pattern, texte, re.IGNORECASE)
        if match:
            valeur = int(match.group(1))
            if valeur > 0 and valeur < 10000:  # Filtre valeurs aberrantes
                budget_vol = _estimer_par_volume(indicateur, valeur)
                if budget_vol:
                    estimations.append(budget_vol)
                    details.append(f"{indicateur}: {valeur} -> ~{budget_vol:,} EUR")

    # 4. Estimation par mots-cles de taille
    if any(mot in texte for mot in ["accord-cadre", "marche a bons de commande", "multi-attributaire"]):
        estimations.append(80_000)
        details.append("Accord-cadre / multi-attributaire -> budget eleve")
    elif any(mot in texte for mot in ["prestation ponctuelle", "mission courte", "intervention"]):
        estimations.append(8_000)
        details.append("Prestation ponctuelle -> budget faible")

    # 5. Estimation par duree du marche
    duree = ao.get("duree_mois")
    if duree:
        if duree <= 3:
            estimations.append(10_000)
        elif duree <= 12:
            estimations.append(30_000)
        elif duree <= 24:
            estimations.append(60_000)
        else:
            estimations.append(100_000)
        details.append(f"Duree {duree} mois")

    # Moyenne ponderee des estimations
    if not estimations:
        # Fallback : estimation par defaut pour formation IA
        return {
            "montant": 20_000,
            "fourchette_basse": 5_000,
            "fourchette_haute": 50_000,
            "confiance": "tres_faible",
            "methode": "Estimation par defaut (pas assez d'indices)",
            "details": ["Aucun indicateur fiable dans l'avis"],
        }

    montant = int(sum(estimations) / len(estimations))
    ecart = max(estimations) - min(estimations) if len(estimations) > 1 else montant * 0.5

    # Confiance basee sur le nombre d'indices concordants
    if len(estimations) >= 3 and ecart < montant * 0.5:
        confiance = "bon"
    elif len(estimations) >= 2:
        confiance = "moyen"
    else:
        confiance = "faible"

    fourchette_basse = max(2_000, int(montant * 0.6))
    fourchette_haute = int(montant * 1.5)

    return {
        "montant": montant,
        "fourchette_basse": fourchette_basse,
        "fourchette_haute": fourchette_haute,
        "confiance": confiance,
        "methode": f"Estimation croisee ({len(estimations)} indices)",
        "details": details,
    }


def _estimer_par_volume(indicateur: str, valeur: int) -> int | None:
    """Estime le budget a partir d'un indicateur de volume."""
    prix = PRIX_REFERENCE

    if indicateur == "nb_personnes":
        # X personnes x ~2 jours x prix/stagiaire/jour + preparation
        nb_groupes = max(1, valeur // 12)  # 12 par groupe
        nb_jours = nb_groupes * 2  # 2 jours par groupe en moyenne
        budget = nb_jours * prix["prix_jour_formation"]
        budget *= (1 + prix["prix_preparation_ratio"] + prix["prix_suivi_ratio"])
        return int(budget)

    elif indicateur == "nb_sessions":
        # X sessions x 1-2 jours x prix/jour
        budget = valeur * 1.5 * prix["prix_jour_formation"]
        budget *= (1 + prix["prix_preparation_ratio"])
        return int(budget)

    elif indicateur == "nb_jours":
        budget = valeur * prix["prix_jour_formation"]
        budget *= (1 + prix["prix_preparation_ratio"] + prix["prix_suivi_ratio"])
        return int(budget)

    elif indicateur == "nb_heures":
        budget = valeur * prix["prix_heure_formation"]
        budget *= (1 + prix["prix_preparation_ratio"])
        return int(budget)

    elif indicateur == "nb_modules":
        # Chaque module ~ 1 jour de formation + preparation
        budget = valeur * prix["prix_jour_formation"] * 1.5
        return int(budget)

    return None


# ============================================================
# ESTIMATION DE LA CONCURRENCE
# ============================================================

# Facteurs qui augmentent la concurrence
FACTEURS_CONCURRENCE_HAUTE = {
    "formation numerique": 15,
    "formation digitale": 15,
    "transformation numerique": 12,
    "transformation digitale": 12,
    "accompagnement numerique": 10,
    "e-learning": 10,
    "bureautique": 20,
    "management": 15,
    "langues": 20,
    "anglais": 18,
    "securite au travail": 15,
    "soft skills": 15,
}

# Facteurs qui reduisent la concurrence (niche Almera)
FACTEURS_CONCURRENCE_BASSE = {
    "intelligence artificielle": -25,
    "IA generative": -30,
    "ChatGPT": -30,
    "copilot": -20,
    "midjourney": -30,
    "prompt": -20,
    "machine learning": -15,
    "deep learning": -20,
    "agent IA": -25,
    "automatisation IA": -20,
    "acculturation IA": -15,
    "deploiement IA": -15,
    "RS6776": -35,       # Tres peu d'organismes certifies
    "Qualiopi": -10,      # Beaucoup de concurrents ne l'ont pas
}

# Facteurs structurels
FACTEURS_STRUCTURE = {
    "ted": 10,               # Marche europeen = plus de concurrents
    "boamp": 0,              # National = standard
    "mapa": -10,             # Souvent petits acheteurs, moins de visibilite
    "procedure adaptee": -10,
    "appel d'offres ouvert": 5,
    "appel d'offres restreint": 5,  # Pre-selection mais concurrence qualifiee
    "accord-cadre": 10,      # Attire les gros
    "multi-attributaire": 5,  # Plusieurs gagnants = plus de candidats
}


def estimer_concurrence(ao: dict) -> dict:
    """Estime le niveau de concurrence pour un AO.

    Returns:
        dict avec: niveau (Tres faible/Faible/Moderee/Forte/Tres forte),
                   score (0-100), concurrence_score (0-100),
                   facteurs, nb_concurrents_estime, avantages_almera
    """
    titre = (ao.get("titre") or "").lower()
    description = (ao.get("description") or "").lower()
    texte = f"{titre} {description}"
    source = (ao.get("source") or "").lower()
    procedure = (ao.get("type_procedure") or "").lower()

    score_concurrence = 40  # Base : optimiste pour acteur de niche
    facteurs = []
    avantages = []

    # 1. Facteurs thematiques (haute concurrence)
    for mot, impact in FACTEURS_CONCURRENCE_HAUTE.items():
        if mot in texte:
            score_concurrence += impact
            facteurs.append(f"Thematique '{mot}' : concurrence +{impact}")

    # 2. Facteurs de niche Almera (basse concurrence)
    for mot, impact in FACTEURS_CONCURRENCE_BASSE.items():
        if mot.lower() in texte:
            score_concurrence += impact  # impact est negatif
            avantages.append(f"Niche '{mot}' : peu de concurrents ({impact})")

    # 3. Facteurs structurels (source, procedure)
    for facteur, impact in FACTEURS_STRUCTURE.items():
        if facteur in source or facteur in procedure:
            score_concurrence += impact
            facteurs.append(f"Structure '{facteur}' : {'+' if impact > 0 else ''}{impact}")

    # 4. Budget et concurrence
    budget = ao.get("budget_estime", 0) or 0
    if budget > 0:
        if budget < 10_000:
            score_concurrence -= 15
            avantages.append("Petit budget : peu attractif pour les gros organismes")
        elif budget < 25_000:
            score_concurrence -= 5
        elif budget > 100_000:
            score_concurrence += 15
            facteurs.append("Gros budget : attire beaucoup de candidats")
        elif budget > 50_000:
            score_concurrence += 8
            facteurs.append("Budget moyen-haut : concurrence significative")

    # 5. Deadline courte = moins de concurrents
    dl = ao.get("date_limite", "")
    if dl:
        try:
            date_dl = datetime.fromisoformat(dl.split("T")[0])
            jours = (date_dl - datetime.now()).days
            if 0 < jours < 7:
                score_concurrence -= 15
                avantages.append(f"Deadline courte ({jours}j) : candidats mal prepares elimines")
            elif 0 < jours < 14:
                score_concurrence -= 5
        except (ValueError, TypeError):
            pass

    # 6. Exigences specifiques = barriere a l'entree
    if "qualiopi" in texte:
        score_concurrence -= 5
        avantages.append("Qualiopi exige : filtre les non-certifies")
    if "rs6776" in texte or "france competences" in texte:
        score_concurrence -= 20
        avantages.append("RS6776 exige : TRES peu d'organismes certifies en IA")
    if "certification" in texte and "ia" in texte:
        score_concurrence -= 10
        avantages.append("Certification IA requise : avantage concurrentiel fort")

    # 7. Donnees historiques (si disponibles)
    nb_concurrents_historique = _estimer_nb_concurrents_historique(ao)
    if nb_concurrents_historique:
        facteurs.append(f"Historique attributions : ~{nb_concurrents_historique} candidats")

    # Borner le score
    score_concurrence = max(5, min(95, score_concurrence))

    # Niveau avec descriptions claires
    if score_concurrence < 20:
        niveau = "Tres faible"
        nb_concurrents = "2-3 candidats probables"
    elif score_concurrence < 40:
        niveau = "Faible"
        nb_concurrents = "3-5 candidats probables"
    elif score_concurrence < 60:
        niveau = "Moderee"
        nb_concurrents = "5-10 candidats probables"
    elif score_concurrence < 80:
        niveau = "Forte"
        nb_concurrents = "10-20 candidats probables"
    else:
        niveau = "Tres forte"
        nb_concurrents = "20+ candidats probables"

    return {
        "niveau": niveau,
        "concurrence_score": score_concurrence,
        "score": score_concurrence,
        "nb_concurrents_estime": nb_concurrents_historique or nb_concurrents,
        "facteurs_hausse": facteurs,
        "avantages_almera": avantages,
    }


def _estimer_nb_concurrents_historique(ao: dict) -> int | None:
    """Estime le nombre de concurrents a partir des attributions passees."""
    if not CONCURRENCE_FILE.exists():
        return None

    try:
        attributions = json.loads(CONCURRENCE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    if len(attributions) < 5:
        return None

    # Compter le nombre moyen de titulaires distincts sur des AO similaires
    titulaires = set()
    for a in attributions:
        t = a.get("titulaire", "").strip()
        if t:
            titulaires.add(t.lower())

    if titulaires:
        # Heuristique : nb titulaires uniques ~ nb moyen de candidats / 3
        # (1 gagnant pour ~3 candidats en moyenne)
        return max(3, len(titulaires) // 3)

    return None


# ============================================================
# SCORE D'ACCESSIBILITE (combine budget + concurrence)
# ============================================================

def calculer_accessibilite(budget_est: dict, concurrence_est: dict, ao: dict) -> dict:
    """Calcule le score d'accessibilite pour prioriser les AO.

    Accessibilite = facilite a gagner cet AO pour Almera.
    Combine : budget adapte + concurrence faible + adequation competences.

    Phase 1 (debut) : viser accessibilite > 70 (petits AO, peu concurrence)
    Phase 2 (croissance) : viser accessibilite 50-70 (AO moyens)
    Phase 3 (maturite) : accepter accessibilite 30-50 (gros AO, forte concurrence)

    Returns:
        dict avec: score (0-100), phase, recommandation, priorite
    """
    score = 50  # Base

    # 1. Concurrence (40% du score)
    conc_score = concurrence_est.get("score", 50)
    # Inverser : faible concurrence = haute accessibilite
    score_conc = max(0, 100 - conc_score)
    score += (score_conc - 50) * 0.4

    # 2. Budget adapte a Almera (30% du score)
    montant = budget_est.get("montant", 20_000)
    if montant < 5_000:
        score_budget = 30     # Trop petit, pas rentable
    elif montant <= 15_000:
        score_budget = 85     # Ideal phase 1
    elif montant <= 40_000:
        score_budget = 95     # Ideal phase 1-2
    elif montant <= 70_000:
        score_budget = 80     # Bon pour phase 2
    elif montant <= 100_000:
        score_budget = 60     # Phase 2-3
    elif montant <= 200_000:
        score_budget = 40     # Phase 3
    else:
        score_budget = 20     # Tres gros, risque de sous-dimensionnement
    score += (score_budget - 50) * 0.3

    # 3. Adequation competences (20% du score)
    score_pertinence = ao.get("score_pertinence", 0)
    score += (score_pertinence * 100 - 50) * 0.2

    # 4. Avantages concurrentiels specifiques (10% du score)
    nb_avantages = len(concurrence_est.get("avantages_almera", []))
    bonus_avantages = min(20, nb_avantages * 5)
    score += bonus_avantages * 0.1

    # Borner
    score = max(5, min(95, int(score)))

    # Phase recommandee
    if score >= 70:
        phase = "Phase 1 - Construction references"
        recommandation = "AO ideal pour construire vos references. Budget accessible, " \
                        "peu de concurrence, forte adequation. Foncez !"
        priorite = "haute"
    elif score >= 50:
        phase = "Phase 2 - Croissance"
        recommandation = "AO de niveau intermediaire. Bonnes chances avec un dossier " \
                        "solide et des references existantes."
        priorite = "moyenne"
    elif score >= 30:
        phase = "Phase 3 - Maturite"
        recommandation = "AO competitif. Necessit des references solides et une " \
                        "offre tres differenciante pour l'emporter."
        priorite = "faible"
    else:
        phase = "Hors cible"
        recommandation = "AO tres competitif ou hors perimetre. A eviter pour le moment, " \
                        "sauf si enjeu strategique particulier."
        priorite = "tres_faible"

    return {
        "score": score,
        "phase": phase,
        "recommandation": recommandation,
        "priorite": priorite,
        "detail_scores": {
            "concurrence": int(score_conc),
            "budget_adapte": score_budget,
            "pertinence": int(score_pertinence * 100),
            "avantages": bonus_avantages,
        },
    }


# ============================================================
# ACCESSIBILITE DEPUIS DONNEES DECP
# ============================================================

def _calcul_accessibilite(decp: dict) -> dict:
    """Calcule le score d'accessibilite a partir des donnees DECP reelles.

    Formule : accessibilite = 100 - (nb_offres_median * 12) + bonus_niche
    Peu d'offres + budget raisonnable = haute accessibilite.
    """
    concurrence = decp.get("concurrence", {})
    budget = decp.get("budget", {})
    nb_offres_median = concurrence.get("nb_offres_median", 5)
    montant_median = budget.get("median", 20_000)

    # Bonus niche : petits budgets sont moins contestes
    if montant_median < 15_000:
        bonus_niche = 15
    elif montant_median < 40_000:
        bonus_niche = 10
    elif montant_median < 80_000:
        bonus_niche = 5
    else:
        bonus_niche = 0

    score = max(10, min(95, 100 - (nb_offres_median * 12) + bonus_niche))

    if score >= 70:
        phase = "Phase 1 - Construction references"
        recommandation = "AO ideal pour construire vos references. Budget accessible, " \
                        "peu de concurrence d'apres les donnees DECP. Foncez !"
        priorite = "haute"
    elif score >= 50:
        phase = "Phase 2 - Croissance"
        recommandation = "AO de niveau intermediaire. Concurrence moderee " \
                        "d'apres les marches similaires passes."
        priorite = "moyenne"
    elif score >= 30:
        phase = "Phase 3 - Maturite"
        recommandation = "AO competitif d'apres l'historique DECP. " \
                        "Necessit des references solides."
        priorite = "faible"
    else:
        phase = "Hors cible"
        recommandation = "Forte concurrence historique. A eviter sauf enjeu strategique."
        priorite = "tres_faible"

    return {
        "score": score,
        "phase": phase,
        "recommandation": recommandation,
        "priorite": priorite,
        "detail_scores": {
            "nb_offres_median": nb_offres_median,
            "bonus_niche": bonus_niche,
            "montant_median": montant_median,
        },
    }


# ============================================================
# FONCTION PRINCIPALE
# ============================================================

def estimer_marche(ao: dict) -> dict:
    """Estimation complete d'un AO : budget, concurrence, accessibilite.

    Tente d'abord d'utiliser les donnees reelles DECP (data.gouv.fr).
    Si l'API est indisponible ou retourne moins de 5 marches similaires,
    retombe sur l'estimation heuristique existante.

    Args:
        ao: Dictionnaire de l'appel d'offres

    Returns:
        dict avec: budget, concurrence, accessibilite (chacun est un dict)
    """
    # --- Tentative donnees reelles DECP ---
    try:
        decp = rechercher_marches_similaires(ao)
        if decp and decp.get("nb_marches_trouves", 0) >= 5:
            return {
                "budget": {
                    "montant": decp["budget"]["median"],
                    "fourchette": decp["budget"]["fourchette_recommandee"],
                    "confiance": decp["budget"]["confiance"],
                    "source": "DECP - {} marches similaires".format(decp["nb_marches_trouves"]),
                    "details": decp["budget"],
                },
                "concurrence": {
                    "niveau": decp["concurrence"]["niveau"],
                    "concurrence_score": decp["concurrence"]["score"],
                    "nb_offres_median": decp["concurrence"]["nb_offres_median"],
                    "description": decp["concurrence"]["description"],
                    "titulaires_frequents": decp.get("titulaires_frequents", [])[:5],
                    "source": "DECP",
                },
                "accessibilite": _calcul_accessibilite(decp),
                "recommandation_prix": decp.get("recommandation_prix", {}),
                "source": "DECP data.gouv.fr ({} marches)".format(decp["nb_marches_trouves"]),
            }
    except Exception as e:
        logger.warning(f"DECP indisponible, fallback heuristique: {e}")

    # --- Fallback : estimation heuristique ---
    budget = estimer_budget(ao)
    concurrence = estimer_concurrence(ao)
    accessibilite = calculer_accessibilite(budget, concurrence, ao)

    return {
        "budget": budget,
        "concurrence": concurrence,
        "accessibilite": accessibilite,
    }
