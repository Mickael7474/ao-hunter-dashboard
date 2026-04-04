"""
Intelligence concurrentielle pour AO Hunter.
Scrape les sites concurrents pour extraire leur positionnement,
arguments et differenciants, puis genere des contre-arguments pour Almera.
"""

import json
import logging
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx

logger = logging.getLogger("ao_hunter.intelligence_concurrentielle")

DASHBOARD_DIR = Path(__file__).parent
CACHE_FILE = DASHBOARD_DIR / "concurrents_cache.json"
CACHE_TTL_JOURS = 30

# ─────────────────────────────────────────────
# Base de donnees des concurrents connus
# ─────────────────────────────────────────────

CONCURRENTS_CONNUS = {
    "orsys": {
        "site": "https://www.orsys.fr",
        "type": "Grand catalogue formation IT",
        "ca_estime": "100M+",
        "certifications": ["Qualiopi"],
        "forces": [
            "Catalogue tres large",
            "Notoriete",
            "Presence nationale",
            "Inter-entreprise",
        ],
        "faiblesses": [
            "Formation catalogue standardisee",
            "Pas de RS6776",
            "Pas specialise IA generative",
            "Prix eleves",
        ],
    },
    "cegos": {
        "site": "https://www.cegos.fr",
        "type": "Leader formation professionnelle",
        "ca_estime": "500M+",
        "certifications": ["Qualiopi"],
        "forces": [
            "Leader marche",
            "International",
            "E-learning + presentiel",
            "Marque forte",
        ],
        "faiblesses": [
            "Tres generaliste",
            "Prix premium",
            "Pas de RS6776",
            "Peu agile",
        ],
    },
    "demos": {
        "site": "https://www.demos.fr",
        "type": "Formation professionnelle generaliste",
        "ca_estime": "50M+",
        "certifications": ["Qualiopi"],
        "forces": ["Large catalogue", "Presence nationale"],
        "faiblesses": ["Generaliste", "Pas de RS6776", "Pas specialise IA"],
    },
    "m2i": {
        "site": "https://www.m2iformation.fr",
        "type": "Formation IT et digital",
        "ca_estime": "30M+",
        "certifications": ["Qualiopi"],
        "forces": ["Specialiste IT", "Bonnes formations techniques"],
        "faiblesses": [
            "Pas de RS6776",
            "Moins specialise IA generative qu'Almera",
        ],
    },
    "ib formation": {
        "site": "https://www.ib-formation.fr",
        "type": "Formation IT Cegos Group",
        "ca_estime": "40M+",
        "certifications": ["Qualiopi"],
        "forces": ["Groupe Cegos", "Specialiste IT"],
        "faiblesses": ["Pas de RS6776", "Catalogue vs sur-mesure"],
    },
}

# Forces connues d'Almera (pour comparaison)
ALMERA_FORCES = {
    "certifications": ["Qualiopi", "RS6776 France Competences"],
    "specialisation": "IA generative (ChatGPT, Claude, Copilot, Midjourney, agents IA)",
    "approche": "100% sur-mesure (vs catalogue)",
    "structure": "Petite structure = agilite, contact direct avec l'expert formateur",
    "reseau": "~10 formateurs freelances specialises",
    "methode": "4 etapes : diagnostic, feuille de route, formation, deploiement",
    "financement": "OPCO + AIF disponibles",
    "prix": "Competitif (modele freelance vs overhead grands organismes)",
    "labels": ["France Num", "Hub France IA", "French Tech"],
}

# Patterns regex pour extraction HTML
PATTERNS_EXTRACTION = {
    "title": r"<title[^>]*>(.*?)</title>",
    "meta_description": r'<meta\s+name=["\']description["\']\s+content=["\'](.*?)["\']',
    "h1": r"<h1[^>]*>(.*?)</h1>",
    "h2": r"<h2[^>]*>(.*?)</h2>",
    "h3": r"<h3[^>]*>(.*?)</h3>",
    "certifications": r"(?i)(qualiopi|opco|cpf|rs\s?\d{4,5}|rncp|datadock|france\s+comp[eé]tences)",
    "prix": r"(\d[\d\s]*[,.]?\d*)\s*(?:€|euros?)\s*(?:/\s*(?:jour|j|personne|pers|stagiaire|participant))?",
}

