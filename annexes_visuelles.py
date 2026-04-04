"""
Generateur d'annexes visuelles professionnelles pour les dossiers AO.
Produit des fichiers HTML/CSS/SVG autonomes (aucune dependance externe).
Chaque visuel est ouvrable dans un navigateur et imprimable.
"""

import re
import math
import logging
from pathlib import Path
from datetime import datetime, timedelta

logger = logging.getLogger("ao_hunter.annexes_visuelles")

# ---------------------------------------------------------------------------
# Style CSS commun Almera
# ---------------------------------------------------------------------------
ALMERA_CSS = """
<style>
  @page { size: A4; margin: 15mm; }
  @media print {
    body { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
    .no-print { display: none; }
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: Calibri, 'Segoe UI', Arial, sans-serif;
    color: #1e293b;
    background: #fff;
    line-height: 1.5;
    padding: 24px;
  }
  .almera-header {
    display: flex; justify-content: space-between; align-items: center;
    border-bottom: 3px solid #1E3A5F; padding-bottom: 10px; margin-bottom: 24px;
  }
  .almera-header .logo-text {
    font-size: 22px; font-weight: 700; color: #1E3A5F; letter-spacing: 1px;
  }
  .almera-header .site {
    font-size: 12px; color: #64748b;
  }
  h1 { font-size: 20px; color: #1E3A5F; margin-bottom: 16px; }
  h2 { font-size: 16px; color: #2563EB; margin: 20px 0 10px; }
  .subtitle { font-size: 13px; color: #64748b; margin-bottom: 20px; }
</style>
"""

ALMERA_HEADER_HTML = """
<div class="almera-header">
  <span class="logo-text">ALMERA</span>
  <span class="site">almera.one</span>
</div>
"""


