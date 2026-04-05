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
        "tjm": 1_500,
        "unite": "EUR/jour",
        "description": "Formation intra-entreprise (presentiel, par groupe)",
        "tjm_distanciel": 1_200,
        "demi_journee": 850,
    },
    "formation_inter": {
        "tjm": 300,
        "unite": "EUR/personne/jour",
        "description": "Formation inter-entreprises / e-learning",
    },
    "consulting": {
        "tjm": 1_400,
        "unite": "EUR/jour",
        "description": "Conseil / Audit / Diagnostic / Strategie IA",
    },
    "taux_horaire": {
        "tjm": 200 * 7,  # 200 EUR/h x 7h = 1400 EUR/jour equivalent
        "taux_horaire": 200,
        "unite": "EUR/h",
        "description": "Taux horaire (suivi individuel, coaching)",
    },
}

# ============================================================
# BENCHMARKS MARCHE (sources croisees mai 2025)
# ============================================================
# Sources : CNFCE, Plateya, Silkhom, RH Solutions, Free-Work,
#           Blog du Moderateur, OPCO Atlas, Seminaire.ai, DECP
#
# Ces donnees servent de garde-fou : on ne veut pas etre >20%
# au-dessus de la mediane marche (trop cher = elimination),
# ni >30% en dessous (pas credible, signale du low-cost).

BENCHMARK_MARCHE = {
    "formation_intra": {
        # Prix organisme par jour/groupe (8-12 pers), presentiel
        "tjm_bas": 800,       # Formateurs generalistes, regions
        "tjm_median": 1_500,  # Median marche formation IA/numerique
        "tjm_haut": 3_000,    # Formations premium IA avancee
        "source": "CNFCE, Plateya, NoCode IA, formation-ia.blog",
    },
    "formation_inter": {
        # Prix par personne/jour (catalogue)
        "tjm_bas": 450,
        "tjm_median": 900,
        "tjm_haut": 1_300,
        "source": "EFE, catalogues organismes certifies",
    },
    "consulting": {
        # TJM consultant transformation digitale/IA
        "tjm_bas": 600,
        "tjm_median": 900,
        "tjm_haut": 1_500,
        "source": "Silkhom, RH Solutions, Blog du Moderateur 2025",
    },
    "taux_horaire": {
        "tjm_bas": 700,       # Formateur IA freelance
        "tjm_median": 1_000,
        "tjm_haut": 1_500,
        "source": "Plateya, Juwa, Free-Work",
    },
    # OPCO : plafond remboursement (pas un prix de vente)
    "opco_plafond_horaire": 40,   # OPCO Atlas - 40 EUR/h max
    "opco_plafond_jour": 280,     # 40 EUR x 7h
}

# ============================================================
# GRILLE TARIFAIRE ADAPTATIVE PAR PALIER DE BUDGET
# ============================================================
# Philosophie : on veut GAGNER les AO tout en margeant.
# Calibre sur les benchmarks marche :
# - Median formation intra = 1 500 EUR/j -> notre prix catalogue
# - Petit budget : on descend a ~1 100-1 200 (toujours au-dessus du plancher)
# - Gros budget : on monte a ~1 650-1 750 (reste sous le haut du marche a 3 000)
#
# Pour le conseil, la mediane marche est a 900 EUR/j mais les experts IA
# sont a 1 000-1 500 -> notre ref a 1 400 se justifie par l'expertise IA.

PALIERS_BUDGET = {
    "formation_intra": [
        # (budget_max, coeff_tjm, nom_palier)
        (15_000,  0.78, "competitif"),    # TJM ~1 170 EUR - sous la mediane pour gagner
        (40_000,  0.90, "standard_bas"),  # TJM ~1 350 EUR - proche mediane
        (80_000,  1.00, "standard"),      # TJM ~1 500 EUR (= mediane marche)
        (150_000, 1.10, "premium"),       # TJM ~1 650 EUR - au-dessus mais justifie
        (999_999, 1.17, "premium_plus"),  # TJM ~1 750 EUR - reste < haut marche (3k)
    ],
    "consulting": [
        (15_000,  0.72, "competitif"),    # TJM ~1 000 EUR - median expert IA
        (40_000,  0.86, "standard_bas"),  # TJM ~1 200 EUR
        (80_000,  1.00, "standard"),      # TJM ~1 400 EUR (ref catalogue)
        (150_000, 1.07, "premium"),       # TJM ~1 500 EUR = haut marche
        (999_999, 1.07, "premium_plus"),  # Plafonne a 1 500 (au-dela pas credible)
    ],
    "formation_inter": [
        (15_000,  0.80, "competitif"),
        (40_000,  0.90, "standard_bas"),
        (80_000,  1.00, "standard"),
        (150_000, 1.10, "premium"),
        (999_999, 1.15, "premium_plus"),
    ],
    "taux_horaire": [
        (15_000,  0.80, "competitif"),
        (40_000,  0.90, "standard_bas"),
        (80_000,  1.00, "standard"),
        (150_000, 1.05, "premium"),
        (999_999, 1.10, "premium_plus"),
    ],
}