# Mots-cles pour identifier les arguments/USPs
MOTS_CLES_ARGUMENTS = [
    "nos atouts",
    "pourquoi nous choisir",
    "nos engagements",
    "notre difference",
    "nos plus",
    "avantages",
    "points forts",
    "notre expertise",
    "notre approche",
    "certifie",
    "certification",
    "sur-mesure",
    "personnalise",
    "catalogue",
    "inter-entreprise",
    "intra-entreprise",
    "e-learning",
    "blended",
    "distanciel",
    "presentiel",
]


# ─────────────────────────────────────────────
# Cache
# ─────────────────────────────────────────────


def _charger_cache() -> dict:
    """Charge le cache des analyses concurrents."""
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("Cache concurrents corrompu, reinitialisation")
    return {}


def _sauvegarder_cache(cache: dict):
    """Sauvegarde le cache des analyses concurrents."""
    try:
        CACHE_FILE.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        logger.error(f"Erreur sauvegarde cache concurrents: {e}")


def _cache_valide(entry: dict) -> bool:
    """Verifie si une entree du cache est encore valide (< 30 jours)."""
    if "date_analyse" not in entry:
        return False
    try:
        date_analyse = datetime.fromisoformat(entry["date_analyse"])
        return datetime.now() - date_analyse < timedelta(days=CACHE_TTL_JOURS)
    except (ValueError, TypeError):
        return False


# ─────────────────────────────────────────────
# Scraping
# ─────────────────────────────────────────────


def _nettoyer_html(html: str) -> str:
    """Retire les tags HTML et normalise les espaces."""
    texte = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    texte = re.sub(r"<style[^>]*>.*?</style>", "", texte, flags=re.DOTALL | re.IGNORECASE)
    texte = re.sub(r"<[^>]+>", " ", texte)
    texte = re.sub(r"\s+", " ", texte)
    return texte.strip()


def _extraire_texte_balises(html: str, pattern_key: str) -> list[str]:
    """Extrait le texte des balises correspondant a un pattern."""
    pattern = PATTERNS_EXTRACTION.get(pattern_key, "")
    if not pattern:
        return []
    matches = re.findall(pattern, html, re.IGNORECASE | re.DOTALL)
    return [_nettoyer_html(m).strip() for m in matches if m.strip()]


def _scraper_page(url: str) -> str | None:
    """Recupere le HTML d'une URL. Retourne None en cas d'erreur."""
    try:
        with httpx.Client(
            timeout=10,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept-Language": "fr-FR,fr;q=0.9",
            },
        ) as client:
            resp = client.get(url)
            if resp.status_code == 200:
                return resp.text
            logger.debug(f"HTTP {resp.status_code} pour {url}")
    except httpx.TimeoutException:
        logger.debug(f"Timeout pour {url}")
    except httpx.ConnectError:
        logger.debug(f"Connexion impossible pour {url}")
    except Exception as e:
        logger.debug(f"Erreur scraping {url}: {e}")
    return None


def _trouver_site(nom: str) -> str | None:
    """Essaie de deviner le site web d'un concurrent par patterns courants."""
    nom_clean = re.sub(r"[^a-z0-9]", "", nom.lower())

    candidats = [
        f"https://www.{nom_clean}.fr",
        f"https://www.{nom_clean}.com",
        f"https://www.{nom_clean}-formation.fr",
        f"https://{nom_clean}.fr",
    ]

    for url in candidats:
        try:
            with httpx.Client(timeout=5, follow_redirects=True) as client:
                resp = client.head(url)
                if resp.status_code < 400:
                    logger.info(f"Site trouve pour {nom}: {url}")
                    return url
        except Exception:
            continue

    return None


