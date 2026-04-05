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
MODELE_REVIEW = "claude-sonnet-4-20250514"  # Haiku non dispo, fallback Sonnet


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

    # Preparer un resume du dossier
    # Pieces principales en entier (memoire, lettre, BPU) - les autres tronquees
    PIECES_PRIORITAIRES = {"memoire", "lettre", "bpu", "dpgf"}
    resume_dossier = ""
    for nom, contenu in fichiers.items():
        nom_lower = nom.lower()
        is_prioritaire = any(p in nom_lower for p in PIECES_PRIORITAIRES)
        if is_prioritaire:
            # Pieces principales : envoyer jusqu'a 6000 chars (suffisant pour voir debut+fin)
            if len(contenu) > 6000:
                extrait = contenu[:3000] + "\n[...milieu omis...]\n" + contenu[-3000:]
            else:
                extrait = contenu
        else:
            # Autres pieces : 2500 chars
            extrait = contenu[:2500] if len(contenu) > 2500 else contenu
        resume_dossier += f"\n\n--- {nom} ---\n{extrait}"

    # Tronquer le resume total a 40000 caracteres
    if len(resume_dossier) > 40000:
        resume_dossier = resume_dossier[:40000] + "\n[... reste omis ...]"

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

ATTENTION - Regles anti faux-positifs :
- Le SIRET 98900455100010 est VALIDE (14 chiffres, format correct). Ne le signale pas comme invalide.
- Les documents peuvent avoir ete TRONQUES pour la review (milieu omis). Ne signale PAS comme "document incomplet" ou "texte coupe" un document dont tu ne vois que le debut et la fin. Juge la qualite sur ce que tu vois.
- Si budget_estime ou date_limite sont "None", signale-le comme "information manquante dans les donnees AO" (severite "mineur"), pas comme probleme critique du dossier.
- Un document est reellement tronque UNIQUEMENT s'il se termine au milieu d'un mot ou d'une phrase sans ponctuation finale.

