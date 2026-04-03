"""
Groupement / Co-traitance - AO Hunter
Detecte les AO qui depassent la capacite d'Almera seul et suggere un montage en groupement.
"""

import re
from datetime import datetime

# --- Constantes Almera ---
ALMERA_CA = 200_000  # CA annuel ~ 200k EUR
ALMERA_EFFECTIF = 11  # 1 dirigeant + 10 freelances
ALMERA_SPECIALITES = [
    "intelligence artificielle", "ia", "formation", "transformation numerique",
    "data", "prompt engineering", "chatgpt", "machine learning", "deep learning",
    "automatisation", "no-code", "low-code", "competences numeriques",
]
ALMERA_FORMATEURS_MAX_SIMULTANES = 5

# Competences hors perimetre Almera
COMPETENCES_HORS_PERIMETRE = {
    "cybersecurite": ["cybersecurite", "securite informatique", "ssi", "pentest", "soc", "rssi", "iso 27001", "rgpd technique"],
    "infrastructure": ["infrastructure", "datacenter", "reseau", "systeme", "vmware", "cloud infrastructure", "devops", "kubernetes", "docker"],
    "developpement_logiciel": ["developpement logiciel", "dev web", "application mobile", "fullstack", "backend", "frontend", "java", "c#", ".net", "react", "angular"],
    "conseil_strategie": ["conseil en strategie", "transformation organisationnelle", "conduite du changement", "amoa", "moa"],
    "design_ux": ["design", "ux", "ui", "ergonomie", "experience utilisateur"],
    "audit_conformite": ["audit", "conformite", "compliance", "controle interne"],
    "communication": ["communication", "marketing digital", "community management", "reseaux sociaux"],
    "bureautique": ["bureautique", "excel avance", "word", "powerpoint", "office 365"],
    "langues": ["anglais", "espagnol", "allemand", "fle", "francais langue etrangere"],
    "management": ["management", "leadership", "gestion de projet", "prince2", "pmp", "agile", "scrum"],
    "rh_formation": ["ressources humaines", "paie", "droit social", "gpec", "recrutement"],
}


def _extraire_texte_ao(ao: dict) -> str:
    """Extrait tout le texte utile d'un AO pour analyse."""
    parties = [
        ao.get("titre", ""),
        ao.get("description", ""),
        ao.get("criteres_attribution", ""),
    ]
    # Lots
    for lot in ao.get("lots_detectes", []):
        parties.append(lot.get("description", ""))
    return " ".join(parties).lower()


def _extraire_budget(ao: dict) -> float | None:
    """Extrait ou estime le budget de l'AO."""
    if ao.get("budget_estime"):
        try:
            return float(ao["budget_estime"])
        except (ValueError, TypeError):
            pass

    if ao.get("attribution_montant"):
        try:
            return float(ao["attribution_montant"])
        except (ValueError, TypeError):
            pass

    # Tenter d'extraire un montant du texte
    texte = ao.get("description", "") + " " + ao.get("titre", "")
    montants = re.findall(r'(\d[\d\s]*[\d])\s*(?:euros?|EUR|eur)', texte, re.IGNORECASE)
    if montants:
        try:
            return float(montants[0].replace(" ", "").replace("\u00a0", ""))
        except (ValueError, TypeError):
            pass

    return None


def _detecter_competences_manquantes(texte: str) -> list[dict]:
    """Detecte les competences requises hors perimetre Almera."""
    manquantes = []
    for domaine, mots_cles in COMPETENCES_HORS_PERIMETRE.items():
        matches = [kw for kw in mots_cles if kw in texte]
        if matches:
            manquantes.append({
                "domaine": domaine.replace("_", " ").title(),
                "mots_cles_trouves": matches,
                "poids": len(matches),
            })
    return sorted(manquantes, key=lambda x: x["poids"], reverse=True)


