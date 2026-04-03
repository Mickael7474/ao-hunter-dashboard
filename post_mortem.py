"""
Post-mortem des AO perdus.
Analyse automatiquement les causes de defaite et capitalise les lecons.
"""

import os
import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("ao_hunter.post_mortem")

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODELE_HAIKU = "claude-haiku-3-5-20251001"

DASHBOARD_DIR = Path(__file__).parent
POST_MORTEM_FILE = DASHBOARD_DIR / "post_mortem.json"
DOSSIERS_GENERES_DIR = DASHBOARD_DIR / "dossiers_generes"
RESULTATS_DIR = DASHBOARD_DIR.parent / "resultats"


def _charger_post_mortem() -> list[dict]:
    """Charge l'historique post-mortem."""
    if not POST_MORTEM_FILE.exists():
        return []
    try:
        return json.loads(POST_MORTEM_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _sauvegarder_post_mortem(data: list[dict]):
    """Sauvegarde l'historique post-mortem."""
    POST_MORTEM_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _appel_claude(prompt: str, max_tokens: int = 2000) -> str:
    """Appelle Claude Haiku via httpx."""
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


def _extraire_json(texte: str) -> str | None:
    """Extrait le premier bloc JSON valide d'un texte."""
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


def _lire_dossier_genere(ao: dict) -> str:
    """Lit le contenu du dossier genere pour un AO (memoire technique + BPU)."""
    ao_id = ao.get("id", "")
    clean_id = ao_id.replace("BOAMP-", "").replace("PLACE-", "").replace("TED-", "").replace("MSEC-", "").replace("AWS-", "")

    contenu = ""
    for base in [RESULTATS_DIR, DOSSIERS_GENERES_DIR]:
        if not base.exists():
            continue
        for d in base.iterdir():
            if d.is_dir() and clean_id in d.name:
                # Lire les fichiers cles
                for fichier_cle in ["memoire_technique.md", "bpu_dpgf.md", "analyse_go_no_go.md"]:
                    f = d / fichier_cle
                    if f.exists():
                        texte = f.read_text(encoding="utf-8", errors="ignore")
                        # Tronquer a 2000 chars par fichier
                        contenu += f"\n--- {fichier_cle} ---\n{texte[:2000]}\n"
                if contenu:
                    return contenu
    return contenu


def analyser_defaite(ao: dict, dossier_path: str = None) -> dict:
    """Analyse les causes de defaite d'un AO perdu.

    Args:
        ao: dict de l'appel d'offres (avec attribution_titulaire, attribution_montant)
        dossier_path: chemin vers le dossier genere (optionnel)

    Returns:
        dict avec causes_probables, prix_ecart_pct, lecons, recommandations, score_competitivite
    """
    # Verifier si deja analyse
    historique = _charger_post_mortem()
    existant = next((pm for pm in historique if pm.get("ao_id") == ao.get("id")), None)
    if existant:
        return existant

    # Infos d'attribution
    titulaire = ao.get("attribution_titulaire", "Inconnu")
    montant_gagnant = ao.get("attribution_montant")
    budget_estime = ao.get("budget_estime")

    # Ecart de prix
    prix_ecart_pct = None
    if montant_gagnant and budget_estime:
        try:
            prix_ecart_pct = round(((budget_estime - montant_gagnant) / montant_gagnant) * 100, 1)
        except (TypeError, ZeroDivisionError):
            pass

    # Lire le dossier genere
    contenu_dossier = ""
    if dossier_path:
        try:
            p = Path(dossier_path)
            if p.exists():
                for f in p.glob("*.md"):
                    texte = f.read_text(encoding="utf-8", errors="ignore")
                    contenu_dossier += f"\n--- {f.name} ---\n{texte[:2000]}\n"
        except Exception:
            pass
    if not contenu_dossier:
        contenu_dossier = _lire_dossier_genere(ao)

    # Si pas d'API key, retourner une analyse basique
    if not API_KEY:
        result = _analyse_basique(ao, prix_ecart_pct)
        _sauvegarder_dans_historique(historique, ao, result)
        return result

    # Construire le prompt
    bloc_dossier = ""
    if contenu_dossier:
        if len(contenu_dossier) > 8000:
            contenu_dossier = contenu_dossier[:8000] + "\n[... tronque ...]"
        bloc_dossier = f"""
=== NOTRE DOSSIER (extraits) ===
{contenu_dossier}
"""

    bloc_prix = ""
    if prix_ecart_pct is not None:
        bloc_prix = f"\nEcart de prix : notre estimation {budget_estime} EUR vs gagnant {montant_gagnant} EUR (ecart {prix_ecart_pct}%)"

    prompt = f"""Tu es un expert en marches publics francais. Analyse pourquoi nous avons perdu cet appel d'offres.

=== APPEL D'OFFRES ===
Titre : {ao.get('titre', 'Non precise')}
Acheteur : {ao.get('acheteur', 'Non precise')}
Type : {ao.get('type_marche', 'Non precise')}
Budget estime : {budget_estime or 'Non precise'} EUR
Criteres : {ao.get('criteres_attribution', 'Non precises')}

=== RESULTAT ===
Titulaire retenu : {titulaire}
Montant attribue : {montant_gagnant or 'Non communique'} EUR
{bloc_prix}

=== NOTRE PROFIL ===
Almera (AI MENTOR) - Organisme de formation IA certifie Qualiopi
Specialite : formation Intelligence Artificielle, transformation numerique
50+ entreprises accompagnees, 2000+ personnes formees
{bloc_dossier}

=== INSTRUCTIONS ===
Analyse les causes probables de notre defaite et reponds UNIQUEMENT en JSON valide :
{{
    "causes_probables": ["cause 1 (la plus probable)", "cause 2", ...],
    "prix_ecart_pct": {prix_ecart_pct if prix_ecart_pct is not None else "null"},
    "lecons": ["lecon 1", "lecon 2", ...],
    "recommandations": ["action concrete 1", "action concrete 2", ...],
    "score_competitivite": <0-100>,
    "analyse_resume": "Resume en 2-3 phrases"
}}

Causes possibles : prix trop eleve, references insuffisantes, hors coeur de metier,
memoire technique trop generique, manque de certifications specifiques, concurrent sortant avantage,
offre technique faible, non-conformite administrative.
Sois precis et base sur les donnees disponibles."""

    try:
        reponse = _appel_claude(prompt, max_tokens=2000)
        json_str = _extraire_json(reponse)

        if json_str:
            result = json.loads(json_str)
            result.setdefault("causes_probables", ["Analyse non disponible"])
            result.setdefault("lecons", [])
            result.setdefault("recommandations", [])
            result.setdefault("score_competitivite", 50)
            result.setdefault("prix_ecart_pct", prix_ecart_pct)
            result.setdefault("analyse_resume", "")
            result["score_competitivite"] = max(0, min(100, int(result["score_competitivite"])))
        else:
            result = _analyse_basique(ao, prix_ecart_pct)

    except Exception as e:
        logger.error(f"Erreur analyse post-mortem: {e}")
        result = _analyse_basique(ao, prix_ecart_pct)

    _sauvegarder_dans_historique(historique, ao, result)
    return result


def _analyse_basique(ao: dict, prix_ecart_pct: float | None) -> dict:
    """Analyse basique sans appel IA."""
    causes = []
    if prix_ecart_pct is not None and prix_ecart_pct > 10:
        causes.append("Prix probablement trop eleve")
    if prix_ecart_pct is not None and prix_ecart_pct < -10:
        causes.append("Prix anormalement bas du gagnant")
    if not causes:
        causes.append("Donnees insuffisantes pour determiner la cause")

    return {
        "causes_probables": causes,
        "prix_ecart_pct": prix_ecart_pct,
        "lecons": ["Verifier le positionnement tarifaire sur ce type de marche"],
        "recommandations": ["Analyser les resultats d'attribution pour ajuster les prochaines offres"],
        "score_competitivite": 40,
        "analyse_resume": "Analyse basique - API non disponible.",
    }


def _sauvegarder_dans_historique(historique: list[dict], ao: dict, result: dict):
    """Sauvegarde le resultat dans l'historique post-mortem."""
    entry = {
        "ao_id": ao.get("id"),
        "ao_titre": ao.get("titre", ""),
        "acheteur": ao.get("acheteur", ""),
        "date": datetime.now().strftime("%Y-%m-%d"),
        "titulaire": ao.get("attribution_titulaire", ""),
        "montant_gagnant": ao.get("attribution_montant"),
        **result,
    }
    historique.append(entry)
    _sauvegarder_post_mortem(historique)


def stats_post_mortem() -> dict:
    """Agregge les statistiques des causes de defaite.

    Returns:
        dict avec causes_frequentes, score_moyen, nb_analyses, recommandations_top
    """
    historique = _charger_post_mortem()

    if not historique:
        return {
            "nb_analyses": 0,
            "causes_frequentes": [],
            "score_moyen": 0,
            "recommandations_top": [],
            "ecart_prix_moyen": None,
        }

    # Compter les causes
    causes_count = {}
    scores = []
    ecarts = []
    recommandations_count = {}

    for pm in historique:
        for cause in pm.get("causes_probables", []):
            causes_count[cause] = causes_count.get(cause, 0) + 1
        score = pm.get("score_competitivite")
        if score is not None:
            scores.append(score)
        ecart = pm.get("prix_ecart_pct")
        if ecart is not None:
            ecarts.append(ecart)
        for reco in pm.get("recommandations", []):
            recommandations_count[reco] = recommandations_count.get(reco, 0) + 1

    causes_triees = sorted(causes_count.items(), key=lambda x: x[1], reverse=True)
    reco_triees = sorted(recommandations_count.items(), key=lambda x: x[1], reverse=True)

    return {
        "nb_analyses": len(historique),
        "causes_frequentes": [{"cause": c, "count": n} for c, n in causes_triees[:10]],
        "score_moyen": round(sum(scores) / len(scores), 1) if scores else 0,
        "ecart_prix_moyen": round(sum(ecarts) / len(ecarts), 1) if ecarts else None,
        "recommandations_top": [{"recommandation": r, "count": n} for r, n in reco_triees[:5]],
    }


def appliquer_lecons(ao_nouveau: dict) -> list[str]:
    """Pour un nouvel AO, cherche dans l'historique des lecons applicables.

    Args:
        ao_nouveau: dict du nouvel appel d'offres

    Returns:
        Liste de lecons/recommandations pertinentes
    """
    historique = _charger_post_mortem()
    if not historique:
        return []

    titre_new = (ao_nouveau.get("titre", "") + " " + ao_nouveau.get("description", "")).lower()
    acheteur_new = (ao_nouveau.get("acheteur", "")).lower()
    type_new = (ao_nouveau.get("type_marche", "")).lower()

    lecons_pertinentes = []
    seen = set()

    for pm in historique:
        # Verifier la pertinence par acheteur, type ou mots-cles communs
        pertinent = False

        # Meme acheteur
        if acheteur_new and pm.get("acheteur", "").lower() and \
           (acheteur_new[:15] in pm.get("acheteur", "").lower() or
            pm.get("acheteur", "").lower()[:15] in acheteur_new):
            pertinent = True

        # Mots-cles communs dans le titre
        mots_pm = set(m.lower() for m in pm.get("ao_titre", "").split() if len(m) > 4)
        mots_new = set(m.lower() for m in titre_new.split() if len(m) > 4)
        if mots_pm and mots_new and len(mots_pm & mots_new) >= 2:
            pertinent = True

        if pertinent:
            for lecon in pm.get("lecons", []) + pm.get("recommandations", []):
                if lecon not in seen:
                    seen.add(lecon)
                    lecons_pertinentes.append(lecon)

    return lecons_pertinentes[:10]
