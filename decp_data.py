"""
Donnees Essentielles de la Commande Publique (DECP) - data.gouv.fr

Interroge l'API DECP pour obtenir des donnees REELLES sur les marches publics
attribues en France : montants, nombre d'offres, titulaires, acheteurs, tendances.

Remplace l'estimation heuristique par des statistiques basees sur les attributions
reelles publiees sur data.gouv.fr.

API: https://tabular-api.data.gouv.fr/api/resources/22847056-61df-452d-837d-8b8ceadbfc52/data/
Rate limit: 100 req/s
Champs cles: codeCPV, objet, montant, offresRecues, titulaire_nom, acheteur_nom,
             dateNotification, procedure, dureeMois, nature

Usage:
    from decp_data import rechercher_marches_similaires
    resultat = rechercher_marches_similaires(ao)
    # resultat["budget"]["median"], resultat["concurrence"]["score"], etc.
"""

import hashlib
import json
import logging
import re
import unicodedata
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import httpx

logger = logging.getLogger("ao_hunter.decp_data")

# ============================================================
# CONFIGURATION
# ============================================================

DECP_API_URL = (
    "https://tabular-api.data.gouv.fr/api/resources/"
    "22847056-61df-452d-837d-8b8ceadbfc52/data/"
)
TIMEOUT = 15  # secondes
PAGE_SIZE = 100  # max par requete
MAX_PAGES = 10  # 1000 resultats max par recherche

DASHBOARD_DIR = Path(__file__).parent
CACHE_FILE = DASHBOARD_DIR / "decp_cache.json"
CACHE_TTL_DAYS = 7

# ============================================================
# MAPPING CPV
# ============================================================

CPV_MAPPING = {
    "formation informatique": "80533000",
    "formation intelligence artificielle": "80533000",
    "formation ia": "80533000",
    "formation numerique": "80533000",
    "formation digitale": "80533000",
    "formation management": "80532000",
    "formation professionnelle": "80530000",
    "formation specialisee": "80510000",
    "formation": "80500000",
    "conseil": "79400000",
    "etude": "79311000",
    "audit": "79212000",
    "accompagnement": "79400000",
}

# Mots vides a ignorer pour l'extraction de termes
STOP_WORDS = {
    "de", "du", "des", "le", "la", "les", "un", "une", "et", "en", "au",
    "aux", "pour", "par", "sur", "dans", "avec", "son", "sa", "ses",
    "ce", "cette", "ces", "qui", "que", "ou", "ne", "pas", "plus",
    "marche", "public", "relatif", "relatifs", "prestation", "prestations",
    "service", "services", "accord", "cadre", "lot", "lots", "fourniture",
}


# ============================================================
# UTILITAIRES
# ============================================================

def _normaliser(texte: str) -> str:
    """Supprime accents et met en minuscules."""
    if not texte:
        return ""
    texte = unicodedata.normalize("NFD", texte)
    texte = "".join(c for c in texte if unicodedata.category(c) != "Mn")
    return texte.lower().strip()


def _percentile(valeurs: list[float], p: int) -> float:
    """Calcule le percentile p (0-100) d'une liste triee, sans numpy."""
    if not valeurs:
        return 0.0
    valeurs_triees = sorted(valeurs)
    n = len(valeurs_triees)
    k = (p / 100) * (n - 1)
    f = int(k)
    c = f + 1
    if c >= n:
        return valeurs_triees[-1]
    d = k - f
    return valeurs_triees[f] + d * (valeurs_triees[c] - valeurs_triees[f])


def _cache_key(cpv: str, keywords: list[str] | None = None) -> str:
    """Genere une cle de cache unique pour CPV + mots-cles."""
    raw = f"{cpv}|{'|'.join(sorted(keywords or []))}"
    return hashlib.md5(raw.encode()).hexdigest()


def _charger_cache() -> dict:
    """Charge le cache depuis le fichier JSON."""
    if not CACHE_FILE.exists():
        return {}
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Cache DECP corrompu, reinitialisation: %s", e)
        return {}