def _html_wrapper(title: str, body: str, extra_css: str = "") -> str:
    """Emballe le contenu dans un document HTML complet."""
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} - Almera</title>
{ALMERA_CSS}
<style>{extra_css}</style>
</head>
<body>
{ALMERA_HEADER_HTML}
{body}
</body>
</html>"""


# ---------------------------------------------------------------------------
# 1. Planning Gantt
# ---------------------------------------------------------------------------

def _parse_planning_md(planning_md: str) -> list[dict]:
    """Extrait les phases depuis le planning_previsionnel.md."""
    phases = []
    # Patterns courants : "## Phase 1 : Cadrage (Semaine 1)"
    # ou "| Phase 1 | Cadrage | S1-S2 |" (tableau)
    # ou "### 1. Cadrage - Semaine 1 a 2"

    lines = planning_md.split("\n") if planning_md else []
    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Pattern "## Phase X : Nom" ou "### X. Nom"
        m = re.match(r"(?:#{2,4}\s*)?(?:Phase\s*\d+\s*[:.-]\s*|(\d+)\.\s*)(.+)", line, re.IGNORECASE)
        if m:
            nom = m.group(2).strip()
            # Extraire duree si mentionnee
            duree_m = re.search(r"(\d+)\s*(?:semaine|sem|S)", nom, re.IGNORECASE)
            duree = int(duree_m.group(1)) if duree_m else 1
            # Nettoyer le nom
            nom_clean = re.sub(r"\s*[\(\[].+?[\)\]]", "", nom).strip(" -:*")
            if nom_clean and len(nom_clean) > 2:
                phases.append({"nom": nom_clean, "duree_sem": duree})
            continue

        # Pattern tableau "| ... | ... |"
        if "|" in line and not line.startswith("|--"):
            cols = [c.strip() for c in line.split("|") if c.strip()]
            if len(cols) >= 2:
                nom = cols[1] if len(cols) > 2 else cols[0]
                duree_m = re.search(r"(\d+)", cols[-1]) if cols[-1] else None
                duree = int(duree_m.group(1)) if duree_m else 1
                nom_clean = re.sub(r"\s*[\(\[].+?[\)\]]", "", nom).strip(" -:*#")
                if nom_clean and len(nom_clean) > 2 and not any(
                    w in nom_clean.lower() for w in ["phase", "etape", "---", "duree", "periode"]
                ):
                    phases.append({"nom": nom_clean, "duree_sem": min(duree, 12)})

    return phases


def _phases_par_defaut(ao: dict) -> list[dict]:
    """Genere des phases par defaut basees sur la duree du marche."""
    description = ao.get("description", "") + " " + ao.get("titre", "")
    is_formation = any(w in description.lower() for w in ["formation", "formateur", "pedagogique", "stagiaire"])

    if is_formation:
        return [
            {"nom": "Cadrage et analyse des besoins", "duree_sem": 1},
            {"nom": "Ingenierie pedagogique", "duree_sem": 2},
            {"nom": "Preparation logistique", "duree_sem": 1},
            {"nom": "Sessions de formation", "duree_sem": 4},
            {"nom": "Evaluation et certification", "duree_sem": 1},
            {"nom": "Bilan et preconisations", "duree_sem": 1},
        ]
    else:
        return [
            {"nom": "Cadrage et diagnostic", "duree_sem": 2},
            {"nom": "Conception de la solution", "duree_sem": 3},
            {"nom": "Realisation et deploiement", "duree_sem": 4},
            {"nom": "Recette et ajustements", "duree_sem": 2},
            {"nom": "Bilan et transfert", "duree_sem": 1},
        ]


def generer_gantt(ao: dict, planning_md: str = "") -> str:
    """Genere un diagramme de Gantt en HTML/CSS pur."""
    phases = _parse_planning_md(planning_md) if planning_md else []
    if not phases or len(phases) < 2:
        phases = _phases_par_defaut(ao)

    # Calculer les positions
    total_sem = sum(p["duree_sem"] for p in phases)
    if total_sem == 0:
        total_sem = 10

    couleurs = ["#1E3A5F", "#2563EB", "#16a34a", "#7c3aed", "#dc2626", "#d97706", "#0891b2", "#be185d"]

    # Construire les barres
    barres_html = ""
    offset = 0
    for i, phase in enumerate(phases):
        pct_left = (offset / total_sem) * 100
        pct_width = (phase["duree_sem"] / total_sem) * 100
        couleur = couleurs[i % len(couleurs)]
        sem_debut = offset + 1
        sem_fin = offset + phase["duree_sem"]
        label_sem = f"S{sem_debut}" if sem_debut == sem_fin else f"S{sem_debut}-S{sem_fin}"

        barres_html += f"""
        <div class="gantt-row">
          <div class="gantt-label">{phase["nom"]}</div>
          <div class="gantt-track">
            <div class="gantt-bar" style="left:{pct_left:.1f}%;width:{pct_width:.1f}%;background:{couleur};">
              <span class="gantt-bar-text">{label_sem}</span>
            </div>
          </div>
        </div>"""
        offset += phase["duree_sem"]

    # Graduations
    grads_html = ""
    step = max(1, total_sem // 12)
    for s in range(0, total_sem + 1, step):
        pct = (s / total_sem) * 100
        grads_html += f'<div class="gantt-grad" style="left:{pct:.1f}%"><span>S{s}</span></div>'

    titre = ao.get("titre", "Appel d'offres")

    extra_css = """
    .gantt-container { margin-top: 10px; }
    .gantt-row { display: flex; align-items: center; margin-bottom: 6px; min-height: 38px; }
    .gantt-label {
      width: 240px; min-width: 240px; font-size: 13px; font-weight: 600;
      color: #334155; padding-right: 16px; text-align: right;
    }
    .gantt-track {
      flex: 1; position: relative; height: 32px;
      background: #f1f5f9; border-radius: 4px; overflow: visible;
    }
    .gantt-bar {
      position: absolute; top: 2px; height: 28px; border-radius: 4px;
      display: flex; align-items: center; justify-content: center;
      color: #fff; font-size: 11px; font-weight: 600;
      box-shadow: 0 1px 3px rgba(0,0,0,0.15);
      transition: opacity 0.2s;
    }
    .gantt-bar:hover { opacity: 0.85; }
    .gantt-bar-text { white-space: nowrap; text-shadow: 0 1px 2px rgba(0,0,0,0.3); }
    .gantt-graduation {
      position: relative; height: 24px; margin-left: 240px; margin-top: 4px;
      border-top: 1px solid #cbd5e1;
    }
    .gantt-grad {
      position: absolute; top: 0; transform: translateX(-50%);
    }
    .gantt-grad span {
      font-size: 10px; color: #94a3b8; display: block; padding-top: 4px;
    }
    .gantt-legend {
      margin-top: 20px; padding: 12px 16px; background: #f8fafc;
      border-radius: 6px; font-size: 12px; color: #64748b;
      border-left: 4px solid #1E3A5F;
    }
    """

    body = f"""
    <h1>Planning previsionnel</h1>
    <p class="subtitle">{titre}</p>
    <div class="gantt-container">
      {barres_html}
      <div class="gantt-graduation">
        {grads_html}
      </div>
    </div>
    <div class="gantt-legend">
      Duree totale estimee : <strong>{total_sem} semaines</strong> | {len(phases)} phases
    </div>
    """

    return _html_wrapper("Planning Gantt", body, extra_css)


# ---------------------------------------------------------------------------
# 2. Organigramme equipe projet
# ---------------------------------------------------------------------------

def _parse_cv_md(cv_md: str) -> list[dict]:
    """Extrait les formateurs depuis cv_formateurs.md."""
    formateurs = []
    if not cv_md:
        return formateurs

    # Pattern "## Nom Prenom" ou "### Nom Prenom" ou "**Nom Prenom**"
    blocks = re.split(r"(?=#{2,3}\s+|\*\*[A-Z])", cv_md)
    for block in blocks:
        nom_m = re.match(r"(?:#{2,3}\s+|\*\*)(.+?)(?:\*\*)?$", block.split("\n")[0])
        if not nom_m:
            continue
        nom = nom_m.group(1).strip(" *#")
        # Chercher le role
        role_m = re.search(r"(?:Role|Fonction|Poste)\s*[:]\s*(.+)", block, re.IGNORECASE)
        role = role_m.group(1).strip() if role_m else ""
        # Chercher les specialites
        spec_m = re.search(r"(?:Specialit|Expertise|Competence)\s*[:]\s*(.+)", block, re.IGNORECASE)
        spec = spec_m.group(1).strip() if spec_m else ""

        if nom and len(nom) > 2:
            formateurs.append({"nom": nom, "role": role, "specialites": spec})

    return formateurs


def generer_organigramme(ao: dict, cv_md: str = "") -> str:
    """Genere un organigramme hierarchique de l'equipe projet en HTML/CSS."""
    description = ao.get("description", "") + " " + ao.get("titre", "")
    is_formation = any(w in description.lower() for w in ["formation", "formateur", "pedagogique"])

    # Chef de projet toujours Mickael Bertolla
    chef = {"nom": "Mickael Bertolla", "role": "Chef de projet", "detail": "President Almera"}

    # Equipe depuis le CV ou par defaut
    equipe = _parse_cv_md(cv_md) if cv_md else []
    if not equipe:
        # Selectionner les formateurs pertinents depuis les donnees entreprise
        if is_formation:
            equipe = [
                {"nom": "Charles Courbet", "role": "Formateur senior", "specialites": "IA creative, Midjourney"},
                {"nom": "Romy Chen", "role": "Formatrice", "specialites": "Prompt engineering, Marketing IA"},
                {"nom": "Guillaume Martin", "role": "Formateur", "specialites": "Microsoft Copilot, Power Platform"},
            ]
        else:
            equipe = [
                {"nom": "Yann Delaporte", "role": "Consultant senior", "specialites": "LLM, deploiement IA"},
                {"nom": "Stephanie Moreau", "role": "Consultante", "specialites": "No-code, automatisation"},
                {"nom": "Romy Chen", "role": "Consultante", "specialites": "SEO/GEO, marketing IA"},
            ]

    # Support admin
    support = {"nom": "Support administratif", "role": "Coordination logistique", "detail": "Almera"}

    # Construire le HTML
    type_label = "Formateur" if is_formation else "Consultant"

    equipe_boxes = ""
    for m in equipe:
        spec = m.get("specialites", m.get("role", ""))
        equipe_boxes += f"""
        <div class="org-card org-member">
          <div class="org-name">{m["nom"]}</div>
          <div class="org-role">{m.get("role", type_label)}</div>
          <div class="org-spec">{spec}</div>
        </div>"""

    titre = ao.get("titre", "Appel d'offres")

    extra_css = """
    .org-container { display: flex; flex-direction: column; align-items: center; gap: 0; }
    .org-card {
      border: 2px solid #e2e8f0; border-radius: 8px; padding: 14px 20px;
      text-align: center; background: #fff; min-width: 200px;
      box-shadow: 0 2px 6px rgba(0,0,0,0.06);
    }
    .org-chef {
      border-color: #1E3A5F; background: #1E3A5F; color: #fff;
      min-width: 260px;
    }
    .org-chef .org-name { font-size: 16px; font-weight: 700; }
    .org-chef .org-role { font-size: 12px; opacity: 0.85; margin-top: 2px; }
    .org-chef .org-spec { font-size: 11px; opacity: 0.7; margin-top: 4px; }
    .org-connector {
      width: 2px; height: 28px; background: #cbd5e1; margin: 0 auto;
    }
    .org-branch-line {
      height: 2px; background: #cbd5e1; margin: 0 40px;
    }
    .org-team {
      display: flex; gap: 20px; justify-content: center; flex-wrap: wrap;
      position: relative; padding-top: 28px;
    }
    .org-team::before {
      content: ''; position: absolute; top: 0; left: 50%;
      transform: translateX(-50%); width: 2px; height: 28px;
      background: #cbd5e1;
    }
    .org-team-wrapper { position: relative; }
    .org-team-wrapper::before {
      content: ''; position: absolute; top: -28px;
      left: 0; right: 0; height: 2px; background: #cbd5e1;
    }
    .org-member {
      border-color: #2563EB; position: relative;
    }
    .org-member::before {
      content: ''; position: absolute; top: -28px; left: 50%;
      transform: translateX(-50%); width: 2px; height: 28px;
      background: #cbd5e1;
    }
    .org-name { font-size: 14px; font-weight: 700; color: #1e293b; }
    .org-chef .org-name { color: #fff; }
    .org-role { font-size: 12px; color: #2563EB; margin-top: 2px; }
    .org-spec { font-size: 11px; color: #64748b; margin-top: 4px; }
    .org-support {
      border-color: #16a34a; margin-top: 0;
    }
    .org-support .org-role { color: #16a34a; }
    .org-support-wrapper {
      display: flex; flex-direction: column; align-items: center;
      margin-top: 0;
    }
    .org-section-label {
      font-size: 11px; color: #94a3b8; text-transform: uppercase;
      letter-spacing: 1px; margin: 8px 0 4px; font-weight: 600;
    }
    """

    body = f"""
    <h1>Organigramme de l'equipe projet</h1>
    <p class="subtitle">{titre}</p>

    <div class="org-container">
      <div class="org-section-label">Direction de projet</div>
      <div class="org-card org-chef">
        <div class="org-name">{chef["nom"]}</div>
        <div class="org-role">{chef["role"]}</div>
        <div class="org-spec">{chef["detail"]}</div>
      </div>

      <div class="org-connector"></div>
      <div class="org-section-label">Equipe {type_label.lower()}s</div>

      <div class="org-team-wrapper">
        <div class="org-team">
          {equipe_boxes}
        </div>
      </div>

      <div class="org-connector"></div>
      <div class="org-section-label">Support</div>

      <div class="org-support-wrapper">
        <div class="org-card org-support">
          <div class="org-name">{support["nom"]}</div>
          <div class="org-role">{support["role"]}</div>
          <div class="org-spec">{support["detail"]}</div>
        </div>
      </div>
    </div>
    """

    return _html_wrapper("Organigramme equipe", body, extra_css)


