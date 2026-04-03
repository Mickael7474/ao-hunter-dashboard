"""
Veille TED multi-langue - Feature 11
Detecte les AO en anglais/allemand/espagnol pertinents pour Almera
sur le portail TED (institutions EU, pays francophones/limitrophes).
Traduction automatique via Claude Haiku.
"""

import os
import json
import logging
from datetime import datetime, timedelta

import httpx

logger = logging.getLogger("ao_hunter.ted_multilangue")

API_URL = "https://api.ted.europa.eu/v3/notices/search"
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODELE_HAIKU = "claude-haiku-3-5-20251001"

# Mots-cles par langue
MOTS_CLES_MULTILANGUE = {
    "EN": [
        "artificial intelligence training",
        "AI training",
        "digital transformation training",
        "machine learning training",
        "generative AI",
    ],
    "DE": [
        "KI Schulung",
        "kuenstliche Intelligenz",
        "digitale Transformation",
        "KI Training",
    ],
    "ES": [
        "formacion inteligencia artificial",
        "formacion digital",
        "transformacion digital",
        "inteligencia artificial capacitacion",
    ],
}

# Pays francophones ou limitrophes + institutions EU
PAYS_CIBLES = ["FRA", "BEL", "LUX", "CHE", "MCO"]

# Filtre TED : pays cibles OU institutions EU (pas de filtre pays)
_PAYS_FILTER = " OR ".join([f'organisation-country-buyer = "{p}"' for p in PAYS_CIBLES])

# Mots-cles pour identifier les institutions EU
INSTITUTIONS_EU = [
    "european commission", "commission europeenne", "european parliament",
    "parlement europeen", "council of the european union", "european council",
    "european central bank", "european investment bank", "frontex",
    "europol", "eurojust", "efsa", "ema", "echa", "easa",
    "eu-osha", "cedefop", "enisa", "berec",
]

# CPV formation/conseil (les memes que veille_render.py)
CODES_CPV = [
    "80500000", "80530000", "80533000", "72220000", "79400000",
]


def _appel_claude_haiku(prompt: str, max_tokens: int = 2000) -> str:
    """Appelle Claude Haiku pour la traduction."""
    if not API_KEY:
        logger.warning("ANTHROPIC_API_KEY manquante, traduction impossible")
        return ""

    try:
        with httpx.Client(timeout=60) as client:
            resp = client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": MODELE_HAIKU,
                    "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            resp.raise_for_status()
            data = resp.json()
        return data["content"][0]["text"]
    except Exception as e:
        logger.warning(f"Erreur appel Claude Haiku: {e}")
        return ""


def traduire_lot(texte: str, langue_source: str) -> str:
    """Traduction rapide d'un texte via Claude Haiku.

    Args:
        texte: le texte a traduire
        langue_source: "EN", "DE", "ES"

    Returns:
        Traduction en francais
    """
    if not texte or not texte.strip():
        return texte

    langues = {"EN": "anglais", "DE": "allemand", "ES": "espagnol"}
    nom_langue = langues.get(langue_source, "anglais")

    prompt = (
        f"Traduis ce texte de {nom_langue} en francais. "
        f"Reponds UNIQUEMENT avec la traduction, sans commentaire.\n\n"
        f"{texte}"
    )
    result = _appel_claude_haiku(prompt, max_tokens=500)
    return result.strip() if result else texte


