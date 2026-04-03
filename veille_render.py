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
import feedparser
from bs4 import BeautifulSoup

logger = logging.getLogger("ao_hunter.veille_render")

DASHBOARD_DIR = Path(__file__).parent
AO_CACHE = DASHBOARD_DIR / "ao_pertinents.json"
HISTORIQUE_VEILLE = DASHBOARD_DIR / "historique_veille.json"

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


LOTS_NON_PERTINENTS = [
    "travaux", "btp", "construction", "nettoyage", "restauration",
    "transport", "voirie", "assainissement", "cablage", "fibre optique",
    "cybersecurite", "pentest", "videosurveillance", "infrastructure",
    "fourniture", "materiel informatique", "equipement", "mobilier",
    "electricite", "plomberie", "chauffage", "climatisation", "espaces verts",
    "securite incendie", "gardiennage", "maintenance batiment",
]


def detecter_lots_pertinents(ao: dict) -> list[dict]:
    """Detecte les lots dans le titre/description d'un AO et evalue leur pertinence.

    Returns:
        list de {numero, description, pertinent: bool, score}
    """
    titre = ao.get("titre") or ""
    description = ao.get("description") or ""
    texte = f"{titre}\n{description}"

    lots = []
    patterns = [
        r"lot\s*n?\s*[°º]?\s*(\d+)\s*[:\-–]\s*(.+?)(?:\n|$)",
        r"lot\s+(\d+)\s*[:\-–]\s*(.+?)(?:\n|$)",
        r"lot\s+(\d+)\s*[:\-–]\s*(.+?)(?:\.|;|$)",
    ]
    for pattern in patterns:
        matches = re.findall(pattern, texte, re.IGNORECASE)
        for num, desc in matches:
            desc = desc.strip()[:150]
            if len(desc) > 3:
                lots.append({"numero": int(num), "description": desc})

    # Dedoublonner par numero
    vus = set()
    uniques = []
    for lot in lots:
        if lot["numero"] not in vus:
            vus.add(lot["numero"])
            uniques.append(lot)

    # Scorer chaque lot
    for lot in uniques:
        desc_lower = lot["description"].lower()

        # Verifier exclusion
        exclu = any(e in desc_lower for e in LOTS_NON_PERTINENTS)

        # Compter les mots-cles positifs
        hits = 0
        for mot in MOTS_CLES_SCORING:
            if mot.lower() in desc_lower:
                hits += 1
        score = min(hits / 5.0, 1.0) if not exclu else 0.0

        lot["score"] = round(score, 2)
        lot["pertinent"] = score >= 0.2 and not exclu

    return uniques


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


def rechercher_marches_securises() -> list[dict]:
    """Interroge marches-securises.fr via flux RSS."""
    RSS_URL = "https://www.marches-securises.fr/entreprise/rss"
    resultats = []

    for mot in MOTS_CLES_RECHERCHE[:6]:
        try:
            with httpx.Client(timeout=30, follow_redirects=True) as client:
                resp = client.get(RSS_URL, params={"q": mot})
                if resp.status_code != 200:
                    continue

            feed = feedparser.parse(resp.text)

            for entry in feed.entries[:30]:
                entry_id = entry.get("id", entry.get("link", ""))
                if not entry_id:
                    continue
                clean_id = re.sub(r"[^\w-]", "", entry_id[-30:])
                titre = entry.get("title", "Sans titre")
                description = entry.get("summary", entry.get("description", ""))
                url = entry.get("link", "")

                acheteur = ""
                if " - " in titre:
                    parts = titre.split(" - ", 1)
                    if len(parts[0]) < 60:
                        acheteur = parts[0].strip()

                score = scorer_ao(titre, description)
                if score < 0.15:
                    continue

                resultats.append({
                    "id": f"MSEC-{clean_id}",
                    "titre": titre,
                    "source": "M-Securises",
                    "acheteur": acheteur,
                    "date_publication": entry.get("published", ""),
                    "date_limite": "",
                    "description": description,
                    "url": url,
                    "region": "",
                    "code_cpv": "",
                    "type_marche": "",
                    "type_procedure": "",
                    "budget_estime": None,
                    "score_pertinence": score,
                    "statut": "nouveau",
                    "pieces_dossier": [],
                })
        except Exception as e:
            logger.warning(f"Erreur M-Securises pour '{mot}': {e}")

    # Dedoublonner
    vus = set()
    uniques = []
    for ao in resultats:
        if ao["id"] not in vus:
            vus.add(ao["id"])
            uniques.append(ao)
    logger.info(f"M-Securises: {len(uniques)} AO trouves")
    return uniques


