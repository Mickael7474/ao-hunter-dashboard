"""
Veille legere pour Render - interroge BOAMP + TED sans dependances lourdes.
Scoring par mots-cles (pas d'IA pour economiser les tokens).
"""

import json
import re
import logging
from datetime import datetime, timedelta
from pathlib import Path

import httpx

logger = logging.getLogger("ao_hunter.veille_render")

DASHBOARD_DIR = Path(__file__).parent
AO_CACHE = DASHBOARD_DIR / "ao_pertinents.json"

# Mots-cles principaux pour la recherche (subset du config.yaml)
MOTS_CLES_RECHERCHE = [
    "formation intelligence artificielle",
    "formation IA",
    "formation ChatGPT",
    "formation copilot",
    "formation numerique",
    "formation digitale",
    "intelligence artificielle",
    "IA generative",
    "accompagnement IA",
    "acculturation IA",
    "transformation digitale",
    "automatisation IA",
    "prompt engineering",
]

# Mots-cles de scoring (plus large, pour calculer la pertinence)
MOTS_CLES_SCORING = [
    "formation", "IA", "intelligence artificielle", "numerique", "digital",
    "ChatGPT", "copilot", "midjourney", "prompt", "automatisation",
    "acculturation", "sensibilisation", "accompagnement", "consulting",
    "strategie", "deploiement", "diagnostic", "audit", "conduite du changement",
    "referent", "competences", "transformation", "generative", "machine learning",
    "data", "donnees", "e-learning", "LMS", "Qualiopi", "OPCO",
    "agent IA", "chatbot", "no code", "low code", "Make", "n8n",
]

# Mots-cles d'exclusion
EXCLUSIONS = [
    "travaux", "BTP", "construction", "nettoyage", "restauration scolaire",
    "transport", "voirie", "assainissement", "cablage", "fibre optique",
    "cybersecurite", "pentest", "videosurveillance", "videoprotection",
]

# Codes CPV pertinents
CODES_CPV = [
    "80500000", "80530000", "80533000", "80533100", "80570000",
    "79632000", "79400000", "79410000", "79411000", "72220000",
]


def scorer_ao(titre: str, description: str, code_cpv: str = "") -> float:
    """Score un AO par mots-cles (0.0 a 1.0)."""
    texte = f"{titre} {description}".lower()

    # Exclusions
    for excl in EXCLUSIONS:
        if excl.lower() in texte:
            return 0.0

    # Score par mots-cles
    hits = 0
    for mot in MOTS_CLES_SCORING:
        if mot.lower() in texte:
            hits += 1

    # Bonus CPV
    if code_cpv:
        for cpv in CODES_CPV:
            if cpv in code_cpv:
                hits += 3
                break

    # Normaliser entre 0 et 1
    score = min(hits / 8.0, 1.0)
    return round(score, 2)


