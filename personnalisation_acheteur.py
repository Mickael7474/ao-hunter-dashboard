"""
Personnalisation automatique par type d'acheteur.
Detecte le type d'acheteur, selectionne les references pertinentes,
et genere un angle de personnalisation pour les prompts Claude.
"""

import re
import logging

logger = logging.getLogger("ao_hunter.personnalisation_acheteur")

# Mapping mots-cles -> type d'acheteur
_PATTERNS_ACHETEUR = {
    "collectivite_territoriale": [
        "mairie", "commune", "communaute d'agglomeration", "communaute de communes",
        "metropole", "conseil departemental", "conseil regional", "departement",
        "region", "intercommunalite", "syndicat mixte", "sivu", "sivom",
        "communaute urbaine", "collectivite", "territorial",
    ],
    "etablissement_sante": [
        "chu", "hopital", "centre hospitalier", "ehpad", "ars",
        "agence regionale de sante", "clinique", "groupe hospitalier",
        "assistance publique", "ap-hp", "sante", "medico-social",
        "etablissement de sante", "ghm", "ght",
    ],
    "ministere": [
        "ministere", "direction generale", "dgfip", "dinum", "dgse",
        "prefect", "secretariat d'etat", "premier ministre",
        "administration centrale", "sgae", "dila",
    ],
    "entreprise_publique": [
        "sncf", "ratp", "edf", "engie", "la poste", "orange",
        "caisse des depots", "cdc", "bpifrance", "action logement",
        "epic", "sem", "societe d'economie mixte", "entreprise publique",
        "banque de france", "aeroports de paris", "adp",
    ],
    "education": [
        "universite", "ecole", "lycee", "college", "academie",
        "rectorat", "crous", "cnrs", "inria", "inserm",
        "enseignement", "recherche", "iut", "bts", "education nationale",
        "cnam", "cci", "chambre de commerce", "chambre de metiers",
        "opco", "france competences", "france travail", "pole emploi",
    ],
}

# Vocabulaire et angles par type d'acheteur
_ANGLES = {
    "collectivite_territoriale": {
        "vocabulaire": [
            "service public", "usagers", "agents territoriaux",
            "transformation numerique des territoires", "e-administration",
            "relation citoyenne", "demarche participative",
        ],
        "points_forts": [
            "Experience avec collectivites et institutions publiques (Caisse des Depots, CCI, Action Logement)",
            "Certification Qualiopi garantissant la qualite de la formation pour les fonds publics",
            "Activateur France Num : accompagnement de la transformation numerique des territoires",
            "Capacite a former des agents de tous niveaux (elus, cadres, agents d'accueil)",
        ],
        "ton": "Formel et institutionnel, insistant sur le service public et la modernisation",
    },
    "etablissement_sante": {
        "vocabulaire": [
            "parcours patient", "soignants", "praticiens",
            "systeme d'information hospitalier", "DPI",
            "qualite des soins", "personnel soignant", "GHT",
        ],
        "points_forts": [
            "Reference Eli Lilly (secteur pharmaceutique/sante)",
            "Formation adaptee aux contraintes des professionnels de sante (planning, disponibilite)",
            "Approche pragmatique : cas pratiques directement applicables au quotidien hospitalier",
            "Modules de formation courts et modulables (demi-journees possibles)",
        ],
        "ton": "Empathique et pragmatique, centre sur l'amelioration des conditions de travail et la qualite des soins",
    },
    "ministere": {
        "vocabulaire": [
            "politique publique", "souverainete numerique",
            "RGPD", "SecNumCloud", "cadre reglementaire",
            "transformation de l'Etat", "agents publics",
        ],
        "points_forts": [
            "Membre Hub France IA : engagement dans l'ecosysteme IA souverain francais",
            "Expertise Mistral AI (IA souveraine francaise) en complement des outils internationaux",
            "References secteur public : Caisse des Depots, CCI, Action Logement",
            "Formation a la conformite RGPD et a l'ethique de l'IA",
        ],
        "ton": "Tres formel, vocabulaire administratif, accent sur la souverainete et la conformite",
    },
    "entreprise_publique": {
        "vocabulaire": [
            "performance operationnelle", "conduite du changement",
            "transformation digitale", "excellence operationnelle",
            "innovation", "competitivite",
        ],
        "points_forts": [
            "References grands groupes : Havas, Eiffage, Carrefour, Orange, 3DS (Dassault Systemes)",
            "Experience de formation a grande echelle (2000+ personnes formees)",
            "Approche sur mesure par metier et par direction",
            "Certification RS6776 France Competences pour la montee en competences certifiante",
        ],
        "ton": "Professionnel et oriente resultats, vocabulaire corporate",
    },
    "education": {
        "vocabulaire": [
            "ingenierie pedagogique", "montee en competences",
            "parcours de formation", "certification professionnelle",
            "competences transversales", "RNCP", "blocs de competences",
        ],
        "points_forts": [
            "Certification RS6776 France Competences (IA generative) : formation certifiante reconnue",
            "Catalogue de 29 formations structurees avec objectifs pedagogiques alignes RNCP",
            "Expertise en ingenierie pedagogique et conception de parcours sur mesure",
            "Experience avec CCI et organismes de formation professionnelle",
        ],
        "ton": "Pedagogique et structure, vocabulaire de la formation professionnelle",
    },
    "autre": {
        "vocabulaire": [
            "transformation digitale", "innovation",
            "competitivite", "productivite", "ROI",
        ],
        "points_forts": [
            "2000+ personnes formees dans des secteurs diversifies",
            "50+ entreprises clientes (grands groupes et PME)",
            "Note de satisfaction 4.9/5 (Google)",
            "Formation adaptee a tous les niveaux et tous les metiers",
        ],
        "ton": "Professionnel et adaptatif",
    },
}

