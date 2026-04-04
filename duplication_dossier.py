"""
Duplication de dossier AO existant.

Permet de dupliquer un dossier genere pour un AO precedent et de l'adapter
a un nouvel AO (remplacement titre, acheteur, dates, references).
Le contenu technique (memoire, BPU, planning) n'est PAS modifie.
"""

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from difflib import unified_diff

DASHBOARD_DIR = Path(__file__).parent
BASE_DIR = DASHBOARD_DIR.parent
RESULTATS_DIR = BASE_DIR / "resultats"
DOSSIERS_GENERES_DIR = DASHBOARD_DIR / "dossiers_generes"


def lister_dossiers_existants() -> list[dict]:
    """Scanne les dossiers generes (resultats/ et dossiers_generes/).

    Returns:
        Liste de dicts {ao_id, titre, date_generation, nb_fichiers, dossier_path}
    """
    dossiers = []
    seen_ids = set()

    for base in [RESULTATS_DIR, DOSSIERS_GENERES_DIR]:
        if not base.exists():
            continue
        for d in sorted(base.iterdir(), reverse=True):
            if not d.is_dir() or d.name.startswith("__") or d.name.startswith("DCE_"):
                continue

            fichiers_md = [f for f in d.glob("*.md") if f.is_file()]
            if not fichiers_md:
                continue

            # Extraire l'ID AO depuis le nom du dossier ou fiche_ao.json
            ao_id = _extraire_ao_id(d)
            if ao_id in seen_ids:
                continue
            seen_ids.add(ao_id)

            # Titre depuis fiche_ao.json si disponible
            titre = _extraire_titre(d, ao_id)

            nb_fichiers = len([f for f in d.glob("*") if f.is_file()])
            date_gen = datetime.fromtimestamp(d.stat().st_ctime).strftime("%Y-%m-%d %H:%M")

            dossiers.append({
                "ao_id": ao_id,
                "titre": titre,
                "date_generation": date_gen,
                "nb_fichiers": nb_fichiers,
                "dossier_path": str(d),
            })

    return dossiers


