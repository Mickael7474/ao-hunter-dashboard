"""
Analyse semantique complete du DCE via Claude Sonnet.
Envoie tout le texte du DCE a Claude pour extraire TOUTES les exigences
(CCTP, CCAP, RC, etc.) et verifier lesquelles Almera remplit.
"""

import os
import json
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger("ao_hunter.analyse_semantique_dce")

DASHBOARD_DIR = Path(__file__).parent
DOSSIERS_DIR = DASHBOARD_DIR / "dossiers_generes"

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODELE = "claude-sonnet-4-20250514"
MAX_CHARS_DCE = 15000

# Infos Almera pour le prompt
INFOS_ALMERA = """
- Raison sociale: AI MENTOR (nom commercial: Almera), SASU
- SIRET: 98900455100010
- NDA: 11757431975
- President: Mickael Bertolla (Ingenieur Mines Saint-Etienne, MSc Skema USA, auteur Ed. ENI)
- Certifications: Qualiopi (actions de formation), RS6776 France Competences (IA generative), Activateur France Num, Membre Hub France IA, Membre French Tech
- 6 formateurs specialises IA (Mickael Bertolla, Charles Courbet, Guillaume Martin, Romy Chen, Yann Delaporte, Stephanie Moreau)
- 23+ references clients (Havas, Eiffage, Carrefour, Orange, Caisse des Depots, BPI France, etc.)
- 29 formations IA au catalogue (ChatGPT, Claude, Midjourney, Copilot, agents IA, prompt engineering, etc.)
- 2000+ personnes formees, 50+ entreprises, note 4.9/5 Google
- CA ~200k EUR
- TVA exoneree (art. 261-4-4 CGI) pour les prestations de formation
- Domaines: formation IA, consulting transformation digitale, accompagnement deploiement IA
- Pieces admin disponibles: Kbis, attestations URSSAF/fiscales, Qualiopi, RIB, DC1/DC2/DUME auto-generes
- Pieces en attente: RC Pro (en attente devis), certificat RS6776 (en attente ~2 mois)
"""


def _appel_claude(prompt: str, max_tokens: int = 4000) -> str:
    """Appelle l'API Claude via httpx et retourne le texte."""
    import httpx

    with httpx.Client(timeout=120) as client:
        resp = client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": MODELE,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
        data = resp.json()

    return data["content"][0]["text"]


def analyser_dce_complet_ia(dossier_dce: Path, ao: dict) -> dict:
    """Analyse semantique complete du DCE via Claude Sonnet.

    Args:
        dossier_dce: Path vers le dossier contenant les fichiers DCE
        ao: dict de l'appel d'offres

    Returns:
        dict avec toutes les exigences extraites et l'adequation Almera
    """
    from dce_parser import extraire_texte_dce

    # Extraire le texte de tous les documents
    texte_dce = extraire_texte_dce(dossier_dce)

    if not texte_dce or len(texte_dce.strip()) < 100:
        logger.warning(f"Texte DCE trop court ({len(texte_dce)} chars) pour analyse semantique")
        return {"erreur": "Texte DCE insuffisant pour l'analyse"}

    # Tronquer a MAX_CHARS_DCE
    if len(texte_dce) > MAX_CHARS_DCE:
        texte_dce = texte_dce[:MAX_CHARS_DCE] + "\n[... TRONQUE ...]"

    # Construire le prompt
    prompt = f"""Tu es un expert en marches publics francais. Analyse ce DCE (Dossier de Consultation des Entreprises) et extrais TOUTES les exigences, puis evalue l'adequation avec le profil de l'entreprise candidate.

## APPEL D'OFFRES
- Titre: {ao.get('titre', 'Non precise')}
- Acheteur: {ao.get('acheteur', 'Non precise')}
- Budget estime: {ao.get('budget_estime', 'Non precise')} EUR
- Date limite: {ao.get('date_limite', 'Non precisee')}
- Type: {ao.get('type_marche', 'Non precise')}

## PROFIL ENTREPRISE CANDIDATE (Almera)
{INFOS_ALMERA}

## TEXTE DU DCE
{texte_dce}

## INSTRUCTIONS
Reponds UNIQUEMENT avec un JSON valide (sans markdown, sans commentaires) contenant:

{{
  "exigences_administratives": [
    {{"exigence": "description", "obligatoire": true/false, "almera_conforme": true/false, "commentaire": "..."}}
  ],
  "exigences_techniques": [
    {{"exigence": "description", "obligatoire": true/false, "almera_conforme": true/false, "commentaire": "..."}}
  ],
  "criteres_notation": [
    {{"critere": "nom", "poids_pct": 0, "sous_criteres": ["..."]}}
  ],
  "pieces_demandees": [
    {{"piece": "nom", "obligatoire": true/false, "disponible_almera": true/false}}
  ],
  "contraintes_calendrier": [
    {{"contrainte": "description", "date": "YYYY-MM-DD ou null", "commentaire": "..."}}
  ],
  "points_attention": ["element inhabituel ou risque 1", "..."],
  "avantages_almera": ["point fort 1 par rapport aux exigences", "..."],
  "score_adequation": 0
}}

Le score_adequation va de 0 a 100 (100 = adequation parfaite).
Sois precis et exhaustif. Ne mets PAS de blocs markdown autour du JSON."""

    logger.info(f"Analyse semantique DCE: appel Claude ({len(texte_dce)} chars)")

    try:
        reponse = _appel_claude(prompt, max_tokens=4000)
    except Exception as e:
        logger.error(f"Erreur appel Claude pour analyse semantique: {e}")
        return {"erreur": f"Erreur API Claude: {str(e)}"}

    # Parser le JSON
    try:
        # Nettoyer la reponse (enlever d'eventuels blocs markdown)
        reponse_clean = reponse.strip()
        if reponse_clean.startswith("```"):
            reponse_clean = reponse_clean.split("\n", 1)[1]
            if reponse_clean.endswith("```"):
                reponse_clean = reponse_clean[:-3]
            reponse_clean = reponse_clean.strip()

        analyse = json.loads(reponse_clean)
    except json.JSONDecodeError as e:
        logger.error(f"Erreur parsing JSON: {e}\nReponse: {reponse[:500]}")
        return {"erreur": f"Reponse IA non parsable: {str(e)}"}

    # Enrichir avec les metadonnees
    analyse["timestamp"] = datetime.now().isoformat()
    analyse["ao_id"] = ao.get("id", "")
    analyse["ao_titre"] = ao.get("titre", "")
    analyse["nb_chars_dce"] = len(texte_dce)

    # Sauvegarder dans le dossier de l'AO
    try:
        fichier_sortie = dossier_dce / "analyse_dce_complete.json"
        with open(fichier_sortie, "w", encoding="utf-8") as f:
            json.dump(analyse, f, ensure_ascii=False, indent=2)
        logger.info(f"Analyse semantique sauvegardee: {fichier_sortie}")
    except Exception as e:
        logger.warning(f"Erreur sauvegarde analyse: {e}")

    return analyse


