"""
Modeles de prix dynamiques pour AO Hunter.

Maintient une base de donnees de prix par type de prestation,
apprend des resultats (gagne/perdu) pour recommander des prix competitifs.

Usage:
    from modeles_prix import recommander_prix, enregistrer_prix, stats_prix
    reco = recommander_prix(ao, type_prestation="formation_intra")
    # reco = {
    #     "tjm_recommande": 1100,
    #     "fourchette": [950, 1300],
    #     "prix_total_estime": 5500,
    #     "strategie": "standard",
    #     "details": "..."
    # }
"""

import json
import logging
from pathlib import Path
from datetime import datetime
from statistics import mean, median, stdev

logger = logging.getLogger("ao_hunter.modeles_prix")

DASHBOARD_DIR = Path(__file__).parent
HISTORIQUE_FILE = DASHBOARD_DIR / "historique_prix.json"

# ============================================================
# PRIX DE REFERENCE ALMERA (defaut quand pas d'historique)
# ============================================================

PRIX_REFERENCE = {
    "formation_intra": {
        "tjm": 1_200,
        "unite": "EUR/jour",
        "description": "Formation intra-entreprise",
    },
    "formation_inter": {
        "tjm": 200,
        "unite": "EUR/personne/jour",
        "description": "Formation inter-entreprises",
    },
    "consulting": {
        "tjm": 1_500,
        "unite": "EUR/jour",
        "description": "Consulting / Audit",
    },
    "taux_horaire": {
        "tjm": 170 * 7,  # 170 EUR/h x 7h = 1190 EUR/jour equivalent
        "taux_horaire": 170,
        "unite": "EUR/h",
        "description": "Taux horaire",
    },
}

# Mapping mots-cles -> type de prestation
MAPPING_TYPE = {
    "formation intra": "formation_intra",
    "intra-entreprise": "formation_intra",
    "intra entreprise": "formation_intra",
    "formation inter": "formation_inter",
    "inter-entreprises": "formation_inter",
    "inter entreprises": "formation_inter",
    "consulting": "consulting",
    "conseil": "consulting",
    "audit": "consulting",
    "diagnostic": "consulting",
    "accompagnement": "consulting",
    "formation": "formation_intra",  # Par defaut, formation = intra
}


# ============================================================
# CHARGEMENT / SAUVEGARDE HISTORIQUE
# ============================================================