# ---------------------------------------------------------------------------
# 3. Radar de competences (SVG inline)
# ---------------------------------------------------------------------------

def generer_radar_competences(ao: dict) -> str:
    """Genere un radar chart SVG des competences Almera vs. besoins AO."""
    description = (ao.get("description", "") + " " + ao.get("titre", "")).lower()

    # Axes et scores (0-100)
    axes_possibles = [
        ("IA Generative", 95, ["ia generative", "chatgpt", "gpt", "llm", "ia", "intelligence artificielle"]),
        ("Prompt Engineering", 90, ["prompt", "engineering", "ingenierie de prompt"]),
        ("Data & Analytics", 75, ["data", "donnees", "analytics", "analyse"]),
        ("Transformation Digitale", 85, ["transformation", "digital", "numerique", "transition"]),
        ("Pedagogie", 92, ["formation", "pedagogie", "pedagogique", "formateur", "stagiaire", "apprenant"]),
        ("Accompagnement", 88, ["accompagnement", "conseil", "consulting", "suivi", "coaching"]),
        ("Automatisation", 82, ["automatisation", "automation", "process", "workflow", "no-code", "make"]),
        ("Design IA", 78, ["midjourney", "image", "creatif", "creative", "design", "visuel"]),
        ("Microsoft / Copilot", 80, ["microsoft", "copilot", "office", "365", "power platform"]),
        ("Certification", 90, ["certification", "certifie", "qualiopi", "rs6776", "competences"]),
    ]

    # Selectionner les 6 axes les plus pertinents pour cet AO
    scored_axes = []
    for nom, score, keywords in axes_possibles:
        relevance = sum(1 for kw in keywords if kw in description)
        scored_axes.append((nom, score, relevance))

    # Toujours garder Pedagogie et IA Generative, trier le reste par pertinence
    must_have = [a for a in scored_axes if a[0] in ("IA Generative", "Pedagogie")]
    others = sorted([a for a in scored_axes if a[0] not in ("IA Generative", "Pedagogie")],
                    key=lambda x: x[2], reverse=True)
    selected = must_have + others[:4]
    # S'assurer d'avoir 6 axes
    while len(selected) < 6:
        for a in others:
            if a not in selected:
                selected.append(a)
            if len(selected) >= 6:
                break

    selected = selected[:6]
    n = len(selected)
    noms = [a[0] for a in selected]
    scores = [a[1] for a in selected]

    # Parametres SVG
    cx, cy = 250, 220
    r_max = 160
    angle_offset = -math.pi / 2  # Commencer en haut

    def polar_to_xy(angle, radius):
        x = cx + radius * math.cos(angle)
        y = cy + radius * math.sin(angle)
        return x, y

    # Grille concentrique
    grille_svg = ""
    for level in [0.25, 0.5, 0.75, 1.0]:
        r = r_max * level
        points = []
        for i in range(n):
            angle = angle_offset + (2 * math.pi * i / n)
            x, y = polar_to_xy(angle, r)
            points.append(f"{x:.1f},{y:.1f}")
        grille_svg += f'<polygon points="{" ".join(points)}" fill="none" stroke="#e2e8f0" stroke-width="1"/>\n'

    # Axes
    axes_svg = ""
    for i in range(n):
        angle = angle_offset + (2 * math.pi * i / n)
        x, y = polar_to_xy(angle, r_max + 5)
        axes_svg += f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="#cbd5e1" stroke-width="1"/>\n'

    # Polygone des scores
    score_points = []
    for i in range(n):
        angle = angle_offset + (2 * math.pi * i / n)
        r = r_max * (scores[i] / 100)
        x, y = polar_to_xy(angle, r)
        score_points.append(f"{x:.1f},{y:.1f}")

    polygon_svg = f'<polygon points="{" ".join(score_points)}" fill="rgba(37,99,235,0.15)" stroke="#2563EB" stroke-width="2.5"/>\n'

    # Points et labels
    points_svg = ""
    labels_svg = ""
    for i in range(n):
        angle = angle_offset + (2 * math.pi * i / n)
        r = r_max * (scores[i] / 100)
        x, y = polar_to_xy(angle, r)
        points_svg += f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="#2563EB" stroke="#fff" stroke-width="2"/>\n'

        # Score text
        points_svg += f'<text x="{x:.1f}" y="{y - 10:.1f}" text-anchor="middle" font-size="11" font-weight="700" fill="#1E3A5F">{scores[i]}%</text>\n'

        # Labels
        lx, ly = polar_to_xy(angle, r_max + 28)
        anchor = "middle"
        if lx < cx - 20:
            anchor = "end"
        elif lx > cx + 20:
            anchor = "start"

        labels_svg += f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" font-size="12" font-weight="600" fill="#334155">{noms[i]}</text>\n'

    svg = f"""
    <svg viewBox="0 0 500 440" xmlns="http://www.w3.org/2000/svg" style="max-width:520px;margin:0 auto;display:block;">
      {grille_svg}
      {axes_svg}
      {polygon_svg}
      {points_svg}
      {labels_svg}
    </svg>
    """

    titre = ao.get("titre", "Appel d'offres")

    extra_css = """
    .radar-container { text-align: center; }
    .radar-legend {
      display: flex; flex-wrap: wrap; gap: 8px 20px; justify-content: center;
      margin-top: 16px; padding: 12px; background: #f8fafc; border-radius: 6px;
    }
    .radar-legend-item {
      font-size: 12px; color: #475569; display: flex; align-items: center; gap: 6px;
    }
    .radar-dot {
      width: 10px; height: 10px; border-radius: 50%; background: #2563EB;
    }
    .radar-note {
      margin-top: 12px; font-size: 11px; color: #94a3b8; text-align: center;
    }
    """

    legend_items = "".join(
        f'<span class="radar-legend-item"><span class="radar-dot"></span>{noms[i]} : {scores[i]}%</span>'
        for i in range(n)
    )

    body = f"""
    <h1>Cartographie des competences</h1>
    <p class="subtitle">{titre}</p>
    <div class="radar-container">
      {svg}
      <div class="radar-legend">{legend_items}</div>
      <div class="radar-note">Scores bases sur le catalogue de formations, les certifications et les references Almera</div>
    </div>
    """

    return _html_wrapper("Radar competences", body, extra_css)


