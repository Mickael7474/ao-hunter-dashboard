"""
Auto-review IA du dossier avant soumission.
Fait un appel Claude Haiku pour relire et verifier la coherence
du dossier complet genere.
"""

import os
import json
import logging

logger = logging.getLogger("ao_hunter.auto_review")

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODELE_REVIEW = "claude-haiku-3-5-20251001"


def _appel_claude_review(prompt: str, max_tokens: int = 3000) -> str:
    """Appelle Claude Haiku pour la review."""
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
                "model": MODELE_REVIEW,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
        data = resp.json()

    return data["content"][0]["text"]


def review_dossier(fichiers: dict, ao: dict, criteres_attribution: list = None) -> dict:
    """Effectue une auto-review du dossier complet.

    Args:
        fichiers: dict {nom_fichier: contenu_markdown}
        ao: dict de l'appel d'offres
        criteres_attribution: liste de criteres detectes (optionnel)

    Returns:
        dict {score_qualite: 0-100, problemes: [...], suggestions: [...], conforme: bool}
    """
    if not API_KEY:
        logger.warning("ANTHROPIC_API_KEY non configuree, review impossible")
        return {
            "score_qualite": 0,
            "problemes": ["API key non configuree, review non effectuee"],
            "suggestions": [],
            "conforme": False,
        }

    # Preparer un resume du dossier (tronquer pour rester dans les limites)
    resume_dossier = ""
    for nom, contenu in fichiers.items():
        # Limiter chaque fichier a 1500 caracteres pour le resume
        extrait = contenu[:1500] if len(contenu) > 1500 else contenu
        resume_dossier += f"\n\n--- {nom} ---\n{extrait}"

    # Tronquer le resume total a 15000 caracteres
    if len(resume_dossier) > 15000:
        resume_dossier = resume_dossier[:15000] + "\n[... tronque ...]"

    # Bloc criteres d'attribution
    criteres_bloc = ""
    if criteres_attribution:
        criteres_lignes = []
        for c in criteres_attribution:
            ligne = f"- {c['nom']} ({c['poids_pct']}%)"
            if c.get("sous_criteres"):
                for sc in c["sous_criteres"]:
                    ligne += f"\n  - {sc['nom']} ({sc['poids_pct']}%)"
            criteres_lignes.append(ligne)
        criteres_bloc = f"""
Criteres d'attribution identifies :
{chr(10).join(criteres_lignes)}
"""

    # Infos entreprise attendues
    infos_attendues = """
Informations entreprise a verifier :
- Raison sociale : AI MENTOR (nom commercial : Almera)
- SIRET : 98900455100010
- NDA : 11757431975
- Forme juridique : SASU
- Adresse : 25 rue Campagne Premiere, 75014 Paris
- Representant : Mickael Bertolla, President
"""

    prompt = f"""Tu es un expert en marches publics francais. Tu dois relire un dossier de candidature
et identifier tous les problemes potentiels AVANT soumission.

=== APPEL D'OFFRES ===
Titre : {ao.get('titre', 'Non precise')}
Acheteur : {ao.get('acheteur', 'Non precise')}
Budget estime : {ao.get('budget_estime', 'Non precise')} EUR
Date limite : {ao.get('date_limite', 'Non precisee')}
{criteres_bloc}
{infos_attendues}

=== DOSSIER A RELIRE ===
{resume_dossier}

=== INSTRUCTIONS ===
Analyse le dossier et reponds UNIQUEMENT en JSON valide avec cette structure exacte :
{{
    "score_qualite": <nombre entre 0 et 100>,
    "conforme": <true/false>,
    "problemes": [
        {{"type": "<coherence_montants|criteres_manquants|contradiction|completude|info_entreprise>", "description": "<description du probleme>", "severite": "<critique|important|mineur>"}}
    ],
    "suggestions": [
        "<suggestion d'amelioration>"
    ],
    "resume": "<resume en 2-3 phrases de la qualite globale>"
}}

Verifie :
1. COHERENCE DES MONTANTS : les prix dans le BPU/DPGF sont-ils coherents avec le memoire technique ?
2. CRITERES D'ATTRIBUTION : tous les criteres sont-ils traites dans le memoire ?
3. CONTRADICTIONS : y a-t-il des informations contradictoires entre les pieces ?
4. COMPLETUDE : toutes les pieces essentielles sont-elles presentes (memoire, lettre, BPU, DC1/DC2) ?
5. INFORMATIONS ENTREPRISE : SIRET, NDA, representant legal sont-ils corrects partout ?

Sois strict mais juste. Ne signale que les vrais problemes."""

    try:
        reponse = _appel_claude_review(prompt, max_tokens=3000)

        # Parser le JSON de la reponse
        # Chercher le bloc JSON dans la reponse
        json_match = _extraire_json(reponse)
        if json_match:
            result = json.loads(json_match)
            # Valider la structure
            result.setdefault("score_qualite", 50)
            result.setdefault("problemes", [])
            result.setdefault("suggestions", [])
            result.setdefault("conforme", True)
            result.setdefault("resume", "Review effectuee")

            # S'assurer que score_qualite est un entier entre 0 et 100
            result["score_qualite"] = max(0, min(100, int(result["score_qualite"])))

            # Determiner conforme a partir des problemes critiques
            problemes_critiques = [p for p in result["problemes"]
                                   if isinstance(p, dict) and p.get("severite") == "critique"]
            if problemes_critiques:
                result["conforme"] = False

            logger.info(f"Auto-review: score={result['score_qualite']}/100, "
                        f"{len(result['problemes'])} probleme(s), conforme={result['conforme']}")
            return result
        else:
            logger.warning("Auto-review: impossible de parser le JSON de la reponse")
            return {
                "score_qualite": 50,
                "problemes": [{"type": "completude", "description": "Review non parseable", "severite": "mineur"}],
                "suggestions": ["Relire manuellement le dossier"],
                "conforme": True,
                "resume": "La review automatique n'a pas pu etre parsee correctement.",
                "reponse_brute": reponse[:2000],
            }

    except Exception as e:
        logger.error(f"Erreur auto-review: {e}")
        return {
            "score_qualite": 0,
            "problemes": [{"type": "completude", "description": f"Erreur lors de la review: {e}", "severite": "critique"}],
            "suggestions": ["Relire manuellement le dossier"],
            "conforme": False,
            "resume": f"Erreur lors de la review automatique: {e}",
        }


def _extraire_json(texte: str) -> str | None:
    """Extrait le premier bloc JSON valide d'un texte."""
    # Essayer de trouver un bloc JSON entre accolades
    depth = 0
    start = None
    for i, c in enumerate(texte):
        if c == '{':
            if depth == 0:
                start = i
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0 and start is not None:
                candidate = texte[start:i + 1]
                try:
                    json.loads(candidate)
                    return candidate
                except json.JSONDecodeError:
                    start = None
                    continue
    return None
