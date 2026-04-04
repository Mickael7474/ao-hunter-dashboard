"""
Scoring IA - Evaluation de l'adequation reelle AO/Almera via Claude.
Remplace le scoring par mots-cles pour les AO importants.
"""
import os
import json
import logging
import httpx

logger = logging.getLogger("ao_hunter.scoring_ia")

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODELE = "claude-sonnet-4-20250514"

PROFIL_ALMERA = """Almera (AI MENTOR, SASU) est un organisme de formation certifie Qualiopi, specialise en:
- Formation IA generative (ChatGPT, Claude, Copilot, Midjourney)
- Deploiement et gouvernance IA en entreprise
- Automatisation par l'IA (n8n, Make, Zapier)
- Consulting et accompagnement transformation IA
- Certification RS6776 France Competences (IA generative)
Clients: Lilly, Havas, Eiffage, Orange, Caisse des Depots, Carrefour, CCI
2000+ personnes formees, 50+ entreprises, note 4.9/5"""


def scorer_ao_ia(ao: dict) -> dict:
    """Score un AO via Claude. Retourne score 0-100 + justification courte."""
    if not API_KEY:
        return {"score": ao.get("score_pertinence", 0) * 100, "methode": "mots_cles", "justification": "API key non disponible"}

    titre = ao.get("titre", "")
    acheteur = ao.get("acheteur", "")
    description = ao.get("description", "")
    type_marche = ao.get("type_marche", "")
    budget = ao.get("budget_estime", "Non precise")

    prompt = f"""Evalue l'adequation de cet appel d'offres avec le profil Almera.

PROFIL ALMERA:
{PROFIL_ALMERA}

APPEL D'OFFRES:
- Titre: {titre}
- Acheteur: {acheteur}
- Description: {description}
- Type: {type_marche}
- Budget: {budget}

Reponds UNIQUEMENT en JSON (pas de markdown):
{{"score": <0-100>, "justification": "<1 phrase max>", "recommandation": "<GO|NO-GO|A_EVALUER>"}}"""

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": MODELE,
                    "max_tokens": 200,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            resp.raise_for_status()
            text = resp.json()["content"][0]["text"].strip()
            # Parse JSON
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            result = json.loads(text)
            result["methode"] = "ia"
            return result
    except Exception as e:
        logger.warning(f"Scoring IA echue pour {ao.get('id')}: {e}")
        return {
            "score": round(ao.get("score_pertinence", 0) * 100),
            "methode": "mots_cles_fallback",
            "justification": f"Fallback mots-cles (erreur IA: {str(e)[:50]})"
        }


def scorer_batch(aos: list, seuil_score: float = 0.5) -> list:
    """Score un batch d'AO. Ne score via IA que ceux au-dessus du seuil mots-cles."""
    resultats = []
    for ao in aos:
        if ao.get("score_pertinence", 0) >= seuil_score:
            result = scorer_ao_ia(ao)
            result["ao_id"] = ao.get("id")
            resultats.append(result)
    return resultats
