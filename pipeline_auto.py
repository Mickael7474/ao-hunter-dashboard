"""
Pipeline full-auto : veille -> scoring -> Go/No-Go -> generation dossier -> brouillon Gmail.

Apres chaque cycle de veille, analyse les nouveaux AO pertinents (score >= seuil),
lance le Go/No-Go, genere le dossier automatiquement si GO, et cree un brouillon
Gmail pour relecture humaine avant envoi.

Le mail n'est JAMAIS envoye automatiquement : il est place dans les brouillons Gmail.
"""

import os
import json
import imaplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("ao_hunter.pipeline_auto")

DASHBOARD_DIR = Path(__file__).parent
AO_FILE = DASHBOARD_DIR / "ao_pertinents.json"
PIPELINE_LOG = DASHBOARD_DIR / "pipeline_auto_log.json"

# Seuils configurables
SCORE_MIN_PIPELINE = 0.70      # Score pertinence minimum pour entrer dans le pipeline
SCORE_GO_MIN = 60              # Score Go/No-Go minimum pour generer un dossier
MAX_DOSSIERS_PAR_JOUR = 1      # Max 1 dossier genere par jour (phase prudente)

# Config email (brouillons Gmail via IMAP)
IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
EMAIL_USER = "mickael.bertolla@gmail.com"
EMAIL_DEST = "contact@almera.one"
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")

# URL du dashboard
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "https://ao-hunter-dashboard.onrender.com")


