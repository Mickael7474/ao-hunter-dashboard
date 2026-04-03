"""
Memoire technique auto-adaptative.
Sauvegarde les memoires techniques des AO gagnes comme modeles,
et les reutilise pour enrichir les futurs memoires techniques.
"""

import json
import re
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger("ao_hunter.memoire_adaptative")

DASHBOARD_DIR = Path(__file__).parent
MEMOIRES_INDEX = DASHBOARD_DIR / "memoires_gagnants.json"
DOSSIERS_DIR = DASHBOARD_DIR / "dossiers_generes"

# Stopwords francais courants pour le filtrage des mots-cles
STOPWORDS = {
    "le", "la", "les", "de", "des", "du", "un", "une", "et", "en", "au", "aux",
    "pour", "par", "sur", "dans", "avec", "est", "sont", "a", "ce", "cette",
    "ces", "qui", "que", "dont", "ou", "son", "sa", "ses", "nos", "notre",
    "votre", "vos", "leur", "leurs", "il", "elle", "ils", "elles", "nous",
    "vous", "on", "se", "ne", "pas", "plus", "tout", "tous", "toute", "toutes",
    "autre", "autres", "entre", "vers", "chez", "comme", "mais", "donc",
    "car", "ni", "si", "puis", "aussi", "bien", "tres", "trop", "peu",
    "sous", "sans", "depuis", "lors", "apres", "avant", "pendant",
    "selon", "afin", "ainsi", "alors", "aucun", "aucune", "aux", "chaque",
    "contre", "encore", "meme", "non", "oui", "peut", "quel", "quelle",
    "quels", "quelles", "sera", "seront", "soit", "ont", "fait", "faire",
    "ete", "avoir", "etre", "lors", "jusqu", "cet", "qu", "d", "l", "n",
    "s", "j", "c", "m", "y", "marche", "marches", "public", "publics",
    "appel", "offres", "offre", "lot", "lots", "relatif", "relative",
    "prestations", "prestation", "mise", "oeuvre", "cadre",
}