Sois strict mais juste. Ne signale que les vrais problemes."""

    try:
        reponse = _appel_claude_review(prompt, max_tokens=4000)

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

    # Guard: si rc_data est un str au lieu d'un dict, on ne peut rien faire
    if not isinstance(rc_data, dict):
        return {"conforme": False, "score": 0, "pieces_manquantes": [],
                "pieces_presentes": [], "alertes": ["rc_data invalide (type: {})".format(type(rc_data).__name__)],
                "suggestions": []}

    pieces_exigees = rc_data.get("pieces_exigees", [])
    if isinstance(pieces_exigees, str):
        pieces_exigees = [l.strip() for l in pieces_exigees.split("\n") if l.strip()]
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
    if isinstance(criteres, str):
        criteres = [l.strip() for l in criteres.split("\n") if l.strip()]
    # Criteres a NE PAS chercher dans le memoire (traites dans d'autres pieces)
    CRITERES_HORS_MEMOIRE = ["prix", "cout", "tarif", "montant", "remise", "rabais"]

    if criteres and memoire_contenu:
        memoire_lower = memoire_contenu.lower()
        # Aussi verifier dans tout le dossier pour les criteres hors-memoire
        for critere in criteres:
            # Critere peut etre un dict {"nom": ..., "poids": ...} ou un str "Valeur technique : 40%"
            if isinstance(critere, dict):
                nom_critere = critere.get("nom", "")
                poids = critere.get("poids", critere.get("poids_pct", 0))
            else:
                nom_critere = str(critere)
                import re as _re
                m = _re.search(r'(\d+)\s*%', nom_critere)
                poids = int(m.group(1)) if m else 0

            # Ignorer les criteres prix/cout (traites dans le BPU, pas le memoire)
            nom_lower = nom_critere.lower()
            if any(mot in nom_lower for mot in CRITERES_HORS_MEMOIRE):
                continue

            # Verifier que le critere est au moins mentionne dans le memoire
            mots_critere = [m for m in nom_lower.split() if len(m) > 3]
            couvert = any(m in memoire_lower for m in mots_critere) if mots_critere else True
            if not couvert and poids >= 10:
                score -= 8
                alertes.append({
                    "niveau": "critique" if poids >= 30 else "attention",
                    "message": f"Le critere '{nom_critere}' ({poids}%) ne semble pas traite dans le memoire"
                })

    # --- 6. Conditions de participation ---
    conditions = rc_data.get("conditions_participation", [])
    if isinstance(conditions, str):
        conditions = [l.strip() for l in conditions.split("\n") if l.strip()]
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


def verifier_coherence_inter_documents(fichiers: dict) -> dict:
    """Verifie la coherence croisee entre les documents du dossier (regex, pas d'appel API).

    Args:
        fichiers: dict {nom_fichier: contenu_markdown} de tous les .md du dossier

    Returns:
        dict {score: 0-100, incoherences: [{documents, champ, valeurs, message}]}
    """
    incoherences = []

    # --- Identifier les fichiers par role ---
    memoire = ""
    memoire_nom = ""
    bpu = ""
    bpu_nom = ""
    acte = ""
    acte_nom = ""
    planning = ""
    planning_nom = ""
    cv = ""
    cv_nom = ""
    references = ""
    references_nom = ""

    for nom, contenu in fichiers.items():
        nom_lower = nom.lower()
        if "memoire" in nom_lower:
            memoire = contenu
            memoire_nom = nom
        elif "bpu" in nom_lower or "dpgf" in nom_lower:
            bpu = contenu
            bpu_nom = nom
        elif "acte" in nom_lower and "engagement" in nom_lower:
            acte = contenu
            acte_nom = nom
        elif "planning" in nom_lower:
            planning = contenu
            planning_nom = nom
        elif "cv" in nom_lower:
            cv = contenu
            cv_nom = nom
        elif "reference" in nom_lower:
            references = contenu
            references_nom = nom

    # --- a. Nombre de personnes formees (memoire vs references) ---
    if memoire and references:
        pattern_personnes = re.compile(
            r"(\d+)\s*(?:personnes?\s*form[ée]e?s?|stagiaires?\s*form[ée]e?s?|apprenants?\s*form[ée]e?s?|participants?\s*form[ée]e?s?)",
            re.IGNORECASE,
        )
        nb_memoire = pattern_personnes.findall(memoire)
        nb_refs = pattern_personnes.findall(references)
        if nb_memoire and nb_refs:
            # Comparer le nombre le plus grand cite dans chaque document
            max_memoire = max(int(n) for n in nb_memoire)
            max_refs = max(int(n) for n in nb_refs)
            if max_memoire != max_refs and abs(max_memoire - max_refs) > 50:
                incoherences.append({
                    "documents": [memoire_nom, references_nom],
                    "champ": "nombre_personnes_formees",
                    "valeurs": [str(max_memoire), str(max_refs)],
                    "message": f"Le memoire mentionne {max_memoire} personnes formees mais les references indiquent {max_refs}",
                })

    # --- b. Budget/montant (BPU total vs acte d'engagement) ---
    if bpu and acte:
        pattern_montant = re.compile(
            r"(?:total|montant\s*global|montant\s*total|prix\s*total)[^\d]{0,30}?"
            r"(\d[\d\s]*(?:[.,]\d{1,2})?)\s*(?:EUR|€|euros?)",
            re.IGNORECASE,
        )
        # Aussi chercher les montants avec label "HT" ou "TTC"
        pattern_montant_ht = re.compile(
            r"(\d[\d\s]*(?:[.,]\d{1,2})?)\s*(?:EUR|€|euros?)\s*(?:HT|TTC)",
            re.IGNORECASE,
        )

        def _extraire_montant_principal(texte, nom_doc):
            """Extrait le montant total le plus probable d'un document."""
            # Priorite aux montants explicitement "total"
            totaux = pattern_montant.findall(texte)
            if totaux:
                return totaux[-1]  # Dernier "total" = souvent le grand total
            # Sinon chercher les montants HT/TTC
            ht = pattern_montant_ht.findall(texte)
            if ht:
                return ht[-1]
            return None

        montant_bpu = _extraire_montant_principal(bpu, bpu_nom)
        montant_acte = _extraire_montant_principal(acte, acte_nom)

        if montant_bpu and montant_acte:
            # Normaliser pour comparer
            def _normaliser(m):
                return float(m.replace(" ", "").replace(",", "."))

            try:
                v_bpu = _normaliser(montant_bpu)
                v_acte = _normaliser(montant_acte)
                if v_bpu > 0 and v_acte > 0 and abs(v_bpu - v_acte) / max(v_bpu, v_acte) > 0.01:
                    incoherences.append({
                        "documents": [bpu_nom, acte_nom],
                        "champ": "montant_total",
                        "valeurs": [montant_bpu.strip(), montant_acte.strip()],
                        "message": f"Le BPU indique un total de {montant_bpu.strip()} EUR mais l'acte d'engagement mentionne {montant_acte.strip()} EUR",
                    })
            except (ValueError, ZeroDivisionError):
                pass

    # --- c. Duree (planning vs memoire) ---
    if planning and memoire:
        pattern_duree = re.compile(
            r"(\d+)\s*(?:jours?|heures?|h|j|semaines?|mois)",
            re.IGNORECASE,
        )
        durees_planning = pattern_duree.findall(planning)
        durees_memoire = pattern_duree.findall(memoire)

        if durees_planning and durees_memoire:
            # Chercher specifiquement la duree totale (pattern "duree totale : X jours")
            pattern_duree_totale = re.compile(
                r"(?:dur[ée]e\s*(?:totale|globale|de\s*la\s*formation)?)\s*[:\-]?\s*(\d+)\s*(jours?|heures?|h|j)",
                re.IGNORECASE,
            )
            total_planning = pattern_duree_totale.findall(planning)
            total_memoire = pattern_duree_totale.findall(memoire)

            if total_planning and total_memoire:
                val_p = int(total_planning[0][0])
                unite_p = total_planning[0][1].lower().rstrip("s")
                val_m = int(total_memoire[0][0])
                unite_m = total_memoire[0][1].lower().rstrip("s")

                # Comparer seulement si meme unite
                if unite_p == unite_m and val_p != val_m:
                    incoherences.append({
                        "documents": [planning_nom, memoire_nom],
                        "champ": "duree",
                        "valeurs": [f"{val_p} {total_planning[0][1]}", f"{val_m} {total_memoire[0][1]}"],
                        "message": f"Le planning indique une duree de {val_p} {total_planning[0][1]} mais le memoire mentionne {val_m} {total_memoire[0][1]}",
                    })

    # --- d. Noms formateurs (CV vs memoire) ---
    if cv and memoire:
        # Extraire les noms des formateurs depuis les CV (titres de sections ou "Nom : ...")
        pattern_nom_cv = re.compile(
            r"(?:^#+\s*(?:CV\s*(?:de|:)?\s*)?|(?:Nom|Formateur|Formatrice|Intervenant)\s*[:\-]\s*)"
            r"([A-Z][a-zéèêëàâäùûüôöîïç]+(?:\s+[A-Z][a-zéèêëàâäùûüôöîïç]+)?(?:\s+[A-Z]{2,})?)",
            re.MULTILINE,
        )
        noms_cv = pattern_nom_cv.findall(cv)
        # Nettoyer : garder seulement les noms plausibles (2+ mots ou au moins 4 chars)
        # Filtrer : garder seulement les vrais noms (pas les titres de section generiques)
        FAUX_NOMS = {"Specialites", "Experience", "Formation", "Competences", "Diplomes",
                      "Pertinence", "Organisation", "Certifications", "References",
                      "Objectifs", "Parcours", "Profil", "Missions", "Expertises"}
        noms_cv = [n.strip() for n in noms_cv
                   if len(n.strip()) >= 4 and n.strip().split()[0] not in FAUX_NOMS]

        if noms_cv:
            memoire_lower = memoire.lower()
            noms_absents = []
            for nom in noms_cv:
                # Chercher au moins le nom de famille (dernier mot)
                parties = nom.split()
                nom_famille = parties[-1] if len(parties) > 1 else parties[0]
                if nom_famille.lower() not in memoire_lower and nom.lower() not in memoire_lower:
                    noms_absents.append(nom)

            if noms_absents:
                incoherences.append({
                    "documents": [cv_nom, memoire_nom],
                    "champ": "noms_formateurs",
                    "valeurs": noms_absents,
                    "message": f"Formateur(s) present(s) dans les CV mais absent(s) du memoire technique: {', '.join(noms_absents)}",
                })

    # --- e. Certifications (Qualiopi/RS6776 coherentes entre documents) ---
    certifications_a_verifier = [
        ("Qualiopi", re.compile(r"qualiopi", re.IGNORECASE)),
        ("RS6776", re.compile(r"RS\s*6776", re.IGNORECASE)),
    ]

    for cert_nom, cert_pattern in certifications_a_verifier:
        docs_avec = []
        docs_sans = []
        # Verifier dans les documents cles (memoire, lettre, dc1_dc2, references)
        docs_cles = {}
        for nom, contenu in fichiers.items():
            nom_lower = nom.lower()
            if any(k in nom_lower for k in ["memoire", "lettre", "dc1", "dc2", "reference", "dume"]):
                docs_cles[nom] = contenu

        for nom, contenu in docs_cles.items():
            if cert_pattern.search(contenu):
                docs_avec.append(nom)
            else:
                docs_sans.append(nom)

        # Incoherence seulement si certains documents la mentionnent et d'autres non
        if docs_avec and docs_sans and len(docs_avec) >= 1:
            incoherences.append({
                "documents": docs_avec + docs_sans,
                "champ": f"certification_{cert_nom}",
                "valeurs": [f"present dans {', '.join(docs_avec)}", f"absent de {', '.join(docs_sans)}"],
                "message": f"{cert_nom} est mentionne dans {', '.join(docs_avec)} mais pas dans {', '.join(docs_sans)}",
            })

    # --- Calcul du score ---
    # Chaque incoherence coute des points selon la gravite
    poids_champs = {
        "montant_total": 25,
        "nombre_personnes_formees": 15,
        "duree": 20,
        "noms_formateurs": 15,
        "certification_Qualiopi": 10,
        "certification_RS6776": 5,
    }
    penalite = 0
    for inc in incoherences:
        penalite += poids_champs.get(inc["champ"], 10)

    score = max(0, min(100, 100 - penalite))

    logger.info(f"Coherence inter-documents: score={score}/100, {len(incoherences)} incoherence(s)")
    return {
        "score": score,
        "incoherences": incoherences,
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
