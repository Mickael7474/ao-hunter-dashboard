"""
Objectifs CA - AO Hunter
Gere les objectifs de chiffre d'affaires et le tracking de progression.
"""

import json
from pathlib import Path
from datetime import datetime, date

DASHBOARD_DIR = Path(__file__).parent
OBJECTIFS_FILE = DASHBOARD_DIR / "objectifs.json"
AO_CACHE = DASHBOARD_DIR / "ao_pertinents.json"

# Fallback si resultats/ existe (mode local)
_parent = DASHBOARD_DIR.parent
if (_parent / "resultats" / "ao_pertinents.json").exists():
    AO_CACHE = _parent / "resultats" / "ao_pertinents.json"


def _charger_objectifs() -> dict:
    """Charge les objectifs depuis le fichier JSON."""
    if OBJECTIFS_FILE.exists():
        try:
            return json.loads(OBJECTIFS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _sauvegarder_objectifs(data: dict):
    """Sauvegarde les objectifs dans le fichier JSON."""
    OBJECTIFS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _charger_ao() -> list[dict]:
    """Charge la base AO."""
    if not AO_CACHE.exists():
        return []
    try:
        return json.loads(AO_CACHE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def definir_objectif(annuel: float, marge_cible_pct: float = 30) -> dict:
    """
    Definit l'objectif annuel de CA.

    Args:
        annuel: objectif annuel en EUR
        marge_cible_pct: marge cible en pourcentage (defaut 30%)

    Retourne:
        les objectifs sauvegardes
    """
    objectifs = {
        "objectif_annuel": annuel,
        "objectif_mensuel": round(annuel / 12, 2),
        "date_debut": datetime.now().strftime("%Y-01-01"),
        "marge_cible_pct": marge_cible_pct,
        "date_modification": datetime.now().isoformat(),
    }
    _sauvegarder_objectifs(objectifs)
    return objectifs


def progression() -> dict:
    """
    Calcule la progression vers l'objectif annuel.

    Retourne:
        {
            ca_gagne, ca_pipeline, ca_potentiel,
            progression_pct, projection_annuelle,
            mois_restants, objectif_mensuel_restant,
            on_track, gap, objectifs, stats_par_mois
        }
    """
    objectifs = _charger_objectifs()
    appels = _charger_ao()

    objectif_annuel = objectifs.get("objectif_annuel", 0)
    marge_cible = objectifs.get("marge_cible_pct", 30)

    # CA gagne : somme des AO gagnes
    ao_gagnes = [a for a in appels if a.get("statut") == "gagne"]
    ca_gagne = sum(_extraire_montant(a) for a in ao_gagnes)

    # Taux de conversion historique
    ao_soumis_total = [a for a in appels if a.get("statut") in ("soumis", "gagne", "perdu")]
    nb_soumis = len(ao_soumis_total)
    nb_gagnes = len(ao_gagnes)
    taux_conversion = (nb_gagnes / nb_soumis * 100) if nb_soumis > 0 else 25  # defaut 25%

    # CA pipeline : AO soumis * taux de conversion
    ao_soumis = [a for a in appels if a.get("statut") == "soumis"]
    ca_soumis_brut = sum(_extraire_montant(a) for a in ao_soumis)
    ca_pipeline = round(ca_soumis_brut * taux_conversion / 100, 2)

    # CA potentiel : AO en candidature * probabilite basee sur score Go/No-Go
    ao_candidature = [a for a in appels if a.get("statut") in ("candidature", "analyse")]
    ca_potentiel = 0
    for a in ao_candidature:
        montant = _extraire_montant(a)
        score = a.get("score_pertinence", 0.5)
        # Probabilite = score * taux_conversion / 2 (plus incertain)
        proba = score * taux_conversion / 200
        ca_potentiel += montant * proba
    ca_potentiel = round(ca_potentiel, 2)

    # Progression
    progression_pct = round(ca_gagne / objectif_annuel * 100, 1) if objectif_annuel > 0 else 0

    # Mois restants dans l'annee
    today = date.today()
    mois_restants = max(1, 12 - today.month + 1)
    mois_ecoules = today.month

    # Projection annuelle (extrapolation lineaire)
    if mois_ecoules > 0:
        rythme_mensuel = ca_gagne / mois_ecoules
        projection_annuelle = round(rythme_mensuel * 12, 2)
    else:
        projection_annuelle = 0

    # Objectif mensuel restant
    reste_a_gagner = max(0, objectif_annuel - ca_gagne)
    objectif_mensuel_restant = round(reste_a_gagner / mois_restants, 2)

    # On track ?
    on_track = projection_annuelle >= objectif_annuel
    gap = round(projection_annuelle - objectif_annuel, 2)

    # Stats par mois (pour timeline)
    stats_par_mois = _calculer_stats_mensuelles(appels, objectifs)

    return {
        "ca_gagne": ca_gagne,
        "ca_pipeline": ca_pipeline,
        "ca_potentiel": ca_potentiel,
        "progression_pct": progression_pct,
        "projection_annuelle": projection_annuelle,
        "mois_restants": mois_restants,
        "mois_ecoules": mois_ecoules,
        "objectif_mensuel_restant": objectif_mensuel_restant,
        "on_track": on_track,
        "gap": gap,
        "objectifs": objectifs,
        "taux_conversion": round(taux_conversion, 1),
        "nb_gagnes": nb_gagnes,
        "nb_soumis": nb_soumis,
        "stats_par_mois": stats_par_mois,
    }


def _extraire_montant(ao: dict) -> float:
    """Extrait le montant d'un AO (attribution ou budget estime)."""
    for champ in ("attribution_montant", "budget_estime"):
        val = ao.get(champ)
        if val:
            try:
                return float(val)
            except (ValueError, TypeError):
                pass
    # Estimation par defaut basee sur la duree
    duree = ao.get("duree_mois", 0) or 0
    if duree > 0:
        return duree * 5000  # estimation grossiere: 5k/mois
    return 15000  # estimation par defaut pour un AO formation


def _calculer_stats_mensuelles(appels: list[dict], objectifs: dict) -> list[dict]:
    """Calcule les stats CA par mois pour la timeline."""
    annee = date.today().year
    objectif_mensuel = objectifs.get("objectif_mensuel", 0)

    mois_labels = [
        "Jan", "Fev", "Mar", "Avr", "Mai", "Jun",
        "Jul", "Aou", "Sep", "Oct", "Nov", "Dec"
    ]

    stats = []
    for m in range(1, 13):
        # AO gagnes ce mois-ci
        gagnes_mois = []
        for a in appels:
            if a.get("statut") != "gagne":
                continue
            # Chercher la date de changement de statut ou la date de publication
            date_str = a.get("date_attribution") or a.get("date_publication", "")
            if date_str:
                try:
                    dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                    if dt.year == annee and dt.month == m:
                        gagnes_mois.append(a)
                except (ValueError, TypeError):
                    pass

        ca_mois = sum(_extraire_montant(a) for a in gagnes_mois)

        stats.append({
            "mois": m,
            "label": mois_labels[m - 1],
            "ca_gagne": ca_mois,
            "objectif": objectif_mensuel,
            "nb_gagnes": len(gagnes_mois),
            "atteint": ca_mois >= objectif_mensuel if objectif_mensuel > 0 else False,
        })

    return stats


def recommander_pipeline(objectif_mensuel: float | None = None) -> dict:
    """
    Recommande le nombre d'AO a detecter/soumettre par mois pour atteindre l'objectif.

    Retourne:
        {ao_a_detecter, ao_a_soumettre, taux_conversion_moyen, montant_moyen_ao,
         details}
    """
    prog = progression()
    if objectif_mensuel is None:
        objectif_mensuel = prog["objectif_mensuel_restant"]

    taux_conversion = prog["taux_conversion"]
    if taux_conversion <= 0:
        taux_conversion = 25  # defaut

    appels = _charger_ao()

    # Montant moyen des AO gagnes
    ao_gagnes = [a for a in appels if a.get("statut") == "gagne"]
    if ao_gagnes:
        montant_moyen = sum(_extraire_montant(a) for a in ao_gagnes) / len(ao_gagnes)
    else:
        montant_moyen = 15000  # estimation par defaut

    # Nombre d'AO a soumettre par mois
    if montant_moyen > 0:
        ao_gagnes_necessaires = objectif_mensuel / montant_moyen
        ao_a_soumettre = ao_gagnes_necessaires / (taux_conversion / 100)
    else:
        ao_a_soumettre = 0

    # Taux analyse -> soumission (estimer a 50% si pas de donnees)
    ao_analyses = len([a for a in appels if a.get("statut") in ("analyse", "candidature", "soumis", "gagne", "perdu")])
    ao_soumis_total = len([a for a in appels if a.get("statut") in ("soumis", "gagne", "perdu")])
    taux_analyse_soumission = (ao_soumis_total / ao_analyses * 100) if ao_analyses > 0 else 50

    # Taux detection -> analyse (estimer a 30%)
    total_detectes = len(appels)
    taux_detection_analyse = (ao_analyses / total_detectes * 100) if total_detectes > 0 else 30

    ao_a_analyser = ao_a_soumettre / (taux_analyse_soumission / 100) if taux_analyse_soumission > 0 else ao_a_soumettre * 2
    ao_a_detecter = ao_a_analyser / (taux_detection_analyse / 100) if taux_detection_analyse > 0 else ao_a_analyser * 3

    return {
        "ao_a_detecter": max(1, round(ao_a_detecter)),
        "ao_a_analyser": max(1, round(ao_a_analyser)),
        "ao_a_soumettre": max(1, round(ao_a_soumettre)),
        "taux_conversion_moyen": round(taux_conversion, 1),
        "taux_analyse_soumission": round(taux_analyse_soumission, 1),
        "montant_moyen_ao": round(montant_moyen),
        "objectif_mensuel": round(objectif_mensuel),
        "details": f"Pour atteindre {objectif_mensuel:,.0f} EUR/mois, detecter ~{max(1, round(ao_a_detecter))} AO, en soumettre ~{max(1, round(ao_a_soumettre))} (taux conversion {taux_conversion:.0f}%, montant moyen {montant_moyen:,.0f} EUR)",
    }


def alertes_objectif() -> list[str]:
    """
    Genere des alertes sur la progression vers l'objectif.

    Retourne:
        Liste de messages d'alerte
    """
    objectifs = _charger_objectifs()
    if not objectifs.get("objectif_annuel"):
        return ["Aucun objectif annuel defini. Definissez un objectif pour activer le suivi."]

    prog = progression()
    alertes = []

    # Pipeline insuffisant
    recommandation = recommander_pipeline()
    appels = _charger_ao()
    ao_soumis_ce_mois = 0
    today = date.today()
    for a in appels:
        if a.get("statut") != "soumis":
            continue
        date_str = a.get("date_publication", "")
        if date_str:
            try:
                dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                if dt.year == today.year and dt.month == today.month:
                    ao_soumis_ce_mois += 1
            except (ValueError, TypeError):
                pass

    if ao_soumis_ce_mois < recommandation["ao_a_soumettre"]:
        alertes.append(
            f"Pipeline insuffisant : {ao_soumis_ce_mois} AO soumis ce mois, "
            f"il en faut {recommandation['ao_a_soumettre']} pour atteindre l'objectif"
        )

    # Rythme de detection
    # Comparer le nombre d'AO detectes ce mois vs le mois dernier
    ao_ce_mois = 0
    ao_mois_dernier = 0
    mois_dernier = today.month - 1 if today.month > 1 else 12
    annee_mois_dernier = today.year if today.month > 1 else today.year - 1

    for a in appels:
        date_str = a.get("date_publication", "")
        if date_str:
            try:
                dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                if dt.year == today.year and dt.month == today.month:
                    ao_ce_mois += 1
                elif dt.year == annee_mois_dernier and dt.month == mois_dernier:
                    ao_mois_dernier += 1
            except (ValueError, TypeError):
                pass

    if ao_mois_dernier > 0 and ao_ce_mois < ao_mois_dernier * 0.7:
        pct_baisse = round((1 - ao_ce_mois / ao_mois_dernier) * 100)
        alertes.append(f"Rythme de detection en baisse : -{pct_baisse}% vs mois dernier")

    # Objectif mensuel atteint
    objectif_mensuel = objectifs.get("objectif_mensuel", 0)
    ca_mois = 0
    for a in appels:
        if a.get("statut") != "gagne":
            continue
        date_str = a.get("date_attribution") or a.get("date_publication", "")
        if date_str:
            try:
                dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                if dt.year == today.year and dt.month == today.month:
                    ca_mois += _extraire_montant(a)
            except (ValueError, TypeError):
                pass

    if objectif_mensuel > 0 and ca_mois >= objectif_mensuel:
        alertes.append("Felicitations : objectif mensuel atteint !")

    # Projection vs objectif
    if not prog["on_track"] and prog["objectifs"].get("objectif_annuel", 0) > 0:
        gap_abs = abs(prog["gap"])
        alertes.append(
            f"Projection annuelle ({prog['projection_annuelle']:,.0f} EUR) inferieure a l'objectif "
            f"({prog['objectifs']['objectif_annuel']:,.0f} EUR) - ecart de {gap_abs:,.0f} EUR"
        )

    if not alertes:
        alertes.append("Tout est sur la bonne voie !")

    return alertes