def _charger_index() -> list[dict]:
    """Charge l'index des memoires gagnants."""
    if not MEMOIRES_INDEX.exists():
        return []
    try:
        with open(MEMOIRES_INDEX, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def _sauvegarder_index(index: list[dict]):
    """Sauvegarde l'index des memoires gagnants."""
    MEMOIRES_INDEX.write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _extraire_mots_cles(texte: str) -> list[str]:
    """Extrait les mots-cles significatifs d'un texte (sans stopwords)."""
    if not texte:
        return []
    # Normaliser : minuscules, retirer accents basiques, garder alphanumerique
    texte_clean = texte.lower()
    texte_clean = re.sub(r"[^a-z0-9àâäéèêëïîôùûüÿçœæ\s-]", " ", texte_clean)
    mots = texte_clean.split()
    # Filtrer stopwords et mots trop courts
    mots_cles = [m for m in mots if m not in STOPWORDS and len(m) > 2]
    # Deduplication en gardant l'ordre
    vus = set()
    result = []
    for m in mots_cles:
        if m not in vus:
            vus.add(m)
            result.append(m)
    return result


def _detecter_type_prestation(ao: dict) -> str:
    """Detecte le type de prestation a partir du titre/description."""
    texte = f"{ao.get('titre', '')} {ao.get('description', '')}".lower()
    if any(k in texte for k in ["formation", "formateur", "pedagogique", "stagiaire"]):
        return "formation"
    if any(k in texte for k in ["consulting", "conseil", "audit", "accompagnement", "amo"]):
        return "consulting"
    if any(k in texte for k in ["developpement", "logiciel", "application", "plateforme"]):
        return "developpement"
    return "mixte"


def _detecter_type_acheteur(ao: dict) -> str:
    """Detecte le type d'acheteur."""
    acheteur = ao.get("acheteur", "").lower()
    if any(k in acheteur for k in ["ministere", "etat", "dgfip", "prefecture"]):
        return "etat"
    if any(k in acheteur for k in ["region", "departement", "conseil"]):
        return "collectivite"
    if any(k in acheteur for k in ["commune", "mairie", "ville", "metropole", "communaute"]):
        return "collectivite"
    if any(k in acheteur for k in ["chu", "hopital", "centre hospitalier", "ars"]):
        return "sante"
    if any(k in acheteur for k in ["universite", "ecole", "lycee", "college", "academie"]):
        return "education"
    if any(k in acheteur for k in ["chambre", "cci", "cma"]):
        return "chambre_consulaire"
    return "autre"


def _trouver_dossier_ao(ao_id: str) -> Path | None:
    """Trouve le dossier genere pour un AO."""
    clean_id = ao_id.replace("BOAMP-", "").replace("PLACE-", "").replace(
        "TED-", "").replace("MSEC-", "").replace("AWS-", "")
    # Chercher dans dossiers_generes/
    if DOSSIERS_DIR.exists():
        for d in DOSSIERS_DIR.iterdir():
            if d.is_dir() and clean_id in d.name:
                return d
    # Chercher dans resultats/ (local)
    resultats_dir = DASHBOARD_DIR.parent / "resultats"
    if resultats_dir.exists():
        for d in resultats_dir.iterdir():
            if d.is_dir() and clean_id in d.name:
                return d
    return None


def _extraire_meilleurs_passages(contenu: str, n: int = 3) -> list[str]:
    """Extrait les N meilleurs passages du memoire technique.

    Selectionne les sections les plus substantielles (hors titres generiques).
    """
    if not contenu:
        return []
    # Decouper par sections ##
    sections = re.split(r'\n#{1,3}\s+', contenu)
    # Filtrer les sections trop courtes ou generiques
    passages = []
    for section in sections:
        section = section.strip()
        if len(section) < 100:
            continue
        # Prendre les 500 premiers chars de chaque section
        passages.append(section[:500].strip())
    # Trier par longueur (les plus substantielles d'abord)
    passages.sort(key=len, reverse=True)
    return passages[:n]


def sauvegarder_memoire_gagnant(ao: dict, dossier_path: str = None) -> dict | None:
    """Sauvegarde le memoire technique d'un AO gagne comme modele.

    Args:
        ao: dict de l'AO (avec id, titre, acheteur, description, etc.)
        dossier_path: chemin du dossier genere (optionnel, auto-detecte sinon)

    Returns:
        dict de l'entree indexee, ou None si pas de memoire trouve
    """
    ao_id = ao.get("id", "")

    # Trouver le dossier
    if dossier_path:
        dossier = Path(dossier_path)
    else:
        dossier = _trouver_dossier_ao(ao_id)

    if not dossier or not dossier.exists():
        logger.warning(f"Memoire adaptative: dossier non trouve pour {ao_id}")
        return None

    # Chercher le memoire technique
    memoire_path = dossier / "memoire_technique.md"
    if not memoire_path.exists():
        # Essayer d'autres noms possibles
        for pattern in ["*memoire*", "*technique*"]:
            candidats = list(dossier.glob(pattern))
            if candidats:
                memoire_path = candidats[0]
                break
        else:
            logger.warning(f"Memoire adaptative: memoire_technique.md non trouve dans {dossier}")
            return None

    # Lire le contenu
    try:
        contenu = memoire_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"Memoire adaptative: erreur lecture {memoire_path}: {e}")
        return None

    if len(contenu) < 200:
        logger.warning(f"Memoire adaptative: memoire trop court pour {ao_id} ({len(contenu)} chars)")
        return None

    # Extraire les mots-cles du titre + description
    texte_complet = f"{ao.get('titre', '')} {ao.get('description', '')}"
    mots_cles = _extraire_mots_cles(texte_complet)

    # Construire l'entree
    entree = {
        "ao_id": ao_id,
        "titre": ao.get("titre", ""),
        "acheteur": ao.get("acheteur", ""),
        "type_prestation": _detecter_type_prestation(ao),
        "type_acheteur": _detecter_type_acheteur(ao),
        "mots_cles": mots_cles[:30],  # Limiter a 30 mots-cles
        "memoire_path": str(memoire_path),
        "score_pertinence": ao.get("score", 0),
        "date_victoire": datetime.now().strftime("%Y-%m-%d"),
        "budget": ao.get("budget", ao.get("montant", "")),
        "criteres_attribution": ao.get("criteres_attribution", []),
    }

    # Charger l'index existant et verifier les doublons
    index = _charger_index()
    # Remplacer si deja present
    index = [e for e in index if e.get("ao_id") != ao_id]
    index.append(entree)
    _sauvegarder_index(index)

    logger.info(f"Memoire adaptative: indexe {ao_id} ({len(mots_cles)} mots-cles)")
    return entree


def _jaccard(set_a: set, set_b: set) -> float:
    """Calcule la similarite de Jaccard entre deux ensembles."""
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


