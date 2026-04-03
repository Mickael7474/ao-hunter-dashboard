"""
Analyse DCE automatique + Go/No-Go intelligent.
Extrait les exigences cles d'un DCE (PDF) et evalue la pertinence
pour Almera avant de lancer la generation complete du dossier.
"""

import re
import json
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger("ao_hunter.analyse_dce")

# --- Pieces administratives connues d'Almera ---
PIECES_DISPONIBLES = {
    "attestation_honneur": ["attestation sur l'honneur", "attestation honneur", "dc1"],
    "kbis": ["kbis", "extrait k-bis", "extrait kbis", "registre du commerce"],
    "urssaf": ["attestation urssaf", "vigilance", "cotisations sociales", "attestation sociale"],
    "fiscale": ["attestation fiscale", "impots", "dgfip", "obligations fiscales"],
    "rc_pro": ["assurance", "rc pro", "responsabilite civile", "rc professionnelle"],
    "qualiopi": ["qualiopi", "certification qualite", "qualite formation"],
    "rib": ["rib", "releve d'identite bancaire", "coordonnees bancaires"],
    "certificat_rs6776": ["rs6776", "france competences", "repertoire specifique"],
}

PIECES_MANQUANTES = {
    "rc_pro": "Assurance RC Pro (en attente devis Hiscox/Simplis)",
    "certificat_rs6776": "Certificat RS6776 (en attente ~2 mois)",
}

# --- Criteres de scoring Go/No-Go ---
COMPETENCES_ALMERA = [
    "formation", "intelligence artificielle", "IA", "ChatGPT", "copilot",
    "midjourney", "prompt", "numerique", "digital", "transformation",
    "acculturation", "sensibilisation", "automatisation", "data", "donnees",
    "e-learning", "consultant", "accompagnement", "diagnostic", "audit",
    "strategie", "deploiement", "conduite du changement", "no code", "low code",
    "generative", "machine learning", "agent IA", "chatbot",
]

SECTEURS_FORTS = [
    "formation", "conseil", "consulting", "accompagnement", "audit",
    "diagnostic", "strategie", "acculturation", "sensibilisation",
]

SECTEURS_HORS_PERIMETRE = [
    "travaux", "btp", "construction", "nettoyage", "restauration",
    "transport", "voirie", "assainissement", "cablage", "fibre optique",
    "cybersecurite", "pentest", "videosurveillance", "infrastructure",
    "fourniture", "materiel informatique", "equipement",
]


def analyser_dce_texte(texte: str, ao: dict) -> dict:
    """Analyse le texte extrait d'un DCE et retourne une analyse structuree.

    Args:
        texte: Texte brut extrait des PDFs du DCE
        ao: Dictionnaire de l'appel d'offres

    Returns:
        dict avec: pieces_demandees, criteres_notation, exigences, delais, etc.
    """
    texte_lower = texte.lower()
    analyse = {
        "pieces_demandees": _extraire_pieces_demandees(texte_lower),
        "criteres_notation": _extraire_criteres_notation(texte),
        "exigences_techniques": _extraire_exigences(texte_lower),
        "delais": _extraire_delais(texte),
        "lots": _extraire_lots(texte),
        "nb_pages_dce": texte.count("--- DOCUMENT:"),
    }
    return analyse


def _extraire_pieces_demandees(texte: str) -> list[dict]:
    """Detecte les pieces administratives demandees dans le DCE."""
    pieces = []

    patterns_pieces = {
        "DC1": [r"dc\s*1", r"lettre de candidature"],
        "DC2": [r"dc\s*2", r"declaration du candidat"],
        "DC4": [r"dc\s*4", r"declaration de sous.traitance"],
        "Kbis": [r"k\.?bis", r"extrait.*registre.*commerce"],
        "Attestation URSSAF": [r"attestation.*urssaf", r"attestation.*sociale", r"vigilance"],
        "Attestation fiscale": [r"attestation.*fiscale", r"attestation.*impot"],
        "Assurance RC Pro": [r"assurance.*responsabilit", r"rc\s*pro", r"attestation.*assurance"],
        "Qualiopi": [r"qualiopi", r"certification.*qualit"],
        "RIB": [r"\brib\b", r"relev.*identit.*bancaire"],
        "Memoire technique": [r"m[eé]moire technique", r"offre technique"],
        "BPU / DPGF": [r"\bbpu\b", r"\bdpgf\b", r"bordereau.*prix", r"decomposition.*prix"],
        "Planning": [r"planning", r"calendrier.*execution", r"gantt"],
        "CV formateurs": [r"cv.*formateur", r"curriculum.*vitae", r"r[eé]f[eé]rences.*formateur"],
        "Programme de formation": [r"programme.*formation", r"contenu.*pedagogique", r"d[eé]roul[eé].*p[eé]dagogique"],
        "Moyens pedagogiques": [r"moyens.*p[eé]dagogique", r"supports.*formation", r"modalit[eé]s.*p[eé]dagogique"],
        "References clients": [r"r[eé]f[eé]rences.*client", r"liste.*r[eé]f[eé]rences", r"exp[eé]riences.*similaires"],
        "Attestation sur l'honneur": [r"attestation.*honneur", r"d[eé]claration.*honneur"],
        "Acte d'engagement": [r"acte.*engagement", r"\bae\b"],
        "DUME": [r"\bdume\b", r"document unique.*march[eé].*europ[eé]en"],
    }

    for piece, patterns in patterns_pieces.items():
        for pattern in patterns:
            if re.search(pattern, texte):
                # Verifier si Almera peut fournir cette piece
                disponible = _piece_disponible(piece)
                pieces.append({
                    "nom": piece,
                    "disponible": disponible["disponible"],
                    "note": disponible.get("note", ""),
                })
                break

    return pieces