# TJM plancher absolu (ne jamais descendre en dessous)
# Base sur le bas du marche : on ne veut pas faire du low-cost
TJM_PLANCHER = {
    "formation_intra": 1_000,   # Bas marche formation IA = 800, on garde de la marge
    "formation_inter": 200,
    "consulting": 800,          # Bas marche conseil = 600, marge de securite
    "taux_horaire": 900,
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
                round(ref * 0.78),
                round(ref * 1.10),
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


def _coeff_palier_budget(budget: float, type_prestation: str = "formation_intra") -> tuple:
    """Retourne (coefficient, nom_palier) selon le budget et le type de prestation."""
    if budget <= 0:
        return 1.0, "standard"
    paliers = PALIERS_BUDGET.get(type_prestation, PALIERS_BUDGET["formation_intra"])
    for budget_max, coeff, nom in paliers:
        if budget <= budget_max:
            return coeff, nom
    return paliers[-1][1], paliers[-1][2]


def recommander_prix(ao: dict, type_prestation: str = "formation_intra") -> dict:
    """Recommande un prix pour un AO base sur l'historique, le budget et le marche.

    Strategie adaptative :
    - Petit budget (<15k) : prix competitifs pour gagner (coeff 0.78)
    - Budget moyen (15-80k) : prix standard (coeff 0.89-1.0)
    - Gros budget (>80k) : prix premium, maximiser la marge (coeff 1.05-1.10)
    - L'historique des AO gagnes/perdus affine progressivement les prix

    Args:
        ao: Dictionnaire de l'appel d'offres
        type_prestation: Type de prestation

    Returns:
        dict avec: tjm_recommande, fourchette, prix_total_estime, strategie, palier, details
    """
    # Detecter le type si "formation" generique
    if type_prestation == "formation":
        type_prestation = _detecter_type_prestation(ao)

    historique = _charger_historique()
    entrees_type = [h for h in historique if h.get("type_prestation") == type_prestation]
    gagnes = [h for h in entrees_type if h.get("resultat") == "gagne"]
    tjm_gagnes = [h["tjm"] for h in gagnes if h.get("tjm") and h["tjm"] > 0]

    # TJM de reference (prix catalogue)
    ref = PRIX_REFERENCE.get(type_prestation, PRIX_REFERENCE["formation_intra"])
    tjm_ref = ref["tjm"]
    plancher = TJM_PLANCHER.get(type_prestation, 1_200)

    # --- Determiner le TJM de base (historique ou reference) ---
    if tjm_gagnes and len(tjm_gagnes) >= 3:
        tjm_base = round(mean(tjm_gagnes))
        source_tjm = f"Moyenne historique gagnante ({len(tjm_gagnes)} AO)"
    elif tjm_gagnes:
        tjm_hist = mean(tjm_gagnes)
        poids_hist = len(tjm_gagnes) / 3
        tjm_base = round(tjm_hist * poids_hist + tjm_ref * (1 - poids_hist))
        source_tjm = f"Mix historique ({len(tjm_gagnes)} AO) + reference Almera"
    else:
        tjm_base = tjm_ref
        source_tjm = "Prix catalogue Almera (pas encore d'historique)"

    # --- Recuperer le budget de l'AO ---
    budget_ao = ao.get("budget_estime", 0) or 0
    budget_declare = budget_ao > 0  # True si l'acheteur a publie un budget
    ajustement_budget = ""

    # Essayer estimation_marche si disponible (pour accessibilite surtout)
    accessibilite = None
    try:
        from estimation_marche import estimer_marche
        estimation = estimer_marche(ao)
        if not budget_ao:
            budget_ao = estimation.get("budget", {}).get("montant", 0)
            ajustement_budget = f"Budget estime via marche: {budget_ao:,} EUR. "
        accessibilite = estimation.get("accessibilite", {}).get("score", 50)
    except ImportError:
        pass

    # --- Appliquer le palier budget (logique principale) ---
    # Si le budget n'est pas declare par l'acheteur, on reste prudent : palier standard
    # On ne veut pas sur-pricer sur la base d'une estimation hasardeuse
    if budget_declare:
        coeff_budget, nom_palier = _coeff_palier_budget(budget_ao, type_prestation)
    else:
        coeff_budget, nom_palier = 1.0, "standard"
        ajustement_budget += "Budget non declare, prix catalogue. "

    # --- Ajuster selon accessibilite (bonus/malus secondaire) ---
    coeff_accessibilite = 1.0
    if accessibilite is not None:
        if accessibilite >= 70:
            coeff_accessibilite = 1.0  # Pas de reduction, on garde notre prix
            explication_strategie = "AO tres accessible, on maintient le prix catalogue"
        elif accessibilite >= 50:
            coeff_accessibilite = 0.97
            explication_strategie = "AO moyennement accessible, leger ajustement"
        elif accessibilite >= 30:
            coeff_accessibilite = 0.92
            explication_strategie = "AO competitif, prix ajuste a la baisse"
        else:
            coeff_accessibilite = 0.87
            explication_strategie = "AO tres competitif, prix agressif"
    else:
        explication_strategie = "Pas d'estimation d'accessibilite"

    # --- Calcul du TJM recommande ---
    tjm_recommande = round(tjm_base * coeff_budget * coeff_accessibilite)

    # Si budget DECLARE par l'acheteur, verifier qu'on vise 80-90%
    # On ne fait PAS cet ajustement sur un budget estime (trop incertain)
    nb_jours_estime = _estimer_nb_jours(ao)
    if budget_declare and budget_ao > 0:
        prix_total_brut = tjm_recommande * nb_jours_estime
        ratio_budget = prix_total_brut / budget_ao if budget_ao else 1

        # Si on depasse 95% du budget, reduire pour rester sous le seuil
        if ratio_budget > 0.95:
            tjm_recommande = round(budget_ao * 0.85 / max(1, nb_jours_estime))
            ajustement_budget += "Ajuste pour rester sous 90% du budget. "
        # Si on est trop bas (<60% du budget), on peut monter
        elif ratio_budget < 0.60 and nom_palier not in ("competitif",):
            tjm_recommande = round(budget_ao * 0.80 / max(1, nb_jours_estime))
            ajustement_budget += "Remonte pour viser ~80% du budget. "

    # Appliquer le plancher absolu
    tjm_recommande = max(tjm_recommande, plancher)

    # Garde-fou benchmark : ne pas depasser le haut du marche
    bench = BENCHMARK_MARCHE.get(type_prestation)
    if bench:
        tjm_haut_marche = bench["tjm_haut"]
        if tjm_recommande > tjm_haut_marche:
            tjm_recommande = tjm_haut_marche
            ajustement_budget += f"Plafonne au haut du marche ({tjm_haut_marche} EUR). "

    # --- Determiner la strategie affichee ---
    if nom_palier == "competitif":
        strategie = "agressif"
    elif nom_palier in ("premium", "premium_plus"):
        strategie = "premium"
    else:
        strategie = "standard"

    # --- Fourchette ---
    fourchette_min = max(round(tjm_recommande * 0.88), plancher)
    fourchette_max = round(tjm_recommande * 1.12)

    if tjm_gagnes and len(tjm_gagnes) >= 2:
        fourchette_min = max(fourchette_min, round(min(tjm_gagnes) * 0.9))
        fourchette_max = min(fourchette_max, round(max(tjm_gagnes) * 1.1))
        fourchette_min = min(fourchette_min, tjm_recommande - 50)
        fourchette_max = max(fourchette_max, tjm_recommande + 50)

    # --- Prix total estime ---
    prix_total_estime = tjm_recommande * nb_jours_estime

    # --- Detail ---
    details = (
        f"Type: {type_prestation} | TJM catalogue: {tjm_ref} EUR | "
        f"Source TJM: {source_tjm} | "
        f"Palier budget: {nom_palier} (coeff {coeff_budget}) | "
        f"Nb jours estime: {nb_jours_estime} | "
        f"Accessibilite: {explication_strategie}"
    )
    if ajustement_budget:
        details += f" | {ajustement_budget}"
    if budget_ao > 0:
        details += f" | Budget AO: {budget_ao:,.0f} EUR | Ratio: {prix_total_estime/budget_ao*100:.0f}%"

    return {
        "tjm_recommande": tjm_recommande,
        "fourchette": [fourchette_min, fourchette_max],
        "prix_total_estime": prix_total_estime,
        "nb_jours_estime": nb_jours_estime,
        "strategie": strategie,
        "palier": nom_palier,
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
        return max(1, round(budget / 1600))  # TJM moyen entre paliers

    # Defaut : 5 jours (formation courte standard)
    return 5