def resume_adequation(analyse: dict) -> str:
    """Resume texte court de l'adequation Almera / DCE.

    Args:
        analyse: dict retourne par analyser_dce_complet_ia()

    Returns:
        str de 5 lignes resumant l'adequation
    """
    if "erreur" in analyse:
        return f"Analyse impossible: {analyse['erreur']}"

    # Compter les exigences admin
    exig_admin = analyse.get("exigences_administratives", [])
    admin_ok = sum(1 for e in exig_admin if e.get("almera_conforme"))
    admin_total = len(exig_admin)

    # Compter les exigences techniques
    exig_tech = analyse.get("exigences_techniques", [])
    tech_ok = sum(1 for e in exig_tech if e.get("almera_conforme"))
    tech_total = len(exig_tech)

    # Pieces
    pieces = analyse.get("pieces_demandees", [])
    pieces_ok = sum(1 for p in pieces if p.get("disponible_almera"))
    pieces_total = len(pieces)

    # Points attention
    nb_attention = len(analyse.get("points_attention", []))

    # Score
    score = analyse.get("score_adequation", 0)

    # Avantages
    nb_avantages = len(analyse.get("avantages_almera", []))

    total_ok = admin_ok + tech_ok
    total_exig = admin_total + tech_total

    lignes = [
        f"Score: {score}/100",
        f"Exigences admin: {admin_ok}/{admin_total} conformes | Techniques: {tech_ok}/{tech_total} conformes",
        f"Pieces: {pieces_ok}/{pieces_total} disponibles",
        f"{nb_attention} point(s) d'attention | {nb_avantages} avantage(s) Almera",
        f"Total: {total_ok}/{total_exig} exigences remplies",
    ]
    return "\n".join(lignes)


def matrice_conformite(analyse: dict) -> list[dict]:
    """Matrice plate pour affichage tableau.

    Args:
        analyse: dict retourne par analyser_dce_complet_ia()

    Returns:
        list[dict] avec {categorie, exigence, obligatoire, conforme, commentaire}
    """
    if "erreur" in analyse:
        return []

    matrice = []

    for e in analyse.get("exigences_administratives", []):
        matrice.append({
            "categorie": "Administrative",
            "exigence": e.get("exigence", ""),
            "obligatoire": e.get("obligatoire", False),
            "conforme": e.get("almera_conforme", False),
            "commentaire": e.get("commentaire", ""),
        })

    for e in analyse.get("exigences_techniques", []):
        matrice.append({
            "categorie": "Technique",
            "exigence": e.get("exigence", ""),
            "obligatoire": e.get("obligatoire", False),
            "conforme": e.get("almera_conforme", False),
            "commentaire": e.get("commentaire", ""),
        })

    for p in analyse.get("pieces_demandees", []):
        matrice.append({
            "categorie": "Piece demandee",
            "exigence": p.get("piece", ""),
            "obligatoire": p.get("obligatoire", False),
            "conforme": p.get("disponible_almera", False),
            "commentaire": "",
        })

    return matrice