def _estimer_volume_formateurs(ao: dict) -> int:
    """Estime le nombre de formateurs requis simultanement."""
    texte = _extraire_texte_ao(ao)

    # Chercher des indices de volume
    nb_lots = len(ao.get("lots_detectes", []))
    if nb_lots > 5:
        return nb_lots

    # Chercher des mentions de sessions simultanees
    patterns = [
        r'(\d+)\s*(?:sessions?\s*simultanee?s?|groupes?\s*simultane)',
        r'(\d+)\s*(?:formateurs?\s*requis|intervenants?\s*requis)',
        r'(\d+)\s*(?:sites?\s*differents|lieux?\s*d.execution)',
    ]
    for pattern in patterns:
        m = re.search(pattern, texte)
        if m:
            try:
                return int(m.group(1))
            except (ValueError, IndexError):
                pass

    # Duree longue + budget eleve = probablement plusieurs formateurs
    duree = ao.get("duree_mois", 0) or 0
    budget = _extraire_budget(ao)
    if budget and budget > 200_000 and duree > 12:
        return 6

    return 1


def _detecter_exigences_rc(ao: dict) -> dict:
    """Detecte les exigences du RC (reglement de consultation)."""
    texte = _extraire_texte_ao(ao)
    exigences = {
        "ca_minimum": None,
        "references_specifiques": [],
        "certifications_requises": [],
    }

    # CA minimum
    ca_patterns = [
        r"chiffre\s*d.affaires?\s*(?:minimum|superieur|au\s*moins)\s*(?:de\s*)?(\d[\d\s]*\d?)\s*(?:euros?|EUR|k)",
        r"ca\s*(?:minimum|>=?|superieur)\s*(\d[\d\s]*\d?)",
    ]
    for pattern in ca_patterns:
        m = re.search(pattern, texte)
        if m:
            try:
                val = float(m.group(1).replace(" ", "").replace("\u00a0", ""))
                if "k" in texte[m.end():m.end()+5].lower():
                    val *= 1000
                exigences["ca_minimum"] = val
            except (ValueError, TypeError):
                pass
            break

    # References dans des domaines specifiques
    ref_patterns = [
        r"references?\s*(?:dans|en)\s*(?:le\s*domaine\s*(?:de\s*(?:la\s*)?)?)?([^,.\n]+)",
        r"experience\s*(?:dans|en)\s*(?:le\s*domaine\s*(?:de\s*(?:la\s*)?)?)?([^,.\n]+)",
    ]
    for pattern in ref_patterns:
        matches = re.findall(pattern, texte)
        for m in matches[:3]:
            domaine = m.strip()
            if len(domaine) > 5 and len(domaine) < 100:
                # Verifier si c'est un domaine qu'Almera ne couvre pas
                est_couvert = any(spec in domaine for spec in ALMERA_SPECIALITES)
                if not est_couvert:
                    exigences["references_specifiques"].append(domaine)

    return exigences


