"""
Systeme de rappels automatiques pour les deadlines AO.
Envoie des emails de rappel a J-7, J-3, J-1 pour les AO
en statut candidature ou soumis.
"""

import json
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("ao_hunter.rappels")

DASHBOARD_DIR = Path(__file__).parent
RAPPELS_FILE = DASHBOARD_DIR / "rappels_envoyes.json"

# Config email (memes parametres que config.yaml)
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
EXPEDITEUR = "mickael.bertolla@gmail.com"
DESTINATAIRE = "contact@almera.one"
# Le mot de passe est dans config.yaml, on le passe en env var pour Render
import os
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")

JOURS_RAPPEL = [7, 3, 1]  # Rappels a J-7, J-3, J-1
STATUTS_A_SURVEILLER = ["candidature", "soumis", "analyse"]


def charger_rappels_envoyes() -> dict:
    """Charge l'historique des rappels envoyes."""
    if RAPPELS_FILE.exists():
        try:
            return json.loads(RAPPELS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def sauvegarder_rappels(rappels: dict):
    RAPPELS_FILE.write_text(json.dumps(rappels, ensure_ascii=False, indent=2), encoding="utf-8")


def verifier_deadlines(appels: list[dict]) -> list[dict]:
    """Verifie les deadlines et retourne les AO qui necessitent un rappel.

    Returns:
        list de dict avec: ao, jours_restants, urgence (rouge/orange/jaune)
    """
    now = datetime.now()
    alertes = []

    for ao in appels:
        statut = ao.get("statut", "nouveau")
        if statut not in STATUTS_A_SURVEILLER:
            continue

        dl = ao.get("date_limite", "")
        if not dl:
            continue

        try:
            date_dl = datetime.fromisoformat(dl.split("T")[0])
            jours = (date_dl - now).days

            if jours < 0:
                continue  # Deja passe

            if jours <= 1:
                urgence = "rouge"
            elif jours <= 3:
                urgence = "orange"
            elif jours <= 7:
                urgence = "jaune"
            else:
                continue

            alertes.append({
                "ao": ao,
                "jours_restants": jours,
                "urgence": urgence,
                "date_limite": dl.split("T")[0],
            })
        except (ValueError, TypeError):
            continue

    alertes.sort(key=lambda a: a["jours_restants"])
    return alertes


def envoyer_rappels(appels: list[dict]) -> dict:
    """Verifie les deadlines et envoie les rappels email non encore envoyes.

    Returns:
        dict avec: envoyes (int), erreurs (int), details (list)
    """
    alertes = verifier_deadlines(appels)
    if not alertes:
        return {"envoyes": 0, "erreurs": 0, "details": []}

    rappels = charger_rappels_envoyes()
    envoyes = 0
    erreurs = 0
    details = []

    for alerte in alertes:
        ao = alerte["ao"]
        ao_id = ao.get("id", "")
        jours = alerte["jours_restants"]

        # Verifier si ce rappel a deja ete envoye
        cle = f"{ao_id}_J{jours}"
        if cle in rappels:
            continue

        # Envoyer le rappel
        ok = _envoyer_email_rappel(ao, jours, alerte["urgence"])
        if ok:
            rappels[cle] = datetime.now().isoformat()
            envoyes += 1
            details.append(f"Rappel J-{jours}: {ao.get('titre', '')[:60]}")
        else:
            erreurs += 1

    sauvegarder_rappels(rappels)
    return {"envoyes": envoyes, "erreurs": erreurs, "details": details}


def _envoyer_email_rappel(ao: dict, jours_restants: int, urgence: str) -> bool:
    """Envoie un email de rappel pour un AO."""
    if not SMTP_PASSWORD:
        logger.warning("SMTP_PASSWORD non configure - rappel non envoye")
        return False

    urgence_emoji = {"rouge": "🔴", "orange": "🟠", "jaune": "🟡"}.get(urgence, "⚪")
    titre_ao = ao.get("titre", "Sans titre")[:80]

    subject = f"{urgence_emoji} AO Hunter - Deadline J-{jours_restants} : {titre_ao}"

    body_html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">
        <div style="background:{'#dc2626' if urgence == 'rouge' else '#f59e0b' if urgence == 'orange' else '#eab308'};
                    color:white;padding:12px 20px;border-radius:8px 8px 0 0;">
            <h2 style="margin:0;">{urgence_emoji} Deadline dans {jours_restants} jour{'s' if jours_restants > 1 else ''}</h2>
        </div>
        <div style="padding:20px;border:1px solid #e2e8f0;border-top:0;border-radius:0 0 8px 8px;">
            <h3 style="margin-top:0;">{ao.get('titre', 'Sans titre')}</h3>
            <table style="width:100%;border-collapse:collapse;">
                <tr><td style="padding:4px 8px;color:#64748b;">Acheteur</td><td style="padding:4px 8px;">{ao.get('acheteur', '-')}</td></tr>
                <tr><td style="padding:4px 8px;color:#64748b;">Date limite</td><td style="padding:4px 8px;font-weight:bold;">{ao.get('date_limite', '-')}</td></tr>
                <tr><td style="padding:4px 8px;color:#64748b;">Statut</td><td style="padding:4px 8px;">{ao.get('statut', '-')}</td></tr>
                <tr><td style="padding:4px 8px;color:#64748b;">Budget</td><td style="padding:4px 8px;">{ao.get('budget_estime', 'Non precise')}</td></tr>
                <tr><td style="padding:4px 8px;color:#64748b;">Source</td><td style="padding:4px 8px;">{ao.get('source', '-')}</td></tr>
            </table>
            <div style="margin-top:16px;">
                <a href="{ao.get('url', '#')}" style="background:#2563eb;color:white;padding:8px 20px;text-decoration:none;border-radius:6px;">Voir l'avis</a>
            </div>
        </div>
    </div>
    """

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = EXPEDITEUR
        msg["To"] = DESTINATAIRE
        msg.attach(MIMEText(body_html, "html"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(EXPEDITEUR, SMTP_PASSWORD)
            server.sendmail(EXPEDITEUR, DESTINATAIRE, msg.as_string())

        logger.info(f"Rappel envoye: J-{jours_restants} {ao.get('id', '')}")
        return True
    except Exception as e:
        logger.error(f"Erreur envoi rappel: {e}")
        return False
