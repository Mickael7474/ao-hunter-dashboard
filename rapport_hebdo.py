"""
Rapport hebdomadaire automatique AO Hunter.

Genere un email recapitulatif de la semaine et le place en brouillon Gmail.
Execute chaque lundi a 8h via APScheduler, ou manuellement via l'API.
"""

import os
import json
import imaplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("ao_hunter.rapport_hebdo")

DASHBOARD_DIR = Path(__file__).parent
AO_FILE = DASHBOARD_DIR / "ao_pertinents.json"
DOSSIERS_INDEX_FILE = DASHBOARD_DIR / "dossiers_index.json"
RAPPORTS_FILE = DASHBOARD_DIR / "rapports_hebdo.json"

# Config email (meme que pipeline_auto.py)
IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
EMAIL_USER = "mickael.bertolla@gmail.com"
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")

DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "https://ao-hunter-dashboard.onrender.com")


def _charger_json(path: Path) -> list | dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return [] if path.name.endswith(".json") and path.stem != "dossiers_index" else {}


def _charger_rapports() -> list[dict]:
    if RAPPORTS_FILE.exists():
        try:
            return json.loads(RAPPORTS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _sauvegarder_rapports(rapports: list[dict]):
    rapports = rapports[-52:]  # garder 1 an max
    RAPPORTS_FILE.write_text(json.dumps(rapports, ensure_ascii=False, indent=2), encoding="utf-8")


def generer_rapport_hebdo() -> dict:
    """Charge les donnees et calcule les metriques de la semaine.

    Returns:
        dict avec toutes les metriques du rapport hebdomadaire.
    """
    maintenant = datetime.now()
    il_y_a_7j = maintenant - timedelta(days=7)
    dans_7j = maintenant + timedelta(days=7)

    il_y_a_7j_str = il_y_a_7j.strftime("%Y-%m-%d")
    dans_7j_str = dans_7j.strftime("%Y-%m-%d")
    maintenant_str = maintenant.strftime("%Y-%m-%d")

    # Charger les AO
    ao_data = _charger_json(AO_FILE)
    if isinstance(ao_data, dict):
        ao_data = []

    # Charger l'index des dossiers
    dossiers_data = _charger_json(DOSSIERS_INDEX_FILE)
    if isinstance(dossiers_data, list):
        dossiers_index = {}
        for d in dossiers_data:
            if isinstance(d, dict) and d.get("ao_id"):
                dossiers_index[d["ao_id"]] = d
    elif isinstance(dossiers_data, dict):
        dossiers_index = dossiers_data
    else:
        dossiers_index = {}

    # --- Nouveaux AO (detectes cette semaine) ---
    nouveaux_ao = []
    for ao in ao_data:
        date_pub = ao.get("date_publication") or ao.get("date_ajout") or ao.get("date_detection", "")
        if isinstance(date_pub, str) and date_pub:
            date_cmp = date_pub[:10]
            if date_cmp >= il_y_a_7j_str:
                nouveaux_ao.append(ao)

    # --- Deadlines dans les 7 prochains jours ---
    deadlines_semaine = []
    for ao in ao_data:
        dl = ao.get("date_limite", "")
        if isinstance(dl, str) and dl:
            dl_cmp = dl[:10]
            if maintenant_str <= dl_cmp <= dans_7j_str:
                deadlines_semaine.append(ao)
    deadlines_semaine.sort(key=lambda a: a.get("date_limite", ""))

    # --- Dossiers generes cette semaine ---
    dossiers_generes = []
    for ao_id, info in dossiers_index.items():
        ts = info.get("timestamp") or info.get("date_generation") or info.get("date", "")
        if isinstance(ts, str) and ts:
            if ts[:10] >= il_y_a_7j_str:
                dossiers_generes.append({"ao_id": ao_id, **info})

    # --- Statuts changes (AO avec pipeline_auto ou statut non-nouveau) ---
    statuts_changes = []
    for ao in ao_data:
        date_pipeline = ao.get("date_pipeline", "")
        if isinstance(date_pipeline, str) and date_pipeline and date_pipeline[:10] >= il_y_a_7j_str:
            statuts_changes.append(ao)

    # --- Resultats d'attribution ---
    resultats_attribution = []
    for ao in ao_data:
        statut = ao.get("statut", "")
        if statut in ("gagne", "perdu"):
            date_attr = ao.get("date_attribution") or ao.get("date_pipeline") or ao.get("date_modification", "")
            if isinstance(date_attr, str) and date_attr and date_attr[:10] >= il_y_a_7j_str:
                resultats_attribution.append(ao)

    # --- Pipeline actif (nb AO par statut) ---
    pipeline_actif = {"nouveau": 0, "analyse": 0, "candidature": 0, "soumis": 0}
    for ao in ao_data:
        statut = ao.get("statut", "nouveau")
        if statut in pipeline_actif:
            pipeline_actif[statut] += 1

    total_pipeline = sum(pipeline_actif.values())

    # --- KPIs ---
    nb_gagnes = sum(1 for ao in ao_data if ao.get("statut") == "gagne")
    nb_soumis = sum(1 for ao in ao_data if ao.get("statut") in ("soumis", "gagne", "perdu"))
    taux_conversion = round((nb_gagnes / nb_soumis * 100) if nb_soumis > 0 else 0, 1)

    ca_gagne = sum(
        float(ao.get("montant_gagne") or ao.get("budget_estime") or 0)
        for ao in ao_data if ao.get("statut") == "gagne"
    )
    ca_pipeline = sum(
        float(ao.get("budget_estime") or 0)
        for ao in ao_data if ao.get("statut") in ("analyse", "candidature", "soumis")
    )

    kpis = {
        "taux_conversion": taux_conversion,
        "ca_gagne_total": ca_gagne,
        "ca_pipeline": ca_pipeline,
        "nb_gagnes": nb_gagnes,
        "nb_soumis": nb_soumis,
    }

    # --- Actions recommandees ---
    actions = []
    if deadlines_semaine:
        actions.append(f"{len(deadlines_semaine)} AO arrivent a deadline cette semaine, preparer les soumissions")
    nb_nouveaux_haut_score = sum(1 for ao in nouveaux_ao if ao.get("score_pertinence", 0) >= 0.7)
    if nb_nouveaux_haut_score:
        actions.append(f"{nb_nouveaux_haut_score} nouveaux AO a fort score (>70%) detectes, analyser en priorite")
    nb_sans_dossier = sum(
        1 for ao in ao_data
        if ao.get("statut") in ("analyse", "candidature")
        and not ao.get("dossier_genere")
    )
    if nb_sans_dossier:
        actions.append(f"{nb_sans_dossier} AO en cours sans dossier genere, lancer la generation")
    if not actions:
        actions.append("Aucune action urgente cette semaine")

    rapport = {
        "date_generation": maintenant.isoformat(),
        "semaine_du": il_y_a_7j.strftime("%d/%m/%Y"),
        "semaine_au": maintenant.strftime("%d/%m/%Y"),
        "nouveaux_ao": [{"id": a.get("id"), "titre": a.get("titre", ""), "acheteur": a.get("acheteur", ""), "score": a.get("score_pertinence", 0)} for a in nouveaux_ao],
        "nb_nouveaux_ao": len(nouveaux_ao),
        "deadlines_semaine": [{"id": a.get("id"), "titre": a.get("titre", ""), "acheteur": a.get("acheteur", ""), "date_limite": a.get("date_limite", ""), "statut": a.get("statut", "")} for a in deadlines_semaine],
        "nb_deadlines": len(deadlines_semaine),
        "dossiers_generes": dossiers_generes,
        "nb_dossiers_generes": len(dossiers_generes),
        "statuts_changes": [{"id": a.get("id"), "titre": a.get("titre", ""), "statut": a.get("statut", "")} for a in statuts_changes],
        "resultats_attribution": [{"id": a.get("id"), "titre": a.get("titre", ""), "statut": a.get("statut", ""), "titulaire": a.get("titulaire", ""), "montant": a.get("montant_gagne") or a.get("budget_estime", "")} for a in resultats_attribution],
        "pipeline_actif": pipeline_actif,
        "total_pipeline": total_pipeline,
        "kpis": kpis,
        "actions_recommandees": actions,
        "nb_total_ao": len(ao_data),
    }

    return rapport


def formater_rapport_html(rapport: dict) -> str:
    """Formate le rapport en email HTML avec le style Almera.

    Args:
        rapport: dict retourne par generer_rapport_hebdo()

    Returns:
        str HTML pret a etre place en brouillon Gmail.
    """
    date_gen = rapport.get("date_generation", "")
    try:
        dt = datetime.fromisoformat(date_gen)
        date_affichee = dt.strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        date_affichee = date_gen[:10] if date_gen else "N/A"

    semaine_du = rapport.get("semaine_du", "")
    semaine_au = rapport.get("semaine_au", "")

    nb_nouveaux = rapport.get("nb_nouveaux_ao", 0)
    nb_dossiers = rapport.get("nb_dossiers_generes", 0)
    kpis = rapport.get("kpis", {})
    taux_conv = kpis.get("taux_conversion", 0)
    ca_gagne = kpis.get("ca_gagne_total", 0)
    ca_pipeline = kpis.get("ca_pipeline", 0)

    # --- Section 1 : KPIs ---
    kpis_html = f"""
    <div style="display:flex;gap:12px;margin-bottom:24px;">
        <div style="flex:1;background:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;padding:16px;text-align:center;">
            <div style="font-size:0.8em;color:#64748b;margin-bottom:4px;">Nouveaux AO</div>
            <div style="font-size:2em;font-weight:bold;color:#1E3A5F;">{nb_nouveaux}</div>
            <div style="font-size:0.75em;color:#94a3b8;">cette semaine</div>
        </div>
        <div style="flex:1;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:16px;text-align:center;">
            <div style="font-size:0.8em;color:#64748b;margin-bottom:4px;">Dossiers generes</div>
            <div style="font-size:2em;font-weight:bold;color:#16a34a;">{nb_dossiers}</div>
            <div style="font-size:0.75em;color:#94a3b8;">cette semaine</div>
        </div>
        <div style="flex:1;background:#fefce8;border:1px solid #fde68a;border-radius:8px;padding:16px;text-align:center;">
            <div style="font-size:0.8em;color:#64748b;margin-bottom:4px;">Taux conversion</div>
            <div style="font-size:2em;font-weight:bold;color:#ca8a04;">{taux_conv}%</div>
            <div style="font-size:0.75em;color:#94a3b8;">cumule</div>
        </div>
    </div>
    """

    # --- Section 2 : Deadlines urgentes ---
    deadlines = rapport.get("deadlines_semaine", [])
    if deadlines:
        rows = ""
        for d in deadlines:
            dl = d.get("date_limite", "")
            if "T" in str(dl):
                dl = dl.split("T")[0]
            statut_color = {"nouveau": "#3b82f6", "analyse": "#f59e0b", "candidature": "#8b5cf6", "soumis": "#16a34a"}.get(d.get("statut", ""), "#64748b")
            rows += f"""
            <tr>
                <td style="padding:8px 12px;border-bottom:1px solid #e2e8f0;">{d.get('titre', '')[:60]}</td>
                <td style="padding:8px 12px;border-bottom:1px solid #e2e8f0;">{d.get('acheteur', '')[:40]}</td>
                <td style="padding:8px 12px;border-bottom:1px solid #e2e8f0;font-weight:600;color:#dc2626;">{dl}</td>
                <td style="padding:8px 12px;border-bottom:1px solid #e2e8f0;">
                    <span style="background:{statut_color};color:white;padding:2px 8px;border-radius:4px;font-size:0.8em;">{d.get('statut', 'nouveau')}</span>
                </td>
            </tr>
            """
        deadlines_html = f"""
        <h3 style="color:#1E3A5F;border-bottom:2px solid #1E3A5F;padding-bottom:6px;">Deadlines urgentes ({len(deadlines)})</h3>
        <table style="width:100%;border-collapse:collapse;margin-bottom:24px;">
            <tr style="background:#f1f5f9;">
                <th style="padding:8px 12px;text-align:left;font-size:0.85em;color:#64748b;">Titre</th>
                <th style="padding:8px 12px;text-align:left;font-size:0.85em;color:#64748b;">Acheteur</th>
                <th style="padding:8px 12px;text-align:left;font-size:0.85em;color:#64748b;">Deadline</th>
                <th style="padding:8px 12px;text-align:left;font-size:0.85em;color:#64748b;">Statut</th>
            </tr>
            {rows}
        </table>
        """
    else:
        deadlines_html = """
        <h3 style="color:#1E3A5F;border-bottom:2px solid #1E3A5F;padding-bottom:6px;">Deadlines urgentes</h3>
        <p style="color:#64748b;font-style:italic;">Aucune deadline dans les 7 prochains jours.</p>
        """

    # --- Section 3 : Nouveaux AO pertinents (top 5 par score) ---
    nouveaux = sorted(rapport.get("nouveaux_ao", []), key=lambda a: a.get("score", 0), reverse=True)[:5]
    if nouveaux:
        rows = ""
        for ao in nouveaux:
            score_pct = int(ao.get("score", 0) * 100) if ao.get("score", 0) <= 1 else int(ao.get("score", 0))
            score_color = "#16a34a" if score_pct >= 70 else "#f59e0b" if score_pct >= 50 else "#64748b"
            ao_url = f"{DASHBOARD_URL}/ao/{ao.get('id', '')}"
            rows += f"""
            <tr>
                <td style="padding:8px 12px;border-bottom:1px solid #e2e8f0;">
                    <a href="{ao_url}" style="color:#2563eb;text-decoration:none;">{ao.get('titre', '')[:55]}</a>
                </td>
                <td style="padding:8px 12px;border-bottom:1px solid #e2e8f0;">{ao.get('acheteur', '')[:35]}</td>
                <td style="padding:8px 12px;border-bottom:1px solid #e2e8f0;font-weight:600;color:{score_color};">{score_pct}%</td>
            </tr>
            """
        nouveaux_html = f"""
        <h3 style="color:#1E3A5F;border-bottom:2px solid #1E3A5F;padding-bottom:6px;">Nouveaux AO pertinents (top 5)</h3>
        <table style="width:100%;border-collapse:collapse;margin-bottom:24px;">
            <tr style="background:#f1f5f9;">
                <th style="padding:8px 12px;text-align:left;font-size:0.85em;color:#64748b;">Titre</th>
                <th style="padding:8px 12px;text-align:left;font-size:0.85em;color:#64748b;">Acheteur</th>
                <th style="padding:8px 12px;text-align:left;font-size:0.85em;color:#64748b;">Score</th>
            </tr>
            {rows}
        </table>
        """
    else:
        nouveaux_html = """
        <h3 style="color:#1E3A5F;border-bottom:2px solid #1E3A5F;padding-bottom:6px;">Nouveaux AO pertinents</h3>
        <p style="color:#64748b;font-style:italic;">Aucun nouvel AO detecte cette semaine.</p>
        """

    # --- Section 4 : Resultats d'attribution ---
    resultats = rapport.get("resultats_attribution", [])
    if resultats:
        rows = ""
        for r in resultats:
            icon = "V" if r.get("statut") == "gagne" else "X"
            bg = "#f0fdf4" if r.get("statut") == "gagne" else "#fef2f2"
            color = "#16a34a" if r.get("statut") == "gagne" else "#dc2626"
            montant = r.get("montant", "")
            if montant:
                try:
                    montant = f"{float(montant):,.0f} EUR".replace(",", " ")
                except (ValueError, TypeError):
                    montant = str(montant)
            rows += f"""
            <tr style="background:{bg};">
                <td style="padding:8px 12px;border-bottom:1px solid #e2e8f0;color:{color};font-weight:600;">{icon} {r.get('statut', '').upper()}</td>
                <td style="padding:8px 12px;border-bottom:1px solid #e2e8f0;">{r.get('titre', '')[:50]}</td>
                <td style="padding:8px 12px;border-bottom:1px solid #e2e8f0;">{r.get('titulaire', '-')}</td>
                <td style="padding:8px 12px;border-bottom:1px solid #e2e8f0;">{montant or '-'}</td>
            </tr>
            """
        resultats_html = f"""
        <h3 style="color:#1E3A5F;border-bottom:2px solid #1E3A5F;padding-bottom:6px;">Resultats d'attribution</h3>
        <table style="width:100%;border-collapse:collapse;margin-bottom:24px;">
            <tr style="background:#f1f5f9;">
                <th style="padding:8px 12px;text-align:left;font-size:0.85em;color:#64748b;">Resultat</th>
                <th style="padding:8px 12px;text-align:left;font-size:0.85em;color:#64748b;">Titre</th>
                <th style="padding:8px 12px;text-align:left;font-size:0.85em;color:#64748b;">Titulaire</th>
                <th style="padding:8px 12px;text-align:left;font-size:0.85em;color:#64748b;">Montant</th>
            </tr>
            {rows}
        </table>
        """
    else:
        resultats_html = ""

    # --- Section 5 : Pipeline commercial ---
    pipeline = rapport.get("pipeline_actif", {})
    total = rapport.get("total_pipeline", 1) or 1
    pipeline_bars = ""
    colors_pipeline = {"nouveau": "#3b82f6", "analyse": "#f59e0b", "candidature": "#8b5cf6", "soumis": "#16a34a"}
    labels_pipeline = {"nouveau": "Nouveau", "analyse": "Analyse", "candidature": "Candidature", "soumis": "Soumis"}
    for statut in ("nouveau", "analyse", "candidature", "soumis"):
        count = pipeline.get(statut, 0)
        pct = round(count / total * 100) if total > 0 else 0
        color = colors_pipeline.get(statut, "#64748b")
        label = labels_pipeline.get(statut, statut)
        pipeline_bars += f"""
        <div style="margin-bottom:8px;">
            <div style="display:flex;justify-content:space-between;font-size:0.85em;margin-bottom:2px;">
                <span>{label}</span>
                <span style="font-weight:600;">{count}</span>
            </div>
            <div style="background:#e2e8f0;border-radius:4px;height:20px;overflow:hidden;">
                <div style="background:{color};height:100%;width:{pct}%;border-radius:4px;min-width:{2 if count > 0 else 0}px;"></div>
            </div>
        </div>
        """

    ca_gagne_fmt = f"{ca_gagne:,.0f} EUR".replace(",", " ") if ca_gagne else "0 EUR"
    ca_pipeline_fmt = f"{ca_pipeline:,.0f} EUR".replace(",", " ") if ca_pipeline else "0 EUR"

    pipeline_html = f"""
    <h3 style="color:#1E3A5F;border-bottom:2px solid #1E3A5F;padding-bottom:6px;">Pipeline commercial</h3>
    <div style="margin-bottom:16px;">{pipeline_bars}</div>
    <div style="display:flex;gap:12px;margin-bottom:24px;">
        <div style="flex:1;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:6px;padding:10px;text-align:center;">
            <div style="font-size:0.75em;color:#64748b;">CA gagne</div>
            <div style="font-weight:bold;color:#16a34a;">{ca_gagne_fmt}</div>
        </div>
        <div style="flex:1;background:#f0f9ff;border:1px solid #bae6fd;border-radius:6px;padding:10px;text-align:center;">
            <div style="font-size:0.75em;color:#64748b;">CA pipeline</div>
            <div style="font-weight:bold;color:#1E3A5F;">{ca_pipeline_fmt}</div>
        </div>
    </div>
    """

    # --- Section 6 : Actions recommandees ---
    actions = rapport.get("actions_recommandees", [])
    actions_items = "".join(f'<li style="padding:4px 0;">{a}</li>' for a in actions)
    actions_html = f"""
    <h3 style="color:#1E3A5F;border-bottom:2px solid #1E3A5F;padding-bottom:6px;">Actions recommandees</h3>
    <ul style="margin:0 0 24px;padding-left:20px;color:#334155;">
        {actions_items}
    </ul>
    """

    # --- Assemblage final ---
    html = f"""
    <div style="font-family:Calibri,Arial,sans-serif;max-width:700px;margin:0 auto;">

        <!-- Header -->
        <div style="background:#1E3A5F;color:white;padding:16px 24px;border-radius:8px 8px 0 0;">
            <h2 style="margin:0;font-size:1.4em;">AO Hunter - Rapport Hebdomadaire</h2>
            <p style="margin:6px 0 0;font-size:0.9em;opacity:0.85;">
                Semaine du {semaine_du} au {semaine_au}
            </p>
        </div>

        <div style="padding:24px;border:1px solid #e2e8f0;border-top:0;">

            {kpis_html}

            {deadlines_html}

            {nouveaux_html}

            {resultats_html}

            {pipeline_html}

            {actions_html}

        </div>

        <!-- Footer -->
        <div style="background:#f1f5f9;padding:12px 24px;border-radius:0 0 8px 8px;
                    font-size:0.8em;color:#64748b;text-align:center;border:1px solid #e2e8f0;border-top:0;">
            <a href="{DASHBOARD_URL}" style="color:#2563eb;text-decoration:none;">Ouvrir le dashboard AO Hunter</a>
            &nbsp;|&nbsp; Rapport genere automatiquement le {date_affichee}
        </div>
    </div>
    """

    return html


def creer_brouillon_rapport(rapport_html: str) -> bool:
    """Cree un brouillon Gmail avec le rapport hebdomadaire via IMAP.

    Meme pattern que pipeline_auto.py creer_brouillon_gmail.

    Args:
        rapport_html: contenu HTML du rapport

    Returns:
        True si le brouillon a ete cree avec succes.
    """
    if not SMTP_PASSWORD:
        logger.warning("SMTP_PASSWORD non configure - brouillon rapport non cree")
        return False

    maintenant = datetime.now()
    subject = f"AO Hunter - Rapport Hebdo du {maintenant:%d/%m/%Y}"

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = EMAIL_USER
        msg["To"] = EMAIL_USER
        msg["X-AO-Hunter"] = "rapport-hebdo"
        msg.attach(MIMEText(rapport_html, "html", "utf-8"))

        imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        imap.login(EMAIL_USER, SMTP_PASSWORD)

        # Gmail : trouver le dossier brouillons (FR ou EN)
        draft_folder = None
        for folder_name in ["[Gmail]/Brouillons", "[Gmail]/Drafts"]:
            status, _ = imap.select(f'"{folder_name}"')
            if status == "OK":
                draft_folder = folder_name
                break

        if not draft_folder:
            status, folders = imap.list()
            if status == "OK":
                for f in folders:
                    f_decoded = f.decode("utf-8") if isinstance(f, bytes) else f
                    if "draft" in f_decoded.lower() or "brouillon" in f_decoded.lower():
                        parts = f_decoded.split('"')
                        if len(parts) >= 4:
                            draft_folder = parts[-2]
                            break

        if not draft_folder:
            logger.error("Impossible de trouver le dossier Brouillons Gmail")
            imap.logout()
            return False

        msg_bytes = msg.as_bytes()
        status, _ = imap.append(
            f'"{draft_folder}"',
            "\\Draft",
            None,
            msg_bytes,
        )

        imap.logout()

        if status == "OK":
            logger.info(f"Brouillon rapport hebdo cree: {subject}")
            return True
        else:
            logger.error(f"Erreur IMAP append rapport: {status}")
            return False

    except Exception as e:
        logger.error(f"Erreur creation brouillon rapport: {e}")
        return False


def rapport_et_envoi() -> dict:
    """Orchestre la generation du rapport, le formatage HTML et la creation du brouillon.

    Anti-doublon : max 1 rapport par semaine (verifie la date du dernier).

    Returns:
        dict avec les cles: date, rapport, brouillon_cree, erreur (optionnel)
    """
    # Anti-doublon : verifier le dernier rapport
    rapports = _charger_rapports()
    if rapports:
        dernier = rapports[-1]
        try:
            date_dernier = datetime.fromisoformat(dernier["date"])
            jours_depuis = (datetime.now() - date_dernier).days
            if jours_depuis < 6:
                logger.info(f"Rapport deja genere il y a {jours_depuis} jour(s), annule (anti-doublon)")
                return {
                    "date": dernier["date"],
                    "rapport": dernier.get("rapport", {}),
                    "brouillon_cree": False,
                    "erreur": f"Rapport deja genere il y a {jours_depuis} jour(s)",
                    "doublon": True,
                }
        except (ValueError, KeyError):
            pass

    # Generer le rapport
    try:
        rapport = generer_rapport_hebdo()
    except Exception as e:
        logger.error(f"Erreur generation rapport: {e}")
        return {"date": datetime.now().isoformat(), "rapport": {}, "brouillon_cree": False, "erreur": str(e)}

    # Formater en HTML
    try:
        rapport_html = formater_rapport_html(rapport)
    except Exception as e:
        logger.error(f"Erreur formatage HTML rapport: {e}")
        return {"date": datetime.now().isoformat(), "rapport": rapport, "brouillon_cree": False, "erreur": str(e)}

    # Creer le brouillon
    brouillon_ok = creer_brouillon_rapport(rapport_html)

    # Sauvegarder dans l'historique
    entry = {
        "date": datetime.now().isoformat(),
        "rapport": rapport,
        "brouillon_cree": brouillon_ok,
    }
    rapports.append(entry)
    _sauvegarder_rapports(rapports)

    logger.info(f"Rapport hebdo genere: {rapport.get('nb_nouveaux_ao', 0)} nouveaux AO, brouillon={'OK' if brouillon_ok else 'ECHEC'}")

    return entry