def _charger_historique() -> list:
    """Charge l'historique des prix depuis le fichier JSON."""
    if not HISTORIQUE_FILE.exists():
        return []
    try:
        data = json.loads(HISTORIQUE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Erreur lecture historique prix: {e}")
        return []


def _sauvegarder_historique(historique: list):
    """Sauvegarde l'historique des prix."""
    try:
        HISTORIQUE_FILE.write_text(
            json.dumps(historique, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        logger.error(f"Erreur sauvegarde historique prix: {e}")


# ============================================================
# ENREGISTREMENT DE PRIX
# ============================================================

def enregistrer_prix(
    ao_id: str,
    type_prestation: str,
    montant: float,
    nb_jours: float = None,
    nb_personnes: int = None,
    resultat: str = None,
) -> dict:
    """Enregistre un prix soumis (ou gagne/perdu) pour apprentissage.

    Args:
        ao_id: Identifiant de l'AO
        type_prestation: formation_intra, formation_inter, consulting, taux_horaire
        montant: Montant total HT soumis
        nb_jours: Nombre de jours (si connu)
        nb_personnes: Nombre de personnes (si connu)
        resultat: "gagne", "perdu", ou None (en attente)

    Returns:
        dict de l'entree creee
    """
    if resultat and resultat not in ("gagne", "perdu"):
        resultat = None

    # Calculer le TJM
    tjm = None
    if nb_jours and nb_jours > 0:
        tjm = round(montant / nb_jours, 2)

    entree = {
        "ao_id": ao_id,
        "type_prestation": type_prestation,
        "nb_jours": nb_jours,
        "nb_personnes": nb_personnes,
        "montant_total": montant,
        "tjm": tjm,
        "resultat": resultat,
        "date": datetime.now().isoformat(),
    }

    historique = _charger_historique()

    # Mise a jour si meme ao_id + type_prestation existe deja
    existant = False
    for i, h in enumerate(historique):
        if h.get("ao_id") == ao_id and h.get("type_prestation") == type_prestation:
            historique[i] = entree
            existant = True
            break

    if not existant:
        historique.append(entree)

    _sauvegarder_historique(historique)
    logger.info(f"Prix enregistre: AO {ao_id}, {type_prestation}, {montant} EUR, resultat={resultat}")
    return entree


# ============================================================
# STATISTIQUES PAR TYPE DE PRESTATION
# ============================================================

def stats_prix(type_prestation: str = None) -> dict:
    """Calcule des statistiques de prix par type de prestation.

    Args:
        type_prestation: Filtre optionnel sur un type

    Returns:
        dict avec stats globales et par type
    """
    historique = _charger_historique()

    if not historique:
        return {
            "total_entrees": 0,
            "par_type": {},
            "message": "Aucun historique de prix. Les donnees se rempliront au fur et a mesure des AO.",
        }

    # Filtrer par type si demande
    if type_prestation:
        historique = [h for h in historique if h.get("type_prestation") == type_prestation]

    # Grouper par type
    par_type = {}
    for h in historique:
        tp = h.get("type_prestation", "inconnu")
        if tp not in par_type:
            par_type[tp] = []
        par_type[tp].append(h)

    stats = {}
    for tp, entrees in par_type.items():
        tjms = [e["tjm"] for e in entrees if e.get("tjm") and e["tjm"] > 0]
        montants = [e["montant_total"] for e in entrees if e.get("montant_total")]
        gagnes = [e for e in entrees if e.get("resultat") == "gagne"]
        perdus = [e for e in entrees if e.get("resultat") == "perdu"]
        en_attente = [e for e in entrees if not e.get("resultat")]

        tjm_gagnes = [e["tjm"] for e in gagnes if e.get("tjm") and e["tjm"] > 0]
        tjm_perdus = [e["tjm"] for e in perdus if e.get("tjm") and e["tjm"] > 0]

        stat = {
            "nb_entrees": len(entrees),
            "nb_gagnes": len(gagnes),
            "nb_perdus": len(perdus),
            "nb_en_attente": len(en_attente),
            "taux_reussite": round(len(gagnes) / max(1, len(gagnes) + len(perdus)) * 100, 1),
        }

        if tjms:
            stat["tjm_moyen"] = round(mean(tjms), 2)
            stat["tjm_median"] = round(median(tjms), 2)
            stat["tjm_min"] = min(tjms)
            stat["tjm_max"] = max(tjms)

        if tjm_gagnes:
            stat["tjm_moyen_gagnant"] = round(mean(tjm_gagnes), 2)
        if tjm_perdus:
            stat["tjm_moyen_perdant"] = round(mean(tjm_perdus), 2)

        # Fourchette competitive = entre TJM moyen gagnant et reference
        ref = PRIX_REFERENCE.get(tp, {}).get("tjm")
        if tjm_gagnes and len(tjm_gagnes) >= 2:
            stat["fourchette_competitive"] = [
                round(min(tjm_gagnes)),
                round(max(tjm_gagnes)),
            ]
        elif ref:
            stat["fourchette_competitive"] = [
                round(ref * 0.8),
                round(ref * 1.15),
            ]

        # Prix plancher et plafond
        if montants:
            stat["prix_plancher"] = min(montants)
            stat["prix_plafond"] = max(montants)
            stat["montant_moyen"] = round(mean(montants), 2)

        stats[tp] = stat

    return {
        "total_entrees": len(historique),
        "par_type": stats,
        "types_disponibles": list(PRIX_REFERENCE.keys()),
        "prix_reference": {k: v["tjm"] for k, v in PRIX_REFERENCE.items()},
    }


# ============================================================
# RECOMMANDATION DE PRIX
# ============================================================

def _detecter_type_prestation(ao: dict) -> str:
    """Detecte le type de prestation a partir du contenu de l'AO."""
    titre = (ao.get("titre") or "").lower()
    description = (ao.get("description") or "").lower()
    texte = f"{titre} {description}"

    for mot_cle, tp in MAPPING_TYPE.items():
        if mot_cle in texte:
            return tp

    # Par defaut : formation intra (coeur de metier Almera)
    return "formation_intra"


def recommander_prix(ao: dict, type_prestation: str = "formation_intra") -> dict:
    """Recommande un prix pour un AO base sur l'historique et l'estimation marche.

    Args:
        ao: Dictionnaire de l'appel d'offres
        type_prestation: Type de prestation (formation_intra, formation_inter, consulting, taux_horaire)

    Returns:
        dict avec: tjm_recommande, fourchette, prix_total_estime, strategie, details
    """
    # Detecter le type si "formation" generique
    if type_prestation == "formation":
        type_prestation = _detecter_type_prestation(ao)

    historique = _charger_historique()
    entrees_type = [h for h in historique if h.get("type_prestation") == type_prestation]
    gagnes = [h for h in entrees_type if h.get("resultat") == "gagne"]
    tjm_gagnes = [h["tjm"] for h in gagnes if h.get("tjm") and h["tjm"] > 0]

    # TJM de reference
    ref = PRIX_REFERENCE.get(type_prestation, PRIX_REFERENCE["formation_intra"])
    tjm_ref = ref["tjm"]

    # --- Determiner le TJM recommande ---
    if tjm_gagnes and len(tjm_gagnes) >= 3:
        # Assez d'historique gagnant : utiliser la moyenne gagnante
        tjm_base = round(mean(tjm_gagnes))
        source_tjm = f"Moyenne historique gagnante ({len(tjm_gagnes)} AO)"
    elif tjm_gagnes:
        # Peu d'historique : ponderer entre historique et reference
        tjm_hist = mean(tjm_gagnes)
        poids_hist = len(tjm_gagnes) / 3  # 0.33 pour 1, 0.66 pour 2
        tjm_base = round(tjm_hist * poids_hist + tjm_ref * (1 - poids_hist))
        source_tjm = f"Mix historique ({len(tjm_gagnes)} AO) + reference Almera"
    else:
        # Pas d'historique : utiliser le prix de reference
        tjm_base = tjm_ref
        source_tjm = "Prix de reference Almera (pas encore d'historique)"

    # --- Ajuster selon le budget estime de l'AO ---
    budget_ao = ao.get("budget_estime", 0) or 0
    ajustement_budget = ""

    # Essayer d'utiliser estimation_marche si disponible
    accessibilite = None
    try:
        from estimation_marche import estimer_marche
        estimation = estimer_marche(ao)
        if not budget_ao:
            budget_ao = estimation.get("budget", {}).get("montant", 0)
            ajustement_budget = f"Budget estime via estimation_marche: {budget_ao:,} EUR. "
        accessibilite = estimation.get("accessibilite", {}).get("score", 50)
    except ImportError:
        pass

    # --- Determiner la strategie ---
    if accessibilite is not None:
        if accessibilite >= 70:
            strategie = "standard"
            coeff = 1.0
            explication_strategie = "AO tres accessible, prix standard pour maximiser la marge"
        elif accessibilite >= 50:
            strategie = "standard"
            coeff = 0.95
            explication_strategie = "AO moyennement accessible, prix legerement competitif"
        elif accessibilite >= 30:
            strategie = "agressif"
            coeff = 0.85
            explication_strategie = "AO competitif, prix agressif pour maximiser les chances"
        else:
            strategie = "agressif"
            coeff = 0.80
            explication_strategie = "AO tres competitif, prix tres agressif necessaire"
    else:
        # Sans estimation d'accessibilite, strategie standard
        strategie = "standard"
        coeff = 1.0
        explication_strategie = "Strategie standard (pas d'estimation d'accessibilite)"

    # Si le budget AO est connu, ajuster pour viser 80-90%
    if budget_ao > 0:
        # Estimer le nb de jours pour comparer
        nb_jours_estime = _estimer_nb_jours(ao)
        prix_total_budget = budget_ao * 0.85  # Viser 85% du budget
        tjm_budget = prix_total_budget / max(1, nb_jours_estime)

        # Ne pas descendre en dessous du TJM reference * 0.7
        tjm_plancher = tjm_ref * 0.7
        tjm_budget = max(tjm_budget, tjm_plancher)

        # Ponderer entre TJM historique et TJM budget
        tjm_recommande = round((tjm_base * coeff * 0.6 + tjm_budget * 0.4))
        ajustement_budget += f"Ajuste pour viser ~85% du budget estime."
    else:
        tjm_recommande = round(tjm_base * coeff)

    # --- Fourchette ---
    fourchette_min = round(tjm_recommande * 0.85)
    fourchette_max = round(tjm_recommande * 1.15)

    # Si on a des stats de gagnes, affiner la fourchette
    if tjm_gagnes and len(tjm_gagnes) >= 2:
        fourchette_min = max(fourchette_min, round(min(tjm_gagnes) * 0.9))
        fourchette_max = min(fourchette_max, round(max(tjm_gagnes) * 1.1))
        # S'assurer que min < recommande < max
        fourchette_min = min(fourchette_min, tjm_recommande - 50)
        fourchette_max = max(fourchette_max, tjm_recommande + 50)

    # --- Prix total estime ---
    nb_jours_estime = _estimer_nb_jours(ao)
    prix_total_estime = tjm_recommande * nb_jours_estime

    # --- Construire le detail ---
    details = (
        f"Type: {type_prestation} | TJM reference: {tjm_ref} EUR | "
        f"Source TJM: {source_tjm} | "
        f"Nb jours estime: {nb_jours_estime} | "
        f"Strategie: {strategie} (coeff {coeff}) - {explication_strategie}"
    )
    if ajustement_budget:
        details += f" | Budget: {ajustement_budget}"

    return {
        "tjm_recommande": tjm_recommande,
        "fourchette": [fourchette_min, fourchette_max],
        "prix_total_estime": prix_total_estime,
        "nb_jours_estime": nb_jours_estime,
        "strategie": strategie,
        "type_prestation": type_prestation,
        "source_tjm": source_tjm,
        "details": details,
    }


def _estimer_nb_jours(ao: dict) -> int:
    """Estime le nombre de jours de prestation a partir de l'AO."""
    import re
    titre = (ao.get("titre") or "").lower()
    description = (ao.get("description") or "").lower()
    texte = f"{titre} {description}"

    # Chercher nb jours explicite
    match = re.search(r"(\d+)\s*(?:jours?|journees?)\s*(?:de\s*)?(?:formation|prestation)?", texte)
    if match:
        jours = int(match.group(1))
        if 1 <= jours <= 200:
            return jours

    # Chercher nb heures et convertir
    match = re.search(r"(\d+)\s*(?:heures?|h)\s*(?:de\s*)?(?:formation|prestation)?", texte)
    if match:
        heures = int(match.group(1))
        if 1 <= heures <= 2000:
            return max(1, heures // 7)

    # Chercher nb sessions/groupes
    match = re.search(r"(\d+)\s*(?:sessions?|groupes?)", texte)
    if match:
        sessions = int(match.group(1))
        if 1 <= sessions <= 50:
            return sessions * 2  # 2 jours par session en moyenne

    # Estimation par budget si connu
    budget = ao.get("budget_estime", 0) or 0
    if budget > 0:
        return max(1, round(budget / 1200))  # TJM moyen 1200

    # Defaut : 5 jours (formation courte standard)
    return 5