def _sauver_cache(cache: dict) -> None:
    """Sauvegarde le cache en JSON."""
    try:
        CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        logger.error("Impossible de sauvegarder le cache DECP: %s", e)


def _cache_valide(entry: dict) -> bool:
    """Verifie si une entree de cache est encore valide (TTL 7 jours)."""
    try:
        date_cache = datetime.fromisoformat(entry["date"])
        return (datetime.now() - date_cache).days < CACHE_TTL_DAYS
    except (KeyError, ValueError):
        return False


def _extraire_termes(texte: str, max_termes: int = 3) -> list[str]:
    """Extrait les termes significatifs d'un texte pour la recherche."""
    texte_norm = _normaliser(texte)
    mots = re.findall(r"[a-z]{3,}", texte_norm)
    # Filtrer les mots vides
    mots_utiles = [m for m in mots if m not in STOP_WORDS]
    # Priorite aux mots longs (plus discriminants)
    mots_utiles.sort(key=len, reverse=True)
    return mots_utiles[:max_termes]


def _detecter_cpv(ao: dict) -> str:
    """Detecte le code CPV le plus pertinent pour un AO."""
    # 1) CPV explicite dans l'AO
    cpv_direct = ao.get("code_cpv") or ao.get("cpv") or ""
    if cpv_direct and len(cpv_direct) >= 5:
        return cpv_direct.split("-")[0].strip()

    # 2) Inference par mots-cles du titre + description
    texte = _normaliser(f"{ao.get('titre', '')} {ao.get('description', '')}")

    # Tester les patterns du plus specifique au plus generique
    for pattern, cpv in CPV_MAPPING.items():
        if pattern in texte:
            logger.info("CPV detecte par mot-cle '%s' -> %s", pattern, cpv)
            return cpv

    # 3) Defaut : services de formation (coeur de metier Almera)
    return "80500000"


# ============================================================
# APPELS API DECP
# ============================================================

