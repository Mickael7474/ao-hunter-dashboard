"""
Benchmark prix en temps reel - Feature 2
Scrape les avis d'attribution BOAMP pour constituer une base de prix du marche.
Analyse statistique (median, percentiles, tendances) et positionnement prix.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean, median

import httpx

logger = logging.getLogger("ao_hunter.benchmark_prix")

DASHBOARD_DIR = Path(__file__).parent
BENCHMARK_FILE = DASHBOARD_DIR / "benchmark_attributions.json"

API_URL = "https://www.boamp.fr/api/explore/v2.1/catalog/datasets/boamp/records"

# CPV pertinents pour formation/conseil/IA
CODES_CPV_BENCHMARK = [
    "80500000",  # Services de formation
    "80530000",  # Services de formation professionnelle
    "80533000",  # Formation en informatique
    "72000000",  # Services informatiques
    "79400000",  # Conseil en gestion et services connexes
]

# Mots-cles pour trouver les attributions formation/conseil/IA
MOTS_CLES_ATTRIBUTION = [
    "formation intelligence artificielle",
    "formation IA",
    "formation numerique",
    "formation digitale",
    "formation informatique",
    "accompagnement digital",
    "conseil transformation digitale",
    "conseil intelligence artificielle",
]

# Mapping region par code departement
_REGION_MAP = {
    "75": "Ile-de-France", "77": "Ile-de-France", "78": "Ile-de-France",
    "91": "Ile-de-France", "92": "Ile-de-France", "93": "Ile-de-France",
    "94": "Ile-de-France", "95": "Ile-de-France",
    "13": "PACA", "83": "PACA", "84": "PACA", "04": "PACA", "05": "PACA", "06": "PACA",
    "69": "Auvergne-Rhone-Alpes", "01": "Auvergne-Rhone-Alpes", "03": "Auvergne-Rhone-Alpes",
    "07": "Auvergne-Rhone-Alpes", "15": "Auvergne-Rhone-Alpes", "26": "Auvergne-Rhone-Alpes",
    "38": "Auvergne-Rhone-Alpes", "42": "Auvergne-Rhone-Alpes", "43": "Auvergne-Rhone-Alpes",
    "63": "Auvergne-Rhone-Alpes", "73": "Auvergne-Rhone-Alpes", "74": "Auvergne-Rhone-Alpes",
    "31": "Occitanie", "09": "Occitanie", "11": "Occitanie", "12": "Occitanie",
    "30": "Occitanie", "32": "Occitanie", "34": "Occitanie", "46": "Occitanie",
    "48": "Occitanie", "65": "Occitanie", "66": "Occitanie", "81": "Occitanie", "82": "Occitanie",
    "33": "Nouvelle-Aquitaine", "16": "Nouvelle-Aquitaine", "17": "Nouvelle-Aquitaine",
    "19": "Nouvelle-Aquitaine", "23": "Nouvelle-Aquitaine", "24": "Nouvelle-Aquitaine",
    "40": "Nouvelle-Aquitaine", "47": "Nouvelle-Aquitaine", "64": "Nouvelle-Aquitaine",
    "79": "Nouvelle-Aquitaine", "86": "Nouvelle-Aquitaine", "87": "Nouvelle-Aquitaine",
    "44": "Pays de la Loire", "49": "Pays de la Loire", "53": "Pays de la Loire",
    "72": "Pays de la Loire", "85": "Pays de la Loire",
    "35": "Bretagne", "22": "Bretagne", "29": "Bretagne", "56": "Bretagne",
    "59": "Hauts-de-France", "02": "Hauts-de-France", "60": "Hauts-de-France",
    "62": "Hauts-de-France", "80": "Hauts-de-France",
    "67": "Grand Est", "68": "Grand Est", "10": "Grand Est", "51": "Grand Est",
    "52": "Grand Est", "54": "Grand Est", "55": "Grand Est", "57": "Grand Est",
    "88": "Grand Est",
    "76": "Normandie", "14": "Normandie", "27": "Normandie", "50": "Normandie", "61": "Normandie",
    "21": "Bourgogne-Franche-Comte", "25": "Bourgogne-Franche-Comte",
    "39": "Bourgogne-Franche-Comte", "58": "Bourgogne-Franche-Comte",
    "70": "Bourgogne-Franche-Comte", "71": "Bourgogne-Franche-Comte",
    "89": "Bourgogne-Franche-Comte", "90": "Bourgogne-Franche-Comte",
    "45": "Centre-Val de Loire", "18": "Centre-Val de Loire", "28": "Centre-Val de Loire",
    "36": "Centre-Val de Loire", "37": "Centre-Val de Loire", "41": "Centre-Val de Loire",
    "2A": "Corse", "2B": "Corse", "20": "Corse",
}


def _dept_to_region(code_dept: str) -> str:
    """Convertit un code departement en region."""
    if not code_dept:
        return "Inconnue"
    code = str(code_dept).strip()[:2]
    return _REGION_MAP.get(code, "Autre")


def _classifier_acheteur(nom_acheteur: str) -> str:
    """Classifie le type d'acheteur."""
    nom = (nom_acheteur or "").lower()
    if any(w in nom for w in ["region", "conseil regional"]):
        return "Region"
    if any(w in nom for w in ["departement", "conseil departemental", "conseil general"]):
        return "Departement"
    if any(w in nom for w in ["commune", "mairie", "ville de", "metropole", "communaute"]):
        return "Commune/Intercommunalite"
    if any(w in nom for w in ["ministere", "etat", "direction", "prefecture", "sgami"]):
        return "Etat"
    if any(w in nom for w in ["hopital", "chu", "chru", "ars", "ehpad"]):
        return "Sante"
    if any(w in nom for w in ["universite", "ecole", "lycee", "college", "crous", "cnrs", "inria"]):
        return "Education/Recherche"
    if any(w in nom for w in ["chambre", "cci", "cma"]):
        return "Chambre consulaire"
    if any(w in nom for w in ["opco", "france travail", "pole emploi", "afpa"]):
        return "Emploi/Formation"
    return "Autre"