def _extraire_ao_id(dossier: Path) -> str:
    """Extrait l'ID AO depuis le dossier (fiche_ao.json ou nom du dossier)."""
    fiche = dossier / "fiche_ao.json"
    if fiche.exists():
        try:
            with open(fiche, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "id" in data:
                return data["id"]
        except Exception:
            pass

    # Fallback: nom du dossier (ex: AO_BOAMP-25_91265 -> BOAMP-25_91265)
    nom = dossier.name
    if nom.startswith("AO_"):
        return nom[3:]
    # Format dossier_XXXX
    return nom


def _extraire_titre(dossier: Path, ao_id: str) -> str:
    """Extrait le titre de l'AO depuis fiche_ao.json ou le nom du dossier."""
    fiche = dossier / "fiche_ao.json"
    if fiche.exists():
        try:
            with open(fiche, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("titre", ao_id)
        except Exception:
            pass
    return ao_id


def trouver_dossier_similaire(ao_cible: dict, dossiers: list[dict]) -> list[dict]:
    """Compare l'AO cible avec les AO ayant des dossiers generes.

    Score de similarite base sur :
    - Mots-cles communs dans le titre
    - Meme type de prestation
    - Meme type d'acheteur

    Args:
        ao_cible: dict de l'AO cible (avec titre, acheteur, description, etc.)
        dossiers: liste retournee par lister_dossiers_existants()

    Returns:
        Top 3 dossiers les plus similaires avec score (0-100)
    """
    if not dossiers:
        return []

    # Charger la base AO pour avoir les infos completes des AO sources
    ao_cache_path = DASHBOARD_DIR / "ao_pertinents.json"
    if not ao_cache_path.exists():
        ao_cache_path = RESULTATS_DIR / "ao_pertinents.json"
    ao_index = {}
    if ao_cache_path.exists():
        try:
            with open(ao_cache_path, "r", encoding="utf-8") as f:
                for ao in json.load(f):
                    ao_index[ao.get("id")] = ao
        except Exception:
            pass

    mots_cible = _extraire_mots_cles(ao_cible.get("titre", "") + " " + ao_cible.get("description", ""))
    type_cible = _detecter_type_simple(ao_cible)
    acheteur_cible = _normaliser_acheteur(ao_cible.get("acheteur", ""))

    resultats = []
    for dossier in dossiers:
        # Ne pas proposer le meme AO
        if dossier["ao_id"] == ao_cible.get("id"):
            continue

        ao_source = ao_index.get(dossier["ao_id"], {})
        texte_source = dossier["titre"] + " " + ao_source.get("description", "")
        mots_source = _extraire_mots_cles(texte_source)

        score = 0

        # Score mots-cles communs (max 50 pts)
        if mots_cible and mots_source:
            communs = mots_cible & mots_source
            total = mots_cible | mots_source
            jaccard = len(communs) / len(total) if total else 0
            score += int(jaccard * 50)

        # Score type prestation (max 30 pts)
        type_source = _detecter_type_simple(ao_source) if ao_source else _detecter_type_simple({"titre": dossier["titre"]})
        if type_cible and type_source and type_cible == type_source:
            score += 30

        # Score acheteur similaire (max 20 pts)
        acheteur_source = _normaliser_acheteur(ao_source.get("acheteur", "") or dossier.get("titre", ""))
        if acheteur_cible and acheteur_source:
            # Meme type d'organisme (collectivite, ministere, OPCO, etc.)
            types_acheteur_cible = _type_acheteur(acheteur_cible)
            types_acheteur_source = _type_acheteur(acheteur_source)
            if types_acheteur_cible and types_acheteur_source and types_acheteur_cible & types_acheteur_source:
                score += 20
            elif acheteur_cible == acheteur_source:
                score += 20

        if score > 0:
            resultats.append({
                **dossier,
                "score_similarite": min(score, 100),
                "ao_source": ao_source,
            })

    # Trier par score decroissant, retourner top 3
    resultats.sort(key=lambda x: x["score_similarite"], reverse=True)
    return resultats[:3]


def _extraire_mots_cles(texte: str) -> set[str]:
    """Extrait les mots significatifs d'un texte (> 3 chars, pas stopwords)."""
    stopwords = {
        "pour", "dans", "avec", "sans", "plus", "moins", "cette", "sont", "sera",
        "etre", "avoir", "fait", "faire", "tout", "tous", "leur", "leurs", "nous",
        "vous", "elle", "elles", "entre", "comme", "mais", "aussi", "bien", "tres",
        "meme", "autre", "autres", "dont", "peut", "donc", "lors", "depuis",
        "encore", "avant", "apres", "sous", "chez", "vers", "selon",
        "prestations", "prestation", "marche", "objet", "cadre",
    }
    mots = set()
    for mot in re.findall(r"[a-zàâäéèêëïîôùûüç]+", texte.lower()):
        if len(mot) > 3 and mot not in stopwords:
            mots.add(mot)
    return mots


def _detecter_type_simple(ao: dict) -> str:
    """Detection simplifiee du type de prestation (sans import modeles_reponse)."""
    texte = (ao.get("titre", "") + " " + ao.get("description", "")).lower()

    mots_formation = ["formation", "formateur", "pedagogie", "apprenant", "qualiopi", "cpf", "opco"]
    mots_consulting = ["conseil", "consulting", "accompagnement", "audit", "diagnostic", "amo", "expertise"]
    mots_dev = ["developpement", "logiciel", "application", "chatbot", "automatisation", "api"]

    scores = {"formation": 0, "consulting": 0, "dev": 0}
    for m in mots_formation:
        if m in texte:
            scores["formation"] += 1
    for m in mots_consulting:
        if m in texte:
            scores["consulting"] += 1
    for m in mots_dev:
        if m in texte:
            scores["dev"] += 1

    if max(scores.values()) == 0:
        return ""
    return max(scores, key=scores.get)


def _normaliser_acheteur(acheteur: str) -> str:
    """Normalise le nom d'acheteur pour comparaison."""
    return acheteur.strip().lower()


def _type_acheteur(acheteur: str) -> set[str]:
    """Detecte le type d'acheteur (collectivite, ministere, OPCO, etc.)."""
    acheteur = acheteur.lower()
    types = set()
    if any(m in acheteur for m in ["mairie", "commune", "ville", "metropole", "communaute", "departement", "region", "conseil"]):
        types.add("collectivite")
    if any(m in acheteur for m in ["ministere", "direction", "prefect", "etat"]):
        types.add("etat")
    if any(m in acheteur for m in ["opco", "atlas", "akto", "2i", "uniformation"]):
        types.add("opco")
    if any(m in acheteur for m in ["cnfpt", "anfh", "centre de gestion"]):
        types.add("fpt")
    if any(m in acheteur for m in ["universite", "ecole", "academie", "rectorat", "crous"]):
        types.add("enseignement")
    if any(m in acheteur for m in ["hopital", "chu", "ars", "ehpad"]):
        types.add("sante")
    return types


def dupliquer_dossier(ao_source_id: str, ao_cible: dict, dossier_source_path: str) -> dict:
    """Duplique un dossier source et l'adapte pour un nouvel AO.

    - Copie le dossier complet
    - Remplace titre, acheteur, date limite, ID dans les fichiers .md
    - NE touche PAS le contenu technique
    - Ajoute _DUPLICATED_FROM.txt

    Args:
        ao_source_id: ID de l'AO source
        ao_cible: dict complet de l'AO cible
        dossier_source_path: chemin du dossier source

    Returns:
        {dossier_path, nb_fichiers_copies, modifications_appliquees}
    """
    source_path = Path(dossier_source_path)
    if not source_path.exists():
        return {"error": f"Dossier source introuvable: {dossier_source_path}"}

    # Charger les infos de l'AO source
    ao_source = _charger_ao_source(source_path, ao_source_id)

    # Creer le dossier cible
    cible_id = ao_cible.get("id", "unknown")
    clean_id = cible_id.replace("/", "_")
    dossier_cible = DOSSIERS_GENERES_DIR / f"AO_{clean_id}"

    # S'assurer que le dossier parent existe
    DOSSIERS_GENERES_DIR.mkdir(parents=True, exist_ok=True)

    # Copier le dossier complet
    if dossier_cible.exists():
        shutil.rmtree(dossier_cible)
    shutil.copytree(source_path, dossier_cible)

    # Preparer les remplacements
    remplacements = _preparer_remplacements(ao_source, ao_cible, ao_source_id)

    # Appliquer les remplacements dans les fichiers .md
    nb_fichiers = 0
    modifications = []
    for fichier in dossier_cible.glob("*.md"):
        try:
            contenu = fichier.read_text(encoding="utf-8")
            nouveau_contenu = contenu
            fichier_modifs = []

            for ancien, nouveau in remplacements:
                if ancien and nouveau and ancien in nouveau_contenu:
                    nouveau_contenu = nouveau_contenu.replace(ancien, nouveau)
                    fichier_modifs.append(f"{ancien[:50]}... -> {nouveau[:50]}...")

            if nouveau_contenu != contenu:
                fichier.write_text(nouveau_contenu, encoding="utf-8")
                modifications.append({
                    "fichier": fichier.name,
                    "nb_remplacements": len(fichier_modifs),
                    "details": fichier_modifs,
                })

            nb_fichiers += 1
        except Exception:
            nb_fichiers += 1

    # Mettre a jour fiche_ao.json si present
    fiche_cible = dossier_cible / "fiche_ao.json"
    if fiche_cible.exists():
        try:
            fiche_cible.write_text(json.dumps(ao_cible, ensure_ascii=False, indent=2), encoding="utf-8")
            modifications.append({"fichier": "fiche_ao.json", "nb_remplacements": 1, "details": ["Remplace par donnees AO cible"]})
        except Exception:
            pass

    # Ajouter le fichier traceur
    traceur = dossier_cible / "_DUPLICATED_FROM.txt"
    traceur.write_text(
        f"Ce dossier a ete duplique depuis un dossier existant.\n"
        f"Source: {ao_source_id}\n"
        f"Dossier source: {dossier_source_path}\n"
        f"AO cible: {ao_cible.get('id', 'unknown')}\n"
        f"Date duplication: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"\n"
        f"IMPORTANT: Le contenu technique (memoire, BPU, planning) n'a PAS ete modifie.\n"
        f"Seuls le titre, l'acheteur, les dates et les references ont ete remplaces.\n"
        f"Relisez et adaptez chaque piece avant soumission.\n",
        encoding="utf-8",
    )

    return {
        "dossier_path": str(dossier_cible),
        "nb_fichiers_copies": nb_fichiers,
        "modifications_appliquees": modifications,
    }


def _charger_ao_source(dossier: Path, ao_source_id: str) -> dict:
    """Charge les infos de l'AO source depuis fiche_ao.json ou la base."""
    # D'abord fiche_ao.json dans le dossier
    fiche = dossier / "fiche_ao.json"
    if fiche.exists():
        try:
            with open(fiche, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    # Sinon chercher dans la base AO
    for cache_path in [DASHBOARD_DIR / "ao_pertinents.json", RESULTATS_DIR / "ao_pertinents.json"]:
        if cache_path.exists():
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    for ao in json.load(f):
                        if ao.get("id") == ao_source_id:
                            return ao
            except Exception:
                pass

    return {"id": ao_source_id}


def _preparer_remplacements(ao_source: dict, ao_cible: dict, ao_source_id: str) -> list[tuple[str, str]]:
    """Prepare la liste de remplacements (ancien, nouveau) a appliquer."""
    remplacements = []

    # Titre
    titre_source = ao_source.get("titre", "")
    titre_cible = ao_cible.get("titre", "")
    if titre_source and titre_cible and titre_source != titre_cible:
        remplacements.append((titre_source, titre_cible))

    # Acheteur
    acheteur_source = ao_source.get("acheteur", "")
    acheteur_cible = ao_cible.get("acheteur", "")
    if acheteur_source and acheteur_cible and acheteur_source != acheteur_cible:
        remplacements.append((acheteur_source, acheteur_cible))

    # Date limite
    date_source = ao_source.get("date_limite", "")
    date_cible = ao_cible.get("date_limite", "")
    if date_source and date_cible and date_source != date_cible:
        remplacements.append((str(date_source), str(date_cible)))
        # Aussi les formats courts (YYYY-MM-DD)
        date_s_court = str(date_source)[:10]
        date_c_court = str(date_cible)[:10]
        if date_s_court != date_c_court and len(date_s_court) == 10:
            remplacements.append((date_s_court, date_c_court))

    # ID AO
    cible_id = ao_cible.get("id", "")
    if ao_source_id and cible_id and ao_source_id != cible_id:
        remplacements.append((ao_source_id, cible_id))
        # Aussi l'ID sans prefixe source
        clean_source = ao_source_id.replace("BOAMP-", "").replace("PLACE-", "").replace("TED-", "").replace("MSEC-", "").replace("AWS-", "")
        clean_cible = cible_id.replace("BOAMP-", "").replace("PLACE-", "").replace("TED-", "").replace("MSEC-", "").replace("AWS-", "")
        if clean_source != clean_cible:
            remplacements.append((clean_source, clean_cible))

    return remplacements


def diff_dossiers(dossier_source: str, dossier_cible: str) -> list[dict]:
    """Compare deux dossiers fichier par fichier.

    Args:
        dossier_source: chemin du dossier source
        dossier_cible: chemin du dossier cible

    Returns:
        Liste de dicts {fichier, statut, diff_lines} pour chaque difference
    """
    source = Path(dossier_source)
    cible = Path(dossier_cible)
    resultats = []

    if not source.exists() or not cible.exists():
        return [{"fichier": "-", "statut": "erreur", "diff_lines": ["Dossier introuvable"]}]

    # Fichiers source
    fichiers_source = {f.name for f in source.glob("*") if f.is_file()}
    fichiers_cible = {f.name for f in cible.glob("*") if f.is_file()}

    # Fichiers uniquement dans la source
    for f in sorted(fichiers_source - fichiers_cible):
        resultats.append({"fichier": f, "statut": "supprime", "diff_lines": []})

    # Fichiers uniquement dans la cible
    for f in sorted(fichiers_cible - fichiers_source):
        resultats.append({"fichier": f, "statut": "ajoute", "diff_lines": []})

    # Fichiers communs
    for f in sorted(fichiers_source & fichiers_cible):
        if f.endswith((".json", ".xlsx", ".pdf", ".docx", ".png", ".jpg")):
            continue  # Ignorer les binaires
        if f == "_DUPLICATED_FROM.txt":
            continue

        try:
            contenu_source = (source / f).read_text(encoding="utf-8").splitlines()
            contenu_cible = (cible / f).read_text(encoding="utf-8").splitlines()

            diff = list(unified_diff(contenu_source, contenu_cible, fromfile=f"source/{f}", tofile=f"cible/{f}", lineterm=""))
            if diff:
                # Limiter a 30 lignes de diff par fichier
                resultats.append({
                    "fichier": f,
                    "statut": "modifie",
                    "diff_lines": diff[:30],
                    "nb_lignes_diff": len(diff),
                })
        except Exception:
            resultats.append({"fichier": f, "statut": "erreur_lecture", "diff_lines": []})

    return resultats
