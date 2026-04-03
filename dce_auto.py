"""
Auto-download DCE leger pour Render.
Tente de recuperer les documents du DCE depuis l'URL de l'AO
ou du profil acheteur, sans Playwright (HTTP only).
"""

import re
import logging
from pathlib import Path
from datetime import datetime
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("ao_hunter.dce_auto")

DASHBOARD_DIR = Path(__file__).parent
DOSSIERS_DIR = DASHBOARD_DIR / "dossiers_generes"

# Extensions de fichiers DCE acceptees
EXTENSIONS_DCE = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".odt", ".zip", ".rar"}

# Taille max par fichier (10 MB)
MAX_FILE_SIZE = 10 * 1024 * 1024


def telecharger_dce_auto(ao: dict) -> dict:
    """Tente de telecharger les documents DCE depuis les URLs de l'AO.

    Args:
        ao: dict de l'appel d'offres (avec url, url_profil_acheteur)

    Returns:
        dict avec: success, fichiers (list), dossier, erreur
    """
    urls_a_explorer = []

    # URL du profil acheteur (prioritaire, souvent le lien direct vers le DCE)
    if ao.get("url_profil_acheteur"):
        urls_a_explorer.append(ao["url_profil_acheteur"])

    # URL de l'avis
    if ao.get("url"):
        urls_a_explorer.append(ao["url"])

    if not urls_a_explorer:
        return {"success": False, "fichiers": [], "erreur": "Aucune URL disponible"}

    # Creer le dossier
    clean_id = ao.get("id", "inconnu").replace("/", "_")
    dossier_nom = f"DCE_{clean_id}"
    dossier_path = DOSSIERS_DIR / dossier_nom
    dossier_path.mkdir(parents=True, exist_ok=True)

    fichiers_telecharges = []

    for url in urls_a_explorer:
        try:
            liens_dce = _explorer_page(url)
            for lien in liens_dce[:10]:  # Max 10 fichiers
                fichier = _telecharger_fichier(lien, dossier_path)
                if fichier:
                    fichiers_telecharges.append(fichier)
        except Exception as e:
            logger.warning(f"Erreur exploration {url}: {e}")

    if not fichiers_telecharges:
        # Nettoyer le dossier vide
        try:
            dossier_path.rmdir()
        except OSError:
            pass
        return {"success": False, "fichiers": [], "erreur": "Aucun document DCE trouve"}

    return {
        "success": True,
        "fichiers": fichiers_telecharges,
        "dossier": dossier_nom,
        "nb_fichiers": len(fichiers_telecharges),
    }


def _explorer_page(url: str) -> list[str]:
    """Explore une page web pour trouver les liens vers des documents DCE."""
    liens = []

    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            resp = client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            if resp.status_code != 200:
                return liens
    except Exception as e:
        logger.debug(f"Erreur HTTP {url}: {e}")
        return liens

    soup = BeautifulSoup(resp.text, "html.parser")
    base_url = f"{urlparse(url).scheme}://{urlparse(url).netloc}"

    # Chercher tous les liens vers des documents
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        texte = a.get_text(strip=True).lower()

        # Lien direct vers un fichier
        ext = Path(urlparse(href).path).suffix.lower()
        if ext in EXTENSIONS_DCE:
            lien_absolu = urljoin(url, href)
            liens.append(lien_absolu)
            continue

        # Lien avec texte suggestif
        mots_cles_dce = ["telecharger", "download", "dce", "dossier",
                         "reglement", "cctp", "ccap", "bpu", "dpgf",
                         "rc", "cahier des charges", "piece", "document"]
        if any(m in texte for m in mots_cles_dce):
            lien_absolu = urljoin(url, href)
            # Verifier si c'est un lien vers un fichier
            if any(href.lower().endswith(e) for e in EXTENSIONS_DCE):
                liens.append(lien_absolu)
            elif "download" in href.lower() or "telecharger" in href.lower():
                liens.append(lien_absolu)

    # Dedoublonner
    vus = set()
    uniques = []
    for lien in liens:
        if lien not in vus:
            vus.add(lien)
            uniques.append(lien)

    return uniques


def _telecharger_fichier(url: str, dossier: Path) -> str | None:
    """Telecharge un fichier et le sauvegarde dans le dossier."""
    try:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            # HEAD d'abord pour verifier la taille
            try:
                head = client.head(url)
                taille = int(head.headers.get("content-length", 0))
                if taille > MAX_FILE_SIZE:
                    logger.debug(f"Fichier trop gros ({taille}): {url}")
                    return None
            except Exception:
                pass

            resp = client.get(url)
            if resp.status_code != 200:
                return None

            # Determiner le nom du fichier
            nom_fichier = _extraire_nom_fichier(resp, url)
            if not nom_fichier:
                return None

            # Verifier l'extension
            ext = Path(nom_fichier).suffix.lower()
            if ext not in EXTENSIONS_DCE:
                return None

            # Verifier la taille
            if len(resp.content) > MAX_FILE_SIZE:
                return None

            # Sauvegarder
            chemin = dossier / nom_fichier
            # Eviter les ecrasements
            compteur = 1
            while chemin.exists():
                stem = Path(nom_fichier).stem
                chemin = dossier / f"{stem}_{compteur}{ext}"
                compteur += 1

            chemin.write_bytes(resp.content)
            logger.info(f"DCE telecharge: {nom_fichier} ({len(resp.content)} octets)")
            return chemin.name

    except Exception as e:
        logger.debug(f"Erreur telechargement {url}: {e}")
        return None


def _extraire_nom_fichier(resp, url: str) -> str | None:
    """Extrait le nom du fichier depuis la reponse HTTP."""
    # Content-Disposition header
    cd = resp.headers.get("content-disposition", "")
    if cd:
        match = re.search(r'filename[*]?=["\']?([^"\';\n]+)', cd)
        if match:
            return match.group(1).strip()

    # Depuis l'URL
    path = urlparse(url).path
    nom = Path(path).name
    if nom and Path(nom).suffix.lower() in EXTENSIONS_DCE:
        return nom

    return None
