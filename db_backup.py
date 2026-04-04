"""
Backup automatique des donnees JSON vers GitHub.
Resout le probleme du filesystem ephemere Render.
"""
import subprocess
import logging
import os
from pathlib import Path
from datetime import datetime

logger = logging.getLogger("ao_hunter.backup")

DASHBOARD_DIR = Path(__file__).parent
DATA_FILES = [
    "ao_pertinents.json",
    "ao_notes.json",
    "dossiers_index.json",
    "concurrence.json",
    "historique_veille.json",
    "pipeline_auto_log.json",
    "reviews.json",
    "checklist_etat.json",
    "commentaires.json",
    "rappels_envoyes.json",
]


def backup_to_git():
    """Commit et push les fichiers de donnees vers GitHub."""
    try:
        # Verifier qu'on est dans un repo git
        result = subprocess.run(
            ["git", "status", "--porcelain"] + [str(DASHBOARD_DIR / f) for f in DATA_FILES if (DASHBOARD_DIR / f).exists()],
            capture_output=True, text=True, cwd=str(DASHBOARD_DIR), timeout=30
        )

        changed_files = [l.strip().split()[-1] for l in result.stdout.strip().split('\n') if l.strip()]
        if not changed_files:
            logger.info("Backup: aucun changement a sauvegarder")
            return {"saved": 0}

        # Add les fichiers changes
        for f in DATA_FILES:
            fpath = DASHBOARD_DIR / f
            if fpath.exists():
                subprocess.run(["git", "add", str(fpath)], cwd=str(DASHBOARD_DIR), timeout=10)

        # Commit
        msg = f"auto-backup donnees {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        subprocess.run(
            ["git", "commit", "-m", msg],
            capture_output=True, text=True, cwd=str(DASHBOARD_DIR), timeout=30
        )

        # Push
        push = subprocess.run(
            ["git", "push", "origin", "master"],
            capture_output=True, text=True, cwd=str(DASHBOARD_DIR), timeout=60
        )

        if push.returncode == 0:
            logger.info(f"Backup OK: {len(changed_files)} fichier(s) sauvegarde(s)")
            return {"saved": len(changed_files), "files": changed_files}
        else:
            logger.warning(f"Backup push echue: {push.stderr}")
            return {"saved": 0, "error": push.stderr}

    except Exception as e:
        logger.error(f"Erreur backup: {e}")
        return {"saved": 0, "error": str(e)}


def restore_from_git():
    """Pull les dernieres donnees depuis GitHub (au demarrage)."""
    try:
        result = subprocess.run(
            ["git", "pull", "--rebase", "origin", "master"],
            capture_output=True, text=True, cwd=str(DASHBOARD_DIR), timeout=60
        )
        logger.info(f"Restore: {result.stdout.strip()}")
        return {"success": True}
    except Exception as e:
        logger.error(f"Erreur restore: {e}")
        return {"success": False, "error": str(e)}
