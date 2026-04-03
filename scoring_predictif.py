"""
Scoring predictif base sur l'historique gagne/perdu.

Analyse les AO passes pour calibrer un modele statistique simple (pure Python,
pas de numpy/scikit-learn) qui predit la probabilite de victoire sur les nouveaux AO.

Features utilisees :
- score_pertinence (0-1)
- type_procedure (MAPA > ouvert > restreint ...)
- source (BOAMP, TED, etc.)
- budget_range (fourchette budgetaire)
- type_acheteur (collectivite, education, sante ...)
- delai_reponse_jours (temps avant deadline)
- nb_mots_cles_ia (mots-cles IA dans titre+description)
- has_formation (bool)
- has_conseil (bool)

Usage:
    from scoring_predictif import construire_modele, predire_victoire, calibrer_auto, stats_modele
    modele = construire_modele()
    prediction = predire_victoire(ao)
"""

import json
import re
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger("ao_hunter.scoring_predictif")

DASHBOARD_DIR = Path(__file__).parent
AO_CACHE = DASHBOARD_DIR / "ao_pertinents.json"
MODELE_FILE = DASHBOARD_DIR / "modele_scoring.json"

# Mots-cles IA pour le comptage
MOTS_CLES_IA = [
    "intelligence artificielle", "ia", "machine learning", "deep learning",
    "chatbot", "nlp", "traitement automatique", "data science",
    "donnees", "algorithme", "automatisation", "chatgpt", "gpt",
    "llm", "generative", "prompt", "transformation digitale",
    "transformation numerique", "digitalisation", "numerique",
    "cloud", "big data", "python", "api", "deploiement ia",
]

MOTS_CLES_FORMATION = [
    "formation", "formateur", "pedagogie", "stagiaire", "apprenant",
    "competences", "certification", "qualiopi", "cpf", "opco",
    "apprentissage", "enseignement", "e-learning", "module",
    "programme", "parcours", "sensibilisation",
]

MOTS_CLES_CONSEIL = [
    "conseil", "consulting", "consultant", "accompagnement", "audit",
    "strategie", "diagnostic", "expertise", "mission", "etude",
    "preconisation", "recommandation", "assistance",
]


