"""
Auto-review IA du dossier avant soumission.
Fait un appel Claude Haiku pour relire et verifier la coherence
du dossier complet genere.
"""

import os
import re
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


def verifier_conformite_rc(dossier: dict, rc_data: dict) -> dict:
    """Verifie la conformite d'un dossier genere par rapport aux exigences du RC.

    Args:
        dossier: dict {nom_fichier: contenu_markdown} des pieces du dossier
        rc_data: dict retourne par extraction_rc.extraire_rc() ou adapter_dossier()
            Champs utilises: pieces_exigees, criteres_attribution, conditions_participation,
            lots, variantes_autorisees, duree_marche

    Returns:
        dict {conforme, score, pieces_manquantes, pieces_presentes, alertes, suggestions}
    """
    pieces_presentes = []
    pieces_manquantes = []
    alertes = []
    suggestions = []
    score = 100

    noms_fichiers = set(dossier.keys())
    contenu_complet = " ".join(dossier.values())
    contenu_lower = contenu_complet.lower()

    # --- 1. Verification des pieces exigees par le RC ---
    # Mapping entre pieces RC et fichiers du dossier
    MAPPING_PIECES = {
        "memoire technique": ["memoire_technique", "memoire"],
        "lettre de candidature": ["lettre_candidature", "lettre"],
        "bpu": ["bpu", "dpgf", "bordereau"],
        "bordereau de prix unitaires": ["bpu", "dpgf", "bordereau"],
        "dpgf": ["bpu", "dpgf"],
        "dc1": ["dc1_dc2", "dc1"],
        "dc2": ["dc1_dc2", "dc2"],
        "dume": ["dume"],
        "acte d'engagement": ["acte_engagement"],
        "planning": ["planning"],
        "cv": ["cv_formateur", "cv"],
        "curriculum": ["cv_formateur", "cv"],
        "references": ["references_client", "references"],
        "programme de formation": ["programme_formation", "programme"],
        "moyens techniques": ["moyens_techniques", "moyens"],
        "attestation d'assurance": ["attestation_assurance", "assurance"],
        "attestation assurance": ["attestation_assurance", "assurance"],
        "rib": ["rib"],
        "kbis": ["kbis"],
        "extrait k": ["kbis"],
        "qualiopi": ["qualiopi"],
        "certificat qualiopi": ["qualiopi"],
        "attestations fiscales": ["attestation_fiscale", "urssaf"],
        "attestations sociales": ["attestation_sociale", "urssaf"],
    }

    pieces_exigees = rc_data.get("pieces_exigees", [])
    for piece_rc in pieces_exigees:
        piece_lower = piece_rc.lower()
        trouvee = False

        # Verifier dans les noms de fichiers du dossier
        for cle_mapping, patterns in MAPPING_PIECES.items():
            if cle_mapping in piece_lower:
                for pattern in patterns:
                    for nom_fich in noms_fichiers:
                        if pattern in nom_fich.lower():
                            trouvee = True
                            break
                    if trouvee:
                        break
            if trouvee:
                break

        # Fallback : chercher dans le contenu du dossier
        if not trouvee:
            mots_cles = [m for m in piece_lower.split() if len(m) > 3]
            if mots_cles and all(m in contenu_lower for m in mots_cles[:2]):
                trouvee = True

        if trouvee:
            pieces_presentes.append(piece_rc)
        else:
            pieces_manquantes.append(piece_rc)

    # Penaliser les pieces manquantes
    if pieces_manquantes:
        # Pieces admin (assurance, kbis, rib) = moins critique (l'utilisateur les ajoute manuellement)
        pieces_admin = {"attestation", "assurance", "rib", "kbis", "extrait", "fiscale", "sociale", "urssaf"}
        critiques = [p for p in pieces_manquantes if not any(a in p.lower() for a in pieces_admin)]
        admin_manquantes = [p for p in pieces_manquantes if any(a in p.lower() for a in pieces_admin)]

        if critiques:
            score -= len(critiques) * 15
            alertes.append({
                "niveau": "critique",
                "message": f"Piece(s) technique(s) exigee(s) manquante(s): {', '.join(critiques)}"
            })
        if admin_manquantes:
            score -= len(admin_manquantes) * 5
            alertes.append({
                "niveau": "attention",
                "message": f"Piece(s) admin a ajouter manuellement: {', '.join(admin_manquantes)}"
            })

    # --- 2. Verification du nombre de mots du memoire technique ---
    memoire_contenu = ""
    for nom, contenu in dossier.items():
        if "memoire" in nom.lower():
            memoire_contenu = contenu
            break

    if memoire_contenu:
        nb_mots = len(memoire_contenu.split())
        if nb_mots < 1500:
            score -= 20
            alertes.append({
                "niveau": "critique",
                "message": f"Le memoire technique est trop court ({nb_mots} mots, minimum recommande: 3000)"
            })
        elif nb_mots < 3000:
            score -= 10
            alertes.append({
                "niveau": "attention",
                "message": f"Le memoire fait {nb_mots} mots, le RC suggere 3000+"
            })
    else:
        score -= 25
        alertes.append({
            "niveau": "critique",
            "message": "Aucun memoire technique detecte dans le dossier"
        })

    # --- 3. Detection de texte placeholder / generique ---
    placeholders = [
        "[a completer]", "[a renseigner]", "[nom du]", "[votre ",
        "lorem ipsum", "xxx", "[date]", "[montant]", "[a definir]",
        "[inserez", "[ajoutez", "TODO", "FIXME",
    ]
    for nom, contenu in dossier.items():
        contenu_l = contenu.lower()
        for ph in placeholders:
            if ph.lower() in contenu_l:
                score -= 5
                alertes.append({
                    "niveau": "attention",
                    "message": f"Texte placeholder detecte dans {nom}: '{ph}'"
                })
                break  # Un seul placeholder par fichier suffit

    # --- 4. Coherence des prix (BPU vs memoire) ---
    bpu_contenu = ""
    for nom, contenu in dossier.items():
        if "bpu" in nom.lower() or "dpgf" in nom.lower():
            bpu_contenu = contenu
            break

    if bpu_contenu and memoire_contenu:
        # Extraire les montants du BPU
        montants_bpu = re.findall(r"(\d[\d\s]*(?:[.,]\d+)?)\s*(?:EUR|€|euros?)\s*(?:HT|TTC)?", bpu_contenu, re.IGNORECASE)
        montants_memoire = re.findall(r"(\d[\d\s]*(?:[.,]\d+)?)\s*(?:EUR|€|euros?)\s*(?:HT|TTC)?", memoire_contenu, re.IGNORECASE)
        if montants_bpu and not montants_memoire:
            suggestions.append("Le memoire ne mentionne aucun montant alors que le BPU en contient - verifier la coherence")
    elif not bpu_contenu:
        # Verifier si le RC exige un BPU
        for piece in pieces_exigees:
            if "bpu" in piece.lower() or "bordereau" in piece.lower() or "dpgf" in piece.lower():
                score -= 10
                alertes.append({
                    "niveau": "critique",
                    "message": "Le RC exige un BPU/DPGF mais aucun n'est present dans le dossier"
                })
                break

    # --- 5. Verification des criteres d'attribution couverts dans le memoire ---
    criteres = rc_data.get("criteres_attribution", [])
    if criteres and memoire_contenu:
        memoire_lower = memoire_contenu.lower()
        for critere in criteres:
            nom_critere = critere.get("nom", "")
            poids = critere.get("poids", critere.get("poids_pct", 0))
            # Verifier que le critere est au moins mentionne
            mots_critere = [m for m in nom_critere.lower().split() if len(m) > 3]
            couvert = any(m in memoire_lower for m in mots_critere) if mots_critere else True
            if not couvert and poids >= 10:
                score -= 8
                alertes.append({
                    "niveau": "critique" if poids >= 30 else "attention",
                    "message": f"Le critere '{nom_critere}' ({poids}%) ne semble pas traite dans le memoire"
                })

    # --- 6. Conditions de participation ---
    conditions = rc_data.get("conditions_participation", [])
    for cond in conditions:
        cond_lower = cond.lower()
        if "qualiopi" in cond_lower and "qualiopi" not in contenu_lower:
            score -= 10
            alertes.append({
                "niveau": "critique",
                "message": "Qualiopi exige par le RC mais non mentionne dans le dossier"
            })
        if "experience" in cond_lower or "reference" in cond_lower:
            refs_present = any("reference" in nom.lower() for nom in noms_fichiers)
            if not refs_present:
                score -= 5
                alertes.append({
                    "niveau": "attention",
                    "message": f"Condition '{cond}' - pas de fiche references detectee"
                })

    # --- 7. CV formateurs vs exigences RC ---
    cv_contenu = ""
    for nom, contenu in dossier.items():
        if "cv" in nom.lower():
            cv_contenu = contenu
            break

    if cv_contenu:
        # Compter le nombre de CV (heuristique: chercher les separateurs)
        nb_cv = max(1, len(re.findall(r"(?:^|\n)#+ .+(?:formateur|formatrice|intervenant|consultant)", cv_contenu, re.IGNORECASE)))
        for piece in pieces_exigees:
            if "cv" in piece.lower():
                # Si le RC mentionne "CV par formateur" ou similaire
                if "par" in piece.lower() or "chaque" in piece.lower():
                    if nb_cv < 2:
                        score -= 5
                        alertes.append({
                            "niveau": "attention",
                            "message": f"Le RC exige un CV par formateur, seul {nb_cv} CV fourni"
                        })
                break

    # Suggestions generales
    if not any("reference" in nom.lower() for nom in noms_fichiers):
        suggestions.append("Ajouter les references clients specifiques au secteur de l'acheteur")
    if not any("planning" in nom.lower() for nom in noms_fichiers):
        suggestions.append("Ajouter un planning previsionnel detaille")

    # Borner le score
    score = max(0, min(100, score))
    conforme = score >= 50 and not any(a["niveau"] == "critique" for a in alertes)

    return {
        "conforme": conforme,
        "score": score,
        "pieces_manquantes": pieces_manquantes,
        "pieces_presentes": pieces_presentes,
        "alertes": alertes,
        "suggestions": suggestions,
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