def trouver_modeles_similaires(ao_nouveau: dict, n: int = 3) -> list[dict]:
    """Cherche les memoires gagnants les plus similaires a un nouvel AO.

    Args:
        ao_nouveau: dict de l'AO nouveau
        n: nombre de resultats a retourner

    Returns:
        Liste de dicts {ao_id, titre, similarite, memoire_extraits}
    """
    index = _charger_index()
    if not index:
        return []

    # Mots-cles du nouvel AO
    texte_nouveau = f"{ao_nouveau.get('titre', '')} {ao_nouveau.get('description', '')}"
    mots_cles_nouveau = set(_extraire_mots_cles(texte_nouveau))
    type_presta_nouveau = _detecter_type_prestation(ao_nouveau)
    type_acheteur_nouveau = _detecter_type_acheteur(ao_nouveau)

    resultats = []
    for entree in index:
        # Score Jaccard sur mots-cles (poids 60%)
        mots_cles_entree = set(entree.get("mots_cles", []))
        score_jaccard = _jaccard(mots_cles_nouveau, mots_cles_entree)

        # Bonus type prestation (poids 20%)
        bonus_presta = 0.2 if entree.get("type_prestation") == type_presta_nouveau else 0.0

        # Bonus type acheteur (poids 20%)
        bonus_acheteur = 0.2 if entree.get("type_acheteur") == type_acheteur_nouveau else 0.0

        # Score final
        similarite = (score_jaccard * 0.6) + bonus_presta + bonus_acheteur

        if similarite < 0.05:
            continue

        # Charger les extraits du memoire
        memoire_extraits = []
        memoire_path = Path(entree.get("memoire_path", ""))
        if memoire_path.exists():
            try:
                contenu = memoire_path.read_text(encoding="utf-8")
                memoire_extraits = _extraire_meilleurs_passages(contenu, n=3)
            except Exception:
                pass

        resultats.append({
            "ao_id": entree["ao_id"],
            "titre": entree.get("titre", ""),
            "similarite": round(similarite, 3),
            "memoire_extraits": memoire_extraits,
            "type_prestation": entree.get("type_prestation", ""),
            "type_acheteur": entree.get("type_acheteur", ""),
            "date_victoire": entree.get("date_victoire", ""),
        })

    # Trier par similarite decroissante
    resultats.sort(key=lambda r: r["similarite"], reverse=True)
    return resultats[:n]


def generer_prompt_adaptatif(ao: dict, modeles_similaires: list[dict]) -> str:
    """Construit un bloc de contexte pour le prompt Claude avec les extraits
    des memoires gagnants.

    Args:
        ao: dict de l'AO nouveau
        modeles_similaires: resultats de trouver_modeles_similaires()

    Returns:
        Bloc de texte a injecter dans le prompt (max 3000 chars)
    """
    if not modeles_similaires:
        return ""

    lignes = [
        "",
        "=== MEMOIRES TECHNIQUES GAGNANTS (INSPIRATION) ===",
        "Voici des extraits de memoires techniques qui ont GAGNE des AO similaires.",
        "Inspire-toi de leur structure, ton et arguments :",
        "",
    ]

    chars_restants = 3000 - sum(len(l) for l in lignes)

    for i, modele in enumerate(modeles_similaires, 1):
        header = f"--- Modele {i}: {modele['titre'][:80]} (similarite: {modele['similarite']}) ---"
        lignes.append(header)
        chars_restants -= len(header) + 2

        for extrait in modele.get("memoire_extraits", []):
            if chars_restants <= 50:
                break
            # Tronquer l'extrait si necessaire
            extrait_tronque = extrait[:min(len(extrait), chars_restants - 10)]
            lignes.append(extrait_tronque)
            lignes.append("")
            chars_restants -= len(extrait_tronque) + 2

        if chars_restants <= 50:
            break

    lignes.append("=== FIN MODELES GAGNANTS ===")

    return "\n".join(lignes)


def enrichir_base(appels: list[dict], dossiers_dir: str = None):
    """Scan les AO gagnes existants et indexe leurs memoires (rattrapage initial).

    Args:
        appels: liste des AO (ao_pertinents.json)
        dossiers_dir: repertoire des dossiers generes (optionnel)
    """
    compteur = 0
    for ao in appels:
        if ao.get("statut") != "gagne":
            continue
        # Verifier si deja indexe
        index = _charger_index()
        if any(e.get("ao_id") == ao.get("id") for e in index):
            continue
        result = sauvegarder_memoire_gagnant(ao, dossier_path=dossiers_dir)
        if result:
            compteur += 1

    logger.info(f"Memoire adaptative: enrichissement termine, {compteur} memoires indexes")
    return compteur