def _piece_disponible(nom_piece: str) -> dict:
    """Verifie si Almera dispose de cette piece."""
    nom_lower = nom_piece.lower()

    # Pieces toujours generables automatiquement
    auto_generables = ["dc1", "dc2", "dc4", "memoire technique", "bpu", "dpgf",
                       "planning", "cv formateurs", "programme", "references",
                       "acte d'engagement", "dume", "moyens"]
    for ag in auto_generables:
        if ag in nom_lower:
            return {"disponible": True, "note": "Generable automatiquement"}

    # Pieces du dossier permanent
    if "kbis" in nom_lower:
        return {"disponible": True, "note": "Dossier permanent (renouveler < 3 mois)"}
    if "urssaf" in nom_lower or "sociale" in nom_lower:
        return {"disponible": True, "note": "Dossier permanent (08/2025)"}
    if "fiscale" in nom_lower:
        return {"disponible": True, "note": "Dossier permanent (DGFIP)"}
    if "qualiopi" in nom_lower:
        return {"disponible": True, "note": "Dossier permanent (maj 09/02/2026)"}
    if "rib" in nom_lower:
        return {"disponible": True, "note": "Dossier permanent"}
    if "honneur" in nom_lower:
        return {"disponible": True, "note": "Dossier permanent (signee)"}
    if "assurance" in nom_lower or "rc pro" in nom_lower:
        return {"disponible": False, "note": "EN ATTENTE - Devis Hiscox/Simplis"}
    if "rs6776" in nom_lower:
        return {"disponible": False, "note": "EN ATTENTE (~2 mois)"}

    return {"disponible": True, "note": ""}


def _extraire_criteres_notation(texte: str) -> list[dict]:
    """Extrait les criteres de notation et leurs poids."""
    criteres = []
    patterns = [
        r'([A-Za-zÀ-ÿ\s\'/]+?)\s*[:\-–]\s*(\d+)\s*%',
        r'([A-Za-zÀ-ÿ\s\'/]+?)\s*\((\d+)\s*%\)',
        r'([A-Za-zÀ-ÿ\s\'/]+?)\s+(\d+)\s*%',
    ]
    for pattern in patterns:
        matches = re.findall(pattern, texte, re.IGNORECASE)
        for nom, poids in matches:
            nom = nom.strip()
            poids_int = int(poids)
            if 5 <= poids_int <= 80 and len(nom) > 3:
                if not any(c['nom'].lower() == nom.lower() for c in criteres):
                    criteres.append({"nom": nom, "poids": poids_int})

    total = sum(c['poids'] for c in criteres)
    if criteres and abs(total - 100) <= 10:
        return criteres
    elif criteres:
        return criteres
    return []


def _extraire_exigences(texte: str) -> list[str]:
    """Extrait les exigences techniques cles."""
    exigences = []

    patterns_exigences = [
        (r"le.*titulaire.*devra\s+(.*?)[\.\n]", "Obligation"),
        (r"le.*prestataire.*devra\s+(.*?)[\.\n]", "Obligation"),
        (r"il est demand[eé]\s+(.*?)[\.\n]", "Exigence"),
        (r"obligatoire.*?:\s*(.*?)[\.\n]", "Obligatoire"),
        (r"le.*candidat.*doit\s+(.*?)[\.\n]", "Condition"),
        (r"nombre.*(?:minimum|maximum).*?(\d+)\s*(stagiaires?|participants?|personnes?|groupes?|sessions?)", "Capacite"),
        (r"dur[eé]e.*?(\d+)\s*(heures?|jours?|semaines?|mois)", "Duree"),
    ]

    for pattern, categorie in patterns_exigences:
        matches = re.findall(pattern, texte, re.IGNORECASE)
        for match in matches[:3]:  # Max 3 par categorie
            if isinstance(match, tuple):
                texte_match = " ".join(match)
            else:
                texte_match = match
            texte_match = texte_match.strip()[:200]
            if len(texte_match) > 10:
                exigences.append(f"[{categorie}] {texte_match}")

    return exigences[:15]  # Max 15 exigences


