"""
Veille proactive sur les pre-informations (BOAMP + TED).
Anticipe les AO 2-6 mois avant publication officielle.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import httpx

logger = logging.getLogger("ao_hunter.veille_preinfo")

DASHBOARD_DIR = Path(__file__).parent
PREINFOS_FILE = DASHBOARD_DIR / "preinfos.json"

# Memes mots-cles de scoring que veille_render.py
MOTS_CLES_SCORING = [
    "formation", "IA", "intelligence artificielle", "numerique", "digital",
    "ChatGPT", "copilot", "midjourney", "prompt", "automatisation",
    "acculturation", "sensibilisation", "accompagnement", "consulting",
    "strategie", "deploiement", "diagnostic", "audit", "conduite du changement",
    "referent", "competences", "transformation", "generative", "machine learning",
    "data", "donnees", "e-learning", "LMS", "Qualiopi", "OPCO",
    "agent IA", "chatbot", "no code", "low code", "Make", "n8n",
]

EXCLUSIONS = [
    "travaux", "BTP", "construction", "nettoyage", "restauration scolaire",
    "transport", "voirie", "assainissement", "cablage", "fibre optique",
    "cybersecurite", "pentest", "videosurveillance", "videoprotection",
]

# CPV formation / conseil / IA
CODES_CPV_PREINFO = [
    "80500000", "80530000", "80533000", "80533100", "80570000",
    "79632000", "79400000", "79410000", "79411000", "72220000",
]

MOTS_CLES_RECHERCHE_PREINFO = [
    "formation intelligence artificielle",
    "formation IA",
    "formation numerique",
    "formation digitale",
    "intelligence artificielle",
    "accompagnement IA",
    "transformation digitale",
    "conseil strategie numerique",
]


def _scorer_preinfo(titre: str, description: str, cpv: str = "") -> float:
    """Score une pre-info par mots-cles (0.0 a 1.0). Meme logique que veille_render."""
    texte = f"{titre} {description}".lower()

    for excl in EXCLUSIONS:
        if excl.lower() in texte:
            return 0.0

    hits = 0
    for mot in MOTS_CLES_SCORING:
        if mot.lower() in texte:
            hits += 1

    if cpv:
        for code in CODES_CPV_PREINFO:
            if code in cpv:
                hits += 3
                break

    score = min(hits / 8.0, 1.0)
    return round(score, 2)


def _charger_preinfos() -> list[dict]:
    """Charge les pre-infos existantes."""
    if PREINFOS_FILE.exists():
        try:
            return json.loads(PREINFOS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _sauvegarder_preinfos(preinfos: list[dict]):
    """Sauvegarde dans preinfos.json."""
    PREINFOS_FILE.write_text(
        json.dumps(preinfos, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _rechercher_boamp_preinfo() -> list[dict]:
    """Interroge l'API BOAMP open data avec nature=PREINFORMATION pour les CPV formation/conseil/IA."""
    API_URL = "https://www.boamp.fr/api/explore/v2.1/catalog/datasets/boamp/records"
    resultats = []

    # Recherche par CPV pertinents + nature pre-information
    cpv_filter = " OR ".join(f'descripteur_code LIKE "{cpv}%"' for cpv in CODES_CPV_PREINFO)

    # Aussi recherche par mots-cles
    lots = [MOTS_CLES_RECHERCHE_PREINFO[i:i+3] for i in range(0, len(MOTS_CLES_RECHERCHE_PREINFO), 3)]

    queries = []
    # Requete par CPV + nature preinformation
    queries.append(f'({cpv_filter}) AND nature_libelle LIKE "%information%"')
    # Requetes par mots-cles + nature preinformation
    for lot in lots:
        like_parts = [f'objet LIKE "%{m}%"' for m in lot]
        like_clause = " OR ".join(like_parts)
        queries.append(f'({like_clause}) AND nature_libelle LIKE "%information%"')

    for where_clause in queries:
        try:
            params = {
                "select": "id,idweb,objet,nomacheteur,dateparution,datelimitereponse,"
                          "nature_libelle,descripteur_code,code_departement,"
                          "type_marche,url_avis,donnees",
                "where": where_clause,
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

                titre = record.get("objet", "Sans titre")
                cpv = record.get("descripteur_code", "")
                description = titre  # BOAMP pre-infos ont peu de description

                # Tenter d'extraire plus d'infos du champ donnees
                donnees_raw = record.get("donnees", "")
                contact = ""
                budget = None
                if donnees_raw:
                    try:
                        d = json.loads(donnees_raw) if isinstance(donnees_raw, str) else donnees_raw
                        fn = d.get("FNSimple", d.get("PriorInformationNotice", {}))
                        initial = fn.get("initial", fn)
                        desc_extra = initial.get("objet", {}).get("description", "")
                        if desc_extra:
                            description = desc_extra
                        contact_info = initial.get("contact", {})
                        contact = contact_info.get("email", "") or contact_info.get("nom", "")
                        budget_info = initial.get("montant", {})
                        if budget_info:
                            budget = budget_info.get("valeurEstimee") or budget_info.get("montantHT")
                    except (json.JSONDecodeError, TypeError, AttributeError):
                        pass

                score = _scorer_preinfo(titre, description, cpv)

                # Estimer la date de publication AO (2-6 mois apres la pre-info)
                date_pub = record.get("dateparution", "")
                date_estimee = ""
                if date_pub:
                    try:
                        dt = datetime.fromisoformat(date_pub.split("T")[0])
                        date_estimee = (dt + timedelta(days=120)).strftime("%Y-%m-%d")
                    except (ValueError, TypeError):
                        pass

                resultats.append({
                    "id": f"BOAMP-PRE-{record.get('id', '')}",
                    "titre": titre,
                    "acheteur": record.get("nomacheteur", "Inconnu"),
                    "description": description,
                    "cpv": cpv,
                    "date_publication": date_pub,
                    "date_estimee_publication_ao": date_estimee,
                    "region": record.get("code_departement", ""),
                    "budget_estime": budget,
                    "contact": contact,
                    "source": "BOAMP",
                    "score_pertinence": score,
                    "url": url_avis,
                })

        except Exception as e:
            logger.warning(f"Erreur BOAMP pre-info: {e}")

    return resultats


def _rechercher_ted_preinfo() -> list[dict]:
    """Interroge l'API TED v3 pour les prior information notices (subtype=4)."""
    API_URL = "https://api.ted.europa.eu/v3/notices/search"
    resultats = []

    # Mots-cles combines pour la recherche TED
    search_terms = [
        "formation intelligence artificielle",
        "formation numerique",
        "conseil strategie IA",
        "accompagnement transformation digitale",
    ]

    for term in search_terms:
        try:
            params = {
                "q": term,
                "fields": "notice-type,publication-date,title-text,buyer-name,"
                          "cpv-code,place-of-performance,deadline-receipt-tenders",
                "scope": "3",  # EU + national
                "sortField": "publication-date",
                "sortOrder": "desc",
                "pageSize": 25,
                "pageNum": 1,
                "noticeType": "pin",  # prior information notice
            }

            with httpx.Client(timeout=30, follow_redirects=True) as client:
                resp = client.get(API_URL, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                elif resp.status_code in (400, 404):
                    # Fallback : essayer l'ancien format TED
                    params_v2 = {
                        "q": f'TD=["Prior information notice"] AND ({term})',
                        "fields": "ND,TI,CY,AC,OL,DD,DT,RC,MA",
                        "pageSize": 25,
                        "pageNum": 1,
                    }
                    resp2 = client.get(
                        "https://ted.europa.eu/api/v3.0/notices/search",
                        params=params_v2,
                    )
                    if resp2.status_code == 200:
                        data = resp2.json()
                    else:
                        continue
                else:
                    continue

            notices = data.get("notices", data.get("results", []))
            for notice in notices:
                titre = (notice.get("title-text") or notice.get("TI", {}).get("text", "")
                         or notice.get("title", "Sans titre"))
                if isinstance(titre, dict):
                    titre = titre.get("fra", titre.get("eng", str(titre)))

                acheteur = (notice.get("buyer-name") or notice.get("AC", "")
                            or notice.get("buyerName", "Inconnu"))
                if isinstance(acheteur, dict):
                    acheteur = acheteur.get("fra", acheteur.get("eng", str(acheteur)))

                cpv = notice.get("cpv-code", notice.get("cpvCode", ""))
                if isinstance(cpv, list):
                    cpv = cpv[0] if cpv else ""

                description = notice.get("description", titre)
                if isinstance(description, dict):
                    description = description.get("fra", description.get("eng", str(description)))

                date_pub = (notice.get("publication-date") or notice.get("DT", "")
                            or notice.get("publicationDate", ""))

                region = notice.get("place-of-performance", notice.get("RC", ""))
                if isinstance(region, dict):
                    region = region.get("fra", str(region))

                notice_id = notice.get("ND", notice.get("noticeId", notice.get("id", "")))

                score = _scorer_preinfo(str(titre), str(description), str(cpv))

                # Estimer date publication AO
                date_estimee = ""
                if date_pub:
                    try:
                        dt = datetime.fromisoformat(str(date_pub).split("T")[0])
                        date_estimee = (dt + timedelta(days=120)).strftime("%Y-%m-%d")
                    except (ValueError, TypeError):
                        pass

                contact = notice.get("contact", notice.get("contactEmail", ""))
                budget = notice.get("estimatedValue", notice.get("MA", None))

                resultats.append({
                    "id": f"TED-PRE-{notice_id}",
                    "titre": str(titre)[:200],
                    "acheteur": str(acheteur)[:150],
                    "description": str(description)[:500],
                    "cpv": str(cpv),
                    "date_publication": str(date_pub),
                    "date_estimee_publication_ao": date_estimee,
                    "region": str(region)[:50],
                    "budget_estime": budget,
                    "contact": str(contact) if contact else "",
                    "source": "TED",
                    "score_pertinence": score,
                    "url": f"https://ted.europa.eu/notice/{notice_id}" if notice_id else "",
                })

        except Exception as e:
            logger.warning(f"Erreur TED pre-info ({term}): {e}")

    return resultats


def veille_preinfo() -> list[dict]:
    """Lance la veille sur les pre-informations BOAMP et TED.

    Retourne uniquement les pre-infos pertinentes (score > 0.3).
    Sauvegarde dans preinfos.json.
    """
    logger.info("Demarrage veille pre-informations...")

    # Collecter depuis les deux sources
    resultats = []
    resultats.extend(_rechercher_boamp_preinfo())
    resultats.extend(_rechercher_ted_preinfo())

    # Filtrer par score minimum
    pertinentes = [p for p in resultats if p.get("score_pertinence", 0) > 0.3]

    # Dedoublonner par ID
    vus = set()
    uniques = []
    for p in pertinentes:
        if p["id"] not in vus:
            vus.add(p["id"])
            uniques.append(p)

    # Fusionner avec les pre-infos existantes (conserver les anciennes non presentes)
    existantes = _charger_preinfos()
    ids_nouveaux = {p["id"] for p in uniques}
    # Garder les anciennes qui ne sont pas dans les nouveaux resultats
    for ex in existantes:
        if ex["id"] not in ids_nouveaux:
            uniques.append(ex)

    # Trier par score
    uniques.sort(key=lambda p: p.get("score_pertinence", 0), reverse=True)

    _sauvegarder_preinfos(uniques)
    logger.info(f"Veille pre-info terminee: {len(pertinentes)} nouvelles pertinentes, {len(uniques)} total")

    return uniques


def preinfos_actives() -> list[dict]:
    """Filtre les pre-infos dont la date estimee de publication AO est dans le futur."""
    preinfos = _charger_preinfos()
    today = datetime.now().strftime("%Y-%m-%d")

    actives = []
    for p in preinfos:
        date_estimee = p.get("date_estimee_publication_ao", "")
        # Si pas de date estimee, on considere la pre-info comme active
        if not date_estimee or date_estimee >= today:
            actives.append(p)

    return actives


def recommander_actions(preinfo: dict) -> list[str]:
    """Suggestions d'actions proactives pour une pre-information."""
    actions = []
    score = preinfo.get("score_pertinence", 0)
    contact = preinfo.get("contact", "")
    budget = preinfo.get("budget_estime")
    acheteur = preinfo.get("acheteur", "")
    date_estimee = preinfo.get("date_estimee_publication_ao", "")

    # Actions de base toujours pertinentes
    if contact:
        actions.append(f"Contacter l'acheteur ({contact}) pour obtenir des precisions sur le besoin")
    elif acheteur and acheteur != "Inconnu":
        actions.append(f"Rechercher les coordonnees de {acheteur} et prendre contact")

    # Preparation technique
    if score >= 0.5:
        actions.append("Preparer les references clients similaires et les adapter au besoin identifie")
        actions.append("Identifier les formateurs/consultants les plus pertinents pour ce marche")

    if score >= 0.7:
        actions.append("Commencer la redaction d'un memoire technique type adapte au besoin")

    # Budget et groupement
    if budget and isinstance(budget, (int, float)) and budget > 100000:
        actions.append("Envisager la constitution d'un groupement pour renforcer la candidature")

    if not budget:
        actions.append("Estimer le budget probable en se basant sur des marches similaires")

    # Preparation administrative
    actions.append("Verifier que les pieces administratives (Qualiopi, URSSAF, Kbis) sont a jour")

    # Timeline
    if date_estimee:
        try:
            dt = datetime.fromisoformat(date_estimee)
            jours = (dt - datetime.now()).days
            if jours > 90:
                actions.append(f"Publication estimee dans ~{jours} jours : temps suffisant pour une preparation approfondie")
            elif jours > 30:
                actions.append(f"Publication estimee dans ~{jours} jours : accelerer la preparation")
            elif jours > 0:
                actions.append(f"Publication estimee dans ~{jours} jours : surveiller quotidiennement le BOAMP/TED")
        except (ValueError, TypeError):
            pass

    return actions