def rechercher_aws_defense() -> list[dict]:
    """Interroge le portail achats du Ministere des Armees."""
    SEARCH_URL = "https://www.achats.defense.gouv.fr/entreprises-recherche-annonces"
    resultats = []

    for mot in MOTS_CLES_RECHERCHE[:4]:
        try:
            with httpx.Client(timeout=30, follow_redirects=True) as client:
                resp = client.get(SEARCH_URL, params={"fullText": mot})
                if resp.status_code != 200:
                    continue

            soup = BeautifulSoup(resp.text, "html.parser")
            annonces = soup.select(".annonce, .result-item, .consultation-item, tr.odd, tr.even")
            if not annonces:
                annonces = soup.find_all("a", href=re.compile(r"consultation|annonce|avis"))

            for annonce in annonces[:20]:
                if annonce.name == "a":
                    titre = annonce.get_text(strip=True)
                    url = annonce.get("href", "")
                    if not url.startswith("http"):
                        url = f"https://www.achats.defense.gouv.fr{url}"
                else:
                    titre = annonce.get_text(separator=" ", strip=True)[:150]
                    links = annonce.find_all("a", href=True)
                    url = ""
                    for link in links:
                        href = link.get("href", "")
                        if "consultation" in href or "annonce" in href:
                            url = href if href.startswith("http") else f"https://www.achats.defense.gouv.fr{href}"
                            break

                if not titre or len(titre) < 10:
                    continue

                score = scorer_ao(titre, titre)
                if score < 0.15:
                    continue

                clean_id = re.sub(r"[^\w-]", "", titre[:40])
                resultats.append({
                    "id": f"AWS-{clean_id}",
                    "titre": titre,
                    "source": "AWS-Achat",
                    "acheteur": "Ministere des Armees",
                    "date_publication": "",
                    "date_limite": "",
                    "description": titre,
                    "url": url,
                    "region": "",
                    "code_cpv": "",
                    "type_marche": "",
                    "type_procedure": "",
                    "budget_estime": None,
                    "score_pertinence": score,
                    "statut": "nouveau",
                    "pieces_dossier": [],
                })
        except Exception as e:
            logger.warning(f"Erreur AWS pour '{mot}': {e}")

    vus = set()
    uniques = []
    for ao in resultats:
        if ao["id"] not in vus:
            vus.add(ao["id"])
            uniques.append(ao)
    logger.info(f"AWS-Achat: {len(uniques)} AO trouves")
    return uniques


def detecter_doublons(appels: list[dict]) -> list[tuple]:
    """Detecte les doublons cross-sources par similarite de titre.

    Returns:
        list de tuples (ao1_id, ao2_id, score_similarite)
    """
    doublons = []

    def normaliser(texte: str) -> set:
        """Extrait les mots significatifs (>3 chars) en minuscule."""
        mots = re.sub(r"[^\w\s]", " ", texte.lower()).split()
        return {m for m in mots if len(m) > 3}

    # Indexer par mots-cles significatifs pour eviter O(n^2)
    ao_mots = []
    for ao in appels:
        titre = ao.get("titre", "")
        acheteur = ao.get("acheteur", "")
        mots = normaliser(f"{titre} {acheteur}")
        ao_mots.append((ao, mots))

    for i, (ao1, mots1) in enumerate(ao_mots):
        if not mots1:
            continue
        for j in range(i + 1, len(ao_mots)):
            ao2, mots2 = ao_mots[j]
            if not mots2:
                continue
            # Meme source = pas un doublon cross-source
            if ao1.get("source") == ao2.get("source"):
                continue
            # Similarite Jaccard
            intersection = mots1 & mots2
            union = mots1 | mots2
            if not union:
                continue
            similarite = len(intersection) / len(union)
            if similarite >= 0.5:
                doublons.append((ao1["id"], ao2["id"], round(similarite, 2)))

    return doublons


