"""
Resume quotidien - Synthese journaliere des AO et actions recommandees.

Genere un resume structure pour le dirigeant et l'alternant :
- Nouveaux AO detectes
- Deadlines imminentes
- Dossiers a generer/relire
- KPIs du jour
- Actions prioritaires
"""

import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
from collections import Counter

logger = logging.getLogger("ao_hunter.resume_quotidien")

DASHBOARD_DIR = Path(__file__).parent


def _charger_json(nom: str) -> list | dict:
    """Charge un fichier JSON du dashboard."""
    path = DASHBOARD_DIR / nom
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return [] if nom.endswith('.json') else {}
    return [] if '.' not in nom or nom.endswith('.json') else {}


def generer_resume(date: datetime = None) -> dict:
    """Genere le resume du jour.

    Returns:
        {
            'date': str,
            'resume_texte': str,  # Version texte lisible
            'kpis': {total_ao, score_moyen, nb_dossiers, nb_urgents, win_rate},
            'nouveaux_ao': [liste des AO detectes dans les 24h],
            'deadlines_imminentes': [AO avec deadline < 7j],
            'actions_prioritaires': [actions recommandees],
            'dossiers_a_relire': [dossiers en attente de validation],
            'pipeline': {nb_analyse, nb_candidature, nb_soumis},
            'opportunites': [top 5 AO par score],
        }
    """
    date = date or datetime.now()
    date_str = date.strftime('%A %d %B %Y')

    aos = _charger_json("ao_pertinents.json")
    if not isinstance(aos, list):
        aos = []

    dossiers_index = _charger_json("dossiers_index.json")
    if not isinstance(dossiers_index, list):
        if isinstance(dossiers_index, dict):
            dossiers_index = list(dossiers_index.values()) if dossiers_index else []
        else:
            dossiers_index = []

    # --- KPIs ---
    total_ao = len(aos)
    scores = [ao.get('score_pertinence', 0) or 0 for ao in aos]
    score_moyen = round(sum(scores) / len(scores) * 100) if scores else 0

    nb_dossiers = len(dossiers_index) if isinstance(dossiers_index, list) else 0

    # Compter par statut
    statuts = Counter(ao.get('statut', 'nouveau') for ao in aos)
    nb_gagnes = statuts.get('gagne', 0)
    nb_perdus = statuts.get('perdu', 0)
    total_decides = nb_gagnes + nb_perdus
    win_rate = round(nb_gagnes / total_decides * 100) if total_decides > 0 else 0

    # --- Nouveaux AO (24h) ---
    hier = date - timedelta(hours=24)
    nouveaux = []
    for ao in aos:
        date_detect = ao.get('date_detection') or ao.get('date_publication', '')
        if date_detect:
            try:
                dt = datetime.fromisoformat(str(date_detect).replace('Z', '+00:00')).replace(tzinfo=None)
                if dt >= hier:
                    nouveaux.append(ao)
            except Exception:
                pass

    # --- Deadlines imminentes ---
    urgents = []
    for ao in aos:
        dl = ao.get('date_limite', '')
        if dl:
            try:
                dt = datetime.fromisoformat(str(dl).replace('Z', '+00:00')).replace(tzinfo=None)
                jours = (dt - date).days
                if 0 <= jours <= 7:
                    ao_copy = dict(ao)
                    ao_copy['jours_restants'] = jours
                    urgents.append(ao_copy)
            except Exception:
                pass
    urgents.sort(key=lambda x: x.get('jours_restants', 99))

    # --- Opportunites (top 5) ---
    ao_actifs = [ao for ao in aos if ao.get('statut', 'nouveau') not in ('gagne', 'perdu', 'ignore', 'abandon')]
    ao_tries = sorted(ao_actifs, key=lambda x: x.get('score_pertinence', 0) or 0, reverse=True)
    opportunites = ao_tries[:5]

    # --- Actions prioritaires ---
    actions = _generer_actions_prioritaires(aos, urgents, nouveaux, dossiers_index, statuts)

    # --- Pipeline ---
    pipeline = {
        'nouveau': statuts.get('nouveau', 0),
        'analyse': statuts.get('analyse', 0),
        'candidature': statuts.get('candidature', 0),
        'soumis': statuts.get('soumis', 0),
        'gagne': nb_gagnes,
        'perdu': nb_perdus,
    }

    # --- Resume texte ---
    resume_texte = _formater_resume_texte(
        date_str, total_ao, score_moyen, nb_dossiers, win_rate,
        nouveaux, urgents, actions, opportunites, pipeline
    )

    return {
        'date': date_str,
        'resume_texte': resume_texte,
        'kpis': {
            'total_ao': total_ao,
            'score_moyen': score_moyen,
            'nb_dossiers': nb_dossiers,
            'nb_urgents': len(urgents),
            'win_rate': win_rate,
            'nb_gagnes': nb_gagnes,
            'nb_perdus': nb_perdus,
        },
        'nouveaux_ao': [{'id': ao.get('id'), 'titre': ao.get('titre', '')[:80], 'acheteur': ao.get('acheteur', ''), 'score': round((ao.get('score_pertinence', 0) or 0) * 100)} for ao in nouveaux],
        'deadlines_imminentes': [{'id': ao.get('id'), 'titre': ao.get('titre', '')[:80], 'jours': ao.get('jours_restants', '?'), 'acheteur': ao.get('acheteur', '')} for ao in urgents],
        'actions_prioritaires': actions,
        'opportunites': [{'id': ao.get('id'), 'titre': ao.get('titre', '')[:80], 'score': round((ao.get('score_pertinence', 0) or 0) * 100), 'acheteur': ao.get('acheteur', '')} for ao in opportunites],
        'pipeline': pipeline,
    }