def rechercher_boamp() -> list[dict]:
    """Interroge l'API open data BOAMP."""
    API_URL = "https://www.boamp.fr/api/explore/v2.1/catalog/datasets/boamp/records"
    resultats = []

    # Regrouper par lots de 3
    lots = [MOTS_CLES_RECHERCHE[i:i+3] for i in range(0, len(MOTS_CLES_RECHERCHE), 3)]

    for lot in lots:
        try:
            like_parts = [f'objet LIKE "%{m}%"' for m in lot]
            like_clause = " OR ".join(like_parts)

            params = {
                "select": "id,idweb,objet,nomacheteur,dateparution,datelimitereponse,"
                          "nature_libelle,descripteur_code,code_departement,"
                          "type_marche,url_avis,procedure_libelle,donnees",
                "where": f'({like_clause})',
                "order_by": "dateparution DESC",
                "limit": 50,
            }

            with httpx.Client(timeout=30, follow_redirects=True) as client:
                resp = client.get(API_URL, params=params)
                resp.raise_for_status()
                data = resp.json()

            for record in data.get("results", []):
                url_avis = record.get("url_avis", "")
                if not url_avis:
                    idweb = record.get("idweb", record.get("id", ""))
                    url_avis = f"https://www.boamp.fr/avis/detail/{idweb}"

                # Extraire donnees enrichies
                enrichi = _extraire_donnees_boamp(record.get("donnees", ""))

                titre = record.get("objet", "Sans titre")
                description = enrichi.get("description") or titre
                code_cpv = record.get("descripteur_code", "")

                score = scorer_ao(titre, description, code_cpv)
                if score < 0.15:
                    continue

                ao = {
                    "id": f"BOAMP-{record.get('id', '')}",
                    "titre": titre,
                    "source": "BOAMP",
                    "acheteur": record.get("nomacheteur", "Inconnu"),
                    "date_publication": record.get("dateparution", ""),
                    "date_limite": record.get("datelimitereponse", ""),
                    "description": description,
                    "url": url_avis,
                    "region": record.get("code_departement", ""),
                    "code_cpv": code_cpv,
                    "type_marche": record.get("nature_libelle", ""),
                    "type_procedure": record.get("procedure_libelle", ""),
                    "budget_estime": enrichi.get("budget"),
                    "score_pertinence": score,
                    "statut": "nouveau",
                    "lieu_execution": enrichi.get("lieu_execution", ""),
                    "contact_nom": enrichi.get("contact_nom", ""),
                    "contact_email": enrichi.get("contact_email", ""),
                    "url_profil_acheteur": enrichi.get("url_profil_acheteur", ""),
                    "criteres_attribution": enrichi.get("criteres_attribution", ""),
                    "duree_mois": enrichi.get("duree_mois"),
                    "pieces_dossier": [],
                }
                resultats.append(ao)

        except Exception as e:
            logger.warning(f"Erreur BOAMP lot {lot}: {e}")

    # Dedoublonner
    vus = set()
    uniques = []
    for ao in resultats:
        if ao["id"] not in vus:
            vus.add(ao["id"])
            uniques.append(ao)

    logger.info(f"BOAMP: {len(uniques)} AO trouves")
    return uniques


def _extraire_donnees_boamp(donnees_raw) -> dict:
    """Extrait les infos enrichies du champ 'donnees' BOAMP."""
    result = {}
    if not donnees_raw:
        return result
    try:
        d = json.loads(donnees_raw) if isinstance(donnees_raw, str) else donnees_raw
    except (json.JSONDecodeError, TypeError):
        return result

    fn = d.get("FNSimple", d.get("ContractNotice", {}))
    initial = fn.get("initial", fn)
    nature = initial.get("natureMarche", {})
    communication = initial.get("communication", {})
    procedure = initial.get("procedure", {})

    desc = nature.get("description", "")
    if desc:
        result["description"] = desc

    valeur = nature.get("valeurEstimee", {})
    if isinstance(valeur, dict):
        val = valeur.get("valeur")
        if val:
            try:
                result["budget"] = float(val)
            except (ValueError, TypeError):
                pass

    duree = nature.get("dureeMois")
    if duree:
        try:
            result["duree_mois"] = int(duree)
        except (ValueError, TypeError):
            pass

    result["lieu_execution"] = nature.get("lieuExecution", "")
    result["contact_nom"] = communication.get("nomContact", "")
    result["contact_email"] = communication.get("adresseMailContact", "")

    url_profil = communication.get("urlProfilAch", "")
    if url_profil:
        result["url_profil_acheteur"] = url_profil

    criteres = procedure.get("criteresAttrib", "")
    if criteres:
        result["criteres_attribution"] = criteres

    return result