def evaluer_besoin_groupement(ao: dict) -> dict:
    """
    Analyse un AO et determine si un groupement est necessaire.

    Retourne:
        {
            recommande: bool,
            score_besoin: 0-100,
            raisons: [...],
            profil_partenaire_ideal: str,
            type_montage: "co-traitance"|"sous-traitance"|"groupement_conjoint"|"non_necessaire"
        }
    """
    texte = _extraire_texte_ao(ao)
    budget = _extraire_budget(ao)
    competences_manquantes = _detecter_competences_manquantes(texte)
    volume_formateurs = _estimer_volume_formateurs(ao)
    exigences = _detecter_exigences_rc(ao)

    score = 0
    raisons = []

    # 1. Budget vs CA Almera
    if budget:
        if budget > ALMERA_CA:  # > 200k = plus que le CA annuel
            score += 35
            raisons.append(f"Budget estime ({budget:,.0f} EUR) depasse le CA annuel d'Almera ({ALMERA_CA:,.0f} EUR)")
        elif budget > ALMERA_CA * 0.5:  # > 100k = > 50% du CA
            score += 20
            raisons.append(f"Budget estime ({budget:,.0f} EUR) represente plus de 50% du CA d'Almera")
        elif budget > ALMERA_CA * 0.25:  # > 50k
            score += 10
            raisons.append(f"Budget significatif ({budget:,.0f} EUR) par rapport a la taille d'Almera")

    # 2. Competences hors perimetre
    if competences_manquantes:
        poids_total = sum(c["poids"] for c in competences_manquantes)
        score_comp = min(30, poids_total * 8)
        score += score_comp
        domaines = [c["domaine"] for c in competences_manquantes[:3]]
        raisons.append(f"Competences requises hors perimetre : {', '.join(domaines)}")

    # 3. Volume de formateurs
    if volume_formateurs > ALMERA_FORMATEURS_MAX_SIMULTANES:
        score += 15
        raisons.append(f"Volume de {volume_formateurs} formateurs simultanes depasse la capacite ({ALMERA_FORMATEURS_MAX_SIMULTANES})")
    elif volume_formateurs > 3:
        score += 5
        raisons.append(f"Volume de {volume_formateurs} formateurs : capacite tendue")

    # 4. Exigence de CA minimum dans le RC
    if exigences["ca_minimum"]:
        if exigences["ca_minimum"] > ALMERA_CA:
            score += 20
            raisons.append(f"CA minimum exige ({exigences['ca_minimum']:,.0f} EUR) superieur au CA d'Almera")
        elif exigences["ca_minimum"] > ALMERA_CA * 0.5:
            score += 10
            raisons.append(f"CA minimum exige ({exigences['ca_minimum']:,.0f} EUR) represente un risque")

    # 5. References dans un domaine non couvert
    if exigences["references_specifiques"]:
        score += 10
        raisons.append(f"References requises dans : {', '.join(exigences['references_specifiques'][:2])}")

    # Plafonner a 100
    score = min(100, score)

    # Determiner le type de montage
    recommande = score >= 30
    if score >= 60:
        if competences_manquantes and len(competences_manquantes) >= 2:
            type_montage = "groupement_conjoint"
        else:
            type_montage = "co-traitance"
    elif score >= 30:
        type_montage = "sous-traitance"
    else:
        type_montage = "non_necessaire"

    # Profil partenaire ideal
    profil_parts = []
    if competences_manquantes:
        domaines = [c["domaine"] for c in competences_manquantes[:3]]
        profil_parts.append(f"Expert en {', '.join(domaines)}")
    if budget and budget > ALMERA_CA:
        profil_parts.append(f"CA superieur a {budget * 0.3:,.0f} EUR")
    if volume_formateurs > ALMERA_FORMATEURS_MAX_SIMULTANES:
        surplus = volume_formateurs - ALMERA_FORMATEURS_MAX_SIMULTANES
        profil_parts.append(f"Equipe de {surplus}+ formateurs disponibles")
    if exigences["references_specifiques"]:
        profil_parts.append(f"References en {', '.join(exigences['references_specifiques'][:2])}")
    if not profil_parts:
        profil_parts.append("Partenaire complementaire avec references marches publics")

    profil_partenaire = ". ".join(profil_parts)

    return {
        "recommande": recommande,
        "score_besoin": score,
        "raisons": raisons,
        "profil_partenaire_ideal": profil_partenaire,
        "type_montage": type_montage,
        "competences_manquantes": competences_manquantes,
        "volume_formateurs": volume_formateurs,
        "budget_estime": budget,
        "exigences_rc": exigences,
    }


def suggestions_partenaires(ao: dict) -> list[str]:
    """
    Suggere des types de partenaires a chercher selon les competences manquantes.
    """
    texte = _extraire_texte_ao(ao)
    competences_manquantes = _detecter_competences_manquantes(texte)
    suggestions = []

    mapping_partenaires = {
        "Cybersecurite": "SSII specialisee en cybersecurite (ex: cabinet de conseil SSI, integrator securite)",
        "Infrastructure": "ESN avec expertise infrastructure / cloud (hebergeur, integrator reseau)",
        "Developpement Logiciel": "Societe de developpement logiciel ou ESN",
        "Conseil Strategie": "Cabinet de conseil en transformation / organisation",
        "Design Ux": "Agence UX/UI ou studio de design numerique",
        "Audit Conformite": "Cabinet d'audit / conformite / expertise comptable",
        "Communication": "Agence de communication digitale",
        "Bureautique": "Organisme de formation bureautique / certifie TOSA-ICDL",
        "Langues": "Organisme de formation en langues (certifie Qualiopi)",
        "Management": "Cabinet de formation en management / leadership",
        "Rh Formation": "Cabinet RH / organisme de formation RH",
    }

    for comp in competences_manquantes:
        suggestion = mapping_partenaires.get(comp["domaine"])
        if suggestion:
            suggestions.append(suggestion)

    # Suggestions generiques selon le budget
    budget = _extraire_budget(ao)
    if budget and budget > ALMERA_CA:
        suggestions.append("Organisme de formation de taille superieure (CA > 500k EUR) pour porter le groupement")

    if not suggestions:
        suggestions.append("Pas de partenaire specifique identifie - Almera peut repondre seul")

    return suggestions