def _trouver_page_formation(site: str, html_accueil: str) -> str | None:
    """Cherche la page formations/catalogue dans les liens de la homepage."""
    patterns_lien = [
        r'href=["\']([^"\']*(?:formation|catalogue|nos-formations|training)[^"\']*)["\']',
    ]
    for pattern in patterns_lien:
        matches = re.findall(pattern, html_accueil, re.IGNORECASE)
        for lien in matches[:3]:
            if lien.startswith("http"):
                url = lien
            elif lien.startswith("/"):
                url = site.rstrip("/") + lien
            else:
                url = site.rstrip("/") + "/" + lien
            return url
    return None


def _extraire_infos_page(html: str) -> dict:
    """Extrait les informations structurees d'une page HTML."""
    infos = {
        "titre": "",
        "meta_description": "",
        "titres_h1": [],
        "titres_h2": [],
        "titres_h3": [],
        "certifications_detectees": [],
        "prix_detectes": [],
        "arguments_detectes": [],
        "thematiques_formation": [],
    }

    # Titre
    titres = _extraire_texte_balises(html, "title")
    if titres:
        infos["titre"] = titres[0]

    # Meta description
    metas = _extraire_texte_balises(html, "meta_description")
    if metas:
        infos["meta_description"] = metas[0]

    # Headings
    infos["titres_h1"] = _extraire_texte_balises(html, "h1")[:5]
    infos["titres_h2"] = _extraire_texte_balises(html, "h2")[:15]
    infos["titres_h3"] = _extraire_texte_balises(html, "h3")[:20]

    # Certifications
    certs = re.findall(PATTERNS_EXTRACTION["certifications"], html, re.IGNORECASE)
    infos["certifications_detectees"] = list(set(c.strip() for c in certs))

    # Prix
    prix = re.findall(PATTERNS_EXTRACTION["prix"], html)
    infos["prix_detectes"] = list(set(prix[:10]))

    # Arguments / USPs
    texte_brut = _nettoyer_html(html).lower()
    for mot in MOTS_CLES_ARGUMENTS:
        if mot in texte_brut:
            # Extraire le contexte autour du mot-cle (100 chars avant/apres)
            idx = texte_brut.find(mot)
            contexte = texte_brut[max(0, idx - 50) : idx + len(mot) + 150].strip()
            infos["arguments_detectes"].append(contexte)

    # Thematiques formation (dans les h2/h3)
    mots_ia = [
        "intelligence artificielle", "ia ", "chatgpt", "claude", "copilot",
        "midjourney", "machine learning", "deep learning", "data", "python",
        "digital", "numerique", "cybersecurite", "cloud", "devops", "agile",
    ]
    tous_titres = infos["titres_h2"] + infos["titres_h3"]
    for titre in tous_titres:
        titre_lower = titre.lower()
        for mot in mots_ia:
            if mot in titre_lower:
                infos["thematiques_formation"].append(titre)
                break

    return infos


# ─────────────────────────────────────────────
# Fonction principale : analyser un concurrent
# ─────────────────────────────────────────────


