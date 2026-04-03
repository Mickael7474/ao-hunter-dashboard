"""
Extraction legere du texte des documents DCE telecharges.
Utilise pypdf (pas de dependances C) pour extraire le texte des PDF.
Fonctionne sur Render sans dependances lourdes.
"""

import logging
from pathlib import Path

logger = logging.getLogger("ao_hunter.dce_parser")

MAX_CHARS = 30000
EXTENSIONS_LISIBLES = {".pdf", ".docx"}


def extraire_texte_dce(dossier_dce: Path) -> str:
    """Extrait le texte de tous les PDF/DOCX d'un dossier DCE.

    Args:
        dossier_dce: Chemin vers le dossier contenant les fichiers DCE

    Returns:
        Texte concatene avec separateurs par document, tronque a 30000 chars.
    """
    if not dossier_dce.exists() or not dossier_dce.is_dir():
        logger.warning(f"Dossier DCE introuvable: {dossier_dce}")
        return ""

    fichiers = sorted(
        f for f in dossier_dce.iterdir()
        if f.is_file() and f.suffix.lower() in EXTENSIONS_LISIBLES
    )

    if not fichiers:
        logger.info(f"Aucun fichier lisible dans {dossier_dce}")
        return ""

    parties = []
    total_chars = 0

    for fichier in fichiers:
        if total_chars >= MAX_CHARS:
            break

        texte = ""
        try:
            if fichier.suffix.lower() == ".pdf":
                texte = _extraire_pdf(fichier)
            elif fichier.suffix.lower() == ".docx":
                texte = _extraire_docx(fichier)
        except Exception as e:
            logger.warning(f"Erreur extraction {fichier.name}: {e}")
            continue

        if texte.strip():
            bloc = f"--- DOCUMENT: {fichier.name} ---\n{texte.strip()}\n"
            parties.append(bloc)
            total_chars += len(bloc)
            logger.info(f"DCE extrait: {fichier.name} ({len(texte)} chars)")

    resultat = "\n".join(parties)

    # Tronquer si necessaire
    if len(resultat) > MAX_CHARS:
        resultat = resultat[:MAX_CHARS] + "\n[... TRONQUE ...]"
        logger.info(f"Texte DCE tronque a {MAX_CHARS} chars")

    return resultat


def _extraire_pdf(fichier: Path) -> str:
    """Extrait le texte d'un PDF avec pypdf."""
    try:
        from pypdf import PdfReader
    except ImportError:
        logger.warning("pypdf non installe - extraction PDF impossible")
        return ""

    reader = PdfReader(str(fichier))
    pages_texte = []
    for page in reader.pages:
        texte = page.extract_text()
        if texte:
            pages_texte.append(texte)

    return "\n".join(pages_texte)


def _extraire_docx(fichier: Path) -> str:
    """Extrait le texte d'un DOCX (basique, sans python-docx)."""
    # Extraction basique via zipfile (DOCX = ZIP contenant XML)
    import zipfile
    import re

    try:
        with zipfile.ZipFile(str(fichier), "r") as z:
            if "word/document.xml" not in z.namelist():
                return ""
            xml_content = z.read("word/document.xml").decode("utf-8", errors="ignore")
            # Extraire le texte entre balises <w:t>
            texte_parts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", xml_content)
            return " ".join(texte_parts)
    except Exception as e:
        logger.warning(f"Erreur extraction DOCX {fichier.name}: {e}")
        return ""


def analyser_dce_complet(texte_dce: str, ao: dict) -> dict:
    """Analyse complete du DCE : extraction d'exigences + Go/No-Go.

    Args:
        texte_dce: Texte brut extrait des documents DCE
        ao: Dictionnaire de l'appel d'offres

    Returns:
        dict avec: analyse_dce (pieces, criteres, exigences...),
                   go_no_go (decision, score, criteres...)
    """
    from analyse_dce import analyser_dce_texte, go_no_go

    # Analyse structuree du DCE
    analyse = analyser_dce_texte(texte_dce, ao)

    # Go/No-Go enrichi avec l'analyse DCE
    resultat_gng = go_no_go(ao, analyse_dce=analyse)

    return {
        "analyse_dce": analyse,
        "go_no_go": resultat_gng,
    }