def generer_documents_groupement(ao: dict, partenaire_info: dict | None = None) -> dict:
    """
    Pre-genere les documents de groupement.

    Args:
        ao: donnees de l'AO
        partenaire_info: {nom, siret, adresse, representant, ca, specialites, email, tel}

    Retourne:
        {convention: str, dc1_groupement: str, repartition: str}
    """
    # Infos Almera
    almera = {
        "nom": "AI MENTOR (Almera)",
        "siret": "989 004 551 00010",
        "adresse": "25 rue Campagne Premiere, 75014 Paris",
        "representant": "Mickael Bertolla, President",
        "nda": "11757431975",
    }

    # Infos partenaire (ou placeholder)
    partenaire = partenaire_info or {
        "nom": "[NOM DU PARTENAIRE]",
        "siret": "[SIRET]",
        "adresse": "[ADRESSE]",
        "representant": "[NOM ET QUALITE]",
        "ca": "[CA]",
        "specialites": "[SPECIALITES]",
        "email": "[EMAIL]",
        "tel": "[TEL]",
    }

    eval_groupement = evaluer_besoin_groupement(ao)
    type_montage = eval_groupement["type_montage"]
    date_str = datetime.now().strftime("%d/%m/%Y")
    budget = _extraire_budget(ao) or 0

    # --- Convention de groupement ---
    if type_montage == "groupement_conjoint":
        type_label = "groupement conjoint"
        solidarite = "Chaque membre est responsable de l'execution de sa part de prestations."
    else:
        type_label = "groupement solidaire (co-traitance)"
        solidarite = "Les membres du groupement sont solidairement responsables de l'execution de l'ensemble des prestations."

    convention = f"""# Convention de groupement

## Marche : {ao.get('titre', '[TITRE AO]')}

**Acheteur** : {ao.get('acheteur', '[ACHETEUR]')}
**Reference** : {ao.get('id', '[REF]')}
**Date** : {date_str}

---

## Article 1 - Objet

La presente convention a pour objet de definir les conditions dans lesquelles les membres du groupement s'engagent a repondre conjointement a l'appel d'offres cite en reference, sous la forme d'un **{type_label}**.

## Article 2 - Membres du groupement

### Mandataire (representant du groupement)
- **Raison sociale** : {almera['nom']}
- **SIRET** : {almera['siret']}
- **Adresse** : {almera['adresse']}
- **Representant** : {almera['representant']}
- **NDA** : {almera['nda']}

### Membre du groupement
- **Raison sociale** : {partenaire['nom']}
- **SIRET** : {partenaire.get('siret', '[SIRET]')}
- **Adresse** : {partenaire.get('adresse', '[ADRESSE]')}
- **Representant** : {partenaire.get('representant', '[NOM ET QUALITE]')}

## Article 3 - Mandataire

{almera['nom']} est designe mandataire du groupement. A ce titre, le mandataire :
- Represente l'ensemble des membres du groupement aupres de l'acheteur
- Coordonne les prestations entre les membres
- Est l'interlocuteur unique de l'acheteur pour les questions administratives et financieres
- Signe les actes d'engagement au nom du groupement

## Article 4 - Repartition des prestations

| Membre | Part (%) | Domaine d'intervention |
|--------|----------|----------------------|
| {almera['nom']} | [__]% | Formation IA, transformation numerique, accompagnement |
| {partenaire['nom']} | [__]% | {partenaire.get('specialites', '[SPECIALITES]')} |

Le montant total estimatif du marche est de {budget:,.0f} EUR HT.

## Article 5 - Responsabilite

{solidarite}

## Article 6 - Conditions financieres

- La facturation sera effectuee par chaque membre pour sa part de prestations
- Le mandataire pourra facturer l'ensemble et reverser leur part aux membres
- Les conditions de paiement suivent celles du marche public

## Article 7 - Duree

La presente convention prend effet a la date de sa signature et prend fin a l'expiration du marche, y compris les periodes de garantie eventuelles.

## Article 8 - Resiliation

En cas de defaillance d'un membre, le mandataire en informe immediatement l'acheteur et prend les mesures necessaires pour assurer la continuite des prestations.

---

**Signatures :**

Pour {almera['nom']} :
Nom : {almera['representant']}
Date : {date_str}
Signature :



Pour {partenaire['nom']} :
Nom : {partenaire.get('representant', '[NOM ET QUALITE]')}
Date : {date_str}
Signature :
"""

    # --- DC1 Groupement ---
    dc1_groupement = f"""# DC1 - Lettre de candidature (Groupement)

## Marche : {ao.get('titre', '[TITRE AO]')}

**Acheteur** : {ao.get('acheteur', '[ACHETEUR]')}
**Reference** : {ao.get('id', '[REF]')}

---

## 1. Identification du groupement

**Forme du groupement** : {type_label.capitalize()}
**Mandataire** : {almera['nom']}

## 2. Membres du groupement

### Mandataire
| | |
|---|---|
| Denomination | {almera['nom']} |
| SIRET | {almera['siret']} |
| Adresse | {almera['adresse']} |
| Representant | {almera['representant']} |
| Email | contact@almera.one |
| Tel | 06 86 68 06 11 |

### Membre 2
| | |
|---|---|
| Denomination | {partenaire['nom']} |
| SIRET | {partenaire.get('siret', '[SIRET]')} |
| Adresse | {partenaire.get('adresse', '[ADRESSE]')} |
| Representant | {partenaire.get('representant', '[NOM ET QUALITE]')} |
| Email | {partenaire.get('email', '[EMAIL]')} |
| Tel | {partenaire.get('tel', '[TEL]')} |

## 3. Habilitation du mandataire

Les membres du groupement designent {almera['nom']} en qualite de mandataire, qui est habilite a :
- Signer l'offre au nom du groupement
- Signer le marche au nom du groupement
- Coordonner l'execution des prestations

## 4. Engagement

Les membres du groupement s'engagent conjointement et solidairement a realiser les prestations objet du present marche conformement aux conditions definies dans l'offre.

---

Date : {date_str}

Pour le mandataire {almera['nom']} :
{almera['representant']}



Pour {partenaire['nom']} :
{partenaire.get('representant', '[NOM ET QUALITE]')}
"""

    # --- Repartition des prestations ---
    competences_manquantes = eval_groupement.get("competences_manquantes", [])
    domaines_partenaire = [c["domaine"] for c in competences_manquantes[:3]] if competences_manquantes else ["[DOMAINES PARTENAIRE]"]

    repartition = f"""# Repartition des prestations

## Marche : {ao.get('titre', '[TITRE AO]')}

---

## Prestations {almera['nom']} (Mandataire)

| Prestation | Description | Part estimee |
|-----------|-------------|-------------|
| Formation IA | Conception et animation des formations en intelligence artificielle | [__]% |
| Ingenierie pedagogique | Conception des programmes, supports, evaluations | [__]% |
| Coordination projet | Pilotage global, reporting, relation acheteur | [__]% |
| Transformation numerique | Accompagnement au changement, strategie IA | [__]% |

**Competences cles** : IA generative, prompt engineering, data science, formation professionnelle, certification Qualiopi

---

## Prestations {partenaire['nom']}

| Prestation | Description | Part estimee |
|-----------|-------------|-------------|
"""

    for domaine in domaines_partenaire:
        repartition += f"| {domaine} | [DESCRIPTION] | [__]% |\n"

    repartition += f"""
**Competences cles** : {', '.join(domaines_partenaire)}

---

## Synthese

| Membre | Part globale | Montant estime HT |
|--------|-------------|-------------------|
| {almera['nom']} | [__]% | {budget * 0.6:,.0f} EUR |
| {partenaire['nom']} | [__]% | {budget * 0.4:,.0f} EUR |
| **Total** | **100%** | **{budget:,.0f} EUR** |

---

## Planning d'intervention

Les interventions seront coordonnees par le mandataire selon le planning suivant :
1. **Phase de cadrage** : Definition conjointe du plan d'intervention
2. **Phase de realisation** : Execution des prestations par chaque membre selon sa part
3. **Phase de suivi** : Points d'avancement reguliers, reporting unifie

Date : {date_str}
"""

    return {
        "convention": convention,
        "dc1_groupement": dc1_groupement,
        "repartition": repartition,
    }