def analyser_concurrent(nom: str, site_web: str = None) -> dict:
    """
    Analyse un concurrent : scrape son site, extrait positionnement et arguments.

    Args:
        nom: Nom du concurrent (ex: "ORSYS", "CEGOS")
        site_web: URL du site (optionnel, sera devine si absent)

    Returns:
        dict avec les infos structurees du concurrent
    """
    nom_lower = nom.lower().strip()

    # Verifier le cache
    cache = _charger_cache()
    if nom_lower in cache and _cache_valide(cache[nom_lower]):
        logger.info(f"Concurrent '{nom}' charge depuis le cache")
        return cache[nom_lower]

    # Base : infos connues
    infos_connues = CONCURRENTS_CONNUS.get(nom_lower, {})

    resultat = {
        "nom": nom,
        "nom_normalise": nom_lower,
        "site_web": site_web or infos_connues.get("site"),
        "type": infos_connues.get("type", "Inconnu"),
        "ca_estime": infos_connues.get("ca_estime", "Non estime"),
        "certifications": list(infos_connues.get("certifications", [])),
        "forces": list(infos_connues.get("forces", [])),
        "faiblesses": list(infos_connues.get("faiblesses", [])),
        "arguments_site": [],
        "thematiques_ia": [],
        "prix_detectes": [],
        "meta_description": "",
        "scraping_ok": False,
        "date_analyse": datetime.now().isoformat(),
    }

    # Trouver le site si pas fourni
    if not resultat["site_web"]:
        resultat["site_web"] = _trouver_site(nom)

    if not resultat["site_web"]:
        logger.warning(f"Impossible de trouver le site de '{nom}'")
        # Sauvegarder quand meme dans le cache (avec les infos connues)
        cache[nom_lower] = resultat
        _sauvegarder_cache(cache)
        return resultat

    # Scraper la homepage
    html_accueil = _scraper_page(resultat["site_web"])
    if html_accueil:
        infos_accueil = _extraire_infos_page(html_accueil)
        resultat["meta_description"] = infos_accueil["meta_description"]
        resultat["arguments_site"].extend(infos_accueil["arguments_detectes"])
        resultat["thematiques_ia"].extend(infos_accueil["thematiques_formation"])

        # Enrichir certifications depuis le scraping
        for cert in infos_accueil["certifications_detectees"]:
            if cert not in resultat["certifications"]:
                resultat["certifications"].append(cert)

        resultat["prix_detectes"] = infos_accueil["prix_detectes"]

        # Chercher la page formation
        page_formation = _trouver_page_formation(resultat["site_web"], html_accueil)
        if page_formation:
            html_formation = _scraper_page(page_formation)
            if html_formation:
                infos_formation = _extraire_infos_page(html_formation)
                resultat["thematiques_ia"].extend(infos_formation["thematiques_formation"])
                resultat["arguments_site"].extend(infos_formation["arguments_detectes"])
                for cert in infos_formation["certifications_detectees"]:
                    if cert not in resultat["certifications"]:
                        resultat["certifications"].append(cert)
                if infos_formation["prix_detectes"]:
                    resultat["prix_detectes"].extend(infos_formation["prix_detectes"])

        resultat["scraping_ok"] = True

    # Dedoublonner
    resultat["arguments_site"] = list(set(resultat["arguments_site"]))[:10]
    resultat["thematiques_ia"] = list(set(resultat["thematiques_ia"]))[:15]
    resultat["prix_detectes"] = list(set(resultat["prix_detectes"]))[:10]

    # Sauvegarder dans le cache
    cache[nom_lower] = resultat
    _sauvegarder_cache(cache)

    logger.info(f"Analyse de '{nom}' terminee (scraping={'OK' if resultat['scraping_ok'] else 'KO'})")
    return resultat


# ─────────────────────────────────────────────
# Generation d'arguments differenciants
# ─────────────────────────────────────────────