def _charger_ao() -> list:
    """Charge la base AO."""
    if not AO_CACHE.exists():
        return []
    try:
        return json.loads(AO_CACHE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _sauvegarder_modele(modele: dict):
    """Sauvegarde le modele dans modele_scoring.json."""
    MODELE_FILE.write_text(json.dumps(modele, ensure_ascii=False, indent=2), encoding="utf-8")


def _charger_modele() -> dict | None:
    """Charge le modele depuis modele_scoring.json."""
    if not MODELE_FILE.exists():
        return None
    try:
        return json.loads(MODELE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


# --- Extraction de features ---

def _score_type_procedure(ao: dict) -> float:
    """Score selon le type de procedure."""
    proc = (ao.get("type_procedure") or "").lower()
    if "mapa" in proc or "adapte" in proc:
        return 1.0
    if "ouvert" in proc:
        return 0.7
    if "restreint" in proc:
        return 0.5
    if "negocie" in proc:
        return 0.6
    if "concours" in proc:
        return 0.3
    return 0.5  # NC / inconnu


def _score_source(ao: dict) -> float:
    """Score selon la source."""
    source = (ao.get("source") or "").upper()
    mapping = {
        "BOAMP": 0.8,
        "PLACE": 0.9,
        "TED": 0.6,
        "MSEC": 0.7,
        "AWS": 0.5,
        "RSS": 0.6,
    }
    return mapping.get(source, 0.5)


def _score_budget_range(ao: dict) -> float:
    """Score selon la fourchette budgetaire."""
    budget = ao.get("budget_estime")
    if not budget:
        return 0.6  # neutre si inconnu
    try:
        budget = float(budget)
    except (ValueError, TypeError):
        return 0.6
    if budget <= 5000:
        return 1.0
    if budget <= 20000:
        return 0.9
    if budget <= 50000:
        return 0.7
    if budget <= 100000:
        return 0.5
    return 0.3


def _score_type_acheteur(ao: dict) -> float:
    """Score selon le type d'acheteur."""
    acheteur = (ao.get("acheteur") or "").lower()
    # Collectivites territoriales
    if any(k in acheteur for k in ["mairie", "commune", "departement", "region",
                                     "metropole", "agglo", "communaute", "collectivite"]):
        return 0.8
    # Education / formation
    if any(k in acheteur for k in ["universite", "ecole", "cnfpt", "opco",
                                     "education", "rectorat", "academie", "formation"]):
        return 0.7
    # Sante
    if any(k in acheteur for k in ["hopital", "chu", "ars", "sante", "ehpad"]):
        return 0.5
    # Etat / ministere
    if any(k in acheteur for k in ["ministere", "prefecture", "etat", "dgfip",
                                     "direction", "dinum"]):
        return 0.6
    # Entreprises publiques
    if any(k in acheteur for k in ["sncf", "ratp", "edf", "engie",
                                     "la poste", "orange"]):
        return 0.4
    return 0.6  # defaut


def _score_delai_reponse(ao: dict) -> float:
    """Score selon le delai de reponse restant."""
    dl = ao.get("date_limite")
    if not dl:
        return 0.5  # neutre si pas de deadline
    try:
        date_dl = datetime.fromisoformat(dl.split("T")[0])
        jours = (date_dl - datetime.now()).days
        if jours > 30:
            return 1.0
        if jours >= 20:
            return 0.8
        if jours >= 10:
            return 0.5
        if jours >= 0:
            return 0.2
        return 0.0  # expire
    except (ValueError, TypeError):
        return 0.5


def _compter_mots_cles_ia(ao: dict) -> int:
    """Compte le nombre de mots-cles IA dans titre + description."""
    texte = ((ao.get("titre") or "") + " " + (ao.get("description") or "")).lower()
    count = 0
    for kw in MOTS_CLES_IA:
        if kw in texte:
            count += 1
    return count


def _has_formation(ao: dict) -> bool:
    """Detecte si l'AO concerne de la formation."""
    texte = ((ao.get("titre") or "") + " " + (ao.get("description") or "")).lower()
    return any(kw in texte for kw in MOTS_CLES_FORMATION)


def _has_conseil(ao: dict) -> bool:
    """Detecte si l'AO concerne du conseil."""
    texte = ((ao.get("titre") or "") + " " + (ao.get("description") or "")).lower()
    return any(kw in texte for kw in MOTS_CLES_CONSEIL)


def extraire_features(ao: dict) -> dict:
    """Extrait toutes les features d'un AO. Retourne un dict feature -> valeur 0-1."""
    nb_mc = _compter_mots_cles_ia(ao)
    # Normaliser nb_mots_cles_ia entre 0 et 1 (max ~10 mots-cles)
    nb_mc_norm = min(nb_mc / 10.0, 1.0)

    return {
        "score_pertinence": ao.get("score_pertinence", 0) or 0,
        "type_procedure": _score_type_procedure(ao),
        "source": _score_source(ao),
        "budget_range": _score_budget_range(ao),
        "type_acheteur": _score_type_acheteur(ao),
        "delai_reponse": _score_delai_reponse(ao),
        "nb_mots_cles_ia": nb_mc_norm,
        "has_formation": 1.0 if _has_formation(ao) else 0.0,
        "has_conseil": 1.0 if _has_conseil(ao) else 0.0,
    }


# --- Construction du modele ---

def construire_modele() -> dict:
    """
    Construit le modele de scoring a partir de l'historique gagne/perdu.

    Calcule la moyenne ponderee de chaque feature pour les AO gagnes vs perdus,
    puis derive les poids optimaux (ratio gagne/perdu normalise).

    Retourne {poids: {feature: weight}, stats: {nb_gagnes, nb_perdus, precision_estimee}}
    """
    appels = _charger_ao()
    gagnes = [ao for ao in appels if ao.get("statut") == "gagne"]
    perdus = [ao for ao in appels if ao.get("statut") == "perdu"]

    nb_gagnes = len(gagnes)
    nb_perdus = len(perdus)

    if nb_gagnes + nb_perdus < 2:
        # Pas assez de donnees : poids par defaut
        poids_defaut = {
            "score_pertinence": 0.25,
            "type_procedure": 0.10,
            "source": 0.05,
            "budget_range": 0.10,
            "type_acheteur": 0.10,
            "delai_reponse": 0.10,
            "nb_mots_cles_ia": 0.15,
            "has_formation": 0.10,
            "has_conseil": 0.05,
        }
        modele = {
            "poids": poids_defaut,
            "moyennes_gagnes": {},
            "moyennes_perdus": {},
            "stats": {
                "nb_gagnes": nb_gagnes,
                "nb_perdus": nb_perdus,
                "precision_estimee": 0.0,
                "derniere_calibration": datetime.now().isoformat(),
                "message": "Pas assez de donnees - poids par defaut",
            },
        }
        _sauvegarder_modele(modele)
        return modele

    # Calculer les features moyennes pour gagnes et perdus
    feature_names = list(extraire_features(gagnes[0]).keys()) if gagnes else list(extraire_features(perdus[0]).keys())

    moyennes_gagnes = {}
    moyennes_perdus = {}

    for feat in feature_names:
        if gagnes:
            vals_g = [extraire_features(ao)[feat] for ao in gagnes]
            moyennes_gagnes[feat] = sum(vals_g) / len(vals_g)
        else:
            moyennes_gagnes[feat] = 0.5

        if perdus:
            vals_p = [extraire_features(ao)[feat] for ao in perdus]
            moyennes_perdus[feat] = sum(vals_p) / len(vals_p)
        else:
            moyennes_perdus[feat] = 0.5

    # Calculer les poids : ecart gagne-perdu normalise
    ecarts = {}
    for feat in feature_names:
        ecart = moyennes_gagnes[feat] - moyennes_perdus[feat]
        ecarts[feat] = abs(ecart)  # plus l'ecart est grand, plus la feature discrimine

    # Normaliser pour que la somme = 1
    total_ecarts = sum(ecarts.values())
    if total_ecarts > 0:
        poids = {feat: ecarts[feat] / total_ecarts for feat in feature_names}
    else:
        # Pas de discrimination => poids egaux
        poids = {feat: 1.0 / len(feature_names) for feat in feature_names}

    # Minimum weight: aucune feature a 0 (plancher a 0.02)
    for feat in poids:
        if poids[feat] < 0.02:
            poids[feat] = 0.02
    total_p = sum(poids.values())
    poids = {feat: poids[feat] / total_p for feat in poids}

    # Precision estimee par leave-one-out
    precision = _leave_one_out(gagnes, perdus, poids, moyennes_gagnes)

    modele = {
        "poids": poids,
        "moyennes_gagnes": moyennes_gagnes,
        "moyennes_perdus": moyennes_perdus,
        "stats": {
            "nb_gagnes": nb_gagnes,
            "nb_perdus": nb_perdus,
            "precision_estimee": round(precision * 100, 1),
            "derniere_calibration": datetime.now().isoformat(),
        },
    }
    _sauvegarder_modele(modele)
    logger.info(f"Modele scoring recalcule: {nb_gagnes} gagnes, {nb_perdus} perdus, precision {precision*100:.1f}%")
    return modele


def _leave_one_out(gagnes: list, perdus: list, poids: dict, moyennes_gagnes: dict) -> float:
    """
    Cross-validation leave-one-out simple.
    Pour chaque AO, calcule le score sans lui et verifie si la prediction est correcte.
    """
    tous = [(ao, True) for ao in gagnes] + [(ao, False) for ao in perdus]
    if len(tous) < 3:
        return 0.5  # pas assez de donnees

    correct = 0
    for i, (ao_test, est_gagne) in enumerate(tous):
        # Score de l'AO test avec les poids actuels
        features = extraire_features(ao_test)
        score = 0.0
        for feat, val in features.items():
            # Proximite avec la moyenne des gagnes
            moy_g = moyennes_gagnes.get(feat, 0.5)
            proximite = 1.0 - abs(val - moy_g)
            score += poids.get(feat, 0) * proximite

        # Prediction : gagne si score > 0.5
        prediction_gagne = score > 0.5
        if prediction_gagne == est_gagne:
            correct += 1

    return correct / len(tous) if tous else 0.5


# --- Prediction ---

def predire_victoire(ao: dict, modele: dict = None) -> dict:
    """
    Predit la probabilite de victoire pour un AO.

    Retourne {
        score_prediction,
        probabilite_victoire_pct,
        features_detail: {feature: {valeur, poids, contribution}},
        confiance: "haute"/"moyenne"/"faible",
        comparaison: "meilleur que X% des AO gagnes"
    }
    """
    if modele is None:
        modele = _charger_modele()
    if modele is None:
        modele = construire_modele()

    poids = modele.get("poids", {})
    moyennes_gagnes = modele.get("moyennes_gagnes", {})
    stats = modele.get("stats", {})

    features = extraire_features(ao)
    features_detail = {}
    score_total = 0.0

    for feat, val in features.items():
        p = poids.get(feat, 0)
        # Proximite avec la moyenne des gagnes (si on a des donnees)
        if moyennes_gagnes:
            moy_g = moyennes_gagnes.get(feat, 0.5)
            proximite = 1.0 - abs(val - moy_g)
            contribution = p * proximite
        else:
            contribution = p * val

        score_total += contribution
        features_detail[feat] = {
            "valeur": round(val, 3),
            "poids": round(p, 3),
            "contribution": round(contribution, 3),
        }

    # Normaliser le score entre 0 et 100
    score_pct = max(0, min(100, int(score_total * 100)))

    # Confiance basee sur le nombre de donnees d'entrainement
    nb_total = stats.get("nb_gagnes", 0) + stats.get("nb_perdus", 0)
    if nb_total >= 20:
        confiance = "haute"
    elif nb_total >= 10:
        confiance = "moyenne"
    else:
        confiance = "faible"

    # Comparaison avec les AO gagnes
    comparaison = _comparer_avec_gagnes(score_total, modele)

    return {
        "score_prediction": score_pct,
        "probabilite_victoire_pct": score_pct,
        "features_detail": features_detail,
        "confiance": confiance,
        "comparaison": comparaison,
        "nb_donnees": nb_total,
        "precision_modele": stats.get("precision_estimee", 0),
    }


def _comparer_avec_gagnes(score: float, modele: dict) -> str:
    """Compare le score avec les AO gagnes pour donner un percentile."""
    appels = _charger_ao()
    gagnes = [ao for ao in appels if ao.get("statut") == "gagne"]
    if not gagnes:
        return "Pas encore d'AO gagnes pour comparer"

    poids = modele.get("poids", {})
    moyennes_gagnes = modele.get("moyennes_gagnes", {})

    scores_gagnes = []
    for ao in gagnes:
        features = extraire_features(ao)
        s = 0.0
        for feat, val in features.items():
            p = poids.get(feat, 0)
            if moyennes_gagnes:
                moy_g = moyennes_gagnes.get(feat, 0.5)
                proximite = 1.0 - abs(val - moy_g)
                s += p * proximite
            else:
                s += p * val
        scores_gagnes.append(s)

    if not scores_gagnes:
        return "Pas encore d'AO gagnes pour comparer"

    # Pourcentage d'AO gagnes avec un score inferieur
    nb_inferieurs = sum(1 for s in scores_gagnes if s <= score)
    pct = int(nb_inferieurs / len(scores_gagnes) * 100)
    return f"Meilleur que {pct}% des AO gagnes"


# --- Calibration automatique ---

def calibrer_auto() -> dict | None:
    """
    Recalibre le modele automatiquement.
    Appele quand un AO passe en gagne/perdu.
    Ne recalcule que si on a >= 5 AO avec resultat.
    """
    appels = _charger_ao()
    avec_resultat = [ao for ao in appels if ao.get("statut") in ("gagne", "perdu")]

    if len(avec_resultat) < 5:
        logger.info(f"Calibration auto: seulement {len(avec_resultat)} AO avec resultat (min 5)")
        return None

    modele = construire_modele()
    logger.info(
        f"Calibration auto: modele recalcule avec {modele['stats']['nb_gagnes']} gagnes "
        f"et {modele['stats']['nb_perdus']} perdus "
        f"(precision: {modele['stats']['precision_estimee']}%)"
    )
    return modele


# --- Stats du modele ---

def stats_modele() -> dict:
    """
    Retourne les stats du modele :
    nb donnees, features les plus discriminantes, precision, derniere calibration.
    """
    modele = _charger_modele()
    if modele is None:
        modele = construire_modele()

    poids = modele.get("poids", {})
    stats = modele.get("stats", {})

    # Trier les features par poids (les plus discriminantes d'abord)
    features_triees = sorted(poids.items(), key=lambda x: x[1], reverse=True)

    # Labels humains pour les features
    labels = {
        "score_pertinence": "Score de pertinence",
        "type_procedure": "Type de procedure",
        "source": "Source de veille",
        "budget_range": "Fourchette budgetaire",
        "type_acheteur": "Type d'acheteur",
        "delai_reponse": "Delai de reponse",
        "nb_mots_cles_ia": "Mots-cles IA",
        "has_formation": "Formation detectee",
        "has_conseil": "Conseil detecte",
    }

    features_detail = []
    for feat, weight in features_triees:
        features_detail.append({
            "nom": feat,
            "label": labels.get(feat, feat),
            "poids": round(weight, 4),
            "poids_pct": round(weight * 100, 1),
            "moyenne_gagnes": round(modele.get("moyennes_gagnes", {}).get(feat, 0), 3),
            "moyenne_perdus": round(modele.get("moyennes_perdus", {}).get(feat, 0), 3),
        })

    return {
        "nb_gagnes": stats.get("nb_gagnes", 0),
        "nb_perdus": stats.get("nb_perdus", 0),
        "nb_total": stats.get("nb_gagnes", 0) + stats.get("nb_perdus", 0),
        "precision_estimee": stats.get("precision_estimee", 0),
        "derniere_calibration": stats.get("derniere_calibration", "Jamais"),
        "features": features_detail,
        "message": stats.get("message", ""),
    }