# ---------------------------------------------------------------------------
# 4. Tableau de synthese des references
# ---------------------------------------------------------------------------

def _parse_references_md(refs_md: str) -> list[dict]:
    """Extrait les references depuis references_clients.md."""
    refs = []
    if not refs_md:
        return refs

    blocks = re.split(r"(?=#{2,3}\s+)", refs_md)
    for block in blocks:
        lines = block.strip().split("\n")
        if not lines:
            continue

        # Extraire le nom du client depuis le titre
        titre_m = re.match(r"#{2,3}\s+(.+)", lines[0])
        if not titre_m:
            continue
        client = titre_m.group(1).strip(" *#-")

        # Chercher les infos dans le bloc
        secteur = ""
        prestation = ""
        nb_formes = ""
        annee = ""
        satisfaction = ""

        for line in lines[1:]:
            l = line.strip().lower()
            if "secteur" in l:
                secteur = re.sub(r".*?[:]\s*", "", line.strip(), count=1).strip(" *")
            elif "prestation" in l or "mission" in l or "objet" in l:
                prestation = re.sub(r".*?[:]\s*", "", line.strip(), count=1).strip(" *")
            elif any(w in l for w in ["forme", "stagiaire", "participant", "personne"]):
                nb_m = re.search(r"(\d+)", line)
                nb_formes = nb_m.group(1) if nb_m else ""
            elif "annee" in l or "date" in l or "periode" in l:
                yr_m = re.search(r"(20\d{2})", line)
                annee = yr_m.group(1) if yr_m else ""
            elif "satisfaction" in l or "note" in l or "avis" in l:
                sat_m = re.search(r"([\d.,]+\s*/\s*\d+|[\d.,]+%)", line)
                satisfaction = sat_m.group(1) if sat_m else ""

        if client and len(client) > 1:
            refs.append({
                "client": client,
                "secteur": secteur,
                "prestation": prestation,
                "nb_formes": nb_formes,
                "annee": annee,
                "satisfaction": satisfaction,
            })

    return refs