def _requete_decp(params: dict) -> list[dict]:
    """
    Execute une requete paginee vers l'API DECP.

    Args:
        params: Parametres de filtrage (field__contains, field__sort, etc.)

    Returns:
        Liste de tous les enregistrements recuperes (max MAX_PAGES * PAGE_SIZE).
    """
    resultats = []
    params_copie = {**params, "page_size": PAGE_SIZE}

    for page in range(1, MAX_PAGES + 1):
        params_copie["page"] = page
        try:
            logger.debug("DECP API page %d: %s", page, params_copie)
            resp = httpx.get(DECP_API_URL, params=params_copie, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except httpx.TimeoutException:
            logger.warning("Timeout API DECP page %d", page)
            break
        except httpx.HTTPStatusError as e:
            logger.warning("Erreur HTTP DECP: %s", e)
            break
        except (httpx.RequestError, json.JSONDecodeError) as e:
            logger.error("Erreur requete DECP: %s", e)
            break

        records = data.get("data", [])
        if not records:
            break

        resultats.extend(records)
        logger.info("DECP page %d: %d resultats (total: %d)", page, len(records), len(resultats))

        # Pas de page suivante
        if len(records) < PAGE_SIZE:
            break

    return resultats


def _chercher_par_cpv(cpv: str, keywords: list[str] | None = None) -> list[dict]:
    """
    Recherche des marches par code CPV et mots-cles optionnels.

    Strategie de recherche en entonnoir :
    1. CPV exact + mots-cles -> resultats tres pertinents
    2. CPV 5 premiers digits -> resultats elargis
    3. CPV 3 premiers digits (famille) -> resultats larges
    """
    params = {
        "dateNotification__sort": "desc",
        "columns": (
            "codeCPV,objet,montant,offresRecues,titulaire_nom,titulaire_id,"
            "acheteur_nom,acheteur_id,dateNotification,procedure,dureeMois,nature"
        ),
    }

    # Essai 1: CPV complet + mot-cle sur objet
    cpv_court = cpv[:8] if len(cpv) >= 8 else cpv
    params["codeCPV__contains"] = cpv_court

    if keywords:
        # Prendre le mot-cle le plus discriminant
        params["objet__contains"] = keywords[0]

    resultats = _requete_decp(params)

    # Si trop peu de resultats et on a un mot-cle, retenter sans mot-cle
    if len(resultats) < 10 and keywords:
        logger.info("Peu de resultats (%d), retry sans mot-cle objet", len(resultats))
        params.pop("objet__contains", None)
        resultats = _requete_decp(params)

    # Si toujours trop peu, elargir au CPV parent (5 digits)
    if len(resultats) < 10 and len(cpv_court) > 5:
        cpv_parent = cpv_court[:5]
        logger.info("Elargissement CPV %s -> %s", cpv_court, cpv_parent)
        params["codeCPV__contains"] = cpv_parent
        resultats = _requete_decp(params)

    # Dernier recours: CPV famille (3 digits)
    if len(resultats) < 10 and len(cpv_court) > 3:
        cpv_famille = cpv_court[:3]
        logger.info("Elargissement CPV famille %s -> %s", cpv_court, cpv_famille)
        params["codeCPV__contains"] = cpv_famille
        resultats = _requete_decp(params)

    return resultats


# ============================================================
# ANALYSE STATISTIQUE
# ============================================================

def _analyser_budget(marches: list[dict]) -> dict:
    """Calcule les statistiques de budget a partir des marches trouves."""
    montants = []
    for m in marches:
        mt = m.get("montant")
        if mt is not None:
            try:
                val = float(mt)
                # Filtrer les montants aberrants (< 500 EUR ou > 10M EUR)
                if 500 <= val <= 10_000_000:
                    montants.append(val)
            except (ValueError, TypeError):
                continue

    if not montants:
        return {
            "median": 0,
            "moyenne": 0,
            "min": 0,
            "max": 0,
            "percentile_25": 0,
            "percentile_75": 0,
            "fourchette_recommandee": [0, 0],
            "confiance": "aucune",
        }

    montants.sort()
    n = len(montants)
    med = _percentile(montants, 50)
    moy = sum(montants) / n
    p25 = _percentile(montants, 25)
    p75 = _percentile(montants, 75)

    if n > 20:
        confiance = "haute"
    elif n >= 10:
        confiance = "moyenne"
    else:
        confiance = "basse"

    return {
        "median": round(med),
        "moyenne": round(moy),
        "min": round(min(montants)),
        "max": round(max(montants)),
        "percentile_25": round(p25),
        "percentile_75": round(p75),
        "fourchette_recommandee": [round(p25), round(p75)],
        "confiance": confiance,
    }


def _analyser_concurrence(marches: list[dict]) -> dict:
    """Calcule les statistiques de concurrence a partir du champ offresRecues."""
    offres = []
    for m in marches:
        nb = m.get("offresRecues")
        if nb is not None:
            try:
                val = int(nb)
                if val >= 1:
                    offres.append(val)
            except (ValueError, TypeError):
                continue

    if not offres:
        return {
            "nb_offres_median": 0,
            "nb_offres_moyen": 0,
            "nb_offres_min": 0,
            "nb_offres_max": 0,
            "score": 50,
            "niveau": "Inconnue",
            "description": "Pas de donnees sur le nombre d'offres recues",
        }

    offres.sort()
    med = _percentile(offres, 50)
    moy = sum(offres) / len(offres)

    # Score de concurrence (0 = pas de concurrence, 100 = tres forte)
    # Echelle: 1 offre = 0, 3 offres = 25, 5 = 50, 10 = 75, 15+ = 100
    if med <= 1:
        score = 5
    elif med <= 3:
        score = 25
    elif med <= 5:
        score = 50
    elif med <= 10:
        score = 75
    else:
        score = 95

    # Ajuster selon la dispersion
    if max(offres) > 15:
        score = min(100, score + 10)

    if med <= 2:
        niveau = "Faible"
        desc = f"{int(med)} candidat(s) en mediane - marche peu concurrentiel"
    elif med <= 5:
        niveau = "Moderee"
        desc = f"{int(med)} candidats en mediane sur les marches similaires"
    elif med <= 10:
        niveau = "Forte"
        desc = f"{int(med)} candidats en mediane - concurrence significative"
    else:
        niveau = "Tres forte"
        desc = f"{int(med)} candidats en mediane - marche tres dispute"

    return {
        "nb_offres_median": round(med, 1),
        "nb_offres_moyen": round(moy, 1),
        "nb_offres_min": min(offres),
        "nb_offres_max": max(offres),
        "score": score,
        "niveau": niveau,
        "description": desc,
    }


def _analyser_titulaires(marches: list[dict], top_n: int = 10) -> list[dict]:
    """Identifie les titulaires les plus frequents."""
    compteur: dict[str, dict] = {}

    for m in marches:
        nom = (m.get("titulaire_nom") or "").strip()
        if not nom or nom.lower() in ("", "inconnu", "non renseigne"):
            continue

        # Normaliser le nom pour le regroupement
        nom_cle = _normaliser(nom)
        if nom_cle not in compteur:
            compteur[nom_cle] = {"nom": nom, "nb_marches": 0, "montants": []}

        compteur[nom_cle]["nb_marches"] += 1
        mt = m.get("montant")
        if mt is not None:
            try:
                compteur[nom_cle]["montants"].append(float(mt))
            except (ValueError, TypeError):
                pass

    # Trier par nombre de marches
    top = sorted(compteur.values(), key=lambda x: x["nb_marches"], reverse=True)[:top_n]

    resultats = []
    for t in top:
        montant_moyen = round(sum(t["montants"]) / len(t["montants"])) if t["montants"] else 0
        resultats.append({
            "nom": t["nom"],
            "nb_marches": t["nb_marches"],
            "montant_moyen": montant_moyen,
        })

    return resultats


def _analyser_acheteurs(marches: list[dict], top_n: int = 5) -> list[dict]:
    """Identifie les acheteurs les plus frequents."""
    compteur: dict[str, dict] = {}

    for m in marches:
        nom = (m.get("acheteur_nom") or "").strip()
        if not nom or nom.lower() in ("", "inconnu", "non renseigne"):
            continue

        nom_cle = _normaliser(nom)
        if nom_cle not in compteur:
            compteur[nom_cle] = {"nom": nom, "nb_marches": 0, "montants": []}

        compteur[nom_cle]["nb_marches"] += 1
        mt = m.get("montant")
        if mt is not None:
            try:
                compteur[nom_cle]["montants"].append(float(mt))
            except (ValueError, TypeError):
                pass

    top = sorted(compteur.values(), key=lambda x: x["nb_marches"], reverse=True)[:top_n]

    resultats = []
    for t in top:
        montant_moyen = round(sum(t["montants"]) / len(t["montants"])) if t["montants"] else 0
        resultats.append({
            "nom": t["nom"],
            "nb_marches": t["nb_marches"],
            "montant_moyen": montant_moyen,
        })

    return resultats


def _analyser_historique_prix(marches: list[dict]) -> list[dict]:
    """Calcule les moyennes mensuelles de prix pour visualiser la tendance."""
    par_mois: dict[str, list[float]] = {}

    for m in marches:
        date_str = m.get("dateNotification") or ""
        mt = m.get("montant")

        if not date_str or mt is None:
            continue

        try:
            val = float(mt)
            if val < 500 or val > 10_000_000:
                continue
        except (ValueError, TypeError):
            continue

        # Extraire YYYY-MM
        mois = date_str[:7] if len(date_str) >= 7 else None
        if not mois or not re.match(r"\d{4}-\d{2}", mois):
            continue

        par_mois.setdefault(mois, []).append(val)

    # Trier par date
    historique = []
    for mois in sorted(par_mois.keys()):
        vals = par_mois[mois]
        historique.append({
            "date": mois,
            "montant_moyen": round(sum(vals) / len(vals)),
            "nb_marches": len(vals),
        })

    return historique


def _generer_recommandation_prix(budget: dict) -> dict:
    """Genere une recommandation de positionnement prix."""
    med = budget["median"]
    p25 = budget["percentile_25"]
    p75 = budget["percentile_75"]

    if med == 0:
        return {
            "prix_plancher": 0,
            "prix_cible": 0,
            "prix_plafond": 0,
            "strategie": "Donnees insuffisantes pour recommander un positionnement prix.",
        }

    # Strategie adaptee a la phase de construction de references
    prix_cible = med
    prix_plancher = p25
    prix_plafond = p75

    if budget["confiance"] == "haute":
        strategie = (
            f"Se positionner sous la mediane ({round(p25/1000)}k-{round(med/1000)}k EUR HT) "
            f"pour maximiser les chances en phase de construction de references. "
            f"Le marche montre une fourchette large ({round(p25/1000)}k-{round(p75/1000)}k), "
            f"un prix agressif mais credible est recommande."
        )
    elif budget["confiance"] == "moyenne":
        strategie = (
            f"Fourchette indicative {round(p25/1000)}k-{round(p75/1000)}k EUR HT. "
            f"Donnees moderees ({budget.get('nb_marches', 'N/A')} marches), "
            f"verifier avec le RC et les conditions du marche."
        )
    else:
        strategie = (
            f"Peu de donnees disponibles. Estimation large autour de {round(med/1000)}k EUR HT. "
            f"Recommandation : analyser le DCE pour affiner."
        )

    return {
        "prix_plancher": prix_plancher,
        "prix_cible": prix_cible,
        "prix_plafond": prix_plafond,
        "strategie": strategie,
    }


# ============================================================
# FONCTIONS PUBLIQUES
# ============================================================

def rechercher_marches_similaires(ao: dict) -> dict:
    """
    Recherche des marches similaires dans les DECP pour un AO donne.

    Args:
        ao: Dictionnaire representant un AO avec les cles possibles :
            - titre (str): Titre de l'AO
            - description (str): Description / objet du marche
            - acheteur (str): Nom de l'acheteur
            - code_cpv (str): Code CPV si connu
            - acheteur_siret (str): SIRET de l'acheteur si connu

    Returns:
        Dictionnaire complet avec budget, concurrence, titulaires,
        acheteurs similaires, historique prix, recommandation.
    """
    cpv = _detecter_cpv(ao)
    titre = ao.get("titre", "")
    description = ao.get("description", "")
    keywords = _extraire_termes(f"{titre} {description}")

    logger.info("Recherche DECP: CPV=%s, mots-cles=%s", cpv, keywords)

    # Verifier le cache
    cle = _cache_key(cpv, keywords)
    cache = _charger_cache()
    if cle in cache and _cache_valide(cache[cle]):
        logger.info("Cache DECP valide pour %s", cle)
        return cache[cle]["data"]

    # Requete API
    try:
        marches = _chercher_par_cpv(cpv, keywords)
    except Exception as e:
        logger.error("Erreur recherche DECP: %s", e)
        # Retourner les donnees en cache meme expirees plutot que rien
        if cle in cache:
            logger.warning("Retour donnees cache expirees suite a erreur")
            return cache[cle]["data"]
        marches = []

    nb_trouves = len(marches)
    logger.info("DECP: %d marches trouves pour CPV %s", nb_trouves, cpv)

    # Analyses
    budget = _analyser_budget(marches)
    concurrence = _analyser_concurrence(marches)
    titulaires = _analyser_titulaires(marches)
    acheteurs = _analyser_acheteurs(marches)
    historique = _analyser_historique_prix(marches)
    recommandation = _generer_recommandation_prix(budget)

    resultat = {
        "nb_marches_trouves": nb_trouves,
        "budget": budget,
        "concurrence": concurrence,
        "titulaires_frequents": titulaires,
        "acheteurs_similaires": acheteurs,
        "historique_prix": historique,
        "recommandation_prix": recommandation,
        "source": "DECP data.gouv.fr",
        "date_requete": datetime.now().strftime("%Y-%m-%d"),
        "cpv_utilise": cpv,
        "termes_recherche": keywords,
    }

    # Mettre en cache
    cache[cle] = {
        "date": datetime.now().isoformat(),
        "data": resultat,
    }
    # Nettoyer les entrees expirees
    cache = {k: v for k, v in cache.items() if _cache_valide(v)}
    cache[cle] = {
        "date": datetime.now().isoformat(),
        "data": resultat,
    }
    _sauver_cache(cache)

    return resultat


def analyser_concurrents(cpv_code: str, keywords: list[str] | None = None) -> list[dict]:
    """
    Analyse les concurrents principaux pour un code CPV donne.

    Args:
        cpv_code: Code CPV (ex: "80533000")
        keywords: Mots-cles optionnels pour affiner la recherche

    Returns:
        Liste des concurrents avec nb_marches, montant_moyen,
        taux de reussite estime et couverture geographique.
    """
    logger.info("Analyse concurrents CPV=%s, keywords=%s", cpv_code, keywords)

    try:
        marches = _chercher_par_cpv(cpv_code, keywords)
    except Exception as e:
        logger.error("Erreur analyse concurrents: %s", e)
        return []

    if not marches:
        return []

    # Agreger par titulaire
    par_titulaire: dict[str, dict] = {}
    total_marches = len(marches)

    for m in marches:
        nom = (m.get("titulaire_nom") or "").strip()
        if not nom or nom.lower() in ("", "inconnu", "non renseigne"):
            continue

        nom_cle = _normaliser(nom)
        if nom_cle not in par_titulaire:
            par_titulaire[nom_cle] = {
                "nom": nom,
                "nb_marches": 0,
                "montants": [],
                "acheteurs": set(),
                "procedures": [],
            }

        entry = par_titulaire[nom_cle]
        entry["nb_marches"] += 1

        mt = m.get("montant")
        if mt is not None:
            try:
                entry["montants"].append(float(mt))
            except (ValueError, TypeError):
                pass

        acheteur = (m.get("acheteur_nom") or "").strip()
        if acheteur:
            entry["acheteurs"].add(acheteur)

        proc = m.get("procedure") or ""
        if proc:
            entry["procedures"].append(proc)

    # Trier par nombre de marches gagnes
    top = sorted(par_titulaire.values(), key=lambda x: x["nb_marches"], reverse=True)[:15]

    resultats = []
    for t in top:
        montant_moyen = round(sum(t["montants"]) / len(t["montants"])) if t["montants"] else 0
        taux_presence = round(100 * t["nb_marches"] / total_marches, 1) if total_marches else 0

        resultats.append({
            "nom": t["nom"],
            "nb_marches": t["nb_marches"],
            "montant_moyen": montant_moyen,
            "taux_presence_pct": taux_presence,
            "nb_acheteurs_differents": len(t["acheteurs"]),
            "acheteurs_principaux": list(t["acheteurs"])[:5],
        })

    return resultats


def prix_reference_acheteur(acheteur_siret: str) -> dict:
    """
    Analyse l'historique d'achat d'un acheteur specifique par son SIRET.

    Args:
        acheteur_siret: SIRET de l'acheteur (14 chiffres)

    Returns:
        Dictionnaire avec l'historique d'achat, budget moyen,
        types de marches passes, et frequence.
    """
    if not acheteur_siret or len(acheteur_siret) < 9:
        return {"erreur": "SIRET invalide ou manquant"}

    logger.info("Prix reference acheteur SIRET=%s", acheteur_siret)

    params = {
        "acheteur_id__exact": acheteur_siret,
        "dateNotification__sort": "desc",
        "columns": (
            "codeCPV,objet,montant,offresRecues,titulaire_nom,"
            "dateNotification,procedure,dureeMois,nature"
        ),
    }

    try:
        marches = _requete_decp(params)
    except Exception as e:
        logger.error("Erreur prix_reference_acheteur: %s", e)
        return {"erreur": f"Erreur API: {e}"}

    if not marches:
        return {
            "acheteur_siret": acheteur_siret,
            "nb_marches": 0,
            "message": "Aucun marche trouve pour cet acheteur dans les DECP",
        }

    montants = []
    cpv_utilises = Counter()
    natures = Counter()
    procedures = Counter()

    for m in marches:
        mt = m.get("montant")
        if mt is not None:
            try:
                montants.append(float(mt))
            except (ValueError, TypeError):
                pass

        cpv = m.get("codeCPV") or ""
        if cpv:
            cpv_utilises[cpv[:5]] += 1

        nature = m.get("nature") or ""
        if nature:
            natures[nature] += 1

        proc = m.get("procedure") or ""
        if proc:
            procedures[proc] += 1

    acheteur_nom = marches[0].get("acheteur_nom", "Inconnu") if marches else "Inconnu"

    budget_stats = {}
    if montants:
        montants.sort()
        budget_stats = {
            "median": round(_percentile(montants, 50)),
            "moyenne": round(sum(montants) / len(montants)),
            "min": round(min(montants)),
            "max": round(max(montants)),
        }

    return {
        "acheteur_siret": acheteur_siret,
        "acheteur_nom": acheteur_nom,
        "nb_marches": len(marches),
        "budget": budget_stats,
        "cpv_frequents": cpv_utilises.most_common(5),
        "natures": dict(natures),
        "procedures": dict(procedures),
        "derniers_marches": [
            {
                "objet": m.get("objet", ""),
                "montant": m.get("montant"),
                "date": m.get("dateNotification", ""),
                "titulaire": m.get("titulaire_nom", ""),
            }
            for m in marches[:10]
        ],
    }


def tendance_prix(cpv_code: str, mois: int = 24) -> list[dict]:
    """
    Tendance des prix sur les N derniers mois pour un code CPV.

    Args:
        cpv_code: Code CPV (ex: "80533000")
        mois: Nombre de mois en arriere (defaut: 24)

    Returns:
        Liste de dictionnaires {date, montant_moyen, montant_median,
        nb_marches} tries par date croissante.
    """
    logger.info("Tendance prix CPV=%s, mois=%d", cpv_code, mois)

    date_debut = (datetime.now() - timedelta(days=mois * 30)).strftime("%Y-%m-%d")

    params = {
        "codeCPV__contains": cpv_code[:5],
        "dateNotification__greater": date_debut,
        "dateNotification__sort": "asc",
        "columns": "montant,dateNotification,codeCPV",
    }

    try:
        marches = _requete_decp(params)
    except Exception as e:
        logger.error("Erreur tendance_prix: %s", e)
        return []

    if not marches:
        return []

    # Agreger par mois
    par_mois: dict[str, list[float]] = {}
    for m in marches:
        date_str = m.get("dateNotification") or ""
        mt = m.get("montant")

        if not date_str or mt is None:
            continue

        try:
            val = float(mt)
            if val < 500 or val > 10_000_000:
                continue
        except (ValueError, TypeError):
            continue

        mois_str = date_str[:7]
        if not re.match(r"\d{4}-\d{2}", mois_str):
            continue

        par_mois.setdefault(mois_str, []).append(val)

    resultats = []
    for date_mois in sorted(par_mois.keys()):
        vals = par_mois[date_mois]
        vals.sort()
        resultats.append({
            "date": date_mois,
            "montant_moyen": round(sum(vals) / len(vals)),
            "montant_median": round(_percentile(vals, 50)),
            "nb_marches": len(vals),
        })

    return resultats
