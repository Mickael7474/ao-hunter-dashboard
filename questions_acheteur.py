"""
Generation de questions a l'acheteur - Analyse le DCE pour identifier les ambiguites.
Utilise Claude Haiku via httpx (compatible Render).
"""

import os
import json
import logging
from datetime import datetime, timedelta

logger = logging.getLogger("ao_hunter.questions")

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODELE_HAIKU = "claude-sonnet-4-20250514"  # Haiku non dispo, fallback Sonnet


def _appel_claude_haiku(prompt: str, max_tokens: int = 3000) -> str:
    """Appelle Claude Haiku via httpx et retourne le texte."""
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
                "model": MODELE_HAIKU,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
        data = resp.json()

    return data["content"][0]["text"]


def generer_questions(ao: dict, dce_texte: str = "", rc_info: dict = None) -> dict:
    """
    Analyse le DCE/RC pour identifier les zones d'ambiguite et generer des questions.

    Args:
        ao: dict de l'appel d'offres
        dce_texte: texte extrait du DCE (optionnel)
        rc_info: infos du reglement de consultation (optionnel)

    Returns:
        dict avec: questions (list), nb_critiques, nb_importantes, deadline_questions
    """
    if not API_KEY:
        return {
            "questions": [],
            "nb_critiques": 0,
            "nb_importantes": 0,
            "deadline_questions": None,
            "erreur": "ANTHROPIC_API_KEY non configuree",
        }

    # Construire le contexte
    titre = ao.get("titre", "")
    acheteur = ao.get("acheteur", "")
    description = ao.get("description", "")
    criteres = ao.get("criteres_attribution", "")
    budget = ao.get("budget_estime", "")
    date_limite = ao.get("date_limite", "")
    type_marche = ao.get("type_marche", "")
    procedure = ao.get("type_procedure", "")
    duree = ao.get("duree_mois", "")

    rc_texte = ""
    if rc_info:
        rc_texte = f"""
Reglement de consultation :
- Criteres : {rc_info.get('criteres', 'Non precises')}
- Ponderation : {rc_info.get('ponderation', 'Non precisee')}
- Variantes : {rc_info.get('variantes', 'Non precise')}
- Tranches : {rc_info.get('tranches', 'Non precise')}
"""

    prompt = f"""Tu es un expert en marches publics francais. Analyse cette consultation et identifie les zones d'ambiguite, informations manquantes ou points a clarifier.

CONSULTATION :
- Titre : {titre}
- Acheteur : {acheteur}
- Type de marche : {type_marche}
- Procedure : {procedure}
- Budget estime : {budget if budget else 'Non precise'}
- Date limite : {date_limite}
- Duree : {duree if duree else 'Non precisee'} mois
- Criteres d'attribution : {criteres if criteres else 'Non precises'}

Description :
{description[:3000] if description else 'Non disponible'}

{f'Texte DCE :{chr(10)}{dce_texte[:4000]}' if dce_texte else ''}
{rc_texte}

CONTEXTE CANDIDAT : Almera est un organisme de formation certifie Qualiopi, specialise en IA et transformation numerique. Nous devons comprendre precisement le besoin pour optimiser notre reponse.

Genere des questions pertinentes a poser a l'acheteur via la plateforme. Pour chaque question, precise :
1. La question exacte (formulee poliment pour un marche public)
2. La justification (pourquoi cette question est importante)
3. L'impact : "critique" (bloque la reponse), "important" (affecte significativement la qualite), ou "utile" (optimise la reponse)
4. La categorie : "budget", "technique", "administratif", ou "planning"

IMPORTANT :
- Ne pose PAS de questions dont la reponse est evidente dans le DCE
- Concentre-toi sur ce qui impacte vraiment la qualite de la reponse
- Maximum 8 questions, priorisees par impact
- Les questions doivent etre specifiques, pas generiques

Reponds UNIQUEMENT en JSON valide, format :
{{
    "questions": [
        {{
            "question": "texte de la question",
            "justification": "pourquoi cette question",
            "impact": "critique|important|utile",
            "categorie": "budget|technique|administratif|planning"
        }}
    ]
}}"""

    try:
        reponse = _appel_claude_haiku(prompt, max_tokens=3000)

        # Parser le JSON de la reponse
        # Nettoyer la reponse si elle contient du texte autour du JSON
        json_start = reponse.find("{")
        json_end = reponse.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            json_str = reponse[json_start:json_end]
            data = json.loads(json_str)
        else:
            data = {"questions": []}

        questions = data.get("questions", [])

        # Valider et nettoyer les questions
        valid_questions = []
        for q in questions:
            if not isinstance(q, dict) or "question" not in q:
                continue
            q.setdefault("justification", "")
            q.setdefault("impact", "utile")
            q.setdefault("categorie", "technique")
            # Normaliser
            if q["impact"] not in ("critique", "important", "utile"):
                q["impact"] = "utile"
            if q["categorie"] not in ("budget", "technique", "administratif", "planning"):
                q["categorie"] = "technique"
            valid_questions.append(q)

        # Trier par impact (critique > important > utile)
        impact_order = {"critique": 0, "important": 1, "utile": 2}
        valid_questions.sort(key=lambda q: impact_order.get(q["impact"], 2))

        # Calculer la deadline questions (generalement J-10 avant deadline AO)
        deadline_questions = None
        if date_limite:
            try:
                dl = datetime.fromisoformat(date_limite.split("T")[0])
                deadline_q = dl - timedelta(days=10)
                deadline_questions = deadline_q.strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                pass

        nb_critiques = sum(1 for q in valid_questions if q["impact"] == "critique")
        nb_importantes = sum(1 for q in valid_questions if q["impact"] == "important")

        return {
            "questions": valid_questions,
            "nb_critiques": nb_critiques,
            "nb_importantes": nb_importantes,
            "deadline_questions": deadline_questions,
        }

    except json.JSONDecodeError as e:
        logger.error(f"Erreur parsing JSON questions: {e}")
        return {
            "questions": [],
            "nb_critiques": 0,
            "nb_importantes": 0,
            "deadline_questions": None,
            "erreur": f"Erreur parsing reponse IA: {e}",
        }
    except Exception as e:
        logger.error(f"Erreur generation questions: {e}")
        return {
            "questions": [],
            "nb_critiques": 0,
            "nb_importantes": 0,
            "deadline_questions": None,
            "erreur": str(e),
        }