def _references_par_defaut() -> list[dict]:
    """References par defaut (donnees reelles Almera)."""
    return [
        {"client": "Havas", "secteur": "Communication", "prestation": "Formation IA generative equipes creatives", "nb_formes": "120", "annee": "2024", "satisfaction": "4.8/5"},
        {"client": "Eiffage", "secteur": "BTP / Construction", "prestation": "Formation IA dirigeants et chefs de projet", "nb_formes": "85", "annee": "2024", "satisfaction": "4.9/5"},
        {"client": "Carrefour", "secteur": "Grande distribution", "prestation": "Acculturation IA et ChatGPT", "nb_formes": "200", "annee": "2024", "satisfaction": "4.7/5"},
        {"client": "Orange", "secteur": "Telecom", "prestation": "IA generative et prompt engineering", "nb_formes": "150", "annee": "2024", "satisfaction": "4.8/5"},
        {"client": "Caisse des Depots", "secteur": "Institution publique", "prestation": "Formation IA agents et cadres", "nb_formes": "300", "annee": "2024", "satisfaction": "4.9/5"},
        {"client": "Eli Lilly", "secteur": "Pharmaceutique", "prestation": "IA generative R&D et marketing", "nb_formes": "60", "annee": "2024", "satisfaction": "4.9/5"},
        {"client": "3DS (Dassault Systemes)", "secteur": "Technologie", "prestation": "IA et automatisation ingenieurs", "nb_formes": "90", "annee": "2024", "satisfaction": "4.8/5"},
        {"client": "Action Logement", "secteur": "Logement social", "prestation": "Acculturation IA et transformation digitale", "nb_formes": "45", "annee": "2025", "satisfaction": "4.9/5"},
    ]