def _traduire_batch(items: list[dict]) -> list[dict]:
    """Traduit un lot d'AO en un seul appel Claude Haiku.

    Args:
        items: list de dicts avec au moins 'titre', 'description', 'langue_originale'

    Returns:
        Meme liste avec 'traduction_titre' et 'traduction_description' ajoutees
    """
    if not items:
        return items

    # Construire le prompt batch
    lignes = []
    for i, item in enumerate(items):
        titre = item.get("titre", "")
        desc = (item.get("description", "") or "")[:300]
        langue = item.get("langue_originale", "EN")
        lignes.append(f"[{i}] ({langue}) TITRE: {titre}\nDESCRIPTION: {desc}")

    texte_batch = "\n---\n".join(lignes)

    prompt = (
        "Tu es traducteur. Traduis les titres et descriptions suivants en francais.\n"
        "Pour chaque element, reponds au format:\n"
        "[N] TITRE: <traduction du titre>\n"
        "DESCRIPTION: <traduction de la description>\n\n"
        "Textes a traduire:\n\n"
        f"{texte_batch}"
    )

    result = _appel_claude_haiku(prompt, max_tokens=3000)
    if not result:
        # Fallback: pas de traduction, on garde l'original
        for item in items:
            item["traduction_titre"] = item.get("titre", "")
            item["traduction_description"] = item.get("description", "")
        return items

    # Parser les traductions
    import re
    for i, item in enumerate(items):
        pattern = rf"\[{i}\]\s*TITRE:\s*(.+?)(?:\nDESCRIPTION:\s*(.+?))?(?=\n\[|\Z)"
        match = re.search(pattern, result, re.DOTALL)
        if match:
            item["traduction_titre"] = match.group(1).strip()
            item["traduction_description"] = (match.group(2) or "").strip()
        else:
            item["traduction_titre"] = item.get("titre", "")
            item["traduction_description"] = item.get("description", "")

    return items


def _est_institution_eu(nom_acheteur: str) -> bool:
    """Verifie si l'acheteur est une institution europeenne."""
    if not nom_acheteur:
        return False
    nom_lower = nom_acheteur.lower()
    return any(inst in nom_lower for inst in INSTITUTIONS_EU)


def _score_pertinence_multilangue(titre: str, description: str, acheteur: str, pays: str) -> float:
    """Score de pertinence adapte pour les AO multilingues.

    Bonus pour institutions EU basees en France/Belgique.
    """
    from veille_render import scorer_ao, MOTS_CLES_SCORING

    # Score de base avec les mots-cles traduits
    texte = f"{titre} {description}".lower()

    # Mots-cles specifiques multilingues (EN/DE/ES + FR post-traduction)
    mots_bonus = [
        "training", "formation", "artificial intelligence", "intelligence artificielle",
        "ia", "ai", "digital", "transformation", "machine learning",
        "generative", "schulung", "formacion", "capacitacion",
    ]

    hits = 0
    for mot in mots_bonus:
        if mot.lower() in texte:
            hits += 1

    score = min(hits / 6.0, 1.0)

    # Bonus institution EU
    if _est_institution_eu(acheteur):
        score = min(score + 0.15, 1.0)
        # Bonus supplementaire si basee en France ou Belgique
        if pays in ("FRA", "BEL"):
            score = min(score + 0.1, 1.0)

    return round(score, 2)