def rechercher_ted() -> list[dict]:
    """Interroge l'API TED v3 pour les AO europeens France."""
    API_URL = "https://api.ted.europa.eu/v3/notices/search"
    resultats = []

    date_debut = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
    filtre_base = f'PD >= {date_debut} AND organisation-country-buyer = "FRA"'

    # Requetes par paires de mots-cles
    mots = ["formation", "intelligence artificielle", "IA generative",
            "numerique", "transformation digitale", "accompagnement"]
    queries = []
    for i in range(0, len(mots), 2):
        lot = mots[i:i+2]
        ft = " AND ".join([f'FT ~ "{m}"' for m in lot])
        queries.append(f"{ft} AND {filtre_base}")

    # Par CPV
    for cpv in CODES_CPV[:3]:
        queries.append(f'FT ~ "{cpv}" AND {filtre_base}')

    fields = [
        "description-glo",
        "deadline-receipt-tender-date-lot",
        "organisation-city-buyer",
        "organisation-country-buyer",
        "tendering-party-name",
    ]

    for query in queries:
        try:
            payload = {"query": query, "fields": fields, "page": 1, "limit": 50}

            with httpx.Client(timeout=30, follow_redirects=True) as client:
                resp = client.post(API_URL, json=payload)
                if resp.status_code != 200:
                    continue
                data = resp.json()

            for notice in data.get("notices", []):
                pub_number = notice.get("publication-number", "")
                if not pub_number:
                    continue

                desc_glo = notice.get("description-glo", {})
                description = ""
                for lang in ["fra", "FRA", "eng", "ENG"]:
                    texts = desc_glo.get(lang, [])
                    if texts:
                        description = " | ".join(texts) if isinstance(texts, list) else str(texts)
                        break
                if not description and desc_glo:
                    first_lang = next(iter(desc_glo.values()), [])
                    if first_lang:
                        description = first_lang[0] if isinstance(first_lang, list) else str(first_lang)

                buyer = notice.get("tendering-party-name", "")
                if isinstance(buyer, list):
                    buyer = buyer[0] if buyer else ""

                city = notice.get("organisation-city-buyer", "")
                if isinstance(city, list):
                    city = city[0] if city else ""

                deadlines = notice.get("deadline-receipt-tender-date-lot", [])
                date_limite = ""
                if deadlines:
                    if isinstance(deadlines, list):
                        date_limite = deadlines[0].split("+")[0] if deadlines else ""
                    else:
                        date_limite = str(deadlines).split("+")[0]

                titre = description[:120] + "..." if len(description) > 120 else description
                if not titre:
                    titre = f"Avis TED {pub_number}"

                score = scorer_ao(titre, description)
                if score < 0.15:
                    continue

                ao = {
                    "id": f"TED-{pub_number}",
                    "titre": titre,
                    "source": "TED",
                    "acheteur": buyer,
                    "date_publication": "",
                    "date_limite": date_limite,
                    "description": description,
                    "url": f"https://ted.europa.eu/en/notice/{pub_number}",
                    "region": city,
                    "code_cpv": "",
                    "type_marche": "",
                    "type_procedure": "",
                    "budget_estime": None,
                    "score_pertinence": score,
                    "statut": "nouveau",
                    "pieces_dossier": [],
                }
                resultats.append(ao)

        except Exception as e:
            logger.warning(f"Erreur TED: {e}")

    # Dedoublonner
    vus = set()
    uniques = []
    for ao in resultats:
        if ao["id"] not in vus:
            vus.add(ao["id"])
            uniques.append(ao)

    logger.info(f"TED: {len(uniques)} AO trouves")
    return uniques


def lancer_veille() -> dict:
    """Lance la veille BOAMP + TED et met a jour ao_pertinents.json.
    Retourne un dict avec les stats."""
    logger.info("Veille Render demarree...")

    # Charger les AO existants
    existants = []
    if AO_CACHE.exists():
        with open(AO_CACHE, "r", encoding="utf-8") as f:
            existants = json.load(f)
    ids_existants = {ao["id"] for ao in existants}

    # Rechercher
    nouveaux_ao = []
    try:
        boamp = rechercher_boamp()
        nouveaux_ao.extend(boamp)
    except Exception as e:
        logger.error(f"Erreur BOAMP: {e}")

    try:
        ted = rechercher_ted()
        nouveaux_ao.extend(ted)
    except Exception as e:
        logger.error(f"Erreur TED: {e}")

    # Ajouter les nouveaux
    nb_nouveaux = 0
    for ao in nouveaux_ao:
        if ao["id"] not in ids_existants:
            existants.append(ao)
            ids_existants.add(ao["id"])
            nb_nouveaux += 1

    # Sauvegarder
    AO_CACHE.write_text(
        json.dumps(existants, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    result = {
        "timestamp": datetime.now().isoformat(),
        "boamp": len([a for a in nouveaux_ao if a["source"] == "BOAMP"]),
        "ted": len([a for a in nouveaux_ao if a["source"] == "TED"]),
        "nouveaux": nb_nouveaux,
        "total": len(existants),
    }
    logger.info(f"Veille terminee: {nb_nouveaux} nouveaux, {len(existants)} total")
    return result
