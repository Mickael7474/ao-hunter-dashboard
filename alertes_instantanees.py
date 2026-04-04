"""
Alertes instantanees AO Hunter.

Cree un brouillon Gmail des qu'un AO a fort potentiel est detecte.
Criteres : score >= 0.75, concurrence < 40, deadline > 7 jours.
Anti-doublon via alertes_envoyees.json.
"""

import os
import json
import imaplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from pathlib import Path

try:
    from estimation_marche import estimer_marche
except ImportError:
    estimer_marche = None

logger = logging.getLogger("ao_hunter.alertes_instantanees")

DASHBOARD_DIR = Path(__file__).parent
ALERTES_FILE = DASHBOARD_DIR / "alertes_envoyees.json"

# Config email (meme que rapport_hebdo.py)
IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
EMAIL_USER = "mickael.bertolla@gmail.com"
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")

DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "https://ao-hunter-dashboard.onrender.com")


# ============================================================
# Anti-doublon
# ============================================================

def _charger_alertes() -> list[dict]:
    """Charge l'historique des alertes envoyees."""
    if ALERTES_FILE.exists():
        try:
            return json.loads(ALERTES_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _sauvegarder_alertes(alertes: list[dict]):
    """Sauvegarde l'historique (max 500 entrees)."""
    alertes = alertes[-500:]
    ALERTES_FILE.write_text(
        json.dumps(alertes, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _ao_deja_alerte(ao_id: str, alertes: list[dict]) -> bool:
    """Verifie si un AO a deja fait l'objet d'une alerte."""
    return any(a.get("ao_id") == ao_id for a in alertes)


# ============================================================
# Verification criteres alerte
# ============================================================

def verifier_alerte_instantanee(ao: dict) -> dict | None:
    """Verifie si un AO declenche une alerte instantanee (fort potentiel).

    Criteres (TOUS doivent etre vrais) :
    - score_pertinence >= 0.75
    - estimation concurrence score < 40 (faible concurrence)
    - deadline > 7 jours (temps suffisant pour repondre)
    - Pas deja alerte (anti-doublon)

    Returns:
        dict alerte si declenchee, None sinon.
    """
    ao_id = ao.get("id", "")
    if not ao_id:
        return None

    # Critere 1 : score de pertinence
    score = ao.get("score_pertinence", 0)
    if isinstance(score, str):
        try:
            score = float(score)
        except (ValueError, TypeError):
            score = 0
    if score < 0.75:
        return None

    # Critere 2 : deadline > 7 jours
    date_limite = ao.get("date_limite", "")
    if not date_limite:
        return None
    try:
        dl = datetime.fromisoformat(date_limite.replace("Z", "+00:00"))
        # Comparer sans timezone pour simplifier
        dl_naive = dl.replace(tzinfo=None) if dl.tzinfo else dl
        jours_restants = (dl_naive - datetime.now()).days
    except (ValueError, TypeError):
        try:
            dl_naive = datetime.strptime(date_limite[:10], "%Y-%m-%d")
            jours_restants = (dl_naive - datetime.now()).days
        except (ValueError, TypeError):
            return None
    if jours_restants <= 7:
        return None

    # Critere 3 : estimation concurrence faible
    estimation = None
    concurrence_score = ao.get("concurrence_score")
    budget_estime = ao.get("budget_estime")

    if concurrence_score is None and estimer_marche is not None:
        try:
            estimation = estimer_marche(ao, light=True)
            conc_info = estimation.get("concurrence", {})
            concurrence_score = conc_info.get("concurrence_score")
            budget_info = estimation.get("budget", {})
            if not budget_estime:
                budget_estime = budget_info.get("montant")
        except Exception as e:
            logger.debug(f"Estimation echouee pour {ao_id}: {e}")

    if concurrence_score is None:
        concurrence_score = 50  # par defaut, on ne declenche pas
    if concurrence_score >= 40:
        return None

    # Anti-doublon
    alertes = _charger_alertes()
    if _ao_deja_alerte(ao_id, alertes):
        return None

    # --- Construire les raisons ---
    raisons = []
    score_pct = int(score * 100) if score <= 1 else int(score)
    raisons.append(f"Score de pertinence eleve : {score_pct}%")
    raisons.append(f"Concurrence estimee faible : {concurrence_score}/100")
    raisons.append(f"Deadline confortable : {jours_restants} jours restants")
    if budget_estime:
        try:
            budget_fmt = f"{int(float(budget_estime)):,} EUR".replace(",", " ")
            raisons.append(f"Budget estime : {budget_fmt}")
        except (ValueError, TypeError):
            pass

    alerte = {
        "ao_id": ao_id,
        "titre": ao.get("titre", ""),
        "acheteur": ao.get("acheteur", ""),
        "date_limite": date_limite,
        "jours_restants": jours_restants,
        "score_pertinence": score,
        "concurrence_score": concurrence_score,
        "budget_estime": budget_estime,
        "raisons": raisons,
        "date_alerte": datetime.now().isoformat(),
        "estimation": estimation,
    }

    return alerte


# ============================================================
# Email HTML
# ============================================================

def _formater_email_alerte(alerte: dict) -> str:
    """Genere le HTML de l'email d'alerte avec le style Almera."""
    titre = alerte.get("titre", "AO sans titre")
    acheteur = alerte.get("acheteur", "Acheteur inconnu")
    date_limite = alerte.get("date_limite", "")
    jours_restants = alerte.get("jours_restants", 0)
    score = alerte.get("score_pertinence", 0)
    score_pct = int(score * 100) if score <= 1 else int(score)
    concurrence_score = alerte.get("concurrence_score", 0)
    budget_estime = alerte.get("budget_estime")
    raisons = alerte.get("raisons", [])
    ao_id = alerte.get("ao_id", "")
    ao_url = f"{DASHBOARD_URL}/ao/{ao_id}"

    # Formatage date
    try:
        dl = datetime.fromisoformat(date_limite.replace("Z", "+00:00"))
        dl_fmt = dl.strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        dl_fmt = date_limite[:10] if date_limite else "N/A"

    # Budget
    if budget_estime:
        try:
            budget_str = f"{int(float(budget_estime)):,} EUR".replace(",", " ")
        except (ValueError, TypeError):
            budget_str = str(budget_estime)
    else:
        budget_str = "Non communique"

    # Couleur concurrence
    if concurrence_score < 20:
        conc_color = "#16a34a"
        conc_label = "Tres faible"
    elif concurrence_score < 30:
        conc_color = "#16a34a"
        conc_label = "Faible"
    else:
        conc_color = "#f59e0b"
        conc_label = "Moderee"

    # Couleur score
    score_color = "#16a34a" if score_pct >= 80 else "#2563eb"

    # Raisons HTML
    raisons_html = "".join(
        f'<li style="padding:4px 0;color:#334155;">{r}</li>' for r in raisons
    )

    html = f"""
    <div style="font-family:Calibri,Arial,sans-serif;max-width:600px;margin:0 auto;">

        <!-- Header -->
        <div style="background:#1E3A5F;color:white;padding:16px 24px;border-radius:8px 8px 0 0;">
            <h2 style="margin:0;font-size:1.3em;">AO Hunter - Alerte Opportunite</h2>
            <p style="margin:6px 0 0;font-size:0.9em;opacity:0.85;">
                Un appel d'offres a fort potentiel vient d'etre detecte
            </p>
        </div>

        <div style="padding:24px;border:1px solid #e2e8f0;border-top:0;">

            <!-- AO Card -->
            <div style="background:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;padding:20px;margin-bottom:20px;">
                <h3 style="margin:0 0 8px;color:#1E3A5F;font-size:1.15em;">{titre}</h3>
                <table style="width:100%;border-collapse:collapse;">
                    <tr>
                        <td style="padding:6px 0;color:#64748b;font-size:0.9em;width:140px;">Acheteur</td>
                        <td style="padding:6px 0;font-weight:600;color:#334155;">{acheteur}</td>
                    </tr>
                    <tr>
                        <td style="padding:6px 0;color:#64748b;font-size:0.9em;">Date limite</td>
                        <td style="padding:6px 0;font-weight:600;color:#dc2626;">{dl_fmt} ({jours_restants} jours)</td>
                    </tr>
                    <tr>
                        <td style="padding:6px 0;color:#64748b;font-size:0.9em;">Score pertinence</td>
                        <td style="padding:6px 0;font-weight:700;color:{score_color};font-size:1.1em;">{score_pct}%</td>
                    </tr>
                    <tr>
                        <td style="padding:6px 0;color:#64748b;font-size:0.9em;">Budget estime</td>
                        <td style="padding:6px 0;font-weight:600;color:#334155;">{budget_str}</td>
                    </tr>
                    <tr>
                        <td style="padding:6px 0;color:#64748b;font-size:0.9em;">Concurrence</td>
                        <td style="padding:6px 0;font-weight:600;color:{conc_color};">{conc_label} ({concurrence_score}/100)</td>
                    </tr>
                </table>
            </div>

            <!-- Raisons -->
            <div style="margin-bottom:20px;">
                <h4 style="color:#1E3A5F;margin:0 0 8px;font-size:1em;border-bottom:2px solid #1E3A5F;padding-bottom:4px;">
                    Pourquoi cette opportunite est interessante
                </h4>
                <ul style="margin:0;padding-left:20px;">
                    {raisons_html}
                </ul>
            </div>

            <!-- CTA Button -->
            <div style="text-align:center;margin:24px 0 8px;">
                <a href="{ao_url}"
                   style="display:inline-block;background:#1E3A5F;color:white;padding:12px 32px;
                          border-radius:6px;text-decoration:none;font-weight:600;font-size:1em;">
                    Analyser maintenant
                </a>
            </div>

        </div>

        <!-- Footer -->
        <div style="background:#f1f5f9;padding:12px 24px;border-radius:0 0 8px 8px;
                    font-size:0.8em;color:#64748b;text-align:center;border:1px solid #e2e8f0;border-top:0;">
            Alerte automatique - AO Hunter
            &nbsp;|&nbsp;
            <a href="{DASHBOARD_URL}" style="color:#2563eb;text-decoration:none;">Ouvrir le dashboard</a>
        </div>
    </div>
    """
    return html


# ============================================================
# Creation brouillon Gmail
# ============================================================

def creer_alerte_gmail(ao: dict, alerte: dict) -> bool:
    """Cree un brouillon Gmail pour une alerte AO a fort potentiel.

    Uses IMAP (meme pattern que rapport_hebdo.py).

    Args:
        ao: dict de l'AO
        alerte: dict retourne par verifier_alerte_instantanee()

    Returns:
        True si le brouillon a ete cree.
    """
    if not SMTP_PASSWORD:
        logger.warning("SMTP_PASSWORD non configure - alerte Gmail non creee")
        return False

    titre_court = alerte.get("titre", "AO")[:60]
    subject = f"\U0001f3af AO a fort potentiel: {titre_court}"

    try:
        html_body = _formater_email_alerte(alerte)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = EMAIL_USER
        msg["To"] = EMAIL_USER
        msg["X-AO-Hunter"] = "alerte-instantanee"
        msg.attach(MIMEText(html_body, "html", "utf-8"))

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
            logger.info(f"Alerte Gmail creee: {subject}")
            return True
        else:
            logger.error(f"Erreur IMAP append alerte: {status}")
            return False

    except Exception as e:
        logger.error(f"Erreur creation alerte Gmail: {e}")
        return False


# ============================================================
# Traitement batch
# ============================================================

def traiter_alertes_batch(nouveaux_ao: list[dict]) -> list[dict]:
    """Traite un batch de nouveaux AO et cree des alertes pour ceux a fort potentiel.

    Appele apres chaque cycle de veille.

    Args:
        nouveaux_ao: liste des AO nouvellement detectes

    Returns:
        liste des AO qui ont declenche une alerte
    """
    if not nouveaux_ao:
        return []

    alertes_declenchees = []
    alertes_existantes = _charger_alertes()

    for ao in nouveaux_ao:
        ao_id = ao.get("id", "")
        if not ao_id:
            continue

        # Anti-doublon rapide
        if _ao_deja_alerte(ao_id, alertes_existantes):
            continue

        alerte = verifier_alerte_instantanee(ao)
        if alerte is None:
            continue

        # Creer le brouillon Gmail
        brouillon_ok = creer_alerte_gmail(ao, alerte)
        alerte["brouillon_cree"] = brouillon_ok

        # Enregistrer dans l'historique anti-doublon
        alertes_existantes.append({
            "ao_id": ao_id,
            "titre": ao.get("titre", ""),
            "date_alerte": alerte["date_alerte"],
            "brouillon_cree": brouillon_ok,
            "score_pertinence": alerte.get("score_pertinence"),
            "concurrence_score": alerte.get("concurrence_score"),
        })

        alertes_declenchees.append(alerte)
        logger.info(
            f"Alerte instantanee: {ao.get('titre', '')[:50]} "
            f"(score={alerte.get('score_pertinence')}, conc={alerte.get('concurrence_score')}) "
            f"brouillon={'OK' if brouillon_ok else 'ECHEC'}"
        )

    # Sauvegarder l'historique mis a jour
    if alertes_declenchees:
        _sauvegarder_alertes(alertes_existantes)

    logger.info(f"Alertes batch: {len(alertes_declenchees)}/{len(nouveaux_ao)} AO ont declenche une alerte")
    return alertes_declenchees


def get_alertes_recentes(nb: int = 20) -> list[dict]:
    """Retourne les alertes les plus recentes.

    Args:
        nb: nombre max d'alertes a retourner

    Returns:
        liste des alertes triees par date decroissante
    """
    alertes = _charger_alertes()
    alertes.sort(key=lambda a: a.get("date_alerte", ""), reverse=True)
    return alertes[:nb]