def _generer_actions_prioritaires(aos, urgents, nouveaux, dossiers, statuts) -> list:
    """Genere la liste des actions prioritaires."""
    actions = []

    # Urgences deadline
    for ao in urgents[:3]:
        j = ao.get('jours_restants', 0)
        statut = ao.get('statut', 'nouveau')
        if j == 0:
            actions.append({
                'priorite': 'CRITIQUE',
                'action': f"DEADLINE AUJOURD'HUI: {ao.get('titre', '')[:50]}",
                'ao_id': ao.get('id'),
                'type': 'deadline',
            })
        elif j <= 3:
            if statut in ('nouveau', 'analyse'):
                actions.append({
                    'priorite': 'HAUTE',
                    'action': f"J-{j} sans dossier: {ao.get('titre', '')[:50]} - Generer ou abandonner",
                    'ao_id': ao.get('id'),
                    'type': 'deadline',
                })
            elif statut == 'candidature':
                actions.append({
                    'priorite': 'HAUTE',
                    'action': f"J-{j} dossier a finaliser: {ao.get('titre', '')[:50]}",
                    'ao_id': ao.get('id'),
                    'type': 'relecture',
                })

    # Nouveaux AO a scorer
    high_score = [ao for ao in nouveaux if (ao.get('score_pertinence', 0) or 0) >= 0.7]
    if high_score:
        actions.append({
            'priorite': 'MOYENNE',
            'action': f"{len(high_score)} nouvel(s) AO score > 70% a analyser",
            'type': 'analyse',
        })

    # Dossiers a relire
    en_candidature = [ao for ao in aos if ao.get('statut') == 'candidature']
    if en_candidature:
        actions.append({
            'priorite': 'MOYENNE',
            'action': f"{len(en_candidature)} dossier(s) en candidature a finaliser et soumettre",
            'type': 'relecture',
        })

    # Veille
    nb_nouveau = statuts.get('nouveau', 0)
    if nb_nouveau > 20:
        actions.append({
            'priorite': 'BASSE',
            'action': f"{nb_nouveau} AO en statut 'nouveau' a trier (analyser ou ignorer)",
            'type': 'tri',
        })

    # Resultats a renseigner
    soumis = [ao for ao in aos if ao.get('statut') == 'soumis']
    if soumis:
        actions.append({
            'priorite': 'BASSE',
            'action': f"{len(soumis)} AO soumis en attente de resultat - Penser a verifier",
            'type': 'suivi',
        })

    # Tri par priorite
    ordre = {'CRITIQUE': 0, 'HAUTE': 1, 'MOYENNE': 2, 'BASSE': 3}
    actions.sort(key=lambda x: ordre.get(x.get('priorite', 'BASSE'), 4))

    return actions


