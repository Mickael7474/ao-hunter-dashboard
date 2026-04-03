"""
Modeles de reponse par type de prestation.
Fournit des structures de memoire technique adaptees selon
que l'AO est de type Formation, Consulting/AMO, Developpement ou Mixte.
"""

MODELES = {
    "Formation": {
        "titre": "Formation",
        "couleur": "#2563eb",
        "sections_memoire": [
            "1. Comprehension du besoin et contexte",
            "2. Presentation d'Almera et references formation",
            "3. Equipe pedagogique (CV formateurs, certifications)",
            "4. Approche pedagogique et methodes",
            "5. Programme detaille (objectifs, contenu, duree par module)",
            "6. Modalites (presentiel, distanciel, hybride)",
            "7. Supports et outils pedagogiques",
            "8. Evaluation des acquis (avant, pendant, apres)",
            "9. Suivi post-formation et accompagnement",
            "10. Planning previsionnel",
            "11. Indicateurs de qualite et engagement de resultat",
        ],
        "points_forts": [
            "Certification Qualiopi (actions de formation)",
            "Certification RS6776 France Competences (IA generative)",
            "2000+ personnes formees, 50+ entreprises",
            "Financement OPCO / AIF / CPF (en cours)",
            "6 formateurs specialises mobilisables",
            "Personnalisation au contexte de chaque stagiaire",
            "29 formations au catalogue (IA, ChatGPT, Copilot, Midjourney...)",
        ],
        "pieces_specifiques": [
            "Programme de formation detaille",
            "CV formateurs",
            "Certificat Qualiopi",
            "Certificat RS6776",
            "Moyens pedagogiques",
            "Modalites d'evaluation",
        ],
        "conseils_redaction": [
            "Detailler les objectifs pedagogiques par module (verbes d'action Bloom)",
            "Mentionner les outils IA concrets utilises en formation",
            "Inclure des exemples de cas pratiques adaptes au secteur de l'acheteur",
            "Preciser les pre-requis et le public cible",
            "Indiquer les modalites de suivi post-formation (hotline, ressources)",
        ],
    },

    "Consulting / AMO": {
        "titre": "Consulting / AMO",
        "couleur": "#7c3aed",
        "sections_memoire": [
            "1. Comprehension du contexte et des enjeux",
            "2. Presentation d'Almera et references consulting",
            "3. Equipe projet (profils, roles, CV)",
            "4. Methodologie d'intervention (4 etapes Almera)",
            "5. Phase 1 : Diagnostic et audit",
            "6. Phase 2 : Feuille de route et recommandations",
            "7. Phase 3 : Accompagnement et formation des referents",
            "8. Phase 4 : Deploiement et suivi des resultats",
            "9. Livrables attendus par phase",
            "10. Planning et jalons",
            "11. Gouvernance projet et comitologie",
            "12. Indicateurs de performance (KPIs)",
        ],
        "points_forts": [
            "Approche structuree en 4 etapes (diagnostic > feuille de route > accompagnement > deploiement)",
            "Expertise croisee : IA + formation + transformation",
            "References COMEX/CODIR (Airbus, Nexans, Getinge, CNPIAT)",
            "Charles Lerminiaux : 17 ans d'experience, ex-Directeur conseil",
            "Couverture nationale via reseau de consultants",
            "Accompagnement jusqu'au deploiement operationnel",
        ],
        "pieces_specifiques": [
            "Note methodologique",
            "Planning d'intervention",
            "CV consultants",
            "References missions similaires",
            "Livrables types (exemples anonymises)",
        ],
        "conseils_redaction": [
            "Structurer autour des 4 etapes Almera : diagnostic, feuille de route, accompagnement, deploiement",
            "Quantifier les resultats attendus (KPIs concrets)",
            "Adapter le vocabulaire au secteur de l'acheteur",
            "Proposer une gouvernance projet claire (COPIL, points hebdo...)",
            "Mettre en avant l'expertise de Charles pour les missions data/IA",
        ],
    },

    "Developpement": {
        "titre": "Developpement",
        "couleur": "#059669",
        "sections_memoire": [
            "1. Comprehension du besoin fonctionnel",
            "2. Presentation d'Almera et references techniques",
            "3. Equipe technique (profils, competences)",
            "4. Architecture technique proposee",
            "5. Specifications fonctionnelles",
            "6. Methodologie de developpement (agile)",
            "7. Stack technique et outils",
            "8. Securite et conformite (RGPD)",
            "9. Plan de tests et recette",
            "10. Deploiement et mise en production",
            "11. Maintenance et support (TMA/TME)",
            "12. Planning et livrables",
        ],
        "points_forts": [
            "Expertise IA generative appliquee (chatbots, agents, automatisation)",
            "Outils : n8n, Make, Voiceflow, Supabase, RAG",
            "Approche no-code/low-code pour livraison rapide",
            "Integration IA dans les processus metier existants",
            "Accompagnement formation des utilisateurs inclus",
        ],
        "pieces_specifiques": [
            "Architecture technique",
            "Specifications fonctionnelles",
            "Plan de tests",
            "Planning de developpement (sprints)",
            "SLA maintenance",
        ],
        "conseils_redaction": [
            "Privilegier les solutions no-code/low-code quand possible (avantage competitif)",
            "Mettre en avant la dimension IA (chatbot, automatisation, RAG)",
            "Inclure la formation des utilisateurs finaux dans l'offre",
            "Preciser les engagements de maintenance post-livraison",
            "Detailler la conformite RGPD si traitement de donnees personnelles",
        ],
    },

    "Mixte": {
        "titre": "Prestation mixte",
        "couleur": "#d97706",
        "sections_memoire": [
            "1. Comprehension globale du besoin",
            "2. Presentation d'Almera et approche 360",
            "3. Equipe mobilisee (consultants + formateurs)",
            "4. Volet Conseil : diagnostic et feuille de route IA",
            "5. Volet Formation : programme et modalites",
            "6. Volet Deploiement : mise en oeuvre operationnelle",
            "7. Synergie entre les volets",
            "8. Planning integre",
            "9. Livrables par phase",
            "10. Suivi et indicateurs de resultat",
        ],
        "points_forts": [
            "Offre integree : conseil + formation + deploiement",
            "Un seul interlocuteur pour tout le projet",
            "Approche en 4 etapes eprouvee",
            "Equipe pluridisciplinaire (consultants data + formateurs IA)",
        ],
        "pieces_specifiques": [
            "Note methodologique globale",
            "Programme de formation",
            "CV equipe (consultants + formateurs)",
            "Planning integre multi-volets",
        ],
        "conseils_redaction": [
            "Montrer la coherence entre conseil, formation et deploiement",
            "Un seul planning integre avec les jalons de chaque volet",
            "Mettre en avant l'avantage d'un prestataire unique vs multi-lots",
        ],
    },
}