def charger_ao() -> list[dict]:
    if AO_FILE.exists():
        try:
            return json.loads(AO_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return []


def sauvegarder_ao(appels: list[dict]):
    AO_FILE.write_text(json.dumps(appels, ensure_ascii=False, indent=2), encoding="utf-8")


def charger_log() -> list[dict]:
    if PIPELINE_LOG.exists():
        try:
            return json.loads(PIPELINE_LOG.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return []


def sauvegarder_log(log: list[dict]):
    # Garder les 200 derniers
    log = log[-200:]
    PIPELINE_LOG.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


def lancer_pipeline(nouveaux_ao: list[dict] = None) -> dict:
    """Execute le pipeline full-auto sur les AO pertinents.

    Args:
        nouveaux_ao: Liste d'AO a traiter. Si None, charge les AO avec score >= seuil
                     qui n'ont pas encore ete traites.

    Returns:
        dict avec: traites, generes, brouillons, erreurs, details
    """
    from analyse_dce import go_no_go
    from modeles_reponse import detecter_type_prestation

    appels = charger_ao()
    log = charger_log()

    # Identifier les AO deja traites par le pipeline
    ids_traites = {entry["ao_id"] for entry in log if entry.get("ao_id")}

    # Verifier si on a deja genere un dossier aujourd'hui
    aujourd_hui = datetime.now().strftime("%Y-%m-%d")
    dossiers_aujourd_hui = sum(
        1 for entry in log
        if entry.get("action") == "GENERE"
        and entry.get("timestamp", "").startswith(aujourd_hui)
    )
    if dossiers_aujourd_hui >= MAX_DOSSIERS_PAR_JOUR:
        logger.info(f"Pipeline auto: {dossiers_aujourd_hui} dossier(s) deja genere(s) aujourd'hui (max {MAX_DOSSIERS_PAR_JOUR})")
        return {"traites": 0, "generes": 0, "brouillons": 0, "erreurs": 0, "details": [f"Max {MAX_DOSSIERS_PAR_JOUR}/jour atteint"]}

    # Filtrer les AO eligibles
    if nouveaux_ao is None:
        candidats = [
            ao for ao in appels
            if ao.get("score_pertinence", 0) >= SCORE_MIN_PIPELINE
            and ao.get("id") not in ids_traites
            and ao.get("statut", "nouveau") in ("nouveau", "analyse")
        ]
    else:
        candidats = [
            ao for ao in nouveaux_ao
            if ao.get("score_pertinence", 0) >= SCORE_MIN_PIPELINE
            and ao.get("id") not in ids_traites
        ]

    if not candidats:
        logger.info("Pipeline auto: aucun AO eligible")
        return {"traites": 0, "generes": 0, "brouillons": 0, "erreurs": 0, "details": []}

    # Trier par score decroissant
    candidats.sort(key=lambda a: a.get("score_pertinence", 0), reverse=True)

    traites = 0
    generes = 0
    brouillons = 0
    erreurs = 0
    details = []

    dossiers_restants = MAX_DOSSIERS_PAR_JOUR - dossiers_aujourd_hui
    for ao in candidats[:dossiers_restants + 5]:  # +5 pour compenser les NO-GO
        if generes >= dossiers_restants:
            break

        ao_id = ao.get("id", "inconnu")
        titre = ao.get("titre", "Sans titre")[:80]
        score_pct = int(ao.get("score_pertinence", 0) * 100)

        log_entry = {
            "ao_id": ao_id,
            "titre": titre,
            "score_pertinence": score_pct,
            "timestamp": datetime.now().isoformat(),
        }

        traites += 1

        # --- Etape 0 : Tentative de telechargement et parsing DCE ---
        dce_texte = ""
        rc_info = {}
        dossier_dce = None
        try:
            from dce_auto import telecharger_dce_auto
            from dce_parser import extraire_texte_dce, analyser_dce_complet

            result_dce = telecharger_dce_auto(ao)
            if result_dce.get("success") and result_dce.get("dossier"):
                dossier_dce = Path(__file__).parent / "dossiers_generes" / result_dce["dossier"]
                dce_texte = extraire_texte_dce(dossier_dce)
                if dce_texte:
                    log_entry["dce_telecharge"] = True
                    log_entry["dce_nb_chars"] = len(dce_texte)
                    logger.info(f"Pipeline: DCE extrait pour {ao_id} ({len(dce_texte)} chars)")
        except Exception as e:
            logger.warning(f"Pipeline: echec DCE pour {ao_id}: {e}")

        # --- Etape 0b : Extraction du Reglement de Consultation (RC) ---
        try:
            if dossier_dce and dossier_dce.exists():
                from extraction_rc import extraire_rc, adapter_dossier
                rc_info = extraire_rc(dossier_dce)
                if rc_info.get("fichier_rc"):
                    log_entry["rc_extrait"] = True
                    log_entry["rc_fichier"] = rc_info["fichier_rc"]
                    log_entry["rc_nb_criteres"] = len(rc_info.get("criteres_attribution", []))
                    log_entry["rc_nb_pieces"] = len(rc_info.get("pieces_exigees", []))
                    logger.info(f"Pipeline: RC extrait pour {ao_id} ({rc_info['fichier_rc']})")

                    # Enrichir l'AO avec les infos du RC
                    if rc_info.get("date_limite") and not ao.get("date_limite"):
                        ao["date_limite"] = rc_info["date_limite"]
                    if rc_info.get("montant_estime") and not ao.get("budget_estime"):
                        ao["budget_estime"] = rc_info["montant_estime"]
                    if rc_info.get("criteres_attribution"):
                        ao["criteres_rc"] = rc_info["criteres_attribution"]
                    if rc_info.get("lots"):
                        ao["lots_rc"] = rc_info["lots"]
                    if rc_info.get("pieces_exigees"):
                        ao["pieces_exigees_rc"] = rc_info["pieces_exigees"]

                    # Adapter le dossier
                    adaptations = adapter_dossier(rc_info, ao)
                    if adaptations.get("alertes"):
                        log_entry["rc_alertes"] = adaptations["alertes"]
                    if adaptations.get("recommandations"):
                        log_entry["rc_recommandations"] = adaptations["recommandations"][:5]

                    # Enrichir le dce_texte avec les criteres pour le memoire
                    if rc_info.get("criteres_attribution") and dce_texte:
                        criteres_txt = "\n--- CRITERES D'ATTRIBUTION (extraits du RC) ---\n"
                        for c in rc_info["criteres_attribution"]:
                            criteres_txt += f"- {c['nom']} : {c['poids']}%\n"
                        dce_texte = criteres_txt + "\n" + dce_texte
        except Exception as e:
            logger.warning(f"Pipeline: echec extraction RC pour {ao_id}: {e}")

        # --- Etape 1 : Go/No-Go (enrichi DCE si disponible) ---
        try:
            if dce_texte:
                from dce_parser import analyser_dce_complet
                result_analyse = analyser_dce_complet(dce_texte, ao)
                resultat_gng = result_analyse["go_no_go"]
                log_entry["analyse_dce"] = True
            else:
                resultat_gng = go_no_go(ao)

            decision = resultat_gng.get("decision", "NO-GO")
            score_gng = resultat_gng.get("score", 0)
            log_entry["go_no_go"] = decision
            log_entry["score_go_no_go"] = score_gng
            log_entry["atouts"] = resultat_gng.get("atouts", [])
            log_entry["risques"] = resultat_gng.get("risques", [])

            if decision == "NO-GO" or score_gng < SCORE_GO_MIN:
                log_entry["action"] = "SKIP"
                log_entry["raison"] = f"Go/No-Go: {decision} (score {score_gng}/100)"
                log.append(log_entry)
                details.append(f"SKIP {titre[:40]} - {decision} ({score_gng}/100)")
                logger.info(f"Pipeline: SKIP {ao_id} - {decision} ({score_gng}/100)")
                continue

        except Exception as e:
            log_entry["action"] = "ERREUR"
            log_entry["raison"] = f"Erreur Go/No-Go: {str(e)}"
            log.append(log_entry)
            erreurs += 1
            logger.error(f"Pipeline: erreur Go/No-Go {ao_id}: {e}")
            continue

        # --- Etape 2 : Detection type de prestation ---
        type_presta = detecter_type_prestation(ao)
        log_entry["type_prestation"] = type_presta

        # --- Etape 3 : Generation dossier ---
        try:
            from generateur_render import generer_dossier_complet
            result_gen = generer_dossier_complet(ao, type_presta, gng_result=resultat_gng, dce_texte=dce_texte)

            if not result_gen.get("success"):
                log_entry["action"] = "ERREUR_GEN"
                log_entry["raison"] = result_gen.get("erreur", "Erreur inconnue")
                log.append(log_entry)
                erreurs += 1
                details.append(f"ERREUR GEN {titre[:40]} - {result_gen.get('erreur', '')[:50]}")
                logger.error(f"Pipeline: erreur generation {ao_id}: {result_gen.get('erreur')}")
                continue

            dossier_nom = result_gen.get("dossier_nom", "")
            log_entry["dossier"] = dossier_nom
            log_entry["nb_mots"] = result_gen.get("nb_mots", 0)
            generes += 1

            # Mettre a jour le statut de l'AO dans la base
            for a in appels:
                if a.get("id") == ao_id:
                    a["statut"] = "analyse"
                    a["pipeline_auto"] = True
                    a["dossier_genere"] = dossier_nom
                    a["date_pipeline"] = datetime.now().isoformat()
                    break

            logger.info(f"Pipeline: dossier genere {dossier_nom} ({result_gen.get('nb_mots', 0)} mots)")

        except ImportError as e:
            log_entry["action"] = "ERREUR_IMPORT"
            log_entry["raison"] = f"Module non disponible: {str(e)}"
            log.append(log_entry)
            erreurs += 1
            continue
        except Exception as e:
            log_entry["action"] = "ERREUR_GEN"
            log_entry["raison"] = str(e)
            log.append(log_entry)
            erreurs += 1
            logger.error(f"Pipeline: erreur generation {ao_id}: {e}")
            continue

        # --- Etape 4 : Creer brouillon Gmail ---
        try:
            ok = creer_brouillon_gmail(ao, resultat_gng, type_presta, dossier_nom)
            if ok:
                brouillons += 1
                log_entry["brouillon"] = True
                details.append(f"GO {titre[:40]} - dossier + brouillon crees")
            else:
                log_entry["brouillon"] = False
                details.append(f"GO {titre[:40]} - dossier OK, brouillon echoue")
        except Exception as e:
            log_entry["brouillon"] = False
            logger.error(f"Pipeline: erreur brouillon {ao_id}: {e}")
            details.append(f"GO {titre[:40]} - dossier OK, brouillon echoue")

        log_entry["action"] = "GENERE"
        log.append(log_entry)

    # Sauvegarder
    sauvegarder_ao(appels)
    sauvegarder_log(log)

    resultat = {
        "traites": traites,
        "generes": generes,
        "brouillons": brouillons,
        "erreurs": erreurs,
        "details": details,
        "timestamp": datetime.now().isoformat(),
    }

    logger.info(f"Pipeline auto termine: {generes} dossiers generes, {brouillons} brouillons")
    return resultat


def creer_brouillon_gmail(ao: dict, gng: dict, type_presta: str, dossier_nom: str) -> bool:
    """Cree un brouillon dans Gmail via IMAP (pas d'envoi auto).

    Le brouillon contient un resume de l'AO, le resultat Go/No-Go,
    le lien vers le dashboard, et un email type de candidature pre-rempli.

    Returns:
        True si le brouillon a ete cree avec succes.
    """
    if not SMTP_PASSWORD:
        logger.warning("SMTP_PASSWORD non configure - brouillon non cree")
        return False

    titre_ao = ao.get("titre", "Sans titre")
    acheteur = ao.get("acheteur", "Non precise")
    score_pct = int(ao.get("score_pertinence", 0) * 100)
    score_gng = gng.get("score", 0)
    ao_id = ao.get("id", "")
    date_limite = ao.get("date_limite", "Non precisee")
    if "T" in str(date_limite):
        date_limite = date_limite.split("T")[0]

    # URL detail AO sur le dashboard
    detail_url = f"{DASHBOARD_URL}/ao/{ao_id}"
    dossier_url = f"{DASHBOARD_URL}/dossiers/{dossier_nom}"

    # Construire le sujet
    subject = f"[AO Hunter] Candidature {type_presta} - {titre_ao[:70]}"

    # Construire le corps HTML
    atouts_html = ""
    if gng.get("atouts"):
        atouts_html = "<ul>" + "".join(f"<li>✅ {a}</li>" for a in gng["atouts"]) + "</ul>"

    risques_html = ""
    if gng.get("risques"):
        risques_html = "<ul>" + "".join(f"<li>⚠️ {r}</li>" for r in gng["risques"]) + "</ul>"

    body_html = f"""
    <div style="font-family:Calibri,Arial,sans-serif;max-width:700px;margin:0 auto;">

        <!-- Bandeau Pipeline Auto -->
        <div style="background:#1e3a5f;color:white;padding:12px 20px;border-radius:8px 8px 0 0;">
            <h2 style="margin:0;">🤖 AO Hunter - Pipeline Automatique</h2>
            <p style="margin:4px 0 0;font-size:0.9em;opacity:0.9;">
                Dossier genere automatiquement le {datetime.now():%d/%m/%Y a %H:%M}
            </p>
        </div>

        <div style="padding:20px;border:1px solid #e2e8f0;border-top:0;">

            <!-- Resume AO -->
            <h3 style="color:#1e3a5f;margin-top:0;">📋 {titre_ao}</h3>
            <table style="width:100%;border-collapse:collapse;margin-bottom:16px;">
                <tr><td style="padding:6px 12px;color:#64748b;width:140px;">Acheteur</td>
                    <td style="padding:6px 12px;font-weight:600;">{acheteur}</td></tr>
                <tr style="background:#f8fafc;">
                    <td style="padding:6px 12px;color:#64748b;">Type</td>
                    <td style="padding:6px 12px;">{type_presta}</td></tr>
                <tr><td style="padding:6px 12px;color:#64748b;">Date limite</td>
                    <td style="padding:6px 12px;font-weight:600;color:#dc2626;">{date_limite}</td></tr>
                <tr style="background:#f8fafc;">
                    <td style="padding:6px 12px;color:#64748b;">Budget</td>
                    <td style="padding:6px 12px;">{ao.get('budget_estime', 'Non precise')} EUR</td></tr>
                <tr><td style="padding:6px 12px;color:#64748b;">Region</td>
                    <td style="padding:6px 12px;">{ao.get('region', ao.get('lieu_execution', 'Non precise'))}</td></tr>
                <tr style="background:#f8fafc;">
                    <td style="padding:6px 12px;color:#64748b;">Source</td>
                    <td style="padding:6px 12px;">{ao.get('source', '-')}</td></tr>
            </table>

            <!-- Scores -->
            <div style="display:flex;gap:16px;margin-bottom:16px;">
                <div style="flex:1;background:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;padding:12px;text-align:center;">
                    <div style="font-size:0.8em;color:#64748b;">Score pertinence</div>
                    <div style="font-size:1.8em;font-weight:bold;color:{'#16a34a' if score_pct >= 70 else '#f59e0b'};">{score_pct}%</div>
                </div>
                <div style="flex:1;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:12px;text-align:center;">
                    <div style="font-size:0.8em;color:#64748b;">Go/No-Go</div>
                    <div style="font-size:1.8em;font-weight:bold;color:#16a34a;">GO ✅</div>
                    <div style="font-size:0.8em;color:#64748b;">{score_gng}/100</div>
                </div>
            </div>

            <!-- Atouts & Risques -->
            {f'<h4 style="color:#16a34a;">Atouts</h4>{atouts_html}' if atouts_html else ''}
            {f'<h4 style="color:#f59e0b;">Points de vigilance</h4>{risques_html}' if risques_html else ''}

            <!-- Liens -->
            <div style="margin-top:20px;padding:16px;background:#f8fafc;border-radius:8px;">
                <p style="margin:0 0 8px;font-weight:600;">🔗 Actions :</p>
                <a href="{detail_url}" style="display:inline-block;background:#2563eb;color:white;padding:8px 20px;text-decoration:none;border-radius:6px;margin-right:8px;">
                    Voir l'AO sur le dashboard
                </a>
                <a href="{dossier_url}" style="display:inline-block;background:#16a34a;color:white;padding:8px 20px;text-decoration:none;border-radius:6px;">
                    Voir le dossier genere
                </a>
            </div>

            <!-- Email type candidature -->
            <div style="margin-top:20px;padding:16px;background:#fffbeb;border:1px solid #fde68a;border-radius:8px;">
                <p style="margin:0 0 8px;font-weight:600;">📧 Email type de candidature (a adapter) :</p>
                <div style="background:white;padding:12px;border-radius:6px;font-size:0.9em;border:1px solid #e2e8f0;">
                    <p><strong>Objet :</strong> Candidature Almera - {titre_ao[:60]}</p>
                    <hr style="border:0;border-top:1px solid #e2e8f0;">
                    <p>Madame, Monsieur,</p>
                    <p>Nous avons l'honneur de vous soumettre notre candidature pour le marche
                    «&nbsp;{titre_ao}&nbsp;».</p>
                    <p>Almera (AI MENTOR), organisme de formation certifie Qualiopi et
                    titulaire de la certification RS6776 France Competences en Intelligence
                    Artificielle generative, accompagne les organisations dans leur
                    transformation par l'IA.</p>
                    <p>Vous trouverez ci-joint l'ensemble des pieces constitutives de
                    notre offre.</p>
                    <p>Nous restons a votre disposition pour tout complement d'information.</p>
                    <p>Cordialement,<br>
                    <strong>Mickael Bertolla</strong><br>
                    President - Almera<br>
                    contact@almera.one | +33 6 86 68 06 11<br>
                    almera.one</p>
                </div>
            </div>

        </div>

        <!-- Footer -->
        <div style="background:#f1f5f9;padding:10px 20px;border-radius:0 0 8px 8px;
                    font-size:0.8em;color:#64748b;text-align:center;">
            AO Hunter Pipeline Auto - Ce brouillon a ete cree automatiquement.
            Relisez et adaptez avant envoi.
        </div>
    </div>
    """

    try:
        # Construire le message
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = EMAIL_USER
        msg["To"] = EMAIL_DEST
        msg["X-AO-Hunter"] = "pipeline-auto"
        msg.attach(MIMEText(body_html, "html", "utf-8"))

        # Sauvegarder dans les brouillons Gmail via IMAP
        imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        imap.login(EMAIL_USER, SMTP_PASSWORD)

        # Gmail utilise "[Gmail]/Brouillons" en francais ou "[Gmail]/Drafts" en anglais
        # On tente les deux
        draft_folder = None
        for folder_name in ["[Gmail]/Brouillons", "[Gmail]/Drafts"]:
            status, _ = imap.select(f'"{folder_name}"')
            if status == "OK":
                draft_folder = folder_name
                break

        if not draft_folder:
            # Lister les dossiers pour trouver le bon
            status, folders = imap.list()
            if status == "OK":
                for f in folders:
                    f_decoded = f.decode("utf-8") if isinstance(f, bytes) else f
                    if "draft" in f_decoded.lower() or "brouillon" in f_decoded.lower():
                        # Extraire le nom du dossier
                        parts = f_decoded.split('"')
                        if len(parts) >= 4:
                            draft_folder = parts[-2]
                            break

        if not draft_folder:
            logger.error("Impossible de trouver le dossier Brouillons Gmail")
            imap.logout()
            return False

        # Ajouter le message dans les brouillons
        msg_bytes = msg.as_bytes()
        status, _ = imap.append(
            f'"{draft_folder}"',
            "\\Draft",
            None,
            msg_bytes,
        )

        imap.logout()

        if status == "OK":
            logger.info(f"Brouillon Gmail cree pour: {titre_ao[:60]}")
            return True
        else:
            logger.error(f"Erreur IMAP append: {status}")
            return False

    except Exception as e:
        logger.error(f"Erreur creation brouillon Gmail: {e}")
        return False
