"""
Recherche globale - Moteur de recherche full-text pour AO Hunter.

Recherche dans :
- AO (titre, acheteur, description)
- Dossiers generes (contenu des fichiers .md)
- Notes et commentaires
- CRM (noms d'acheteurs, contacts)
"""

import json
import re
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger("ao_hunter.recherche_globale")

DASHBOARD_DIR = Path(__file__).parent
DOSSIERS_DIR = DASHBOARD_DIR / "dossiers_generes"


def _normaliser(texte: str) -> str:
    """Normalise le texte pour la recherche (minuscules, sans accents)."""
    if not texte:
        return ""
    texte = texte.lower()
    remplacements = {
        'é': 'e', 'è': 'e', 'ê': 'e', 'ë': 'e',
        'à': 'a', 'â': 'a', 'ä': 'a',
        'ù': 'u', 'û': 'u', 'ü': 'u',
        'ô': 'o', 'ö': 'o',
        'î': 'i', 'ï': 'i',
        'ç': 'c',
        'œ': 'oe', 'æ': 'ae',
    }
    for src, dst in remplacements.items():
        texte = texte.replace(src, dst)
    return texte


def _score_pertinence(texte: str, termes: list[str]) -> float:
    """Calcule un score de pertinence (0-100) base sur les termes trouves."""
    if not texte or not termes:
        return 0
    texte_norm = _normaliser(texte)
    score = 0
    nb_matches = 0
    for terme in termes:
        terme_norm = _normaliser(terme)
        if not terme_norm:
            continue
        # Match exact = +10, match partiel = +5
        occurrences = texte_norm.count(terme_norm)
        if occurrences > 0:
            nb_matches += 1
            score += min(10, occurrences * 3)  # Max 10 par terme
            # Bonus si dans le titre/debut du texte (premiers 200 chars)
            if terme_norm in texte_norm[:200]:
                score += 5

    if not nb_matches:
        return 0

    # Bonus couverture : plus de termes trouves = meilleur score
    couverture = nb_matches / len(termes)
    score *= (0.5 + 0.5 * couverture)

    return min(100, round(score))


def rechercher(query: str, categories: list[str] = None, limit: int = 50) -> dict:
    """Recherche globale dans toutes les donnees.

    Args:
        query: Texte a rechercher
        categories: ['ao', 'dossiers', 'notes', 'crm'] ou None pour tout
        limit: Nombre max de resultats par categorie

    Returns:
        {
            'query': str,
            'nb_total': int,
            'resultats': {
                'ao': [{'id', 'titre', 'acheteur', 'score_recherche', 'extrait', 'type'}],
                'dossiers': [{'dossier', 'fichier', 'score_recherche', 'extrait', 'type'}],
                'notes': [{'ao_id', 'contenu', 'score_recherche', 'type'}],
                'crm': [{'nom', 'type', 'score_recherche', 'extrait'}],
            }
        }
    """
    if not query or len(query.strip()) < 2:
        return {'query': query, 'nb_total': 0, 'resultats': {'ao': [], 'dossiers': [], 'notes': [], 'crm': []}}

    termes = [t.strip() for t in query.split() if len(t.strip()) >= 2]
    if not termes:
        return {'query': query, 'nb_total': 0, 'resultats': {'ao': [], 'dossiers': [], 'notes': [], 'crm': []}}

    cats = categories or ['ao', 'dossiers', 'notes', 'crm']
    resultats = {}

    if 'ao' in cats:
        resultats['ao'] = _rechercher_ao(termes, limit)
    else:
        resultats['ao'] = []

    if 'dossiers' in cats:
        resultats['dossiers'] = _rechercher_dossiers(termes, limit)
    else:
        resultats['dossiers'] = []

    if 'notes' in cats:
        resultats['notes'] = _rechercher_notes(termes, limit)
    else:
        resultats['notes'] = []

    if 'crm' in cats:
        resultats['crm'] = _rechercher_crm(termes, limit)
    else:
        resultats['crm'] = []

    nb_total = sum(len(v) for v in resultats.values())

    return {
        'query': query,
        'nb_total': nb_total,
        'resultats': resultats,
    }


def _extraire_contexte(texte: str, terme: str, longueur: int = 150) -> str:
    """Extrait un contexte autour du premier match."""
    texte_norm = _normaliser(texte)
    terme_norm = _normaliser(terme)
    pos = texte_norm.find(terme_norm)
    if pos < 0:
        return texte[:longueur] + '...' if len(texte) > longueur else texte

    start = max(0, pos - 60)
    end = min(len(texte), pos + len(terme) + 90)
    extrait = texte[start:end].strip()

    if start > 0:
        extrait = '...' + extrait
    if end < len(texte):
        extrait = extrait + '...'

    return extrait


