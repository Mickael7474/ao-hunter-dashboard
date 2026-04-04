"""
Veille resultats d'attribution - Feature 4
Surveille les avis d'attribution BOAMP pour les AO soumis par Almera.
Met a jour le statut (gagne/perdu), enrichit le post-mortem et sauvegarde un historique.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

import httpx

logger = logging.getLogger("ao_hunter.veille_attributions")

DASHBOARD_DIR = Path(__file__).parent
AO_CACHE = DASHBOARD_DIR / "ao_pertinents.json"
ATTRIBUTIONS_FILE = DASHBOARD_DIR / "attributions.json"

API_URL = "https://www.boamp.fr/api/explore/v2.1/catalog/datasets/boamp/records"

# Noms possibles d'Almera dans les attributions
NOMS_ALMERA = [
    "almera", "ai mentor", "ai-mentor", "aimentor",
]


def _est_almera(nom_titulaire: str) -> bool:
    """Verifie si le titulaire correspond a Almera."""
    if not nom_titulaire:
        return False
    nom_lower = nom_titulaire.lower().strip()
    return any(n in nom_lower for n in NOMS_ALMERA)


def _charger_ao() -> list[dict]:
    """Charge les AO depuis ao_pertinents.json."""
    if not AO_CACHE.exists():
        return []
    try:
        return json.loads(AO_CACHE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _sauvegarder_ao(appels: list[dict]):
    """Sauvegarde les AO dans ao_pertinents.json."""
    AO_CACHE.write_text(
        json.dumps(appels, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _charger_attributions() -> list[dict]:
    """Charge l'historique des attributions."""
    if not ATTRIBUTIONS_FILE.exists():
        return []
    try:
        return json.loads(ATTRIBUTIONS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _sauvegarder_attributions(attributions: list[dict]):
    """Sauvegarde l'historique des attributions."""
    ATTRIBUTIONS_FILE.write_text(
        json.dumps(attributions, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _extraire_attribution_boamp(record: dict) -> dict | None:
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

    # Extraire le nombre d'offres recues
    nb_offres = None
    nb_raw = attribution.get("nbOffres", attribution.get("nombreOffres", "")) if isinstance(attribution, dict) else ""
    if nb_raw:
        try:
            nb_offres = int(str(nb_raw).strip())
        except (ValueError, TypeError):
            pass
    # Chercher aussi dans les lots
    if nb_offres is None and isinstance(lots, list):
        for lot in lots:
            if isinstance(lot, dict):
                n = lot.get("nbOffres", lot.get("nombreOffres", ""))
                if n:
                    try:
                        nb_offres = int(str(n).strip())
                        break
                    except (ValueError, TypeError):
                        pass

    if not titulaire:
        return None

    return {
        "titulaire": titulaire,
        "montant": montant,
        "nb_offres": nb_offres,
        "date": record.get("dateparution", ""),
        "objet": record.get("objet", ""),
        "acheteur": record.get("nomacheteur", ""),
    }


def _chercher_attribution_pour_ao(ao: dict) -> dict | None:
    """Cherche un avis d'attribution BOAMP correspondant a un AO soumis."""
    titre = ao.get("titre", "")
    acheteur = ao.get("acheteur", "")

    if not titre and not acheteur:
        return None

    # Chercher par titre (mots significatifs)
    mots = [m for m in titre.split() if len(m) > 4][:5]
    if not mots:
        return None

    # Construire la requete avec les mots-cles du titre
    like_parts = [f'objet LIKE "%{m}%"' for m in mots[:3]]
    like_clause = " AND ".join(like_parts)

    try:
        params = {
            "select": "id,objet,nomacheteur,dateparution,nature,donnees",
            "where": f'({like_clause}) AND nature = "ATTRIBUTION"',
            "order_by": "dateparution DESC",
            "limit": 10,
        }

        with httpx.Client(timeout=30, follow_redirects=True) as client:
            resp = client.get(API_URL, params=params)
            if resp.status_code != 200:
                return None
            data = resp.json()

        for record in data.get("results", []):
            # Verifier que l'acheteur correspond (si on a l'info)
            record_acheteur = record.get("nomacheteur", "")
            if acheteur and record_acheteur:
                if acheteur.lower()[:15] not in record_acheteur.lower() and \
                   record_acheteur.lower()[:15] not in acheteur.lower():
                    continue

            attrib = _extraire_attribution_boamp(record)
            if attrib:
                return attrib

    except Exception as e:
        logger.warning(f"Erreur recherche attribution pour '{titre[:50]}': {e}")

    return None


def verifier_attributions() -> dict:
    """Verifie les attributions pour les AO au statut 'soumis'.

    Returns:
        dict avec stats: {verifies, trouves, gagnes, perdus, erreurs}
    """
    appels = _charger_ao()
    attributions_hist = _charger_attributions()
    ids_deja_traites = {a["ao_id"] for a in attributions_hist}

    ao_soumis = [
        ao for ao in appels
        if ao.get("statut") == "soumis" and ao.get("id") not in ids_deja_traites
    ]

    stats = {"verifies": 0, "trouves": 0, "gagnes": 0, "perdus": 0, "erreurs": 0}

    for ao in ao_soumis:
        stats["verifies"] += 1
        try:
            attrib = _chercher_attribution_pour_ao(ao)
            if not attrib:
                continue

            stats["trouves"] += 1

            # Determiner si Almera a gagne
            notre_statut = "gagne" if _est_almera(attrib["titulaire"]) else "perdu"
            if notre_statut == "gagne":
                stats["gagnes"] += 1
            else:
                stats["perdus"] += 1

            # Mettre a jour l'AO
            ao["statut"] = notre_statut
            ao["attribution_titulaire"] = attrib["titulaire"]
            ao["attribution_montant"] = attrib["montant"]
            ao["attribution_date"] = attrib["date"]
            ao["attribution_nb_offres"] = attrib.get("nb_offres")

            # Calculer l'ecart de prix avec notre estimation
            ecart_prix = None
            if attrib["montant"] and ao.get("budget_estime"):
                try:
                    ecart_prix = round(ao["budget_estime"] - attrib["montant"], 2)
                except (TypeError, ValueError):
                    pass
            ao["attribution_ecart_prix"] = ecart_prix

            # Ajouter a l'historique
            attributions_hist.append({
                "ao_id": ao["id"],
                "ao_titre": ao.get("titre", ""),
                "acheteur": ao.get("acheteur", ""),
                "titulaire": attrib["titulaire"],
                "montant": attrib["montant"],
                "nb_offres": attrib.get("nb_offres"),
                "ecart_prix": ecart_prix,
                "date": attrib["date"],
                "notre_statut": notre_statut,
                "date_verification": datetime.now().isoformat(),
            })

            logger.info(
                f"Attribution trouvee pour '{ao.get('titre', '')[:50]}' -> "
                f"{attrib['titulaire']} ({notre_statut})"
            )

        except Exception as e:
            stats["erreurs"] += 1
            logger.warning(f"Erreur verification attribution pour {ao.get('id')}: {e}")

    # Sauvegarder si des modifications ont ete faites
    if stats["trouves"] > 0:
        _sauvegarder_ao(appels)
        _sauvegarder_attributions(attributions_hist)

    logger.info(
        f"Veille attributions: {stats['verifies']} verifies, "
        f"{stats['trouves']} trouves ({stats['gagnes']} gagnes, {stats['perdus']} perdus)"
    )
    return stats


def enrichir_post_mortem(ao: dict, attribution: dict) -> dict:
    """Enrichit un AO perdu avec les donnees d'attribution pour capitalisation.

    Args:
        ao: dict de l'AO (avec attribution_titulaire, etc.)
        attribution: dict de l'historique d'attribution

    Returns:
        dict avec titulaire, montant_gagne, ecart_prix, nb_offres,
        date_attribution, analyse, lecons
    """
    titulaire = ao.get("attribution_titulaire", attribution.get("titulaire", "Inconnu"))
    montant_gagne = ao.get("attribution_montant", attribution.get("montant"))
    nb_offres = ao.get("attribution_nb_offres", attribution.get("nb_offres"))
    ecart_prix = ao.get("attribution_ecart_prix", attribution.get("ecart_prix"))
    date_attr = ao.get("attribution_date", attribution.get("date", ""))

    # Construire une analyse basique a partir des donnees disponibles
    analyse_parts = []
    lecons = []

    if titulaire and titulaire != "Inconnu":
        analyse_parts.append(f"Marche attribue a {titulaire}.")

    if ecart_prix is not None:
        if ecart_prix > 0:
            analyse_parts.append(f"Notre estimation etait superieure de {ecart_prix:,.0f} EUR.")
            lecons.append("Revoir le positionnement tarifaire a la baisse")
        elif ecart_prix < 0:
            analyse_parts.append(f"Notre estimation etait inferieure de {abs(ecart_prix):,.0f} EUR.")
            lecons.append("Le prix n'etait pas le facteur determinant")

    if nb_offres:
        analyse_parts.append(f"{nb_offres} offre(s) recue(s).")
        if nb_offres >= 5:
            lecons.append("Forte concurrence - se differencier sur la valeur technique")
        elif nb_offres <= 2:
            lecons.append("Peu de concurrents - opportunite pour les prochains marches similaires")

    if not lecons:
        lecons.append("Analyser en detail le rapport d'attribution quand disponible")

    return {
        "titulaire": titulaire,
        "montant_gagne": montant_gagne,
        "ecart_prix": ecart_prix,
        "nb_offres": nb_offres,
        "date_attribution": date_attr,
        "analyse": " ".join(analyse_parts) if analyse_parts else "Donnees insuffisantes.",
        "lecons": lecons,
    }


def routine_suivi() -> dict:
    """Routine de suivi des attributions, a appeler periodiquement.

    1. Verifie les attributions pour les AO soumis
    2. Pour chaque AO perdu, enrichit le post-mortem automatiquement
    3. Retourne un resume

    Returns:
        dict avec stats verification + post-mortem declenches
    """
    # Etape 1 : verifier les attributions
    stats = verifier_attributions()

    # Etape 2 : declencher le post-mortem pour les AO perdus fraichement detectes
    post_mortem_declenches = 0
    if stats["perdus"] > 0:
        try:
            from post_mortem import analyser_defaite
            appels = _charger_ao()
            attributions_hist = _charger_attributions()

            # Trouver les AO perdus qui viennent d'etre detectes
            for ao in appels:
                if ao.get("statut") != "perdu":
                    continue
                if not ao.get("attribution_titulaire"):
                    continue

                # Verifier si le post-mortem a deja ete fait
                # (analyser_defaite retourne l'existant s'il y a deja un resultat)
                try:
                    analyser_defaite(ao)
                    post_mortem_declenches += 1
                except Exception as e:
                    logger.warning(f"Erreur post-mortem auto pour {ao.get('id')}: {e}")

        except ImportError:
            logger.warning("Module post_mortem non disponible, analyse differee")
        except Exception as e:
            logger.error(f"Erreur enrichissement post-mortem: {e}")

    stats["post_mortem_declenches"] = post_mortem_declenches

    logger.info(
        f"Routine suivi: {stats['trouves']} attributions, "
        f"{stats['perdus']} perdus, {post_mortem_declenches} post-mortem declenches"
    )
    return stats


def charger_attributions() -> list[dict]:
    """Charge l'historique des attributions (pour l'API)."""
    return _charger_attributions()
