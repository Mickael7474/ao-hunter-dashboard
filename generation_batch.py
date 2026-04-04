"""
Generation batch de dossiers pour plusieurs AO.
Sequentiel par defaut pour ne pas surcharger l'API Claude.
"""

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger("ao_hunter.batch")

DASHBOARD_DIR = Path(__file__).parent
RESULTATS_DIR = DASHBOARD_DIR.parent / "resultats"
DOSSIERS_GENERES_DIR = DASHBOARD_DIR / "dossiers_generes"


def _dossier_existe(ao_id: str) -> bool:
    """Verifie si un dossier a deja ete genere pour cet AO."""
    clean_id = ao_id.replace("/", "_").replace("\\", "_")
    # Chercher dans resultats/ (local)
    for d in RESULTATS_DIR.glob(f"AO_{clean_id}*"):
        if d.is_dir():
            return True
    # Chercher dans dossiers_generes/ (Render)
    for d in DOSSIERS_GENERES_DIR.glob(f"AO_{clean_id}*"):
        if d.is_dir():
            return True
    # Chercher dans dossiers_index.json
    index_file = DASHBOARD_DIR / "dossiers_index.json"
    if index_file.exists():
        try:
            index = json.loads(index_file.read_text(encoding="utf-8"))
            for entry in index:
                if entry.get("ao_id") == ao_id:
                    return True
        except Exception:
            pass
    return False


def generer_batch(ao_ids: list[str], appels: list[dict], socketio=None, max_concurrent: int = 1) -> dict:
    """Genere les dossiers pour une liste d'AO.

    Args:
        ao_ids: Liste des IDs d'AO a traiter
        appels: Liste complete des AO (dicts)
        socketio: Instance SocketIO pour emettre la progression
        max_concurrent: Nombre max de generations simultanees (1 = sequentiel)

    Returns:
        dict avec: total, generes, deja_existants, erreurs, details
    """
    total = len(ao_ids)
    generes = 0
    deja_existants = 0
    erreurs = 0
    details = []

    def emit(msg, **kwargs):
        if socketio:
            socketio.emit("batch_progress", {"msg": msg, **kwargs})
        logger.info(msg)

    emit(f"Batch: demarrage pour {total} AO", phase="start", total=total)

    for i, ao_id in enumerate(ao_ids, 1):
        ao_dict = next((a for a in appels if a.get("id") == ao_id), None)
        titre = ao_dict.get("titre", "Sans titre")[:60] if ao_dict else "Inconnu"

        if not ao_dict:
            emit(f"Dossier {i}/{total} : AO {ao_id} non trouve", phase="error", index=i, total=total)
            erreurs += 1
            details.append({"ao_id": ao_id, "statut": "erreur", "raison": "AO non trouve"})
            continue

        # Verifier si dossier existe deja
        if _dossier_existe(ao_id):
            emit(f"Dossier {i}/{total} : {titre} - deja genere", phase="skip", index=i, total=total)
            deja_existants += 1
            details.append({"ao_id": ao_id, "titre": titre, "statut": "deja_existant"})
            continue

        emit(f"Dossier {i}/{total} : {titre} en cours...", phase="generating", index=i, total=total)

        try:
            # Essayer le generateur local d'abord
            try:
                from veille import AppelOffre
                from generateur import GenerateurMemoire
                import yaml

                config_path = DASHBOARD_DIR.parent / "config.yaml"
                config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

                ao_obj = AppelOffre(**{k: v for k, v in ao_dict.items()
                                      if k in AppelOffre.__dataclass_fields__})
                generateur = GenerateurMemoire(config)
                dossier = generateur.generer_dossier_complet(ao_obj)
                emit(f"Dossier {i}/{total} : {titre} - OK (local)", phase="done", index=i, total=total)
                generes += 1
                details.append({"ao_id": ao_id, "titre": titre, "statut": "genere", "mode": "local"})
                continue
            except ImportError:
                pass

            # Generateur Render (leger)
            from generateur_render import generer_dossier_complet
            from modeles_reponse import detecter_type_prestation

            type_presta = detecter_type_prestation(ao_dict)
            result = generer_dossier_complet(ao_dict, type_presta)

            if result.get("success"):
                emit(
                    f"Dossier {i}/{total} : {titre} - OK ({result.get('nb_mots', 0)} mots)",
                    phase="done", index=i, total=total
                )
                generes += 1
                details.append({
                    "ao_id": ao_id, "titre": titre, "statut": "genere",
                    "mode": "render", "nb_mots": result.get("nb_mots", 0),
                    "dossier_nom": result.get("dossier_nom", ""),
                })
            else:
                emit(
                    f"Dossier {i}/{total} : {titre} - ERREUR: {result.get('erreur', 'inconnue')}",
                    phase="error", index=i, total=total
                )
                erreurs += 1
                details.append({
                    "ao_id": ao_id, "titre": titre, "statut": "erreur",
                    "raison": result.get("erreur", "inconnue"),
                })

        except Exception as e:
            emit(f"Dossier {i}/{total} : {titre} - ERREUR: {e}", phase="error", index=i, total=total)
            erreurs += 1
            details.append({"ao_id": ao_id, "titre": titre, "statut": "erreur", "raison": str(e)})

    emit(
        f"Batch termine: {generes} genere(s), {deja_existants} existant(s), {erreurs} erreur(s)",
        phase="complete", total=total, generes=generes,
        deja_existants=deja_existants, erreurs=erreurs,
    )

    return {
        "total": total,
        "generes": generes,
        "deja_existants": deja_existants,
        "erreurs": erreurs,
        "details": details,
    }