def generer_arguments_differenciants(concurrents: list[dict], ao: dict = None) -> dict:
    """
    Genere les arguments differenciants d'Almera face aux concurrents analyses.

    Args:
        concurrents: Liste de dicts retournes par analyser_concurrent()
        ao: Infos de l'AO (optionnel, pour contextualiser)

    Returns:
        dict avec arguments, faiblesses concurrents, messages cles, positionnement prix
    """
    if not concurrents:
        return {
            "arguments_differenciants": [],
            "faiblesses_concurrents": [],
            "messages_cles_memoire": [],
            "positionnement_prix": "",
        }

    # ── Arguments differenciants ──

    arguments = []

    # RS6776 : verifier si aucun concurrent ne l'a
    concurrents_avec_rs6776 = [
        c for c in concurrents
        if any("rs6776" in cert.lower() or "rs 6776" in cert.lower()
               for cert in c.get("certifications", []))
    ]
    if not concurrents_avec_rs6776:
        arguments.append(
            "Seul organisme certifie RS6776 France Competences en IA generative"
        )

    # Sur-mesure vs catalogue
    concurrents_catalogue = [
        c["nom"] for c in concurrents
        if any("catalogue" in f.lower() for f in c.get("forces", []))
    ]
    if concurrents_catalogue:
        noms = ", ".join(concurrents_catalogue)
        arguments.append(
            f"Formation 100% personnalisee (vs catalogue standardise {noms})"
        )
    else:
        arguments.append(
            "Formation 100% personnalisee, adaptee au contexte metier du client"
        )

    # Specialisation IA
    concurrents_generalistes = [
        c["nom"] for c in concurrents
        if any("generaliste" in f.lower() or "generalist" in f.lower()
               for f in c.get("faiblesses", []) + [c.get("type", "")])
    ]
    if concurrents_generalistes:
        arguments.append(
            "Specialiste exclusif IA generative (vs offre generaliste des grands catalogues)"
        )
    else:
        arguments.append(
            "Expertise pointue en IA generative : ChatGPT, Claude, Copilot, Midjourney, agents IA"
        )

    # Agilite petite structure
    grands_concurrents = [
        c["nom"] for c in concurrents
        if c.get("ca_estime", "").replace("+", "").replace("M", "").strip().isdigit()
        and int(c.get("ca_estime", "0").replace("+", "").replace("M", "").strip()) >= 30
    ]
    if grands_concurrents:
        arguments.append(
            "Agilite et reactivite d'une structure a taille humaine (vs lourdeur grands groupes)"
        )

    # Methode 4 etapes
    arguments.append(
        "Methodologie eprouvee en 4 etapes : diagnostic, feuille de route, formation, deploiement"
    )

    # Reseau formateurs
    arguments.append(
        "Reseau de ~10 formateurs-experts freelances, chacun specialise sur un domaine IA"
    )

    # Financement
    arguments.append(
        "Accompagnement financement : OPCO, AIF, CPF (formation certifiante RS6776)"
    )

    # Contextualiser si AO fourni
    if ao:
        objet = ao.get("objet", ao.get("titre", "")).lower()
        if "chatgpt" in objet or "ia generative" in objet or "intelligence artificielle" in objet:
            arguments.insert(0,
                "Coeur de metier exact d'Almera : l'IA generative est notre unique specialite"
            )

    # ── Faiblesses des concurrents ──

    faiblesses_concurrents = []
    for c in concurrents:
        faiblesses = list(c.get("faiblesses", []))
        # Ajouter systematiquement l'absence de RS6776 si non detectee
        if not any("rs6776" in cert.lower() or "rs 6776" in cert.lower()
                    for cert in c.get("certifications", [])):
            if not any("rs6776" in f.lower() or "rs 6776" in f.lower() for f in faiblesses):
                faiblesses.append("Pas de certification RS6776 France Competences")

        faiblesses_concurrents.append({
            "nom": c["nom"],
            "type": c.get("type", ""),
            "faiblesses": faiblesses,
        })

    # ── Messages cles pour le memoire technique ──

    messages = []
    messages.append(
        "Insister sur l'approche sur-mesure vs catalogue : chaque formation est construite "
        "apres un diagnostic des besoins specifiques de l'acheteur"
    )
    messages.append(
        "Mettre en avant la certification RS6776 (barriere a l'entree pour les concurrents, "
        "garantie de qualite pour l'acheteur)"
    )
    messages.append(
        "Souligner la specialisation exclusive en IA generative : pas un organisme generaliste "
        "qui ajoute l'IA a son catalogue"
    )
    messages.append(
        "Valoriser le contact direct avec l'expert-formateur des la phase d'avant-vente "
        "(vs commercial puis formateur different chez les grands)"
    )
    messages.append(
        "Mentionner la demarche en 4 etapes (diagnostic → feuille de route → formation → deploiement) "
        "qui garantit un transfert de competences operationnel"
    )

    if ao:
        objet = ao.get("objet", ao.get("titre", "")).lower()
        if "deploiement" in objet or "accompagnement" in objet:
            messages.append(
                "Insister sur l'accompagnement post-formation (deploiement) : "
                "Almera ne s'arrete pas a la formation"
            )

    # ── Positionnement prix ──

    prix_info = []
    for c in concurrents:
        if c.get("prix_detectes"):
            prix_info.append(f"{c['nom']}: {', '.join(c['prix_detectes'][:3])} detectes")

    # Conseil generique base sur la connaissance du marche
    positionnement = (
        "Se positionner 15-20% sous les prix catalogue des grands organismes "
        "(ORSYS ~1500 EUR/jour, CEGOS ~1600 EUR/jour) tout en restant au-dessus du "
        "seuil de credibilite (~800 EUR/jour). Fourchette recommandee : 900-1300 EUR/jour "
        "selon complexite. Avantage structurel : modele freelance sans overhead de grand groupe."
    )
    if prix_info:
        positionnement += "\n\nPrix detectes sur les sites concurrents:\n" + "\n".join(
            f"  - {p}" for p in prix_info
        )

    return {
        "arguments_differenciants": arguments,
        "faiblesses_concurrents": faiblesses_concurrents,
        "messages_cles_memoire": messages,
        "positionnement_prix": positionnement,
    }


