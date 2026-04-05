"""
Win/Loss Tracker - Suivi des resultats AO pour amelioration continue.

Permet de :
- Logger les resultats (gagne/perdu/abandon)
- Analyser les facteurs de succes/echec
- Calculer le win rate par type d'acheteur, budget, secteur
- Fournir des recommandations basees sur l'historique
"""

import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

logger = logging.getLogger("ao_hunter.win_loss_tracker")

DASHBOARD_DIR = Path(__file__).parent
TRACKER_FILE = DASHBOARD_DIR / "win_loss_tracker.json"


def _charger_tracker() -> list:
    """Charge l'historique des resultats."""
    if TRACKER_FILE.exists():
        try:
            return json.loads(TRACKER_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _sauver_tracker(data: list):
    """Sauvegarde l'historique."""
    TRACKER_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def enregistrer_resultat(ao_id: str, resultat: str, details: dict = None) -> dict:
    """Enregistre le resultat d'un AO.

    Args:
        ao_id: ID de l'AO
        resultat: 'gagne', 'perdu', 'abandon', 'sans_suite'
        details: {
            'raison_principale': str,  # ex: "prix trop eleve", "memoire technique faible"
            'raisons_secondaires': list[str],
            'montant_final': float,
            'concurrent_gagnant': str,  # si perdu
            'feedback_acheteur': str,
            'points_forts': list[str],
            'points_ameliorer': list[str],
            'note_satisfaction': float,  # 1-5 si gagne
        }
    """
    tracker = _charger_tracker()
    details = details or {}

    # Charger les infos de l'AO
    ao_data = _charger_ao(ao_id)

    entry = {
        'ao_id': ao_id,
        'resultat': resultat,
        'date_enregistrement': datetime.now().isoformat(),
        'titre': ao_data.get('titre', '') if ao_data else '',
        'acheteur': ao_data.get('acheteur', '') if ao_data else '',
        'budget_estime': ao_data.get('budget_estime', 0) if ao_data else 0,
        'score_pertinence': ao_data.get('score_pertinence', 0) if ao_data else 0,
        'source': ao_data.get('source', '') if ao_data else '',
        'region': ao_data.get('region', '') if ao_data else '',
        'raison_principale': details.get('raison_principale', ''),
        'raisons_secondaires': details.get('raisons_secondaires', []),
        'montant_final': details.get('montant_final', 0),
        'concurrent_gagnant': details.get('concurrent_gagnant', ''),
        'feedback_acheteur': details.get('feedback_acheteur', ''),
        'points_forts': details.get('points_forts', []),
        'points_ameliorer': details.get('points_ameliorer', []),
        'note_satisfaction': details.get('note_satisfaction', 0),
    }

    # Mettre a jour si deja enregistre
    existing = next((i for i, e in enumerate(tracker) if e['ao_id'] == ao_id), None)
    if existing is not None:
        tracker[existing] = entry
    else:
        tracker.append(entry)

    _sauver_tracker(tracker)
    logger.info(f"Resultat enregistre: AO {ao_id} = {resultat}")

    # Mettre a jour le statut dans ao_pertinents.json
    if ao_data:
        _mettre_a_jour_statut_ao(ao_id, resultat)

    return entry


def _charger_ao(ao_id: str) -> dict:
    """Charge les infos d'un AO."""
    ao_file = DASHBOARD_DIR / "ao_pertinents.json"
    if ao_file.exists():
        try:
            aos = json.loads(ao_file.read_text(encoding="utf-8"))
            for ao in aos:
                if ao.get('id') == ao_id:
                    return ao
        except Exception:
            pass
    return {}


def _mettre_a_jour_statut_ao(ao_id: str, resultat: str):
    """Met a jour le statut dans ao_pertinents.json."""
    ao_file = DASHBOARD_DIR / "ao_pertinents.json"
    if not ao_file.exists():
        return
    try:
        aos = json.loads(ao_file.read_text(encoding="utf-8"))
        for ao in aos:
            if ao.get('id') == ao_id:
                ao['statut'] = resultat
                break
        ao_file.write_text(json.dumps(aos, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Erreur mise a jour statut: {e}")


def _type_acheteur(acheteur: str) -> str:
    """Determine le type d'acheteur."""
    acheteur_lower = (acheteur or '').lower()
    mots_public = ['ministere', 'region', 'departement', 'commune', 'mairie',
                   'collectivite', 'cnrs', 'universite', 'inserm', 'chu', 'hopital',
                   'crous', 'rectorat', 'prefecture', 'agence', 'etablissement',
                   'syndicat', 'communaute', 'metropole', 'conseil']
    mots_education = ['universite', 'ecole', 'lycee', 'college', 'crous', 'rectorat',
                      'education', 'enseignement', 'campus', 'iut']

    if any(m in acheteur_lower for m in mots_education):
        return 'education'
    elif any(m in acheteur_lower for m in mots_public):
        return 'public'
    return 'prive'


def _tranche_budget(budget: float) -> str:
    """Determine la tranche de budget."""
    if not budget or budget <= 0:
        return 'inconnu'
    if budget < 10000:
        return '<10k'
    elif budget < 30000:
        return '10-30k'
    elif budget < 70000:
        return '30-70k'
    elif budget < 150000:
        return '70-150k'
    return '>150k'


def analyser_performances(periode_mois: int = 12) -> dict:
    """Analyse les performances sur une periode donnee.

    Returns:
        {
            'global': {win_rate, nb_gagnes, nb_perdus, nb_total, ca_gagne, ca_moyen},
            'par_type_acheteur': {type: {win_rate, nb_gagnes, nb_perdus}},
            'par_tranche_budget': {tranche: {win_rate, nb_gagnes, nb_perdus}},
            'par_source': {source: {win_rate, nb_gagnes, nb_perdus}},
            'facteurs_succes': [{'facteur': str, 'frequence': int}],
            'facteurs_echec': [{'facteur': str, 'frequence': int}],
            'concurrents_frequents': [{'nom': str, 'nb_fois': int}],
            'tendance': {'mois': [str], 'win_rates': [float]},
            'recommandations': [str],
        }
    """
    tracker = _charger_tracker()

    # Filtrer par periode
    date_min = datetime.now() - timedelta(days=periode_mois * 30)
    entries = []
    for e in tracker:
        try:
            d = datetime.fromisoformat(e.get('date_enregistrement', ''))
            if d >= date_min:
                entries.append(e)
        except Exception:
            entries.append(e)  # Inclure si date invalide

    # Stats globales
    gagnes = [e for e in entries if e['resultat'] == 'gagne']
    perdus = [e for e in entries if e['resultat'] == 'perdu']
    decides = gagnes + perdus

    ca_gagne = sum(e.get('montant_final', 0) or e.get('budget_estime', 0) or 0 for e in gagnes)

    global_stats = {
        'win_rate': round(len(gagnes) / len(decides) * 100) if decides else 0,
        'nb_gagnes': len(gagnes),
        'nb_perdus': len(perdus),
        'nb_abandons': len([e for e in entries if e['resultat'] == 'abandon']),
        'nb_sans_suite': len([e for e in entries if e['resultat'] == 'sans_suite']),
        'nb_total': len(entries),
        'ca_gagne': ca_gagne,
        'ca_moyen': round(ca_gagne / len(gagnes)) if gagnes else 0,
    }

    # Par type d'acheteur
    par_type = defaultdict(lambda: {'gagnes': 0, 'perdus': 0})
    for e in decides:
        t = _type_acheteur(e.get('acheteur', ''))
        if e['resultat'] == 'gagne':
            par_type[t]['gagnes'] += 1
        else:
            par_type[t]['perdus'] += 1

    par_type_result = {}
    for t, v in par_type.items():
        total = v['gagnes'] + v['perdus']
        par_type_result[t] = {
            'win_rate': round(v['gagnes'] / total * 100) if total else 0,
            'nb_gagnes': v['gagnes'],
            'nb_perdus': v['perdus'],
        }

    # Par tranche budget
    par_budget = defaultdict(lambda: {'gagnes': 0, 'perdus': 0})
    for e in decides:
        tr = _tranche_budget(e.get('budget_estime', 0))
        if e['resultat'] == 'gagne':
            par_budget[tr]['gagnes'] += 1
        else:
            par_budget[tr]['perdus'] += 1

    par_budget_result = {}
    for tr, v in par_budget.items():
        total = v['gagnes'] + v['perdus']
        par_budget_result[tr] = {
            'win_rate': round(v['gagnes'] / total * 100) if total else 0,
            'nb_gagnes': v['gagnes'],
            'nb_perdus': v['perdus'],
        }

    # Par source
    par_source = defaultdict(lambda: {'gagnes': 0, 'perdus': 0})
    for e in decides:
        s = e.get('source', 'Inconnu') or 'Inconnu'
        if e['resultat'] == 'gagne':
            par_source[s]['gagnes'] += 1
        else:
            par_source[s]['perdus'] += 1

    par_source_result = {}
    for s, v in par_source.items():
        total = v['gagnes'] + v['perdus']
        par_source_result[s] = {
            'win_rate': round(v['gagnes'] / total * 100) if total else 0,
            'nb_gagnes': v['gagnes'],
            'nb_perdus': v['perdus'],
        }

    # Facteurs de succes/echec
    facteurs_succes = defaultdict(int)
    for e in gagnes:
        for f in e.get('points_forts', []):
            facteurs_succes[f] += 1

    facteurs_echec = defaultdict(int)
    for e in perdus:
        r = e.get('raison_principale', '')
        if r:
            facteurs_echec[r] += 1
        for f in e.get('points_ameliorer', []):
            facteurs_echec[f] += 1

    # Concurrents frequents
    concurrents = defaultdict(int)
    for e in perdus:
        c = e.get('concurrent_gagnant', '')
        if c:
            concurrents[c] += 1

    # Tendance mensuelle
    par_mois = defaultdict(lambda: {'gagnes': 0, 'perdus': 0})
    for e in decides:
        try:
            d = datetime.fromisoformat(e.get('date_enregistrement', ''))
            mois_key = d.strftime('%Y-%m')
            if e['resultat'] == 'gagne':
                par_mois[mois_key]['gagnes'] += 1
            else:
                par_mois[mois_key]['perdus'] += 1
        except Exception:
            pass

    mois_tries = sorted(par_mois.keys())
    tendance = {
        'mois': mois_tries,
        'win_rates': [
            round(par_mois[m]['gagnes'] / (par_mois[m]['gagnes'] + par_mois[m]['perdus']) * 100)
            if (par_mois[m]['gagnes'] + par_mois[m]['perdus']) > 0 else 0
            for m in mois_tries
        ],
    }

    # Recommandations automatiques
    recommandations = _generer_recommandations(global_stats, par_type_result, par_budget_result, facteurs_echec)

    return {
        'global': global_stats,
        'par_type_acheteur': par_type_result,
        'par_tranche_budget': par_budget_result,
        'par_source': par_source_result,
        'facteurs_succes': sorted(
            [{'facteur': f, 'frequence': n} for f, n in facteurs_succes.items()],
            key=lambda x: x['frequence'], reverse=True
        )[:10],
        'facteurs_echec': sorted(
            [{'facteur': f, 'frequence': n} for f, n in facteurs_echec.items()],
            key=lambda x: x['frequence'], reverse=True
        )[:10],
        'concurrents_frequents': sorted(
            [{'nom': c, 'nb_fois': n} for c, n in concurrents.items()],
            key=lambda x: x['nb_fois'], reverse=True
        )[:10],
        'tendance': tendance,
        'recommandations': recommandations,
    }


def _generer_recommandations(global_stats, par_type, par_budget, facteurs_echec) -> list:
    """Genere des recommandations basees sur l'analyse."""
    recos = []

    # Win rate global
    wr = global_stats.get('win_rate', 0)
    if wr > 0:
        if wr < 20:
            recos.append("Win rate faible (<20%) : envisager de cibler uniquement les AO score > 80% et ameliorer la qualite des memoires techniques")
        elif wr < 35:
            recos.append("Win rate correct (20-35%) : concentrer les efforts sur les types d'acheteurs ou on gagne le plus")
        elif wr >= 50:
            recos.append("Excellent win rate (>50%) : envisager d'augmenter le volume de reponses pour maximiser le CA")

    # Meilleur type d'acheteur
    if par_type:
        best_type = max(par_type.items(), key=lambda x: x[1].get('win_rate', 0))
        if best_type[1]['win_rate'] > 0:
            recos.append(f"Meilleur segment : {best_type[0]} ({best_type[1]['win_rate']}% win rate) - Prioriser ce type d'acheteur")

    # Meilleure tranche budget
    if par_budget:
        best_budget = max(par_budget.items(), key=lambda x: x[1].get('win_rate', 0))
        if best_budget[1]['win_rate'] > 0:
            recos.append(f"Tranche budget optimale : {best_budget[0]} ({best_budget[1]['win_rate']}% win rate)")

    # Facteur d'echec le plus frequent
    if facteurs_echec:
        top_echec = max(facteurs_echec.items(), key=lambda x: x[1])
        recos.append(f"Principal facteur d'echec : '{top_echec[0]}' ({top_echec[1]} fois) - Action corrective prioritaire")

    if not recos:
        recos.append("Pas encore assez de donnees pour des recommandations. Continuez a enregistrer les resultats !")

    return recos


def score_predictif_ao(ao: dict) -> dict:
    """Calcule un score predictif de gain base sur l'historique.

    Returns:
        {
            'probabilite_gain': float (0-100),
            'facteurs_positifs': list[str],
            'facteurs_negatifs': list[str],
            'ao_similaires_gagnes': int,
            'ao_similaires_perdus': int,
        }
    """
    tracker = _charger_tracker()
    decides = [e for e in tracker if e['resultat'] in ('gagne', 'perdu')]

    if not decides:
        return {
            'probabilite_gain': 50,  # Pas de donnees = 50/50
            'facteurs_positifs': ['Pas encore de donnees historiques'],
            'facteurs_negatifs': [],
            'ao_similaires_gagnes': 0,
            'ao_similaires_perdus': 0,
        }

    # Scoring multi-criteres
    score = 50  # Base
    positifs = []
    negatifs = []

    type_acheteur = _type_acheteur(ao.get('acheteur', ''))
    tranche = _tranche_budget(ao.get('budget_estime', 0))

    # 1. Win rate par type d'acheteur
    type_entries = [e for e in decides if _type_acheteur(e.get('acheteur', '')) == type_acheteur]
    if type_entries:
        wr = len([e for e in type_entries if e['resultat'] == 'gagne']) / len(type_entries)
        if wr > 0.4:
            score += 15
            positifs.append(f"Bon historique avec acheteurs {type_acheteur} ({round(wr*100)}% win rate)")
        elif wr < 0.2:
            score -= 10
            negatifs.append(f"Historique faible avec acheteurs {type_acheteur} ({round(wr*100)}% win rate)")

    # 2. Win rate par tranche budget
    budget_entries = [e for e in decides if _tranche_budget(e.get('budget_estime', 0)) == tranche]
    if budget_entries:
        wr = len([e for e in budget_entries if e['resultat'] == 'gagne']) / len(budget_entries)
        if wr > 0.4:
            score += 10
            positifs.append(f"Bonne performance sur budgets {tranche} ({round(wr*100)}% win rate)")
        elif wr < 0.2:
            score -= 8
            negatifs.append(f"Performance faible sur budgets {tranche}")

    # 3. Acheteur deja client (repeat business)
    acheteur = (ao.get('acheteur', '') or '').lower()
    acheteur_gagnes = [e for e in decides if e['resultat'] == 'gagne' and acheteur and acheteur in (e.get('acheteur', '') or '').lower()]
    if acheteur_gagnes:
        score += 20
        positifs.append(f"Acheteur deja gagne {len(acheteur_gagnes)} fois - avantage recurrence")

    # 4. Score de pertinence
    score_ao = ao.get('score_pertinence', 0) or 0
    if score_ao >= 0.8:
        score += 10
        positifs.append(f"Excellente pertinence ({round(score_ao*100)}%)")
    elif score_ao < 0.5:
        score -= 15
        negatifs.append(f"Pertinence faible ({round(score_ao*100)}%)")

    # 5. Budget dans la zone ideale
    budget = ao.get('budget_estime', 0) or 0
    if 5000 <= budget <= 70000:
        score += 5
        positifs.append("Budget dans la zone ideale Almera (5-70k)")
    elif budget > 150000:
        score -= 10
        negatifs.append("Budget eleve = plus de concurrence grands comptes")

    # Clamp
    score = max(5, min(95, score))

    # AO similaires
    similaires_gagnes = len([e for e in type_entries if e['resultat'] == 'gagne']) if type_entries else 0
    similaires_perdus = len([e for e in type_entries if e['resultat'] == 'perdu']) if type_entries else 0

    return {
        'probabilite_gain': score,
        'facteurs_positifs': positifs,
        'facteurs_negatifs': negatifs,
        'ao_similaires_gagnes': similaires_gagnes,
        'ao_similaires_perdus': similaires_perdus,
    }