def _rechercher_ao(termes: list[str], limit: int) -> list:
    """Recherche dans les AO."""
    ao_file = DASHBOARD_DIR / "ao_pertinents.json"
    if not ao_file.exists():
        return []

    try:
        aos = json.loads(ao_file.read_text(encoding="utf-8"))
    except Exception:
        return []

    resultats = []
    for ao in aos:
        texte = ' '.join(str(v) for v in [
            ao.get('titre', ''), ao.get('acheteur', ''),
            ao.get('description', ''), ao.get('region', ''),
            ao.get('id', ''),
        ] if v)

        score = _score_pertinence(texte, termes)
        if score > 0:
            extrait = _extraire_contexte(
                ao.get('description', '') or ao.get('titre', ''),
                termes[0]
            )
            resultats.append({
                'id': ao.get('id', ''),
                'titre': ao.get('titre', ''),
                'acheteur': ao.get('acheteur', ''),
                'score_recherche': score,
                'score_pertinence': ao.get('score_pertinence', 0),
                'statut': ao.get('statut', ''),
                'extrait': extrait,
                'type': 'ao',
            })

    resultats.sort(key=lambda x: x['score_recherche'], reverse=True)
    return resultats[:limit]


def _rechercher_dossiers(termes: list[str], limit: int) -> list:
    """Recherche dans les dossiers generes."""
    if not DOSSIERS_DIR.exists():
        return []

    resultats = []
    for dossier_dir in DOSSIERS_DIR.iterdir():
        if not dossier_dir.is_dir():
            continue

        for fichier in dossier_dir.glob('*.md'):
            try:
                contenu = fichier.read_text(encoding='utf-8', errors='ignore')
                score = _score_pertinence(contenu, termes)
                if score > 0:
                    extrait = _extraire_contexte(contenu, termes[0])
                    resultats.append({
                        'dossier': dossier_dir.name,
                        'fichier': fichier.name,
                        'score_recherche': score,
                        'extrait': extrait,
                        'type': 'dossier',
                    })
            except Exception:
                continue

    resultats.sort(key=lambda x: x['score_recherche'], reverse=True)
    return resultats[:limit]


def _rechercher_notes(termes: list[str], limit: int) -> list:
    """Recherche dans les notes et commentaires."""
    resultats = []

    for fichier_notes in ['ao_notes.json', 'commentaires.json']:
        notes_file = DASHBOARD_DIR / fichier_notes
        if not notes_file.exists():
            continue

        try:
            notes = json.loads(notes_file.read_text(encoding="utf-8"))
        except Exception:
            continue

        if isinstance(notes, dict):
            for ao_id, contenu in notes.items():
                if isinstance(contenu, str):
                    texte = contenu
                elif isinstance(contenu, list):
                    texte = ' '.join(str(c) for c in contenu)
                elif isinstance(contenu, dict):
                    texte = ' '.join(str(v) for v in contenu.values())
                else:
                    continue

                score = _score_pertinence(texte, termes)
                if score > 0:
                    resultats.append({
                        'ao_id': ao_id,
                        'contenu': texte[:200],
                        'score_recherche': score,
                        'source': fichier_notes,
                        'type': 'note',
                    })

    resultats.sort(key=lambda x: x['score_recherche'], reverse=True)
    return resultats[:limit]


def _rechercher_crm(termes: list[str], limit: int) -> list:
    """Recherche dans le CRM."""
    resultats = []

    # Rechercher dans ao_pertinents par acheteur unique
    ao_file = DASHBOARD_DIR / "ao_pertinents.json"
    if not ao_file.exists():
        return []

    try:
        aos = json.loads(ao_file.read_text(encoding="utf-8"))
    except Exception:
        return []

    acheteurs_vus = {}
    for ao in aos:
        acheteur = ao.get('acheteur', '')
        if not acheteur or acheteur in acheteurs_vus:
            continue
        acheteurs_vus[acheteur] = acheteurs_vus.get(acheteur, 0) + 1

        score = _score_pertinence(acheteur, termes)
        if score > 0:
            nb_ao = len([a for a in aos if a.get('acheteur') == acheteur])
            resultats.append({
                'nom': acheteur,
                'nb_ao': nb_ao,
                'score_recherche': score,
                'extrait': f"{nb_ao} AO detecte(s)",
                'type': 'crm',
            })

    resultats.sort(key=lambda x: x['score_recherche'], reverse=True)
    return resultats[:limit]


def suggestions_recherche() -> list[str]:
    """Retourne des suggestions de recherche basees sur les donnees existantes."""
    suggestions = []

    # Top acheteurs
    ao_file = DASHBOARD_DIR / "ao_pertinents.json"
    if ao_file.exists():
        try:
            aos = json.loads(ao_file.read_text(encoding="utf-8"))
            acheteurs = {}
            for ao in aos:
                a = ao.get('acheteur', '')
                if a:
                    acheteurs[a] = acheteurs.get(a, 0) + 1
            top = sorted(acheteurs.items(), key=lambda x: x[1], reverse=True)[:5]
            suggestions.extend([a for a, _ in top])
        except Exception:
            pass

    # Mots-cles frequents
    suggestions.extend(['formation IA', 'intelligence artificielle', 'ChatGPT', 'transformation digitale', 'Qualiopi'])

    return suggestions[:10]