# References par pertinence pour chaque type d'acheteur (noms de clients a prioriser)
_REFS_PRIORITAIRES = {
    "collectivite_territoriale": ["Caisse des Depots", "CCI", "Action Logement"],
    "etablissement_sante": ["Eli Lilly", "Caisse des Depots"],
    "ministere": ["Caisse des Depots", "CCI", "Action Logement"],
    "entreprise_publique": ["Orange", "Eiffage", "Havas", "Carrefour", "3DS (Dassault Systemes)"],
    "education": ["CCI", "Caisse des Depots", "Action Logement"],
    "autre": ["Havas", "Eiffage", "Carrefour", "Orange"],
}


def _detecter_type_acheteur(ao: dict) -> str:
    """Detecte le type d'acheteur a partir des informations de l'AO."""
    texte = " ".join([
        ao.get("acheteur", ""),
        ao.get("titre", ""),
        ao.get("description", ""),
        ao.get("type_acheteur", ""),
    ]).lower()

    scores = {}
    for type_ach, patterns in _PATTERNS_ACHETEUR.items():
        score = 0
        for pattern in patterns:
            if pattern in texte:
                score += 1
        scores[type_ach] = score

    meilleur = max(scores, key=scores.get)
    if scores[meilleur] == 0:
        return "autre"
    return meilleur


def _selectionner_references(type_acheteur: str, references: list[dict]) -> list[dict]:
    """Selectionne et ordonne les references les plus pertinentes pour le type d'acheteur."""
    prioritaires = _REFS_PRIORITAIRES.get(type_acheteur, [])

    # Scorer chaque reference
    scored = []
    for ref in references:
        score = 0
        nom_client = ref.get("client", "")
        for i, prio in enumerate(prioritaires):
            if prio.lower() in nom_client.lower():
                score = 100 - i  # Plus haut = plus prioritaire
                break
        scored.append((score, ref))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [ref for _, ref in scored]


def personnaliser(ao: dict, references: list[dict] = None) -> dict:
    """Fonction principale de personnalisation.

    Args:
        ao: Dictionnaire de l'appel d'offres
        references: Liste des references clients (optionnel)

    Returns:
        dict avec: type_acheteur, references_selectionnees, angle, vocabulaire
    """
    type_acheteur = _detecter_type_acheteur(ao)
    angle_data = _ANGLES.get(type_acheteur, _ANGLES["autre"])

    refs_selectionnees = []
    if references:
        refs_selectionnees = _selectionner_references(type_acheteur, references)

    logger.info(f"Type acheteur detecte: {type_acheteur} pour '{ao.get('acheteur', 'inconnu')}'")

    return {
        "type_acheteur": type_acheteur,
        "references_selectionnees": refs_selectionnees,
        "angle": {
            "points_forts": angle_data["points_forts"],
            "ton": angle_data["ton"],
        },
        "vocabulaire": angle_data["vocabulaire"],
    }


def bloc_personnalisation_prompt(perso: dict) -> str:
    """Genere un bloc de texte a injecter dans les prompts Claude."""
    if not perso:
        return ""

    lignes = [f"\n=== PERSONNALISATION ACHETEUR (type: {perso['type_acheteur']}) ==="]
    lignes.append(f"Ton a adopter : {perso['angle']['ton']}")
    lignes.append(f"Vocabulaire a privilegier : {', '.join(perso['vocabulaire'])}")
    lignes.append("Points forts a mettre en avant :")
    for pf in perso["angle"]["points_forts"]:
        lignes.append(f"  - {pf}")

    if perso.get("references_selectionnees"):
        lignes.append("References les plus pertinentes pour cet acheteur (a citer en priorite) :")
        for ref in perso["references_selectionnees"][:5]:
            lignes.append(f"  - {ref.get('client', '')} ({ref.get('secteur', '')}): {ref.get('mission', '')}")

    lignes.append("===")
    return "\n".join(lignes)