def _extraire_delais(texte: str) -> list[str]:
    """Extrait les delais mentionnes dans le DCE."""
    delais = []
    patterns = [
        r"d[eé]lai.*?(\d+)\s*(jours?|semaines?|mois)",
        r"d[eé]but.*?pr[eé]vu.*?(\w+\s+\d{4})",
        r"d[eé]marrage.*?(\w+\s+\d{4})",
        r"dur[eé]e.*?march[eé].*?(\d+)\s*(mois|ans?)",
    ]
    for pattern in patterns:
        matches = re.findall(pattern, texte, re.IGNORECASE)
        for match in matches[:2]:
            if isinstance(match, tuple):
                delais.append(" ".join(match))
            else:
                delais.append(match)
    return delais[:5]


def _extraire_lots(texte: str) -> list[dict]:
    """Detecte si le marche est alloti et extrait les lots."""
    texte_lower = texte.lower()

    # Detecter marche non alloti / lot unique
    if re.search(r"march[eé]\s+non\s+alloti", texte_lower) or \
       re.search(r"marche\s+non\s+alloti", texte_lower):
        return [{"numero": 0, "description": "Marche non alloti (lot unique)",
                 "pertinent": None, "score": None}]

    if re.search(r"lot\s+unique", texte_lower):
        return [{"numero": 1, "description": "Lot unique",
                 "pertinent": None, "score": None}]

    # Detecter "marche alloti en X lots"
    match_alloti = re.search(r"march[eé]?\s+alloti\s+en\s+(\d+)\s+lots?", texte_lower)

    lots = []
    patterns = [
        r"lot\s*n?\s*[°º]?\s*(\d+)\s*[:\-–]\s*(.+?)(?:\n|$)",
        r"lot\s+(\d+)\s*[:\-–]\s*(.+?)(?:\n|$)",
        r"lot\s+(\d+)\s*[:\-–]\s*(.+?)(?:\.|;|$)",
    ]
    for pattern in patterns:
        matches = re.findall(pattern, texte, re.IGNORECASE)
        for num, desc in matches:
            desc = desc.strip()[:100]
            if len(desc) > 5:
                lots.append({"numero": int(num), "description": desc})

    # Dedoublonner par numero
    vus = set()
    uniques = []
    for lot in lots:
        if lot["numero"] not in vus:
            vus.add(lot["numero"])
            uniques.append(lot)

    # Evaluer la pertinence de chaque lot
    for lot in uniques:
        desc_lower = lot["description"].lower()
        # Verifier hors perimetre
        exclu = any(s in desc_lower for s in SECTEURS_HORS_PERIMETRE)
        # Compter les competences matchees
        hits = sum(1 for c in COMPETENCES_ALMERA if c.lower() in desc_lower)
        score = min(hits / 4.0, 1.0) if not exclu else 0.0
        lot["score"] = round(score, 2)
        lot["pertinent"] = score >= 0.2 and not exclu

    return uniques[:20]