def veille_ted_multilangue() -> list[dict]:
    """Interroge TED v3 avec des mots-cles en EN/DE/ES.

    Filtre sur pays francophones/limitrophes + institutions EU.
    Traduit les resultats en francais via Claude Haiku (appel batch).

    Returns:
        list[dict] au format standard AO avec champs langue_originale et traduction_titre
    """
    resultats = []
    date_debut = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")

    fields = [
        "description-glo",
        "deadline-receipt-tender-date-lot",
        "organisation-city-buyer",
        "organisation-country-buyer",
        "tendering-party-name",
    ]

    for langue, mots_cles in MOTS_CLES_MULTILANGUE.items():
        for mot in mots_cles:
            try:
                # Chercher dans les pays cibles
                query = (
                    f'FT ~ "{mot}" AND PD >= {date_debut} '
                    f'AND ({_PAYS_FILTER})'
                )
                payload = {"query": query, "fields": fields, "page": 1, "limit": 50}

                with httpx.Client(timeout=30, follow_redirects=True) as client:
                    resp = client.post(API_URL, json=payload)
                    if resp.status_code != 200:
                        continue
                    data = resp.json()

                for notice in data.get("notices", []):
                    ao = _parser_notice_ted(notice, langue)
                    if ao:
                        resultats.append(ao)

            except Exception as e:
                logger.warning(f"Erreur TED multilangue ({langue}) '{mot}': {e}")

        # Recherche institutions EU (sans filtre pays)
        for mot in mots_cles[:2]:  # limiter les requetes
            try:
                query = f'FT ~ "{mot}" AND PD >= {date_debut}'
                payload = {"query": query, "fields": fields, "page": 1, "limit": 30}

                with httpx.Client(timeout=30, follow_redirects=True) as client:
                    resp = client.post(API_URL, json=payload)
                    if resp.status_code != 200:
                        continue
                    data = resp.json()

                for notice in data.get("notices", []):
                    buyer = notice.get("tendering-party-name", "")
                    if isinstance(buyer, list):
                        buyer = buyer[0] if buyer else ""
                    if _est_institution_eu(buyer):
                        ao = _parser_notice_ted(notice, langue)
                        if ao:
                            resultats.append(ao)

            except Exception as e:
                logger.warning(f"Erreur TED institutions EU ({langue}) '{mot}': {e}")

    # Dedoublonner
    vus = set()
    uniques = []
    for ao in resultats:
        if ao["id"] not in vus:
            vus.add(ao["id"])
            uniques.append(ao)

    logger.info(f"TED multilangue: {len(uniques)} AO trouves avant traduction")

    # Traduire en batch si on a des resultats
    if uniques and API_KEY:
        # Grouper par lots de 10 pour ne pas depasser les limites
        for i in range(0, len(uniques), 10):
            batch = uniques[i:i + 10]
            _traduire_batch(batch)

        # Mettre a jour le titre avec la traduction
        for ao in uniques:
            trad_titre = ao.get("traduction_titre")
            if trad_titre and trad_titre != ao.get("titre"):
                ao["titre"] = trad_titre
            trad_desc = ao.get("traduction_description")
            if trad_desc:
                ao["description"] = trad_desc

    # Recalculer les scores apres traduction
    for ao in uniques:
        ao["score_pertinence"] = _score_pertinence_multilangue(
            ao.get("titre", ""),
            ao.get("description", ""),
            ao.get("acheteur", ""),
            ao.get("pays_acheteur", ""),
        )

    # Filtrer les non-pertinents
    uniques = [ao for ao in uniques if ao.get("score_pertinence", 0) >= 0.15]

    logger.info(f"TED multilangue: {len(uniques)} AO pertinents apres traduction")
    return uniques


def _parser_notice_ted(notice: dict, langue: str) -> dict | None:
    """Parse une notice TED en dict AO standard."""
    pub_number = notice.get("publication-number", "")
    if not pub_number:
        return None

    desc_glo = notice.get("description-glo", {})
    description = ""

    # Chercher dans la langue correspondante d'abord
    lang_codes = {
        "EN": ["eng", "ENG"],
        "DE": ["deu", "DEU", "ger", "GER"],
        "ES": ["spa", "SPA", "esl", "ESL"],
    }
    codes_prio = lang_codes.get(langue, ["eng", "ENG"])
    # Puis francais, puis n'importe quoi
    codes_prio.extend(["fra", "FRA", "eng", "ENG"])

    for lang_code in codes_prio:
        texts = desc_glo.get(lang_code, [])
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

    country = notice.get("organisation-country-buyer", "")
    if isinstance(country, list):
        country = country[0] if country else ""

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

    # Badge source avec la langue
    source = f"TED-{langue}"

    return {
        "id": f"TED-{pub_number}",
        "titre": titre,
        "source": source,
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
        "score_pertinence": 0,
        "statut": "nouveau",
        "pieces_dossier": [],
        "langue_originale": langue,
        "traduction_titre": "",
        "traduction_description": "",
        "pays_acheteur": country,
    }