def generer_tableau_references(ao: dict, refs_md: str = "") -> str:
    """Genere un tableau HTML professionnel des references clients."""
    refs = _parse_references_md(refs_md) if refs_md else []
    if not refs or len(refs) < 2:
        refs = _references_par_defaut()

    # Couleurs par secteur
    secteur_couleurs = {}
    palette = ["#dbeafe", "#dcfce7", "#fef3c7", "#fce7f3", "#e0e7ff", "#f0fdfa", "#fff7ed", "#faf5ff"]
    for i, ref in enumerate(refs):
        s = ref["secteur"]
        if s and s not in secteur_couleurs:
            secteur_couleurs[s] = palette[len(secteur_couleurs) % len(palette)]

    # Calcul total
    total_formes = sum(int(r["nb_formes"]) for r in refs if r["nb_formes"].isdigit())

    # Construire les lignes
    rows = ""
    for ref in refs:
        bg = secteur_couleurs.get(ref["secteur"], "#f8fafc")
        sat_html = ref["satisfaction"] or "-"
        nb = ref["nb_formes"] or "-"
        rows += f"""
        <tr>
          <td class="ref-client">{ref["client"]}</td>
          <td><span class="ref-badge" style="background:{bg}">{ref["secteur"] or "-"}</span></td>
          <td>{ref["prestation"] or "-"}</td>
          <td class="ref-center">{nb}</td>
          <td class="ref-center">{ref["annee"] or "-"}</td>
          <td class="ref-center ref-sat">{sat_html}</td>
        </tr>"""

    titre = ao.get("titre", "Appel d'offres")

    extra_css = """
    .ref-table { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 12px; }
    .ref-table th {
      background: #1E3A5F; color: #fff; padding: 10px 12px; text-align: left;
      font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;
    }
    .ref-table td { padding: 10px 12px; border-bottom: 1px solid #e2e8f0; }
    .ref-table tr:hover { background: #f8fafc; }
    .ref-client { font-weight: 700; color: #1E3A5F; }
    .ref-badge {
      display: inline-block; padding: 2px 10px; border-radius: 12px;
      font-size: 11px; font-weight: 600; color: #334155;
    }
    .ref-center { text-align: center; }
    .ref-sat { font-weight: 700; color: #16a34a; }
    .ref-total {
      background: #f1f5f9; font-weight: 700;
    }
    .ref-total td { border-top: 2px solid #1E3A5F; }
    .ref-summary {
      margin-top: 16px; display: flex; gap: 24px; flex-wrap: wrap;
    }
    .ref-stat {
      padding: 12px 20px; background: #f8fafc; border-radius: 8px;
      border-left: 4px solid #2563EB; font-size: 13px;
    }
    .ref-stat strong { display: block; font-size: 22px; color: #1E3A5F; }
    """

    body = f"""
    <h1>Synthese des references clients</h1>
    <p class="subtitle">{titre}</p>

    <table class="ref-table">
      <thead>
        <tr>
          <th>Client</th>
          <th>Secteur</th>
          <th>Prestation</th>
          <th>Nb formes</th>
          <th>Annee</th>
          <th>Satisfaction</th>
        </tr>
      </thead>
      <tbody>
        {rows}
        <tr class="ref-total">
          <td colspan="3">TOTAL</td>
          <td class="ref-center">{total_formes}</td>
          <td></td>
          <td class="ref-center ref-sat">4.9/5 moy.</td>
        </tr>
      </tbody>
    </table>

    <div class="ref-summary">
      <div class="ref-stat"><strong>{len(refs)}</strong>References majeures</div>
      <div class="ref-stat"><strong>{total_formes}+</strong>Personnes formees</div>
      <div class="ref-stat"><strong>4.9/5</strong>Satisfaction moyenne</div>
    </div>
    """

    return _html_wrapper("References clients", body, extra_css)


