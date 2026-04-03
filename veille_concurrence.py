"""
Veille concurrentielle via les attributions BOAMP.
Detecte qui gagne les marches de formation/IA pour
comprendre le paysage concurrentiel.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import httpx

logger = logging.getLogger("ao_hunter.veille_concurrence")

DASHBOARD_DIR = Path(__file__).parent
CONCURRENCE_FILE = DASHBOARD_DIR / "concurrence.json"

API_URL = "https://www.boamp.fr/api/explore/v2.1/catalog/datasets/boamp/records"

# Mots-cles pour trouver les attributions formation/IA
MOTS_CLES = [
    "formation intelligence artificielle",
    "formation IA",
    "formation numerique",
    "formation digitale",
    "accompagnement IA",
]


def rechercher_attributions() -> list[dict]:
    """Recherche les avis d'attribution recents sur BOAMP."""
    resultats = []

    for mot in MOTS_CLES:
        try:
            params = {
                "select": "id,objet,nomacheteur,dateparution,nature,nature_libelle,donnees",
                "where": f'objet LIKE "%{mot}%" AND nature = "ATTRIBUTION"',
                "order_by": "dateparution DESC",
                "limit": 30,
            }

            with httpx.Client(timeout=30, follow_redirects=True) as client:
                resp = client.get(API_URL, params=params)
                if resp.status_code != 200:
                    continue
                data = resp.json()

            for record in data.get("results", []):
                attrib = _extraire_attribution(record)
                if attrib:
                    resultats.append(attrib)

        except Exception as e:
            logger.warning(f"Erreur attributions BOAMP pour '{mot}': {e}")

    # Dedoublonner
    vus = set()
    uniques = []
    for a in resultats:
        cle = f"{a['acheteur']}_{a['titulaire']}_{a['objet'][:50]}"
        if cle not in vus:
            vus.add(cle)
            uniques.append(a)

    logger.info(f"Attributions: {len(uniques)} trouvees")
    return uniques


def _extraire_attribution(record: dict) -> dict | None:
    """Extrait les infos d'attribution d'un enregistrement BOAMP."""
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

    # Chercher le titulaire
    if isinstance(attribution, dict):
        titulaire = attribution.get("nomTitulaire", "")
        montant_raw = attribution.get("montant", attribution.get("valeurMarche", ""))
        if montant_raw:
            try:
                montant = float(str(montant_raw).replace(",", ".").replace(" ", ""))
            except (ValueError, TypeError):
                pass

    # Si pas dans attribution, chercher dans les lots
    lots = initial.get("lots", [])
    if isinstance(lots, list):
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

    if not titulaire:
        return None

    return {
        "id": record.get("id", ""),
        "objet": record.get("objet", "Sans titre"),
        "acheteur": record.get("nomacheteur", "Inconnu"),
        "titulaire": titulaire,
        "montant": montant,
        "date": record.get("dateparution", ""),
    }


def lancer_veille_concurrence() -> dict:
    """Lance la veille concurrentielle et sauvegarde."""
    attributions = rechercher_attributions()

    # Charger l'existant
    existant = []
    if CONCURRENCE_FILE.exists():
        try:
            existant = json.loads(CONCURRENCE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existant = []

    # Ajouter les nouvelles
    ids_existants = {a.get("id") for a in existant}
    nouvelles = 0
    for a in attributions:
        if a["id"] not in ids_existants:
            existant.append(a)
            ids_existants.add(a["id"])
            nouvelles += 1

    # Garder les 12 derniers mois
    existant = existant[-500:]

    CONCURRENCE_FILE.write_text(
        json.dumps(existant, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Analyser les concurrents
    concurrents = analyser_concurrents(existant)

    return {
        "nouvelles": nouvelles,
        "total": len(existant),
        "top_concurrents": concurrents[:10],
    }


def analyser_concurrents(attributions: list[dict]) -> list[dict]:
    """Analyse les concurrents les plus actifs."""
    par_titulaire = {}
    for a in attributions:
        t = a.get("titulaire", "").strip()
        if not t or len(t) < 3:
            continue
        t_lower = t.lower()
        # Normaliser les noms similaires
        if t_lower not in par_titulaire:
            par_titulaire[t_lower] = {
                "nom": t,
                "nb_marches": 0,
                "montant_total": 0,
                "acheteurs": set(),
                "derniere_attribution": "",
            }
        par_titulaire[t_lower]["nb_marches"] += 1
        if a.get("montant"):
            par_titulaire[t_lower]["montant_total"] += a["montant"]
        par_titulaire[t_lower]["acheteurs"].add(a.get("acheteur", ""))
        if a.get("date", "") > par_titulaire[t_lower]["derniere_attribution"]:
            par_titulaire[t_lower]["derniere_attribution"] = a["date"]

    # Convertir sets en listes pour JSON
    concurrents = []
    for t_lower, info in par_titulaire.items():
        concurrents.append({
            "nom": info["nom"],
            "nb_marches": info["nb_marches"],
            "montant_total": info["montant_total"],
            "nb_acheteurs": len(info["acheteurs"]),
            "derniere_attribution": info["derniere_attribution"],
        })

    concurrents.sort(key=lambda c: c["nb_marches"], reverse=True)
    return concurrents


def charger_concurrence() -> list[dict]:
    """Charge les donnees de concurrence."""
    if CONCURRENCE_FILE.exists():
        try:
            return json.loads(CONCURRENCE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return []