def _formater_resume_texte(date_str, total_ao, score_moyen, nb_dossiers, win_rate,
                           nouveaux, urgents, actions, opportunites, pipeline) -> str:
    """Formate le resume en texte lisible."""

    lignes = [
        f"# Resume AO Hunter - {date_str}",
        "",
        "## KPIs",
        f"- {total_ao} AO dans le pipeline",
        f"- Score moyen de pertinence: {score_moyen}%",
        f"- {nb_dossiers} dossiers generes",
        f"- Win rate: {win_rate}%",
        "",
    ]

    # Pipeline
    lignes.append("## Pipeline")
    for statut, nb in pipeline.items():
        if nb > 0:
            lignes.append(f"- {statut.capitalize()}: {nb}")
    lignes.append("")

    # Nouveaux
    if nouveaux:
        lignes.append(f"## {len(nouveaux)} nouveau(x) AO (24h)")
        for ao in nouveaux[:5]:
            score = round((ao.get('score_pertinence', 0) or 0) * 100)
            lignes.append(f"- [{score}%] {ao.get('titre', '')[:60]} ({ao.get('acheteur', '')})")
        if len(nouveaux) > 5:
            lignes.append(f"  ... et {len(nouveaux) - 5} autres")
        lignes.append("")

    # Urgents
    if urgents:
        lignes.append(f"## {len(urgents)} deadline(s) imminente(s)")
        for ao in urgents[:5]:
            j = ao.get('jours_restants', '?')
            label = "AUJOURD'HUI" if j == 0 else f"J-{j}"
            lignes.append(f"- {label}: {ao.get('titre', '')[:60]}")
        lignes.append("")

    # Actions
    if actions:
        lignes.append("## Actions prioritaires")
        for a in actions[:8]:
            emoji = {'CRITIQUE': '🔴', 'HAUTE': '🟠', 'MOYENNE': '🟡', 'BASSE': '🟢'}.get(a.get('priorite', ''), '⚪')
            lignes.append(f"- [{a.get('priorite', '')}] {a.get('action', '')}")
        lignes.append("")

    # Opportunites
    if opportunites:
        lignes.append("## Top opportunites")
        for ao in opportunites:
            score = round((ao.get('score_pertinence', 0) or 0) * 100)
            lignes.append(f"- [{score}%] {ao.get('titre', '')[:60]}")
        lignes.append("")

    lignes.append(f"---\n*Genere automatiquement le {datetime.now().strftime('%d/%m/%Y a %H:%M')}*")

    return "\n".join(lignes)


def generer_email_resume(destinataire: str = "contact@almera.one") -> dict:
    """Genere un brouillon email avec le resume.

    Returns:
        {
            'sujet': str,
            'corps': str,
            'destinataire': str,
        }
    """
    resume = generer_resume()

    # Sujet
    nb_urgents = resume['kpis']['nb_urgents']
    nb_nouveaux = len(resume['nouveaux_ao'])
    sujet = f"[AO Hunter] Resume du {datetime.now().strftime('%d/%m')} - "
    if nb_urgents > 0:
        sujet += f"{nb_urgents} urgence(s)"
    elif nb_nouveaux > 0:
        sujet += f"{nb_nouveaux} nouveau(x) AO"
    else:
        sujet += "Pipeline sous controle"

    corps = resume['resume_texte']

    # Version HTML simplifiee
    corps_html = corps.replace('\n', '<br>')
    corps_html = corps_html.replace('# ', '<h2>').replace('## ', '<h3>')

    return {
        'sujet': sujet,
        'corps': corps,
        'corps_html': corps_html,
        'destinataire': destinataire,
        'resume': resume,
    }