# ---------------------------------------------------------------------------
# 5. Fiche de synthese visuelle (executive summary 1 page)
# ---------------------------------------------------------------------------

def generer_fiche_synthese(ao: dict, gng: dict = None, estimation: dict = None) -> str:
    """Genere une fiche de synthese A4 type executive summary."""
    titre = ao.get("titre", "Appel d'offres")
    acheteur = ao.get("acheteur", ao.get("organisme", ""))
    date_limite = ao.get("date_limite", ao.get("deadline", ""))
    source = ao.get("source", "")
    ao_id = ao.get("id", "")

    # Score Go/No-Go
    score_gng = gng.get("score", 0) if gng else 0
    decision = gng.get("decision", "GO") if gng else "GO"
    score_color = "#16a34a" if score_gng >= 60 else "#d97706" if score_gng >= 40 else "#dc2626"

    # Points forts
    points_forts = [
        "Certifie Qualiopi et RS6776 France Competences",
        "2000+ personnes formees en IA generative",
        "Equipe de 6 formateurs specialises IA",
    ]
    if gng and gng.get("points_forts"):
        points_forts = gng["points_forts"][:3]

    # Equipe proposee
    equipe = [
        "Mickael Bertolla - Chef de projet",
        "Charles Courbet - Formateur senior",
        "Romy Chen - Formatrice",
    ]

    # Budget
    budget_text = "Sur devis selon cahier des charges"
    if estimation:
        montant = estimation.get("montant_total", "")
        if montant:
            budget_text = f"{montant} EUR HT (TVA exoneree art. 261-4-4 CGI)"

    extra_css = """
    .synthese { max-width: 780px; margin: 0 auto; }
    .synthese-grid {
      display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-top: 16px;
    }
    .synthese-card {
      background: #f8fafc; border-radius: 8px; padding: 16px 20px;
      border-left: 4px solid #2563EB;
    }
    .synthese-card h3 {
      font-size: 12px; text-transform: uppercase; letter-spacing: 1px;
      color: #64748b; margin-bottom: 8px; font-weight: 600;
    }
    .synthese-card p, .synthese-card li {
      font-size: 13px; color: #334155; line-height: 1.6;
    }
    .synthese-card ul { list-style: none; padding: 0; }
    .synthese-card li::before { content: '\\2713\\0020'; color: #16a34a; font-weight: 700; }
    .synthese-score-wrapper {
      grid-column: 1 / -1; display: flex; align-items: center; gap: 24px;
      background: #fff; border: 2px solid #e2e8f0; border-radius: 8px;
      padding: 16px 24px;
    }
    .score-circle {
      width: 80px; height: 80px; border-radius: 50%;
      display: flex; align-items: center; justify-content: center; flex-shrink: 0;
      font-size: 28px; font-weight: 800; color: #fff;
    }
    .score-info { flex: 1; }
    .score-label { font-size: 14px; font-weight: 700; color: #1e293b; }
    .score-detail { font-size: 12px; color: #64748b; margin-top: 4px; }
    .score-bar-bg {
      height: 8px; background: #e2e8f0; border-radius: 4px; margin-top: 8px;
    }
    .score-bar-fill { height: 100%; border-radius: 4px; transition: width 0.5s; }
    .synthese-meta {
      display: flex; gap: 16px; flex-wrap: wrap; margin-top: 12px;
      font-size: 12px; color: #64748b;
    }
    .synthese-meta span { background: #f1f5f9; padding: 4px 10px; border-radius: 4px; }
    .synthese-full { grid-column: 1 / -1; }
    .synthese-footer {
      margin-top: 20px; text-align: center; font-size: 11px; color: #94a3b8;
      border-top: 1px solid #e2e8f0; padding-top: 12px;
    }
    """

    pf_html = "".join(f"<li>{p}</li>" for p in points_forts)
    eq_html = "".join(f"<li>{e}</li>" for e in equipe)

    body = f"""
    <div class="synthese">
      <h1>{titre}</h1>
      <div class="synthese-meta">
        <span>Acheteur : <strong>{acheteur}</strong></span>
        <span>Date limite : <strong>{date_limite or "A confirmer"}</strong></span>
        <span>Source : <strong>{source}</strong></span>
        <span>Ref : <strong>{ao_id}</strong></span>
      </div>

      <div class="synthese-grid">
        <div class="synthese-score-wrapper">
          <div class="score-circle" style="background:{score_color};">{score_gng}</div>
          <div class="score-info">
            <div class="score-label">Score Go/No-Go : {decision}</div>
            <div class="score-detail">Evaluation sur 6 criteres (adequation, capacite, risque, marge, delai, concurrence)</div>
            <div class="score-bar-bg">
              <div class="score-bar-fill" style="width:{score_gng}%;background:{score_color};"></div>
            </div>
          </div>
        </div>

        <div class="synthese-card">
          <h3>Points forts Almera</h3>
          <ul>{pf_html}</ul>
        </div>

        <div class="synthese-card">
          <h3>Equipe proposee</h3>
          <ul>{eq_html}</ul>
        </div>

        <div class="synthese-card">
          <h3>Budget estime</h3>
          <p>{budget_text}</p>
        </div>

        <div class="synthese-card">
          <h3>Certifications</h3>
          <ul>
            <li>Qualiopi (actions de formation)</li>
            <li>RS6776 France Competences</li>
            <li>Activateur France Num</li>
          </ul>
        </div>
      </div>

      <div class="synthese-footer">
        Almera - AI MENTOR SASU | SIRET 98900455100010 | almera.one | contact@almera.one
      </div>
    </div>
    """

    return _html_wrapper("Fiche de synthese", body, extra_css)