def formater_questions_email(questions: list, ao: dict) -> str:
    """
    Formate les questions en email poli pret a copier-coller pour la plateforme.

    Args:
        questions: liste de questions (dicts avec question, justification, impact, categorie)
        ao: dict de l'AO

    Returns:
        str: texte de l'email formate
    """
    titre = ao.get("titre", "la consultation")
    acheteur = ao.get("acheteur", "")
    ref = ao.get("id", "").replace("BOAMP-", "").replace("PLACE-", "").replace("TED-", "").replace("MSEC-", "").replace("AWS-", "")

    # Grouper par categorie
    par_categorie = {}
    for q in questions:
        cat = q.get("categorie", "technique")
        if cat not in par_categorie:
            par_categorie[cat] = []
        par_categorie[cat].append(q)

    cat_labels = {
        "budget": "Questions budgetaires",
        "technique": "Questions techniques",
        "administratif": "Questions administratives",
        "planning": "Questions sur le planning",
    }

    lignes = []
    lignes.append(f"Objet : Demande de precisions - {titre[:80]}")
    lignes.append("")
    lignes.append("Madame, Monsieur,")
    lignes.append("")
    lignes.append(f"Dans le cadre de notre reponse a la consultation \"{titre[:100]}\"{f' (ref. {ref})' if ref else ''}, nous souhaiterions obtenir les precisions suivantes afin de vous proposer une offre la plus adaptee possible :")
    lignes.append("")

    num = 1
    for cat_id in ["technique", "budget", "planning", "administratif"]:
        if cat_id not in par_categorie:
            continue
        lignes.append(f"--- {cat_labels.get(cat_id, cat_id)} ---")
        lignes.append("")
        for q in par_categorie[cat_id]:
            lignes.append(f"{num}. {q['question']}")
            num += 1
        lignes.append("")

    lignes.append("Nous vous remercions par avance pour ces precisions qui nous permettront de formuler une offre de qualite.")
    lignes.append("")
    lignes.append("Cordialement,")
    lignes.append("")
    lignes.append("Mickael Bertolla")
    lignes.append("President - Almera (AI MENTOR)")
    lignes.append("25 rue Campagne Premiere, 75014 Paris")
    lignes.append("Tel : 06 86 68 06 11")
    lignes.append("Email : contact@almera.one")

    return "\n".join(lignes)