def fusionner_doublons(appels: list[dict]) -> tuple[list[dict], int]:
    """Detecte et fusionne les doublons, garde le meilleur score.

    Returns:
        (liste_nettoyee, nb_supprimes)
    """
    doublons = detecter_doublons(appels)
    if not doublons:
        return appels, 0

    ids_a_supprimer = set()
    index = {ao["id"]: ao for ao in appels}

    for id1, id2, sim in doublons:
        if id1 in ids_a_supprimer or id2 in ids_a_supprimer:
            continue
        ao1 = index.get(id1)
        ao2 = index.get(id2)
        if not ao1 or not ao2:
            continue

        # Garder celui avec le meilleur score, ou le plus d'infos
        score1 = ao1.get("score_pertinence", 0) or 0
        score2 = ao2.get("score_pertinence", 0) or 0
        info1 = len(str(ao1.get("description", ""))) + (1 if ao1.get("budget_estime") else 0)
        info2 = len(str(ao2.get("description", ""))) + (1 if ao2.get("budget_estime") else 0)

        if score1 + info1 / 1000 >= score2 + info2 / 1000:
            ids_a_supprimer.add(id2)
            # Enrichir le gagnant avec les infos du doublon
            if not ao1.get("budget_estime") and ao2.get("budget_estime"):
                ao1["budget_estime"] = ao2["budget_estime"]
            if not ao1.get("contact_email") and ao2.get("contact_email"):
                ao1["contact_email"] = ao2["contact_email"]
        else:
            ids_a_supprimer.add(id1)
            if not ao2.get("budget_estime") and ao1.get("budget_estime"):
                ao2["budget_estime"] = ao1["budget_estime"]

    nettoyee = [ao for ao in appels if ao["id"] not in ids_a_supprimer]
    return nettoyee, len(ids_a_supprimer)


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

    # Rechercher sur toutes les sources
    nouveaux_ao = []
    sources = [
        ("BOAMP", rechercher_boamp),
        ("TED", rechercher_ted),
        ("M-Securises", rechercher_marches_securises),
        ("AWS-Achat", rechercher_aws_defense),
    ]
    for nom, func in sources:
        try:
            aos = func()
            nouveaux_ao.extend(aos)
        except Exception as e:
            logger.error(f"Erreur {nom}: {e}")

    # Enrichir les AO avec detection de lots
    for ao in nouveaux_ao:
        lots = detecter_lots_pertinents(ao)
        if lots:
            ao["lots_detectes"] = lots
            ao["lots_pertinents"] = sum(1 for l in lots if l.get("pertinent"))

    # Ajouter les nouveaux
    nb_nouveaux = 0
    for ao in nouveaux_ao:
        if ao["id"] not in ids_existants:
            existants.append(ao)
            ids_existants.add(ao["id"])
            nb_nouveaux += 1

    # Deduplication cross-sources
    existants, nb_doublons = fusionner_doublons(existants)

    # Sauvegarder
    AO_CACHE.write_text(
        json.dumps(existants, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    sources_count = {}
    for ao in nouveaux_ao:
        src = ao.get("source", "?")
        sources_count[src] = sources_count.get(src, 0) + 1

    result = {
        "timestamp": datetime.now().isoformat(),
        "date": datetime.now().strftime("%Y-%m-%d"),
        "heure": datetime.now().strftime("%H:%M"),
        "par_source": sources_count,
        "nouveaux": nb_nouveaux,
        "doublons_supprimes": nb_doublons,
        "total": len(existants),
    }

    # Sauvegarder dans l'historique
    _sauvegarder_historique(result)

    logger.info(f"Veille terminee: {nb_nouveaux} nouveaux, {nb_doublons} doublons, {len(existants)} total")
    return result


def _sauvegarder_historique(result: dict):
    """Ajoute un cycle de veille a l'historique."""
    historique = []
    if HISTORIQUE_VEILLE.exists():
        try:
            historique = json.loads(HISTORIQUE_VEILLE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            historique = []

    historique.append(result)
    # Garder les 90 derniers jours
    historique = historique[-270:]  # ~3 cycles/jour x 90 jours

    HISTORIQUE_VEILLE.write_text(
        json.dumps(historique, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def charger_historique() -> list[dict]:
    """Charge l'historique des veilles."""
    if HISTORIQUE_VEILLE.exists():
        try:
            return json.loads(HISTORIQUE_VEILLE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return []


def stats_tendances(historique: list[dict]) -> dict:
    """Calcule les tendances a partir de l'historique."""
    if not historique:
        return {"par_jour": {}, "total_nouveaux": 0, "moyenne_jour": 0}

    par_jour = {}
    for h in historique:
        date = h.get("date", "")
        if date not in par_jour:
            par_jour[date] = {"nouveaux": 0, "total": 0, "cycles": 0}
        par_jour[date]["nouveaux"] += h.get("nouveaux", 0)
        par_jour[date]["total"] = h.get("total", 0)  # dernier total du jour
        par_jour[date]["cycles"] += 1

    total_nouveaux = sum(h.get("nouveaux", 0) for h in historique)
    nb_jours = len(par_jour) or 1
    moyenne_jour = total_nouveaux / nb_jours

    # 7 derniers jours
    dates_triees = sorted(par_jour.keys(), reverse=True)[:14]
    derniers_jours = {d: par_jour[d] for d in reversed(dates_triees)}

    return {
        "par_jour": derniers_jours,
        "total_nouveaux": total_nouveaux,
        "moyenne_jour": round(moyenne_jour, 1),
        "nb_cycles": len(historique),
        "dernier_cycle": historique[-1] if historique else None,
    }