# ─────────────────────────────────────────────
# Enrichissement prompt memoire technique
# ─────────────────────────────────────────────


def enrichir_prompt_memoire(concurrents_analysis: dict) -> str:
    """
    Genere un bloc de contexte concurrentiel a injecter dans le prompt du memoire technique.

    Args:
        concurrents_analysis: dict retourne par generer_arguments_differenciants()

    Returns:
        str formatee prete a inserer dans un prompt Claude
    """
    if not concurrents_analysis or not concurrents_analysis.get("arguments_differenciants"):
        return ""

    lignes = ["INTELLIGENCE CONCURRENTIELLE:"]

    # Noms des concurrents
    noms = [
        f["nom"] for f in concurrents_analysis.get("faiblesses_concurrents", [])
    ]
    if noms:
        lignes.append(
            f"Vos principaux concurrents sur ce type de marche sont: {', '.join(noms)}."
        )
    lignes.append("")

    # Arguments differenciants
    lignes.append("Vos avantages differenciants par rapport a ces concurrents:")
    for arg in concurrents_analysis["arguments_differenciants"]:
        lignes.append(f"- {arg}")
    lignes.append("")

    # Faiblesses concurrents
    faiblesses = concurrents_analysis.get("faiblesses_concurrents", [])
    if faiblesses:
        lignes.append("Faiblesses de vos concurrents a exploiter (sans les nommer directement dans le memoire):")
        for fc in faiblesses:
            points = "; ".join(fc["faiblesses"][:3])
            lignes.append(f"- {fc['nom']}: {points}")
        lignes.append("")

    # Messages cles
    messages = concurrents_analysis.get("messages_cles_memoire", [])
    if messages:
        lignes.append("Points a souligner dans le memoire technique:")
        for msg in messages:
            lignes.append(f"- {msg}")
        lignes.append("")

    # Prix
    prix = concurrents_analysis.get("positionnement_prix", "")
    if prix:
        lignes.append(f"Conseil prix: {prix.split(chr(10))[0]}")

    return "\n".join(lignes)


# ─────────────────────────────────────────────
# Fonctions utilitaires
# ─────────────────────────────────────────────


def analyser_concurrents_ao(noms_concurrents: list[str], ao: dict = None) -> dict:
    """
    Analyse complete : scrape les concurrents puis genere les arguments.
    Fonction de commodite combinant analyser_concurrent + generer_arguments_differenciants.

    Args:
        noms_concurrents: Liste de noms (ex: ["ORSYS", "CEGOS", "M2I"])
        ao: Infos de l'AO (optionnel)

    Returns:
        dict avec analyses individuelles + arguments differenciants + prompt memoire
    """
    analyses = []
    for nom in noms_concurrents:
        try:
            analyse = analyser_concurrent(nom)
            analyses.append(analyse)
        except Exception as e:
            logger.error(f"Erreur analyse concurrent '{nom}': {e}")

    arguments = generer_arguments_differenciants(analyses, ao)
    prompt = enrichir_prompt_memoire(arguments)

    return {
        "concurrents": analyses,
        "arguments": arguments,
        "prompt_memoire": prompt,
        "date_analyse": datetime.now().isoformat(),
    }


def concurrents_par_defaut() -> list[str]:
    """Retourne la liste des concurrents connus par defaut."""
    return list(CONCURRENTS_CONNUS.keys())


def vider_cache():
    """Supprime le cache des analyses concurrents."""
    if CACHE_FILE.exists():
        CACHE_FILE.unlink()
        logger.info("Cache concurrents supprime")