def _charger_benchmark() -> list[dict]:
    """Charge la base de benchmark existante."""
    if not BENCHMARK_FILE.exists():
        return []
    try:
        return json.loads(BENCHMARK_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _sauvegarder_benchmark(attributions: list[dict]):
    """Sauvegarde la base de benchmark."""
    BENCHMARK_FILE.write_text(
        json.dumps(attributions, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _extraire_attribution_benchmark(record: dict) -> dict | None:
    """Extrait les infos d'attribution enrichies pour le benchmark."""
    donnees_raw = record.get("donnees", "")
    if not donnees_raw:
        return None

    try:
        d = json.loads(donnees_raw) if isinstance(donnees_raw, str) else donnees_raw
    except (json.JSONDecodeError, TypeError):
        return None

    fn = d.get("FNSimple", d.get("ContractNotice", {}))
    initial = fn.get("initial", fn)
    attribution = initial.get("attribution", {})

    titulaire = ""
    montant = None

    if isinstance(attribution, dict):
        titulaire = attribution.get("nomTitulaire", "")
        montant_raw = attribution.get("montant", attribution.get("valeurMarche", ""))
        if montant_raw:
            try:
                montant = float(str(montant_raw).replace(",", ".").replace(" ", ""))
            except (ValueError, TypeError):
                pass

    # Chercher dans les lots
    lots = initial.get("lots", [])
    nb_lots = 0
    if isinstance(lots, list):
        nb_lots = len(lots)
        for lot in lots:
            if isinstance(lot, dict):
                t = lot.get("nomTitulaire", "")
                if t and not titulaire:
                    titulaire = t
                m = lot.get("montant", "")
                if m and not montant:
                    try:
                        montant = float(str(m).replace(",", ".").replace(" ", ""))
                    except (ValueError, TypeError):
                        pass

    # On ne garde que les attributions avec un montant exploitable
    if not montant or montant <= 0:
        return None

    code_dept = record.get("code_departement", "")
    cpv = record.get("descripteur_code", "")

    return {
        "titre": record.get("objet", "Sans titre"),
        "acheteur": record.get("nomacheteur", "Inconnu"),
        "titulaire": titulaire or "Non renseigne",
        "montant": montant,
        "cpv": cpv,
        "date": record.get("dateparution", ""),
        "nb_lots": nb_lots,
        "region": _dept_to_region(code_dept),
        "type_acheteur": _classifier_acheteur(record.get("nomacheteur", "")),
    }


def collecter_attributions(nb_pages: int = 5) -> list[dict]:
    """Interroge l'API BOAMP open data (nature=ATTRIBUTION) pour les marches
    de formation/conseil/IA, filtre par CPV pertinents.

    Args:
        nb_pages: nombre de pages a interroger (50 resultats par page)

    Returns:
        list[dict] avec {titre, acheteur, titulaire, montant, cpv, date, nb_lots, region}
    """
    resultats = []

    # Recherche par mots-cles + nature ATTRIBUTION
    for mot in MOTS_CLES_ATTRIBUTION:
        for page in range(1, nb_pages + 1):
            try:
                params = {
                    "select": "id,objet,nomacheteur,dateparution,nature,nature_libelle,"
                              "descripteur_code,code_departement,donnees",
                    "where": f'objet LIKE "%{mot}%" AND nature = "ATTRIBUTION"',
                    "order_by": "dateparution DESC",
                    "limit": 50,
                    "offset": (page - 1) * 50,
                }

                with httpx.Client(timeout=30, follow_redirects=True) as client:
                    resp = client.get(API_URL, params=params)
                    if resp.status_code != 200:
                        break
                    data = resp.json()

                records = data.get("results", [])
                if not records:
                    break

                for record in records:
                    attrib = _extraire_attribution_benchmark(record)
                    if attrib:
                        resultats.append(attrib)

            except Exception as e:
                logger.warning(f"Erreur collecte attributions '{mot}' page {page}: {e}")
                break

    # Recherche par CPV + nature ATTRIBUTION
    for cpv in CODES_CPV_BENCHMARK:
        try:
            params = {
                "select": "id,objet,nomacheteur,dateparution,nature,nature_libelle,"
                          "descripteur_code,code_departement,donnees",
                "where": f'descripteur_code LIKE "%{cpv}%" AND nature = "ATTRIBUTION"',
                "order_by": "dateparution DESC",
                "limit": 50,
            }

            with httpx.Client(timeout=30, follow_redirects=True) as client:
                resp = client.get(API_URL, params=params)
                if resp.status_code != 200:
                    continue
                data = resp.json()

            for record in data.get("results", []):
                attrib = _extraire_attribution_benchmark(record)
                if attrib:
                    resultats.append(attrib)

        except Exception as e:
            logger.warning(f"Erreur collecte attributions CPV {cpv}: {e}")

    # Dedoublonner par titre+acheteur+montant
    vus = set()
    uniques = []
    for a in resultats:
        cle = f"{a['acheteur'][:30]}_{a['titre'][:40]}_{a['montant']}"
        if cle not in vus:
            vus.add(cle)
            uniques.append(a)

    # Fusionner avec l'existant
    existant = _charger_benchmark()
    cles_existantes = {
        f"{a['acheteur'][:30]}_{a['titre'][:40]}_{a['montant']}"
        for a in existant
    }

    nouvelles = 0
    for a in uniques:
        cle = f"{a['acheteur'][:30]}_{a['titre'][:40]}_{a['montant']}"
        if cle not in cles_existantes:
            existant.append(a)
            cles_existantes.add(cle)
            nouvelles += 1

    # Garder les 2000 plus recentes
    existant.sort(key=lambda x: x.get("date", ""), reverse=True)
    existant = existant[:2000]

    _sauvegarder_benchmark(existant)
    logger.info(f"Benchmark: {nouvelles} nouvelles attributions, {len(existant)} total")
    return existant


def _filtrer_par_type(attributions: list[dict], type_prestation: str) -> list[dict]:
    """Filtre les attributions par type de prestation."""
    type_lower = type_prestation.lower()
    mots_filtre = {
        "formation": ["formation", "stage", "sensibilisation", "acculturation", "pedagogique"],
        "conseil": ["conseil", "accompagnement", "consulting", "audit", "diagnostic", "strategie"],
        "ia": ["intelligence artificielle", "ia ", " ia", "chatgpt", "generative", "machine learning"],
        "numerique": ["numerique", "digital", "transformation digitale", "e-learning"],
    }

    mots = mots_filtre.get(type_lower, mots_filtre.get("formation", []))

    filtre = []
    for a in attributions:
        titre_lower = a.get("titre", "").lower()
        if any(m in titre_lower for m in mots):
            filtre.append(a)

    # Si trop peu de resultats, on garde tout
    if len(filtre) < 5:
        return attributions
    return filtre


def _percentile(values: list[float], p: int) -> float:
    """Calcule le percentile p d'une liste de valeurs."""
    if not values:
        return 0
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * (p / 100)
    f = int(k)
    c = f + 1
    if c >= len(sorted_vals):
        return sorted_vals[-1]
    return sorted_vals[f] + (k - f) * (sorted_vals[c] - sorted_vals[f])


def analyser_benchmark(type_prestation: str = "formation") -> dict:
    """Analyse statistique de la base de benchmark.

    Args:
        type_prestation: "formation", "conseil", "ia", "numerique"

    Returns:
        dict avec prix_median, prix_moyen, percentile_25, percentile_75,
        prix_par_region, prix_par_type_acheteur, tendance, nb_attributions
    """
    attributions = _charger_benchmark()
    if not attributions:
        return {
            "prix_median": 0,
            "prix_moyen": 0,
            "percentile_25": 0,
            "percentile_75": 0,
            "prix_par_region": {},
            "prix_par_type_acheteur": {},
            "tendance": "insuffisant",
            "nb_attributions": 0,
        }

    # Filtrer par type
    filtrees = _filtrer_par_type(attributions, type_prestation)
    montants = [a["montant"] for a in filtrees if a.get("montant") and a["montant"] > 0]

    if not montants:
        return {
            "prix_median": 0,
            "prix_moyen": 0,
            "percentile_25": 0,
            "percentile_75": 0,
            "prix_par_region": {},
            "prix_par_type_acheteur": {},
            "tendance": "insuffisant",
            "nb_attributions": 0,
        }

    # Stats globales
    prix_median = round(median(montants), 2)
    prix_moyen = round(mean(montants), 2)
    p25 = round(_percentile(montants, 25), 2)
    p75 = round(_percentile(montants, 75), 2)

    # Prix par region
    par_region = {}
    for a in filtrees:
        region = a.get("region", "Inconnue")
        if region not in par_region:
            par_region[region] = []
        if a.get("montant"):
            par_region[region].append(a["montant"])
    prix_par_region = {
        r: round(mean(vals), 2) for r, vals in par_region.items() if vals
    }

    # Prix par type d'acheteur
    par_type = {}
    for a in filtrees:
        t = a.get("type_acheteur", "Autre")
        if t not in par_type:
            par_type[t] = []
        if a.get("montant"):
            par_type[t].append(a["montant"])
    prix_par_type_acheteur = {
        t: round(mean(vals), 2) for t, vals in par_type.items() if vals
    }

    # Tendance : comparer les 3 derniers mois vs les 3 precedents
    tendance = _calculer_tendance(filtrees)

    return {
        "prix_median": prix_median,
        "prix_moyen": prix_moyen,
        "percentile_25": p25,
        "percentile_75": p75,
        "prix_par_region": prix_par_region,
        "prix_par_type_acheteur": prix_par_type_acheteur,
        "tendance": tendance,
        "nb_attributions": len(montants),
    }


def _calculer_tendance(attributions: list[dict]) -> str:
    """Compare les prix des 3 derniers mois vs les 3 mois precedents."""
    now = datetime.now()
    date_3m = (now - timedelta(days=90)).strftime("%Y-%m-%d")
    date_6m = (now - timedelta(days=180)).strftime("%Y-%m-%d")

    recents = [
        a["montant"] for a in attributions
        if a.get("date", "") >= date_3m and a.get("montant")
    ]
    precedents = [
        a["montant"] for a in attributions
        if date_6m <= a.get("date", "") < date_3m and a.get("montant")
    ]

    if len(recents) < 3 or len(precedents) < 3:
        return "insuffisant"

    moy_recents = mean(recents)
    moy_precedents = mean(precedents)

    variation = (moy_recents - moy_precedents) / moy_precedents if moy_precedents else 0

    if variation > 0.05:
        return "hausse"
    elif variation < -0.05:
        return "baisse"
    else:
        return "stable"


def positionner_prix(notre_prix: float, type_prestation: str = "formation") -> dict:
    """Positionne notre prix dans la distribution du marche.

    Args:
        notre_prix: le montant propose
        type_prestation: "formation", "conseil", "ia", "numerique"

    Returns:
        dict avec percentile, recommandation, details
    """
    attributions = _charger_benchmark()
    filtrees = _filtrer_par_type(attributions, type_prestation)
    montants = sorted([a["montant"] for a in filtrees if a.get("montant") and a["montant"] > 0])

    if not montants:
        return {
            "percentile": None,
            "recommandation": "donnees insuffisantes",
            "details": "Pas assez d'attributions pour positionner le prix.",
            "nb_attributions": 0,
        }

    # Calculer le percentile de notre prix
    nb_inf = sum(1 for m in montants if m <= notre_prix)
    percentile_pos = round((nb_inf / len(montants)) * 100, 1)

    # Recommandation
    if percentile_pos <= 30:
        recommandation = "competitif"
        details = (
            f"Votre prix ({notre_prix:,.0f} EUR) est dans les 30% les plus bas du marche. "
            f"Positionnement agressif : bonne chance de gagner mais attention a la marge."
        )
    elif percentile_pos <= 65:
        recommandation = "dans la moyenne"
        details = (
            f"Votre prix ({notre_prix:,.0f} EUR) est dans la moyenne du marche "
            f"(percentile {percentile_pos:.0f}%). Bon equilibre prix/qualite."
        )
    else:
        recommandation = "cher"
        details = (
            f"Votre prix ({notre_prix:,.0f} EUR) est au-dessus de {percentile_pos:.0f}% du marche. "
            f"Risque eleve si le prix est un critere important. "
            f"Compensez par un memoire technique solide."
        )

    benchmark = analyser_benchmark(type_prestation)

    return {
        "percentile": percentile_pos,
        "recommandation": recommandation,
        "details": details,
        "nb_attributions": len(montants),
        "prix_median_marche": benchmark["prix_median"],
        "prix_moyen_marche": benchmark["prix_moyen"],
        "fourchette_marche": [benchmark["percentile_25"], benchmark["percentile_75"]],
    }