def detecter_type_prestation(ao: dict) -> str:
    """Detecte le type de prestation principal a partir du titre et description."""
    texte = f"{ao.get('titre', '')} {ao.get('description', '')}".lower()

    scores = {"Formation": 0, "Consulting / AMO": 0, "Developpement": 0}

    mots_formation = ["formation", "formateur", "pedagogie", "stagiaire", "apprenant",
                      "competences", "qualiopi", "cpf", "opco", "e-learning", "module",
                      "programme pedagogique", "certification"]
    mots_consulting = ["conseil", "consulting", "accompagnement", "audit", "diagnostic",
                       "amo", "assistance", "expertise", "strategie", "transformation",
                       "conduite du changement", "schema directeur", "feuille de route"]
    mots_dev = ["developpement", "logiciel", "application", "chatbot", "agent",
                "automatisation", "deploiement", "integration", "api", "plateforme"]

    for mot in mots_formation:
        if mot in texte:
            scores["Formation"] += 1
    for mot in mots_consulting:
        if mot in texte:
            scores["Consulting / AMO"] += 1
    for mot in mots_dev:
        if mot in texte:
            scores["Developpement"] += 1

    max_score = max(scores.values())
    if max_score == 0:
        return "Mixte"

    # Si deux types sont proches, c'est mixte
    types_forts = [t for t, s in scores.items() if s >= max_score * 0.7 and s > 0]
    if len(types_forts) > 1:
        return "Mixte"

    return max(scores, key=scores.get)


def get_modele(type_prestation: str) -> dict:
    """Retourne le modele de reponse pour un type de prestation."""
    return MODELES.get(type_prestation, MODELES["Mixte"])