def go_no_go(ao: dict, analyse_dce: dict = None) -> dict:
    """Analyse Go/No-Go rapide pour un AO.

    Evalue la pertinence d'Almera pour cet AO sur plusieurs criteres
    et retourne une recommandation avec justification.

    Args:
        ao: Dictionnaire de l'appel d'offres
        analyse_dce: Resultat de analyser_dce_texte() si disponible

    Returns:
        dict avec: decision (GO/NO-GO/A EVALUER), score (0-100),
                   criteres (liste de {nom, score, commentaire}),
                   risques, atouts
    """
    criteres_eval = []
    risques = []
    atouts = []

    titre = (ao.get("titre") or "").lower()
    description = (ao.get("description") or "").lower()
    texte_complet = f"{titre} {description}"

    # 1. Adequation competences (0-25 pts)
    score_competences = 0
    hits_competences = []
    for comp in COMPETENCES_ALMERA:
        if comp.lower() in texte_complet:
            score_competences += 2
            hits_competences.append(comp)
    score_competences = min(score_competences, 25)

    if score_competences >= 15:
        atouts.append(f"Forte adequation : {', '.join(hits_competences[:5])}")
    criteres_eval.append({
        "nom": "Adequation competences",
        "score": score_competences,
        "max": 25,
        "commentaire": f"{len(hits_competences)} competences matchees" if hits_competences else "Peu de competences matchees"
    })

    # 2. Hors perimetre ? (-100 pts si oui)
    for excl in SECTEURS_HORS_PERIMETRE:
        if excl in texte_complet:
            risques.append(f"Secteur hors perimetre detecte : {excl}")
            criteres_eval.append({
                "nom": "Secteur d'activite",
                "score": 0,
                "max": 15,
                "commentaire": f"HORS PERIMETRE : {excl}"
            })
            return _construire_resultat("NO-GO", 5, criteres_eval, risques, atouts,
                                       "Secteur hors perimetre Almera")

    # 2b. Secteur pertinent (0-15 pts)
    score_secteur = 0
    for sect in SECTEURS_FORTS:
        if sect in texte_complet:
            score_secteur += 5
    score_secteur = min(score_secteur, 15)
    criteres_eval.append({
        "nom": "Secteur d'activite",
        "score": score_secteur,
        "max": 15,
        "commentaire": "Secteur formation/conseil" if score_secteur >= 10 else "Secteur connexe"
    })

    # 3. Budget (0-20 pts)
    budget = ao.get("budget_estime")
    score_budget = 10  # par defaut si pas de budget
    if budget:
        if budget < 2000:
            score_budget = 0
            risques.append(f"Budget trop faible : {budget:,.0f} EUR")
        elif budget > 200000:
            score_budget = 5
            risques.append(f"Budget tres eleve ({budget:,.0f} EUR) - risque de sur-dimensionnement")
        elif 5000 <= budget <= 70000:
            score_budget = 20
            atouts.append(f"Budget ideal : {budget:,.0f} EUR")
        elif budget <= 100000:
            score_budget = 15
        else:
            score_budget = 10
    criteres_eval.append({
        "nom": "Budget",
        "score": score_budget,
        "max": 20,
        "commentaire": f"{budget:,.0f} EUR" if budget else "Non precise"
    })

    # 4. Deadline (0-15 pts)
    score_deadline = 10
    dl = ao.get("date_limite", "")
    if dl:
        try:
            date_dl = datetime.fromisoformat(dl.split("T")[0])
            jours_restants = (date_dl - datetime.now()).days
            if jours_restants < 0:
                score_deadline = 0
                risques.append("DEADLINE DEPASSEE")
            elif jours_restants < 3:
                score_deadline = 5
                risques.append(f"Deadline tres proche : {jours_restants}j")
            elif jours_restants < 7:
                score_deadline = 10
                risques.append(f"Deadline proche : {jours_restants}j")
            elif jours_restants < 14:
                score_deadline = 15
            else:
                score_deadline = 15
                atouts.append(f"Delai confortable : {jours_restants}j")
        except (ValueError, TypeError):
            pass
    criteres_eval.append({
        "nom": "Deadline",
        "score": score_deadline,
        "max": 15,
        "commentaire": f"{dl.split('T')[0]}" if dl else "Non precisee"
    })

    # 5. Pieces disponibles (0-15 pts) - si analyse DCE dispo
    score_pieces = 12  # par defaut
    if analyse_dce and analyse_dce.get("pieces_demandees"):
        pieces = analyse_dce["pieces_demandees"]
        nb_ok = sum(1 for p in pieces if p["disponible"])
        nb_total = len(pieces)
        if nb_total > 0:
            ratio = nb_ok / nb_total
            score_pieces = int(ratio * 15)
            pieces_manquantes = [p["nom"] for p in pieces if not p["disponible"]]
            if pieces_manquantes:
                risques.append(f"Pieces manquantes : {', '.join(pieces_manquantes)}")
            if ratio == 1.0:
                atouts.append(f"Toutes les pieces disponibles ({nb_total}/{nb_total})")
    criteres_eval.append({
        "nom": "Pieces administratives",
        "score": score_pieces,
        "max": 15,
        "commentaire": f"Analyse DCE" if analyse_dce else "Sans DCE"
    })

    # 6. Score de pertinence existant (0-10 pts)
    score_pertinence = ao.get("score_pertinence", 0)
    score_pertinence_pts = int(score_pertinence * 10)
    criteres_eval.append({
        "nom": "Score pertinence veille",
        "score": score_pertinence_pts,
        "max": 10,
        "commentaire": f"{int(score_pertinence * 100)}%"
    })

    # Score total
    score_total = sum(c["score"] for c in criteres_eval)

    # Decision
    if score_total >= 70:
        decision = "GO"
        raison = "Forte adequation avec les competences Almera"
    elif score_total >= 45:
        decision = "A EVALUER"
        raison = "Adequation partielle - analyser le DCE en detail"
    else:
        decision = "NO-GO"
        raison = "Faible adequation ou risques trop importants"

    return _construire_resultat(decision, score_total, criteres_eval, risques, atouts, raison)


def _construire_resultat(decision, score, criteres, risques, atouts, raison):
    return {
        "decision": decision,
        "score": score,
        "max_score": 100,
        "raison": raison,
        "criteres": criteres,
        "risques": risques,
        "atouts": atouts,
        "timestamp": datetime.now().isoformat(),
    }
