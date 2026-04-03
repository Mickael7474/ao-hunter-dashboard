"""
Lecture des brouillons Gmail via IMAP.

Permet d'afficher les brouillons crees par le pipeline auto directement
dans le dashboard, avec un lien vers Gmail pour les ouvrir/modifier/envoyer.

Ne modifie JAMAIS les brouillons. Lecture seule.
"""

import os
import imaplib
import email
import logging
from email.header import decode_header
from datetime import datetime

logger = logging.getLogger("ao_hunter.brouillons")

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
EMAIL_USER = "mickael.bertolla@gmail.com"
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")


def _decode_header_value(value):
    """Decode un header email (peut etre encode en UTF-8, base64, etc.)."""
    if not value:
        return ""
    decoded_parts = decode_header(value)
    result = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(part)
    return " ".join(result)


def _trouver_dossier_brouillons(imap):
    """Trouve le dossier brouillons Gmail (FR ou EN)."""
    for folder_name in ['"[Gmail]/Brouillons"', '"[Gmail]/Drafts"']:
        status, _ = imap.select(folder_name)
        if status == "OK":
            return folder_name

    # Fallback: lister tous les dossiers
    status, folders = imap.list()
    if status == "OK":
        for f in folders:
            f_decoded = f.decode("utf-8") if isinstance(f, bytes) else f
            if "draft" in f_decoded.lower() or "brouillon" in f_decoded.lower():
                parts = f_decoded.split('"')
                if len(parts) >= 4:
                    folder = parts[-2]
                    status, _ = imap.select(f'"{folder}"')
                    if status == "OK":
                        return f'"{folder}"'
    return None


def lister_brouillons(max_results=50, filtre_ao_hunter=False):
    """Liste les brouillons Gmail.

    Args:
        max_results: nombre max de brouillons a retourner
        filtre_ao_hunter: si True, ne retourne que les brouillons crees par AO Hunter

    Returns:
        list[dict] avec {id, sujet, destinataire, date, apercu, ao_hunter, gmail_url}
    """
    if not SMTP_PASSWORD:
        logger.warning("SMTP_PASSWORD non configure - impossible de lire les brouillons")
        return []

    brouillons = []

    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        imap.login(EMAIL_USER, SMTP_PASSWORD)

        folder = _trouver_dossier_brouillons(imap)
        if not folder:
            logger.error("Dossier brouillons introuvable")
            imap.logout()
            return []

        # Chercher tous les brouillons (ou filtrer par header AO Hunter)
        if filtre_ao_hunter:
            status, msg_ids = imap.search(None, 'HEADER "X-AO-Hunter" "pipeline-auto"')
        else:
            status, msg_ids = imap.search(None, "ALL")

        if status != "OK" or not msg_ids[0]:
            imap.logout()
            return []

        # Prendre les N derniers
        id_list = msg_ids[0].split()
        id_list = id_list[-max_results:]  # les plus recents
        id_list.reverse()  # plus recent en premier

        for msg_id in id_list:
            try:
                # Fetch headers + debut du body (pas tout le message pour la perf)
                status, data = imap.fetch(msg_id, "(RFC822.HEADER BODY.PEEK[TEXT]<0.500>)")
                if status != "OK":
                    continue

                # Parser les headers
                header_data = data[0][1]
                msg = email.message_from_bytes(header_data)

                sujet = _decode_header_value(msg.get("Subject", ""))
                destinataire = _decode_header_value(msg.get("To", ""))
                date_str = msg.get("Date", "")
                is_ao_hunter = msg.get("X-AO-Hunter", "") == "pipeline-auto"

                # Parser la date
                date_parsed = None
                if date_str:
                    try:
                        date_parsed = email.utils.parsedate_to_datetime(date_str)
                    except Exception:
                        pass

                # Apercu du body (texte brut)
                apercu = ""
                if len(data) > 2 and data[2] and len(data[2]) > 1:
                    body_preview = data[2][1]
                    if isinstance(body_preview, bytes):
                        apercu = body_preview.decode("utf-8", errors="replace")[:200]
                    elif isinstance(body_preview, str):
                        apercu = body_preview[:200]
                # Nettoyer l'apercu (enlever HTML tags basiques)
                import re
                apercu = re.sub(r'<[^>]+>', '', apercu).strip()[:150]

                brouillons.append({
                    "id": msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id),
                    "sujet": sujet,
                    "destinataire": destinataire,
                    "date": date_parsed.strftime("%d/%m/%Y %H:%M") if date_parsed else date_str[:20],
                    "date_iso": date_parsed.isoformat() if date_parsed else "",
                    "apercu": apercu,
                    "ao_hunter": is_ao_hunter,
                    "gmail_url": "https://mail.google.com/mail/u/0/#drafts",
                })

            except Exception as e:
                logger.debug(f"Erreur lecture brouillon {msg_id}: {e}")
                continue

        imap.logout()

    except imaplib.IMAP4.error as e:
        logger.error(f"Erreur IMAP: {e}")
    except Exception as e:
        logger.error(f"Erreur lecture brouillons: {e}")

    return brouillons


def compter_brouillons_ao_hunter():
    """Compte rapidement le nombre de brouillons AO Hunter (pour badge nav)."""
    if not SMTP_PASSWORD:
        return 0
    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        imap.login(EMAIL_USER, SMTP_PASSWORD)
        folder = _trouver_dossier_brouillons(imap)
        if not folder:
            imap.logout()
            return 0
        status, msg_ids = imap.search(None, 'HEADER "X-AO-Hunter" "pipeline-auto"')
        imap.logout()
        if status == "OK" and msg_ids[0]:
            return len(msg_ids[0].split())
        return 0
    except Exception:
        return 0


def supprimer_brouillon(msg_id):
    """Supprime un brouillon par son ID IMAP. Retourne True si succes."""
    if not SMTP_PASSWORD:
        return False
    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        imap.login(EMAIL_USER, SMTP_PASSWORD)
        folder = _trouver_dossier_brouillons(imap)
        if not folder:
            imap.logout()
            return False
        imap.store(msg_id.encode() if isinstance(msg_id, str) else msg_id, '+FLAGS', '\\Deleted')
        imap.expunge()
        imap.logout()
        return True
    except Exception as e:
        logger.error(f"Erreur suppression brouillon: {e}")
        return False