# ---------------------------------------------------------------------------
# Orchestrateur
# ---------------------------------------------------------------------------

def generer_toutes_annexes(ao: dict, dossier_path, dossier_contenu: dict = None) -> list:
    """Genere toutes les annexes visuelles pour un dossier AO.

    Args:
        ao: Dictionnaire de l'appel d'offres
        dossier_path: Path du dossier de sortie
        dossier_contenu: dict optionnel {nom_fichier: contenu} des fichiers deja generes

    Returns:
        Liste des noms de fichiers crees
    """
    dossier_path = Path(dossier_path)
    contenu = dossier_contenu or {}

    # Charger les contenus depuis le dossier si pas fournis
    if not contenu:
        for f in dossier_path.glob("*.md"):
            try:
                contenu[f.name] = f.read_text(encoding="utf-8")
            except Exception:
                pass

    fichiers_crees = []

    # Recuperer les contenus utiles
    planning_md = contenu.get("05_planning_previsionnel.md", "")
    cv_md = contenu.get("06_cv_formateurs.md", "")
    refs_md = contenu.get("09_references_clients.md", "")

    # Recuperer le Go/No-Go si disponible
    gng = None
    gng_path = dossier_path / "fiche_ao.json"
    if gng_path.exists():
        try:
            ao_data = __import__("json").loads(gng_path.read_text(encoding="utf-8"))
            gng = ao_data.get("gng_result") or ao_data.get("go_no_go")
        except Exception:
            pass
    # Aussi essayer review_auto.json
    review_path = dossier_path / "review_auto.json"
    review_data = None
    if review_path.exists():
        try:
            review_data = __import__("json").loads(review_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # 1. Planning Gantt
    try:
        html = generer_gantt(ao, planning_md)
        out = dossier_path / "planning_gantt.html"
        out.write_text(html, encoding="utf-8")
        fichiers_crees.append("planning_gantt.html")
        logger.info("Annexe visuelle: planning_gantt.html OK")
    except Exception as e:
        logger.error(f"Erreur annexe planning_gantt: {e}")

    # 2. Organigramme equipe
    try:
        html = generer_organigramme(ao, cv_md)
        out = dossier_path / "organigramme_equipe.html"
        out.write_text(html, encoding="utf-8")
        fichiers_crees.append("organigramme_equipe.html")
        logger.info("Annexe visuelle: organigramme_equipe.html OK")
    except Exception as e:
        logger.error(f"Erreur annexe organigramme: {e}")

    # 3. Radar competences
    try:
        html = generer_radar_competences(ao)
        out = dossier_path / "radar_competences.html"
        out.write_text(html, encoding="utf-8")
        fichiers_crees.append("radar_competences.html")
        logger.info("Annexe visuelle: radar_competences.html OK")
    except Exception as e:
        logger.error(f"Erreur annexe radar: {e}")

    # 4. Tableau references
    try:
        html = generer_tableau_references(ao, refs_md)
        out = dossier_path / "synthese_references.html"
        out.write_text(html, encoding="utf-8")
        fichiers_crees.append("synthese_references.html")
        logger.info("Annexe visuelle: synthese_references.html OK")
    except Exception as e:
        logger.error(f"Erreur annexe references: {e}")

    # 5. Fiche de synthese
    try:
        html = generer_fiche_synthese(ao, gng=gng)
        out = dossier_path / "fiche_synthese.html"
        out.write_text(html, encoding="utf-8")
        fichiers_crees.append("fiche_synthese.html")
        logger.info("Annexe visuelle: fiche_synthese.html OK")
    except Exception as e:
        logger.error(f"Erreur annexe fiche synthese: {e}")

    logger.info(f"Annexes visuelles: {len(fichiers_crees)}/5 generees")
    return fichiers_crees
