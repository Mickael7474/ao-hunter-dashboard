"""
AO HUNTER - Dashboard Web
Interface de suivi des appels d'offres, scores et dossiers generes.

Features:
- Dashboard avec stats et graphiques
- Liste AO avec pagination, filtres, recherche live
- Detail AO avec notes, generation dossier
- Export CSV
- Dark mode
- Notifications navigateur (deadline < 3j)
- WebSocket auto-refresh pendant veille
- API REST complete

Usage:
    python dashboard/app.py
    Ouvrir http://localhost:5000
"""

import sys
import os

# Ensure dependencies are on path (project-local .deps folder)
_deps = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".deps")
if os.path.isdir(_deps) and _deps not in sys.path:
    sys.path.insert(0, _deps)

import csv
import json
import io
import threading
import logging
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from flask import (Flask, render_template, jsonify, request,
                   redirect, url_for, Response, make_response)
from flask_socketio import SocketIO
import yaml

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "ao-hunter-2026")
socketio = SocketIO(app, cors_allowed_origins="*")

# --- Scheduler pour veille automatique (Render) ---
_scheduler = None

def init_scheduler():
    """Initialise APScheduler pour la veille automatique toutes les 8h."""
    global _scheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.interval import IntervalTrigger
        from apscheduler.triggers.cron import CronTrigger

        _scheduler = BackgroundScheduler(daemon=True)
        _scheduler.add_job(
            func=_veille_auto,
            trigger=IntervalTrigger(hours=8),
            id="veille_auto",
            name="Veille automatique BOAMP+TED",
            replace_existing=True,
            next_run_time=datetime.now() + timedelta(minutes=2),  # premiere exec 2min apres boot
        )
        _scheduler.add_job(
            func=_rapport_hebdo_auto,
            trigger=CronTrigger(day_of_week="mon", hour=8),
            id="rapport_hebdo",
            name="Rapport hebdomadaire lundi 8h",
            replace_existing=True,
        )
        _scheduler.start()
        logger.info("Scheduler veille auto + rapport hebdo demarre")
    except ImportError:
        logger.warning("APScheduler non installe - veille auto desactivee")
    except Exception as e:
        logger.error(f"Erreur init scheduler: {e}")


def _veille_auto():
    """Fonction executee par le scheduler."""
    try:
        from veille_render import lancer_veille
        result = lancer_veille()
        logger.info(f"Veille auto terminee: {result}")
        socketio.emit("veille_complete", {
            "nouveaux": result["nouveaux"],
            "total": result["total"],
            "auto": True,
        })
    except Exception as e:
        logger.error(f"Erreur veille auto: {e}")

    # Verifier les deadlines et envoyer les rappels
    try:
        from rappels import envoyer_rappels
        appels = charger_ao()
        rappels_result = envoyer_rappels(appels)
        if rappels_result["envoyes"] > 0:
            logger.info(f"Rappels envoyes: {rappels_result['envoyes']}")
    except Exception as e:
        logger.error(f"Erreur rappels: {e}")

    # Veille attributions (AO soumis -> gagne/perdu)
    try:
        from veille_attributions import verifier_attributions
        attr_result = verifier_attributions()
        if attr_result["trouves"] > 0:
            logger.info(f"Attributions: {attr_result['trouves']} trouvees ({attr_result['gagnes']} gagnes)")
    except Exception as e:
        logger.error(f"Erreur veille attributions: {e}")

    # Veille concurrentielle (1x par jour suffit)
    try:
        from veille_concurrence import lancer_veille_concurrence
        conc = lancer_veille_concurrence()
        logger.info(f"Veille concurrence: {conc['nouvelles']} nouvelles attributions")
    except Exception as e:
        logger.error(f"Erreur veille concurrence: {e}")

    # Benchmark prix (collecte attributions pour stats marche)
    try:
        from benchmark_prix import collecter_attributions
        attribs = collecter_attributions(nb_pages=3)
        logger.info(f"Benchmark prix: {len(attribs)} attributions en base")
    except Exception as e:
        logger.error(f"Erreur benchmark prix: {e}")

    # Veille pre-informations (anticiper les AO 2-6 mois avant)
    try:
        from veille_preinformation import veille_preinfo
        preinfos = veille_preinfo()
        logger.info(f"Veille pre-info: {len(preinfos)} pre-informations")
    except Exception as e:
        logger.error(f"Erreur veille pre-info: {e}")

    # Auto-enrichissement CRM acheteurs
    try:
        from crm_acheteurs import enrichir_acheteur
        appels_crm = charger_ao()
        for ao in appels_crm:
            try:
                enrichir_acheteur(ao)
            except Exception:
                pass
        logger.info(f"CRM: enrichissement de {len(appels_crm)} AO")
    except Exception as e:
        logger.error(f"Erreur enrichissement CRM: {e}")

    # Pipeline full-auto : Go/No-Go -> generation dossier -> brouillon Gmail
    try:
        from pipeline_auto import lancer_pipeline
        pipeline_result = lancer_pipeline()
        if pipeline_result["generes"] > 0:
            logger.info(f"Pipeline auto: {pipeline_result['generes']} dossier(s) genere(s), {pipeline_result['brouillons']} brouillon(s)")
            socketio.emit("pipeline_complete", pipeline_result)
        else:
            logger.info(f"Pipeline auto: aucun dossier genere ({pipeline_result.get('details', [])})")
    except Exception as e:
        logger.error(f"Erreur pipeline auto: {e}")


def _rapport_hebdo_auto():
    """Fonction executee par le scheduler chaque lundi a 8h."""
    try:
        from rapport_hebdo import rapport_et_envoi
        result = rapport_et_envoi()
        if result.get("brouillon_cree"):
            logger.info(f"Rapport hebdo: brouillon cree ({result['rapport'].get('nb_nouveaux_ao', 0)} nouveaux AO)")
        elif result.get("doublon"):
            logger.info("Rapport hebdo: anti-doublon, deja genere cette semaine")
        else:
            logger.warning(f"Rapport hebdo: brouillon non cree ({result.get('erreur', 'inconnu')})")
    except Exception as e:
        logger.error(f"Erreur rapport hebdo auto: {e}")


logger = logging.getLogger("ao_hunter.dashboard")

# Chemins - en local: ao_hunter/resultats/, sur Render: dossier dashboard/
DASHBOARD_DIR = Path(__file__).parent
BASE_DIR = DASHBOARD_DIR.parent
RESULTATS_DIR = BASE_DIR / "resultats"

# Cherche d'abord dans resultats/ (local), sinon dans le dossier dashboard/ (Render)
if (RESULTATS_DIR / "ao_pertinents.json").exists():
    AO_CACHE = RESULTATS_DIR / "ao_pertinents.json"
    NOTES_FILE = RESULTATS_DIR / "ao_notes.json"
else:
    AO_CACHE = DASHBOARD_DIR / "ao_pertinents.json"
    NOTES_FILE = DASHBOARD_DIR / "ao_notes.json"

CONFIG_PATH = BASE_DIR / "config.yaml"

PER_PAGE = 20


# --- Jinja filters ---

@app.template_filter("as_str")
def as_str_filter(value):
    """Convert lists/other types to string for safe display."""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value) if value else ""


# --- Helpers ---

@app.context_processor
def inject_globals():
    return {"now": datetime.now(), "timedelta": timedelta}


def charger_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def charger_ao() -> list[dict]:
    if not AO_CACHE.exists():
        return []
    with open(AO_CACHE, "r", encoding="utf-8") as f:
        return json.load(f)


def sauvegarder_ao(appels: list[dict]):
    AO_CACHE.write_text(json.dumps(appels, ensure_ascii=False, indent=2), encoding="utf-8")


def charger_notes() -> dict:
    if not NOTES_FILE.exists():
        return {}
    with open(NOTES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def sauvegarder_notes(notes: dict):
    NOTES_FILE.write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")


DOSSIERS_INDEX = DASHBOARD_DIR / "dossiers_index.json"
DOSSIERS_GENERES_DIR = DASHBOARD_DIR / "dossiers_generes"
CHECKLIST_FILE = DASHBOARD_DIR / "checklist_etat.json"
COMMENTAIRES_FILE = DASHBOARD_DIR / "commentaires.json"


def _generer_checklist(ao: dict, dossier: dict, modele: dict) -> list[dict]:
    """Genere la checklist de soumission pour un AO."""
    items = [
        {"id": "memoire", "label": "Memoire technique", "categorie": "Offre"},
        {"id": "bpu", "label": "BPU / DPGF (grille tarifaire)", "categorie": "Offre"},
        {"id": "planning", "label": "Planning previsionnel", "categorie": "Offre"},
        {"id": "dc1", "label": "DC1 - Lettre de candidature", "categorie": "Administratif"},
        {"id": "dc2", "label": "DC2 - Declaration du candidat", "categorie": "Administratif"},
        {"id": "kbis", "label": "Extrait Kbis (< 3 mois)", "categorie": "Administratif"},
        {"id": "urssaf", "label": "Attestation URSSAF", "categorie": "Administratif"},
        {"id": "fiscale", "label": "Attestation fiscale", "categorie": "Administratif"},
        {"id": "qualiopi", "label": "Certificat Qualiopi", "categorie": "Administratif"},
        {"id": "rib", "label": "RIB", "categorie": "Administratif"},
        {"id": "assurance", "label": "Attestation RC Pro", "categorie": "Administratif"},
        {"id": "cv", "label": "CV formateurs/consultants", "categorie": "Offre"},
        {"id": "references", "label": "Liste de references", "categorie": "Offre"},
    ]

    # Ajouter les pieces specifiques du modele
    if modele and modele.get("pieces_specifiques"):
        ids_existants = {i["id"] for i in items}
        for p in modele["pieces_specifiques"]:
            pid = p.lower().replace(" ", "_").replace("/", "_")[:20]
            if pid not in ids_existants:
                items.append({"id": pid, "label": p, "categorie": "Specifique"})

    # Marquer auto les pieces du dossier genere
    if dossier and dossier.get("fichiers"):
        fichiers_lower = [f.lower() for f in dossier["fichiers"]]
        for item in items:
            for f in fichiers_lower:
                if item["id"] in f or item["label"].lower().split()[0] in f:
                    item["auto_ok"] = True
                    break

    items.append({"id": "email", "label": "Email de soumission envoye", "categorie": "Soumission"})
    items.append({"id": "depot", "label": "Depot sur la plateforme", "categorie": "Soumission"})

    return items


def _charger_checklist_etat(ao_id: str) -> dict:
    """Charge l'etat de la checklist pour un AO."""
    if CHECKLIST_FILE.exists():
        try:
            data = json.loads(CHECKLIST_FILE.read_text(encoding="utf-8"))
            return data.get(ao_id, {})
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _sauvegarder_checklist_etat(ao_id: str, etat: dict):
    data = {}
    if CHECKLIST_FILE.exists():
        try:
            data = json.loads(CHECKLIST_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    data[ao_id] = etat
    CHECKLIST_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _charger_commentaires(ao_id: str) -> list[dict]:
    """Charge les commentaires pour un AO."""
    if COMMENTAIRES_FILE.exists():
        try:
            data = json.loads(COMMENTAIRES_FILE.read_text(encoding="utf-8"))
            return data.get(ao_id, [])
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _sauvegarder_commentaire(ao_id: str, commentaire: dict):
    data = {}
    if COMMENTAIRES_FILE.exists():
        try:
            data = json.loads(COMMENTAIRES_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    if ao_id not in data:
        data[ao_id] = []
    data[ao_id].append(commentaire)
    COMMENTAIRES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _scan_dossiers_dir(base_dir: Path) -> list[dict]:
    """Scanne un repertoire pour lister les dossiers generes."""
    dossiers = []
    if not base_dir.exists():
        return dossiers
    for d in sorted(base_dir.iterdir(), reverse=True):
        if d.is_dir() and d.name not in ("__pycache__", "test_dc1_dc2"):
            fichiers = [f.name for f in d.glob("*") if f.is_file()]
            if fichiers:
                dossiers.append({
                    "nom": d.name,
                    "chemin": str(d),
                    "nb_fichiers": len(fichiers),
                    "fichiers": fichiers,
                    "date_creation": datetime.fromtimestamp(d.stat().st_ctime).strftime("%Y-%m-%d %H:%M"),
                })
    return dossiers


def lister_dossiers() -> list[dict]:
    # D'abord local (PC)
    dossiers = _scan_dossiers_dir(RESULTATS_DIR)
    # Puis dossiers_generes/ (Render)
    if not dossiers:
        dossiers = _scan_dossiers_dir(DOSSIERS_GENERES_DIR)
    # Fallback: index JSON
    if not dossiers and DOSSIERS_INDEX.exists():
        with open(DOSSIERS_INDEX, "r", encoding="utf-8") as f:
            dossiers = json.load(f)
    return dossiers


def stats_ao(appels: list[dict]) -> dict:
    now = datetime.now()
    stats = {
        "total": len(appels),
        "sources": {},
        "score_moyen": 0,
        "avec_deadline": 0,
        "deadline_proche": 0,
        "expires": 0,
        "par_statut": {},
        "scores_distribution": {"0-20": 0, "20-40": 0, "40-60": 0, "60-80": 0, "80-100": 0},
        "regions": {},
    }
    scores = []
    for ao in appels:
        src = ao.get("source", "Inconnu")
        stats["sources"][src] = stats["sources"].get(src, 0) + 1

        score = ao.get("score_pertinence", 0) or 0
        if score > 0:
            scores.append(score)
        pct = int(score * 100)
        if pct >= 80: stats["scores_distribution"]["80-100"] += 1
        elif pct >= 60: stats["scores_distribution"]["60-80"] += 1
        elif pct >= 40: stats["scores_distribution"]["40-60"] += 1
        elif pct >= 20: stats["scores_distribution"]["20-40"] += 1
        else: stats["scores_distribution"]["0-20"] += 1

        statut = ao.get("statut", "nouveau")
        stats["par_statut"][statut] = stats["par_statut"].get(statut, 0) + 1

        region_raw = ao.get("region") or ""
        if isinstance(region_raw, list):
            region_raw = ", ".join(str(r) for r in region_raw) if region_raw else ""
        region = str(region_raw)[:20]
        if region:
            stats["regions"][region] = stats["regions"].get(region, 0) + 1

        dl = ao.get("date_limite", "")
        if dl:
            stats["avec_deadline"] += 1
            try:
                date_dl = datetime.fromisoformat(dl.split("T")[0])
                delta = (date_dl - now).days
                if delta < 0:
                    stats["expires"] += 1
                elif delta <= 7:
                    stats["deadline_proche"] += 1
            except (ValueError, TypeError):
                pass

    stats["score_moyen"] = sum(scores) / len(scores) if scores else 0
    # Top 10 regions
    stats["top_regions"] = dict(sorted(stats["regions"].items(), key=lambda x: x[1], reverse=True)[:10])
    return stats


def filtrer_ao(appels, source="", score_min=0, statut="", recherche="", tri="score"):
    filtre = appels
    if source:
        filtre = [a for a in filtre if a.get("source") == source]
    if score_min > 0:
        filtre = [a for a in filtre if (a.get("score_pertinence") or 0) >= score_min]
    if statut:
        filtre = [a for a in filtre if a.get("statut") == statut]
    if recherche:
        q = recherche.lower()
        filtre = [a for a in filtre if q in (a.get("titre") or "").lower()
                  or q in (a.get("description") or "").lower()
                  or q in (a.get("acheteur") or "").lower()]
    if tri == "score":
        filtre.sort(key=lambda a: a.get("score_pertinence") or 0, reverse=True)
    elif tri == "date":
        filtre.sort(key=lambda a: a.get("date_limite") or "", reverse=True)
    elif tri == "titre":
        filtre.sort(key=lambda a: (a.get("titre") or "").lower())
    return filtre


def _ao_urgents(appels: list[dict], max_days=7) -> list[dict]:
    now = datetime.now()
    urgents = []
    for ao in appels:
        dl = ao.get("date_limite", "")
        if not dl:
            continue
        try:
            date_dl = datetime.fromisoformat(dl.split("T")[0])
            delta = (date_dl - now).days
            if 0 <= delta <= max_days:
                ao_copy = dict(ao)
                ao_copy["jours_restants"] = delta
                urgents.append(ao_copy)
        except (ValueError, TypeError):
            pass
    urgents.sort(key=lambda a: a.get("jours_restants", 99))
    return urgents


STATUTS_KANBAN = ["nouveau", "analyse", "candidature", "soumis", "gagne", "perdu", "ignore"]

PRESTATIONS_KEYWORDS = {
    "Formation": ["formation", "formateur", "pedagogie", "stagiaire", "apprenant",
                  "competences", "certification", "qualiopi", "cpf", "opco",
                  "apprentissage", "enseignement", "e-learning", "module",
                  "programme pedagogique", "parcours de formation"],
    "Consulting / AMO": ["conseil", "consulting", "accompagnement", "audit",
                         "assistance a maitrise", "amo", "diagnostic", "expertise",
                         "preconisation", "etude", "strategie", "transformation",
                         "conduite du changement", "schema directeur"],
    "Developpement": ["developpement", "logiciel", "application", "site web",
                      "plateforme", "api", "integration", "maintenance applicative",
                      "tma", "devops", "cloud", "hebergement", "infrastructure",
                      "systeme d'information", "numerique", "digital"],
}


def detecter_prestations(ao: dict) -> list[dict]:
    """Detecte les types de prestations pertinentes pour un AO."""
    texte = f"{ao.get('titre', '')} {ao.get('description', '')}".lower()
    resultats = []
    for presta, mots in PRESTATIONS_KEYWORDS.items():
        matches = [m for m in mots if m in texte]
        if matches:
            score = min(100, len(matches) * 25)
            resultats.append({"type": presta, "score": score, "mots_cles": matches[:5]})
    resultats.sort(key=lambda x: x["score"], reverse=True)
    return resultats


# --- Pages ---

@app.route("/")
def index():
    appels = charger_ao()
    dossiers = lister_dossiers()
    statistiques = stats_ao(appels)
    return render_template(
        "index.html",
        stats=statistiques,
        nb_dossiers=len(dossiers),
        top_ao=sorted(appels, key=lambda a: a.get("score_pertinence") or 0, reverse=True)[:10],
        urgents=_ao_urgents(appels),
    )


@app.route("/ao")
def liste_ao():
    appels = charger_ao()
    source = request.args.get("source", "")
    score_min_str = request.args.get("score_min", "0")
    statut = request.args.get("statut", "")
    recherche = request.args.get("q", "")
    tri = request.args.get("tri", "score")
    page = request.args.get("page", "1")

    try:
        score_min = float(score_min_str)
    except ValueError:
        score_min = 0
    try:
        page = max(1, int(page))
    except ValueError:
        page = 1

    filtre = filtrer_ao(appels, source, score_min, statut, recherche, tri)

    total_pages = max(1, (len(filtre) + PER_PAGE - 1) // PER_PAGE)
    page = min(page, total_pages)
    page_appels = filtre[(page - 1) * PER_PAGE: page * PER_PAGE]

    sources = sorted(set(a.get("source", "") for a in appels))
    statuts = sorted(set(a.get("statut", "") for a in appels))

    # Pre-calculer estimations pour la page courante
    estimations = {}
    try:
        from estimation_marche import estimer_marche
        for ao in page_appels:
            try:
                estimations[ao.get("id")] = estimer_marche(ao)
            except Exception:
                pass
    except ImportError:
        pass

    return render_template(
        "ao_liste.html",
        appels=page_appels,
        estimations=estimations,
        total=len(appels),
        total_filtre=len(filtre),
        sources=sources,
        statuts=statuts,
        filtre_source=source,
        filtre_score_min=score_min,
        filtre_statut=statut,
        filtre_recherche=recherche,
        filtre_tri=tri,
        page=page,
        total_pages=total_pages,
        per_page=PER_PAGE,
    )


def _calculer_roi_stats():
    """Calcule toutes les stats ROI a partir des donnees. Retourne un dict."""
    appels = charger_ao()
    dossiers = lister_dossiers()

    # Stats pipeline
    nb_total = len(appels)
    nb_analyses = len([a for a in appels if a.get("statut") not in ("nouveau", "ignore")])
    nb_candidatures = len([a for a in appels if a.get("statut") in ("candidature", "soumis", "gagne", "perdu")])
    nb_soumis = len([a for a in appels if a.get("statut") in ("soumis", "gagne", "perdu")])
    nb_gagnes = len([a for a in appels if a.get("statut") == "gagne"])
    nb_perdus = len([a for a in appels if a.get("statut") == "perdu"])
    nb_ignores = len([a for a in appels if a.get("statut") == "ignore"])
    nb_dossiers = len(dossiers)

    # Taux de conversion
    taux_conversion = (nb_gagnes / nb_soumis * 100) if nb_soumis > 0 else 0

    # Estimation temps economise (en heures)
    temps_veille = nb_total * 0.5
    temps_analyse = nb_analyses * 0.5
    temps_dossiers = nb_dossiers * 8
    temps_total_h = temps_veille + temps_analyse + temps_dossiers

    # Cout API estime
    cout_scoring = nb_total * 0.003
    cout_par_dossier = 0.15
    cout_generation = nb_dossiers * cout_par_dossier
    cout_api_total = cout_scoring + cout_generation

    # Valeur des contrats
    valeur_gagnes = sum(a.get("budget_estime") or 0 for a in appels if a.get("statut") == "gagne")
    valeur_soumis = sum(a.get("budget_estime") or 0 for a in appels if a.get("statut") == "soumis")
    valeur_pipeline = sum(a.get("budget_estime") or 0 for a in appels
                         if a.get("statut") in ("analyse", "candidature"))

    # CA en cours = budget soumis x taux de conversion historique
    ca_en_cours = valeur_soumis * (taux_conversion / 100) if taux_conversion > 0 else 0

    # Temps moyen par dossier (basé sur timestamps de generation)
    temps_moyen_dossier = 0
    if dossiers:
        durees = []
        for d in dossiers:
            dc = d.get("date_creation", "")
            if dc:
                durees.append(1)  # chaque dossier ~ quelques minutes en auto
        temps_moyen_dossier = round(temps_dossiers * 60 / len(dossiers), 1) if dossiers else 0  # min economisees/dossier

    # ROI
    tarif_horaire = 62.5  # 500 EUR/jour / 8h
    cout_temps_valorise = temps_total_h * tarif_horaire
    cout_total = cout_api_total + (nb_dossiers * 5)
    roi_ratio = (valeur_gagnes / cout_total) if cout_total > 0 else 0
    roi_global = (valeur_gagnes / (cout_api_total + cout_temps_valorise)) if (cout_api_total + cout_temps_valorise) > 0 else 0

    tarif_consultant = 500
    jours_economises = temps_total_h / 8
    economie_consultant = jours_economises * tarif_consultant

    # Stats par source avec taux conversion
    sources_stats = {}
    for ao in appels:
        src = ao.get("source", "Inconnu")
        if src not in sources_stats:
            sources_stats[src] = {"total": 0, "soumis": 0, "gagnes": 0, "perdus": 0, "valeur": 0, "taux": 0}
        sources_stats[src]["total"] += 1
        if ao.get("statut") in ("soumis", "gagne", "perdu"):
            sources_stats[src]["soumis"] += 1
        if ao.get("statut") == "gagne":
            sources_stats[src]["gagnes"] += 1
            sources_stats[src]["valeur"] += ao.get("budget_estime") or 0
        if ao.get("statut") == "perdu":
            sources_stats[src]["perdus"] += 1
    for src, s in sources_stats.items():
        s["taux"] = round(s["gagnes"] / s["soumis"] * 100, 1) if s["soumis"] > 0 else 0

    # Evolution mensuelle (AO detectes et gagnes par mois)
    evolution_mensuelle = {}
    for ao in appels:
        dp = ao.get("date_publication", "")
        if dp and len(dp) >= 7:
            mois = dp[:7]  # YYYY-MM
            if mois not in evolution_mensuelle:
                evolution_mensuelle[mois] = {"detectes": 0, "gagnes": 0}
            evolution_mensuelle[mois]["detectes"] += 1
            if ao.get("statut") == "gagne":
                evolution_mensuelle[mois]["gagnes"] += 1
    # Trier par mois
    evolution_mensuelle = dict(sorted(evolution_mensuelle.items()))

    # Top acheteurs
    acheteurs_stats = {}
    for ao in appels:
        acheteur = ao.get("acheteur", "Inconnu")
        if not acheteur:
            acheteur = "Inconnu"
        if acheteur not in acheteurs_stats:
            acheteurs_stats[acheteur] = {"total": 0, "soumis": 0, "gagnes": 0, "valeur": 0}
        acheteurs_stats[acheteur]["total"] += 1
        if ao.get("statut") in ("soumis", "gagne", "perdu"):
            acheteurs_stats[acheteur]["soumis"] += 1
        if ao.get("statut") == "gagne":
            acheteurs_stats[acheteur]["gagnes"] += 1
            acheteurs_stats[acheteur]["valeur"] += ao.get("budget_estime") or 0
    # Top 10 par nombre d'interactions
    top_acheteurs = sorted(acheteurs_stats.items(), key=lambda x: x[1]["total"], reverse=True)[:10]

    # Score moyen par statut
    scores_par_statut = {}
    for ao in appels:
        statut = ao.get("statut", "nouveau")
        score = ao.get("score_pertinence")
        if score is not None:
            if statut not in scores_par_statut:
                scores_par_statut[statut] = {"somme": 0, "count": 0}
            scores_par_statut[statut]["somme"] += score
            scores_par_statut[statut]["count"] += 1
    scores_moyens = {}
    for statut, data in scores_par_statut.items():
        scores_moyens[statut] = round(data["somme"] / data["count"] * 100, 1) if data["count"] > 0 else 0

    # Funnel data (pour les barres CSS)
    funnel_max = max(nb_total, 1)
    funnel = [
        {"label": "Detectes", "count": nb_total, "pct": 100},
        {"label": "Analyses", "count": nb_analyses, "pct": round(nb_analyses / funnel_max * 100, 1)},
        {"label": "Candidatures", "count": nb_candidatures, "pct": round(nb_candidatures / funnel_max * 100, 1)},
        {"label": "Soumis", "count": nb_soumis, "pct": round(nb_soumis / funnel_max * 100, 1)},
        {"label": "Gagnes", "count": nb_gagnes, "pct": round(nb_gagnes / funnel_max * 100, 1)},
    ]

    return {
        "nb_total": nb_total, "nb_analyses": nb_analyses, "nb_candidatures": nb_candidatures,
        "nb_soumis": nb_soumis, "nb_gagnes": nb_gagnes, "nb_perdus": nb_perdus,
        "nb_ignores": nb_ignores, "nb_dossiers": nb_dossiers,
        "taux_conversion": round(taux_conversion, 1),
        "temps_veille": temps_veille, "temps_analyse": temps_analyse,
        "temps_dossiers": temps_dossiers, "temps_total_h": temps_total_h,
        "temps_moyen_dossier": temps_moyen_dossier,
        "cout_scoring": round(cout_scoring, 2), "cout_generation": round(cout_generation, 2),
        "cout_api_total": round(cout_api_total, 2), "cout_par_dossier": cout_par_dossier,
        "valeur_gagnes": valeur_gagnes, "valeur_soumis": valeur_soumis,
        "valeur_pipeline": valeur_pipeline, "ca_en_cours": ca_en_cours,
        "roi_ratio": round(roi_ratio, 1), "roi_global": round(roi_global, 1),
        "economie_consultant": economie_consultant,
        "jours_economises": jours_economises,
        "sources_stats": sources_stats,
        "evolution_mensuelle": evolution_mensuelle,
        "top_acheteurs": top_acheteurs,
        "scores_moyens": scores_moyens,
        "funnel": funnel,
    }


@app.route("/roi")
def roi():
    """Tableau de bord ROI ameliore - stats avancees, funnel, evolution."""
    stats = _calculer_roi_stats()
    return render_template("roi.html", **stats)


@app.route("/brouillons")
def page_brouillons():
    """Page listant les brouillons Gmail (pipeline auto)."""
    filtre_ao = request.args.get("ao_hunter", "1") == "1"
    try:
        from brouillons_gmail import lister_brouillons
        brouillons = lister_brouillons(max_results=50, filtre_ao_hunter=filtre_ao)
    except Exception as e:
        logger.error(f"Erreur lecture brouillons: {e}")
        brouillons = []
    return render_template("brouillons.html", brouillons=brouillons, filtre_ao=filtre_ao)


@app.route("/api/brouillons")
def api_brouillons():
    """GET /api/brouillons - Liste des brouillons Gmail en JSON."""
    filtre_ao = request.args.get("ao_hunter", "0") == "1"
    try:
        from brouillons_gmail import lister_brouillons
        return jsonify(lister_brouillons(max_results=50, filtre_ao_hunter=filtre_ao))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/kanban")
def kanban():
    appels = charger_ao()
    colonnes = {}
    for s in STATUTS_KANBAN:
        colonnes[s] = sorted(
            [a for a in appels if a.get("statut", "nouveau") == s],
            key=lambda a: a.get("score_pertinence") or 0, reverse=True
        )[:50]  # max 50 par colonne
    # Stats conversion
    total_soumis = len([a for a in appels if a.get("statut") in ("soumis", "gagne", "perdu")])
    gagnes = len([a for a in appels if a.get("statut") == "gagne"])
    taux_conversion = (gagnes / total_soumis * 100) if total_soumis > 0 else 0
    sources = sorted(set(a.get("source", "") for a in appels if a.get("source")))

    # Pre-calculer estimations pour le kanban
    estimations_kanban = {}
    try:
        from estimation_marche import estimer_marche
        for ao in appels:
            try:
                estimations_kanban[ao.get("id")] = estimer_marche(ao)
            except Exception:
                pass
    except ImportError:
        pass

    return render_template("kanban.html", colonnes=colonnes, statuts=STATUTS_KANBAN,
                           taux_conversion=taux_conversion, total_soumis=total_soumis,
                           gagnes=gagnes, sources=sources, estimations=estimations_kanban)


@app.route("/ao/<path:ao_id>/statut-ajax", methods=["POST"])
def changer_statut_ajax(ao_id):
    """Change le statut via AJAX (pour le drag & drop Kanban)."""
    appels = charger_ao()
    data = request.get_json()
    nouveau_statut = data.get("statut", "nouveau")
    for ao in appels:
        if ao.get("id") == ao_id:
            ao["statut"] = nouveau_statut
            sauvegarder_ao(appels)
            # Memoire adaptative : indexer le memoire si AO gagne
            if nouveau_statut == "gagne":
                try:
                    from memoire_adaptative import sauvegarder_memoire_gagnant
                    sauvegarder_memoire_gagnant(ao)
                except Exception as e:
                    logger.warning(f"Memoire adaptative: erreur sauvegarde pour {ao_id}: {e}")
            # Recalibrer le scoring predictif si gagne ou perdu
            if nouveau_statut in ("gagne", "perdu"):
                try:
                    from scoring_predictif import calibrer_auto
                    calibrer_auto()
                except Exception as e:
                    logger.warning(f"Scoring predictif: erreur calibration pour {ao_id}: {e}")
            return jsonify({"status": "ok", "id": ao_id, "statut": nouveau_statut})
    return jsonify({"error": "AO non trouve"}), 404


@app.route("/ao/<path:ao_id>")
def detail_ao(ao_id):
    appels = charger_ao()
    ao = next((a for a in appels if a.get("id") == ao_id), None)
    if not ao:
        return redirect(url_for("liste_ao"))

    # Dossier genere - chercher dans resultats/ et dossiers_generes/
    dossier_genere = None
    clean_id = ao_id.replace("BOAMP-", "").replace("PLACE-", "").replace("TED-", "").replace("MSEC-", "").replace("AWS-", "")
    for base in [RESULTATS_DIR, DOSSIERS_GENERES_DIR]:
        if not base.exists():
            continue
        for d in base.iterdir():
            if d.is_dir() and clean_id in d.name:
                dossier_genere = {
                    "nom": d.name,
                    "fichiers": sorted([f.name for f in d.glob("*") if f.is_file()]),
                }
                break
        if dossier_genere:
            break

    # Notes
    notes = charger_notes()
    note = notes.get(ao_id, "")

    # Detection lots si pas encore fait
    if "lots_detectes" not in ao:
        from veille_render import detecter_lots_pertinents
        lots = detecter_lots_pertinents(ao)
        if lots:
            ao["lots_detectes"] = lots
            ao["lots_pertinents"] = sum(1 for l in lots if l.get("pertinent"))

    prestations = detecter_prestations(ao)

    # Go/No-Go rapide
    from analyse_dce import go_no_go
    go_nogo = go_no_go(ao)

    # Modele de reponse recommande
    from modeles_reponse import detecter_type_prestation, get_modele
    type_presta = detecter_type_prestation(ao)
    modele = get_modele(type_presta)

    # Checklist de soumission
    checklist = _generer_checklist(ao, dossier_genere, modele)
    checklist_etat = _charger_checklist_etat(ao_id)

    # Commentaires
    commentaires = _charger_commentaires(ao_id)

    # Estimation budget / concurrence / accessibilite
    try:
        from estimation_marche import estimer_marche
        estimation = estimer_marche(ao)
    except Exception:
        estimation = None

    # Score acheteur
    try:
        from score_acheteur import scorer_acheteur
        score_acheteur_data = scorer_acheteur(ao)
    except Exception:
        score_acheteur_data = None

    # AO similaires (si gagne)
    ao_similaires = []
    if ao.get("statut") == "gagne":
        try:
            from alertes_similaires import trouver_similaires
            ao_similaires = trouver_similaires(ao, appels, n=5)
        except Exception:
            ao_similaires = []

    # Post-mortem (si perdu)
    post_mortem_data = None
    if ao.get("statut") == "perdu":
        try:
            from post_mortem import analyser_defaite
            post_mortem_data = analyser_defaite(ao)
        except Exception as e:
            logger.warning(f"Erreur post-mortem pour {ao_id}: {e}")

    # Signaux faibles (marche fleche)
    signaux_faibles_data = None
    try:
        from signaux_faibles import detecter_signaux
        signaux_faibles_data = detecter_signaux(ao)
    except Exception as e:
        logger.warning(f"Erreur signaux faibles pour {ao_id}: {e}")

    # Groupement / co-traitance
    groupement_eval = None
    try:
        from groupement import evaluer_besoin_groupement
        groupement_eval = evaluer_besoin_groupement(ao)
    except Exception as e:
        logger.warning(f"Erreur groupement pour {ao_id}: {e}")

    # Analyse semantique DCE (si deja lancee)
    analyse_dce_complete = None
    try:
        clean_id_dce = ao_id.replace("BOAMP-", "").replace("PLACE-", "").replace("TED-", "").replace("MSEC-", "").replace("AWS-", "").replace("/", "_")
        fichier_analyse = DOSSIERS_GENERES_DIR / f"DCE_{clean_id_dce}" / "analyse_dce_complete.json"
        if not fichier_analyse.exists():
            fichier_analyse = DOSSIERS_GENERES_DIR / f"DCE_{ao_id.replace('/', '_')}" / "analyse_dce_complete.json"
        if fichier_analyse.exists():
            with open(fichier_analyse, "r", encoding="utf-8") as f:
                analyse_dce_complete = json.load(f)
    except Exception as e:
        logger.warning(f"Erreur chargement analyse DCE pour {ao_id}: {e}")

    # Prediction de victoire (scoring predictif)
    prediction_data = None
    try:
        from scoring_predictif import predire_victoire
        prediction_data = predire_victoire(ao)
    except Exception as e:
        logger.warning(f"Erreur scoring predictif pour {ao_id}: {e}")

    return render_template("ao_detail.html", ao=ao, dossier=dossier_genere, note=note,
                           prestations=prestations, go_nogo=go_nogo,
                           type_presta=type_presta, modele=modele,
                           checklist=checklist, checklist_etat=checklist_etat,
                           commentaires=commentaires, estimation=estimation,
                           score_acheteur=score_acheteur_data,
                           ao_similaires=ao_similaires,
                           post_mortem=post_mortem_data,
                           signaux_faibles=signaux_faibles_data,
                           groupement=groupement_eval,
                           analyse_dce_complete=analyse_dce_complete,
                           prediction=prediction_data)


@app.route("/ao/<path:ao_id>/statut", methods=["POST"])
def changer_statut(ao_id):
    appels = charger_ao()
    nouveau_statut = request.form.get("statut", "nouveau")
    ao_trouve = None
    for ao in appels:
        if ao.get("id") == ao_id:
            ao["statut"] = nouveau_statut
            ao_trouve = ao
            break
    sauvegarder_ao(appels)
    # Memoire adaptative : indexer le memoire si AO gagne
    if nouveau_statut == "gagne" and ao_trouve:
        try:
            from memoire_adaptative import sauvegarder_memoire_gagnant
            sauvegarder_memoire_gagnant(ao_trouve)
        except Exception as e:
            logger.warning(f"Memoire adaptative: erreur sauvegarde pour {ao_id}: {e}")
    # Recalibrer le scoring predictif si gagne ou perdu
    if nouveau_statut in ("gagne", "perdu"):
        try:
            from scoring_predictif import calibrer_auto
            calibrer_auto()
        except Exception as e:
            logger.warning(f"Scoring predictif: erreur calibration pour {ao_id}: {e}")
    return redirect(url_for("detail_ao", ao_id=ao_id))


@app.route("/ao/<path:ao_id>/note", methods=["POST"])
def sauvegarder_note(ao_id):
    notes = charger_notes()
    note_text = request.form.get("note", "").strip()
    if note_text:
        notes[ao_id] = note_text
    elif ao_id in notes:
        del notes[ao_id]
    sauvegarder_notes(notes)
    return redirect(url_for("detail_ao", ao_id=ao_id))


@app.route("/ao/<path:ao_id>/checklist", methods=["POST"])
def maj_checklist(ao_id):
    """Met a jour l'etat d'un item de la checklist via AJAX."""
    data = request.get_json()
    if not data or "item_id" not in data:
        return jsonify({"error": "item_id requis"}), 400
    etat = _charger_checklist_etat(ao_id)
    etat[data["item_id"]] = data.get("checked", False)
    _sauvegarder_checklist_etat(ao_id, etat)
    return jsonify({"status": "ok"})


@app.route("/ao/<path:ao_id>/commentaire", methods=["POST"])
def ajouter_commentaire(ao_id):
    """Ajoute un commentaire sur un AO."""
    auteur = request.form.get("auteur", "").strip()
    texte = request.form.get("texte", "").strip()
    if not texte:
        return redirect(url_for("detail_ao", ao_id=ao_id))
    commentaire = {
        "auteur": auteur or "Anonyme",
        "texte": texte,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _sauvegarder_commentaire(ao_id, commentaire)
    return redirect(url_for("detail_ao", ao_id=ao_id))


@app.route("/ao/<path:ao_id>/telecharger-dce", methods=["POST"])
def telecharger_dce_route(ao_id):
    """POST - Tente de telecharger le DCE automatiquement."""
    appels = charger_ao()
    ao_dict = next((a for a in appels if a.get("id") == ao_id), None)
    if not ao_dict:
        return jsonify({"error": "AO non trouve"}), 404

    def _dl_bg():
        try:
            from dce_auto import telecharger_dce_auto
            socketio.emit("veille_log", {"msg": f"Telechargement DCE: {ao_dict.get('titre', '')[:50]}..."})
            result = telecharger_dce_auto(ao_dict)
            if result["success"]:
                socketio.emit("veille_log", {"msg": f"DCE: {result['nb_fichiers']} fichier(s) telecharge(s)"})
            else:
                socketio.emit("veille_log", {"msg": f"DCE: {result['erreur']}"})
            socketio.emit("dce_complete", result)
        except Exception as e:
            socketio.emit("veille_log", {"msg": f"Erreur DCE: {e}"})
            socketio.emit("dce_complete", {"success": False, "erreur": str(e)})

    thread = threading.Thread(target=_dl_bg, daemon=True)
    thread.start()
    return jsonify({"status": "started"})


@app.route("/ao/<path:ao_id>/generer", methods=["POST"])
def generer_dossier(ao_id):
    """Lance la generation du dossier en arriere-plan via WebSocket."""
    appels = charger_ao()
    ao_dict = next((a for a in appels if a.get("id") == ao_id), None)
    if not ao_dict:
        return jsonify({"error": "AO non trouve"}), 404

    def _generer_en_bg():
        try:
            # Essayer le generateur complet (local)
            try:
                from veille import AppelOffre
                from generateur import GenerateurMemoire
                from dce_downloader import telecharger_dce

                config = charger_config()
                ao = AppelOffre(**{k: v for k, v in ao_dict.items()
                                  if k in AppelOffre.__dataclass_fields__})

                socketio.emit("veille_log", {"msg": f"Generation dossier: {ao.titre[:60]}..."})

                dossier_ao = RESULTATS_DIR / f"AO_{ao.id}"
                dce_path = None
                try:
                    fichiers_dce = telecharger_dce(ao, dossier_ao, config=config)
                    if fichiers_dce:
                        dce_path = dossier_ao / "DCE"
                        socketio.emit("veille_log", {"msg": f"DCE telecharge: {len(fichiers_dce)} fichier(s)"})
                except Exception as e:
                    socketio.emit("veille_log", {"msg": f"DCE non disponible: {e}"})

                generateur = GenerateurMemoire(config)
                dossier = generateur.generer_dossier_complet(ao, dce_path=dce_path)
                socketio.emit("veille_log", {"msg": f"Dossier genere: {dossier.name}"})
                socketio.emit("generation_complete", {"ao_id": ao_id, "dossier": str(dossier)})
                return

            except ImportError:
                pass  # Pas en local, utiliser le generateur Render

            # Generateur Render (leger, memoire technique via API Claude)
            from generateur_render import generer_memoire_technique
            from modeles_reponse import detecter_type_prestation

            socketio.emit("veille_log", {"msg": f"Generation (Render): {ao_dict.get('titre', '')[:60]}..."})
            type_presta = detecter_type_prestation(ao_dict)
            socketio.emit("veille_log", {"msg": f"Type detecte: {type_presta}"})

            result = generer_memoire_technique(ao_dict, type_presta)
            if result["success"]:
                socketio.emit("veille_log", {"msg": f"Memoire genere: {result['nb_mots']} mots"})
                socketio.emit("generation_complete", {"ao_id": ao_id, "dossier": result["dossier_nom"]})
            else:
                socketio.emit("veille_log", {"msg": f"Erreur: {result['erreur']}"})
                socketio.emit("generation_complete", {"ao_id": ao_id, "error": result["erreur"]})

        except Exception as e:
            socketio.emit("veille_log", {"msg": f"Erreur generation: {e}"})
            socketio.emit("generation_complete", {"ao_id": ao_id, "error": str(e)})

    thread = threading.Thread(target=_generer_en_bg, daemon=True)
    thread.start()
    return jsonify({"status": "started", "ao_id": ao_id})


REVIEWS_FILE = DASHBOARD_DIR / "reviews.json"


def charger_reviews() -> dict:
    if not REVIEWS_FILE.exists():
        return {}
    with open(REVIEWS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def sauvegarder_reviews(reviews: dict):
    REVIEWS_FILE.write_text(json.dumps(reviews, ensure_ascii=False, indent=2), encoding="utf-8")


@app.route("/dossiers")
def liste_dossiers():
    dossiers = lister_dossiers()
    reviews = charger_reviews()
    # Enrichir avec le statut de review
    for d in dossiers:
        d["review"] = reviews.get(d["nom"], {})
    return render_template("dossiers.html", dossiers=dossiers)


@app.route("/dossiers/<path:nom>")
def detail_dossier(nom):
    """Affiche les fichiers d'un dossier avec preview."""
    dossiers = lister_dossiers()
    dossier = next((d for d in dossiers if d["nom"] == nom), None)
    if not dossier:
        return redirect(url_for("liste_dossiers"))
    reviews = charger_reviews()
    review = reviews.get(nom, {"statut": "en_attente", "commentaires": []})
    return render_template("dossier_detail.html", dossier=dossier, review=review)


@app.route("/dossiers/<path:nom>/review", methods=["POST"])
def review_dossier(nom):
    """Ajoute un commentaire de relecture."""
    reviews = charger_reviews()
    if nom not in reviews:
        reviews[nom] = {"statut": "en_attente", "commentaires": []}
    data = request.form
    commentaire = data.get("commentaire", "").strip()
    auteur = data.get("auteur", "Anonyme").strip()
    statut = data.get("statut", "")
    if commentaire:
        reviews[nom]["commentaires"].append({
            "auteur": auteur,
            "texte": commentaire,
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
    if statut:
        reviews[nom]["statut"] = statut
    sauvegarder_reviews(reviews)
    return redirect(url_for("detail_dossier", nom=nom))


@app.route("/dossiers/<path:nom>/fichier/<path:fichier>")
def servir_fichier(nom, fichier):
    """Sert un fichier du dossier pour le preview."""
    from flask import send_file
    # Chercher dans les deux emplacements possibles
    for base in [RESULTATS_DIR, DOSSIERS_GENERES_DIR]:
        dossier_path = base / nom
        if not dossier_path.exists():
            continue
        fichier_path = dossier_path / fichier
        if not fichier_path.exists() or not fichier_path.is_file():
            continue
        # Securite : verifier que le chemin reste dans le base dir
        try:
            fichier_path.resolve().relative_to(base.resolve())
        except ValueError:
            return "Acces refuse", 403
        return send_file(str(fichier_path), as_attachment=False)
    return "Fichier non trouve", 404


@app.route("/dossiers/<path:nom>/zip")
def export_dossier_zip(nom):
    """Telecharge tout le dossier en ZIP."""
    import zipfile
    from flask import send_file

    # Trouver le dossier
    dossier_path = None
    for base in [RESULTATS_DIR, DOSSIERS_GENERES_DIR]:
        candidate = base / nom
        if candidate.exists() and candidate.is_dir():
            dossier_path = candidate
            break

    if not dossier_path:
        return "Dossier non trouve", 404

    # Creer le ZIP en memoire
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for fichier in dossier_path.rglob("*"):
            if fichier.is_file():
                arcname = fichier.relative_to(dossier_path)
                zf.write(fichier, arcname)

    zip_buffer.seek(0)
    return send_file(
        zip_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{nom}.zip",
    )


@app.route("/export/csv")
def export_csv():
    """Exporte les AO filtres en CSV."""
    appels = charger_ao()
    source = request.args.get("source", "")
    score_min_str = request.args.get("score_min", "0")
    statut = request.args.get("statut", "")
    recherche = request.args.get("q", "")
    tri = request.args.get("tri", "score")

    try:
        score_min = float(score_min_str)
    except ValueError:
        score_min = 0

    filtre = filtrer_ao(appels, source, score_min, statut, recherche, tri)

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["ID", "Source", "Titre", "Acheteur", "Score", "Date limite",
                     "Region", "Budget", "Statut", "URL", "Description"])
    for ao in filtre:
        writer.writerow([
            ao.get("id", ""),
            ao.get("source", ""),
            ao.get("titre", ""),
            ao.get("acheteur", ""),
            f"{(ao.get('score_pertinence') or 0) * 100:.0f}%",
            ao.get("date_limite", ""),
            ao.get("region", ""),
            ao.get("budget_estime", ""),
            ao.get("statut", ""),
            ao.get("url", ""),
            (ao.get("description") or "")[:200],
        ])

    resp = make_response(output.getvalue())
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = f"attachment; filename=ao_hunter_export_{datetime.now().strftime('%Y%m%d')}.csv"
    return resp


# --- API REST ---

@app.route("/api/ao")
def api_ao_list():
    """GET /api/ao - Liste tous les AO avec filtres optionnels."""
    appels = charger_ao()
    source = request.args.get("source", "")
    score_min = float(request.args.get("score_min", "0"))
    statut = request.args.get("statut", "")
    q = request.args.get("q", "")
    limit = int(request.args.get("limit", "50"))
    offset = int(request.args.get("offset", "0"))

    filtre = filtrer_ao(appels, source, score_min, statut, q)
    return jsonify({
        "total": len(filtre),
        "offset": offset,
        "limit": limit,
        "data": filtre[offset:offset + limit],
    })


@app.route("/api/ao/<path:ao_id>")
def api_ao_detail(ao_id):
    """GET /api/ao/<id> - Detail d'un AO."""
    appels = charger_ao()
    ao = next((a for a in appels if a.get("id") == ao_id), None)
    if not ao:
        return jsonify({"error": "AO non trouve"}), 404
    notes = charger_notes()
    ao["note"] = notes.get(ao_id, "")
    return jsonify(ao)


@app.route("/api/ao/<path:ao_id>", methods=["PATCH"])
def api_ao_update(ao_id):
    """PATCH /api/ao/<id> - Met a jour statut et/ou note."""
    appels = charger_ao()
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body requis"}), 400

    for ao in appels:
        if ao.get("id") == ao_id:
            if "statut" in data:
                ao["statut"] = data["statut"]
            sauvegarder_ao(appels)

            if "note" in data:
                notes = charger_notes()
                if data["note"]:
                    notes[ao_id] = data["note"]
                elif ao_id in notes:
                    del notes[ao_id]
                sauvegarder_notes(notes)

            return jsonify({"status": "ok", "id": ao_id})

    return jsonify({"error": "AO non trouve"}), 404


@app.route("/api/ao/<path:ao_id>/generer", methods=["POST"])
def api_generer(ao_id):
    """POST /api/ao/<id>/generer - Lance la generation du dossier."""
    return generer_dossier(ao_id)


@app.route("/api/ao/<path:ao_id>/analyser-dce", methods=["POST"])
def api_analyser_dce(ao_id):
    """POST /api/ao/<id>/analyser-dce - Analyse semantique complete du DCE."""
    appels = charger_ao()
    ao_dict = next((a for a in appels if a.get("id") == ao_id), None)
    if not ao_dict:
        return jsonify({"error": "AO non trouve"}), 404

    # Trouver le dossier DCE
    clean_id = ao_id.replace("BOAMP-", "").replace("PLACE-", "").replace("TED-", "").replace("MSEC-", "").replace("AWS-", "").replace("/", "_")
    dossier_dce = DOSSIERS_GENERES_DIR / f"DCE_{clean_id}"

    if not dossier_dce.exists():
        # Essayer aussi avec l'ID brut
        dossier_dce = DOSSIERS_GENERES_DIR / f"DCE_{ao_id.replace('/', '_')}"
    if not dossier_dce.exists():
        return jsonify({"error": "DCE non telecharge. Telechargez d'abord le DCE."}), 400

    def _analyser_bg():
        try:
            socketio.emit("veille_log", {"msg": "Analyse semantique du DCE en cours..."})
            from analyse_semantique_dce import analyser_dce_complet_ia
            resultat = analyser_dce_complet_ia(dossier_dce, ao_dict)
            if "erreur" in resultat:
                socketio.emit("veille_log", {"msg": f"Erreur analyse DCE: {resultat['erreur']}"})
                socketio.emit("analyse_dce_complete", {"success": False, "erreur": resultat["erreur"]})
            else:
                score = resultat.get("score_adequation", 0)
                socketio.emit("veille_log", {"msg": f"Analyse DCE terminee - Score: {score}/100"})
                socketio.emit("analyse_dce_complete", {"success": True, "score": score})
        except Exception as e:
            logger.error(f"Erreur analyse semantique DCE: {e}")
            socketio.emit("veille_log", {"msg": f"Erreur analyse DCE: {e}"})
            socketio.emit("analyse_dce_complete", {"success": False, "erreur": str(e)})

    thread = threading.Thread(target=_analyser_bg, daemon=True)
    thread.start()
    return jsonify({"status": "started"})


@app.route("/api/stats")
def api_stats():
    """GET /api/stats - Statistiques globales."""
    appels = charger_ao()
    return jsonify(stats_ao(appels))


@app.route("/api/dossiers")
def api_dossiers():
    """GET /api/dossiers - Liste des dossiers generes."""
    return jsonify(lister_dossiers())


@app.route("/api/urgents")
def api_urgents():
    """GET /api/urgents - AO avec deadline proche (pour notifications)."""
    appels = charger_ao()
    return jsonify(_ao_urgents(appels, max_days=3))


@app.route("/api/concurrence")
def api_concurrence():
    """GET /api/concurrence - Top concurrents et attributions recentes."""
    from veille_concurrence import charger_concurrence, analyser_concurrents
    attributions = charger_concurrence()
    concurrents = analyser_concurrents(attributions)
    return jsonify({
        "total_attributions": len(attributions),
        "top_concurrents": concurrents[:15],
        "dernieres_attributions": attributions[-10:][::-1],
    })


@app.route("/api/historique-veille")
def api_historique_veille():
    """GET /api/historique-veille - Historique des cycles de veille + tendances."""
    from veille_render import charger_historique, stats_tendances
    historique = charger_historique()
    tendances = stats_tendances(historique)
    return jsonify(tendances)


@app.route("/api/deadlines")
def api_deadlines():
    """GET /api/deadlines - AO avec deadlines a surveiller (J-7)."""
    from rappels import verifier_deadlines
    appels = charger_ao()
    alertes = verifier_deadlines(appels)
    return jsonify([{
        "id": a["ao"].get("id"),
        "titre": a["ao"].get("titre"),
        "acheteur": a["ao"].get("acheteur"),
        "statut": a["ao"].get("statut"),
        "date_limite": a["date_limite"],
        "jours_restants": a["jours_restants"],
        "urgence": a["urgence"],
    } for a in alertes])


@app.route("/api/pipeline")
def api_pipeline():
    """GET /api/pipeline - Statut et historique du pipeline automatique."""
    try:
        from pipeline_auto import charger_log
        log = charger_log()
        aujourd_hui = datetime.now().strftime("%Y-%m-%d")
        generes_today = sum(1 for e in log if e.get("action") == "GENERE" and e.get("timestamp", "").startswith(aujourd_hui))
        total_generes = sum(1 for e in log if e.get("action") == "GENERE")
        total_skip = sum(1 for e in log if e.get("action") == "SKIP")
        return jsonify({
            "generes_aujourd_hui": generes_today,
            "max_par_jour": 1,
            "total_generes": total_generes,
            "total_skip": total_skip,
            "derniers": log[-10:][::-1],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/estimation/<path:ao_id>")
def api_estimation(ao_id):
    """GET /api/estimation/<id> - Estimation budget/concurrence/accessibilite."""
    appels = charger_ao()
    ao = next((a for a in appels if a.get("id") == ao_id), None)
    if not ao:
        return jsonify({"error": "AO non trouve"}), 404
    try:
        from estimation_marche import estimer_marche
        return jsonify(estimer_marche(ao))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/veille", methods=["POST"])
def api_lancer_veille():
    """POST /api/veille - Lance une veille en arriere-plan.
    Sur Render: utilise veille_render (BOAMP+TED lightweight).
    En local: utilise le module complet veille+filtre."""
    def _veille_bg():
        try:
            socketio.emit("veille_log", {"msg": "Recherche en cours..."})

            # Sur Render ou si modules complets non dispo: veille legere
            try:
                config = charger_config()
                from veille import Veilleur
                from filtre import FiltreAO
                # Mode local complet
                veilleur = Veilleur(config)
                tous_ao = veilleur.lancer_recherche()
                socketio.emit("veille_log", {"msg": f"{len(tous_ao)} AO bruts trouves"})

                filtre = FiltreAO(config)
                pertinents = filtre.filtrer(tous_ao)
                socketio.emit("veille_log", {"msg": f"{len(pertinents)} AO pertinents"})

                existants = charger_ao()
                ids_existants = {ao["id"] for ao in existants}
                nouveaux = 0
                for ao in pertinents:
                    d = ao.to_dict()
                    if d["id"] not in ids_existants:
                        existants.append(d)
                        nouveaux += 1
                sauvegarder_ao(existants)
            except ImportError:
                # Mode Render: veille legere
                from veille_render import lancer_veille
                result = lancer_veille()
                nouveaux = result["nouveaux"]
                existants = charger_ao()

            socketio.emit("veille_log", {"msg": f"Termine: {nouveaux} nouveaux AO ajoutes"})
            socketio.emit("veille_complete", {"nouveaux": nouveaux, "total": len(existants)})
        except Exception as e:
            socketio.emit("veille_log", {"msg": f"Erreur: {e}"})
            socketio.emit("veille_complete", {"error": str(e)})

    thread = threading.Thread(target=_veille_bg, daemon=True)
    thread.start()
    return jsonify({"status": "started"})


@app.route("/api/roi")
def api_roi():
    """GET /api/roi - Stats ROI completes en JSON."""
    stats = _calculer_roi_stats()
    # Convertir top_acheteurs (list of tuples) en list of dicts pour JSON
    stats["top_acheteurs"] = [{"acheteur": nom, **data} for nom, data in stats["top_acheteurs"]]
    return jsonify(stats)


@app.route("/api/veille/status")
def api_veille_status():
    """GET /api/veille/status - Statut du scheduler et derniere veille."""
    status = {"scheduler_actif": _scheduler is not None and _scheduler.running}
    if _scheduler and _scheduler.running:
        job = _scheduler.get_job("veille_auto")
        if job:
            status["prochaine_execution"] = str(job.next_run_time)
    return jsonify(status)


@app.route("/api/attributions")
def api_attributions():
    """GET /api/attributions - Historique des resultats d'attribution."""
    from veille_attributions import charger_attributions
    attributions = charger_attributions()
    return jsonify({
        "total": len(attributions),
        "attributions": attributions,
        "gagnes": [a for a in attributions if a.get("notre_statut") == "gagne"],
        "perdus": [a for a in attributions if a.get("notre_statut") == "perdu"],
    })


@app.route("/api/acheteurs-cles")
def api_acheteurs_cles():
    """GET /api/acheteurs-cles - Top acheteurs les plus compatibles avec Almera."""
    from score_acheteur import top_acheteurs
    n = int(request.args.get("n", "15"))
    return jsonify(top_acheteurs(n=n))


# --- API Prix ---

@app.route("/api/prix/stats")
def api_prix_stats():
    """GET /api/prix/stats - Statistiques de prix par type de prestation."""
    from modeles_prix import stats_prix
    type_prestation = request.args.get("type")
    return jsonify(stats_prix(type_prestation=type_prestation))


@app.route("/api/prix/enregistrer", methods=["POST"])
def api_prix_enregistrer():
    """POST /api/prix/enregistrer - Enregistrer un prix soumis."""
    from modeles_prix import enregistrer_prix
    data = request.get_json(force=True)
    ao_id = data.get("ao_id")
    type_prestation = data.get("type_prestation", "formation_intra")
    montant = data.get("montant")

    if not ao_id or not montant:
        return jsonify({"error": "ao_id et montant requis"}), 400

    try:
        montant = float(montant)
    except (ValueError, TypeError):
        return jsonify({"error": "montant doit etre un nombre"}), 400

    entree = enregistrer_prix(
        ao_id=ao_id,
        type_prestation=type_prestation,
        montant=montant,
        nb_jours=data.get("nb_jours"),
        nb_personnes=data.get("nb_personnes"),
        resultat=data.get("resultat"),
    )
    return jsonify({"status": "ok", "entree": entree})


# --- API Post-mortem ---

@app.route("/api/post-mortem/stats")
def api_post_mortem_stats():
    """GET /api/post-mortem/stats - Statistiques agregees des AO perdus."""
    from post_mortem import stats_post_mortem
    return jsonify(stats_post_mortem())


# --- API Benchmark Prix ---

@app.route("/api/benchmark")
def api_benchmark():
    """GET /api/benchmark - Stats benchmark prix du marche."""
    from benchmark_prix import analyser_benchmark
    type_prestation = request.args.get("type", "formation")
    return jsonify(analyser_benchmark(type_prestation=type_prestation))


@app.route("/api/benchmark/position")
def api_benchmark_position():
    """GET /api/benchmark/position?prix=15000&type=formation - Positionnement prix."""
    from benchmark_prix import positionner_prix
    prix = request.args.get("prix")
    type_prestation = request.args.get("type", "formation")
    if not prix:
        return jsonify({"error": "parametre 'prix' requis"}), 400
    try:
        prix = float(prix)
    except (ValueError, TypeError):
        return jsonify({"error": "prix doit etre un nombre"}), 400
    return jsonify(positionner_prix(notre_prix=prix, type_prestation=type_prestation))


# --- API Soumission ---

@app.route("/api/ao/<path:ao_id>/preparer-soumission", methods=["POST"])
def api_preparer_soumission(ao_id):
    """POST /api/ao/<id>/preparer-soumission - Prepare le dossier pour soumission."""
    appels = charger_ao()
    ao = next((a for a in appels if a.get("id") == ao_id), None)
    if not ao:
        return jsonify({"error": "AO non trouve"}), 404

    try:
        from soumission_helper import preparer_soumission
        result = preparer_soumission(ao)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Erreur preparation soumission: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/ao/<path:ao_id>/guide-soumission")
def api_guide_soumission(ao_id):
    """GET /api/ao/<id>/guide-soumission - Guide de soumission pour la plateforme."""
    appels = charger_ao()
    ao = next((a for a in appels if a.get("id") == ao_id), None)
    if not ao:
        return jsonify({"error": "AO non trouve"}), 404

    try:
        from soumission_helper import identifier_plateforme, generer_guide_soumission
        plateforme = identifier_plateforme(ao)
        guide = generer_guide_soumission(plateforme["id"])
        return jsonify({
            "plateforme": plateforme["nom"],
            "plateforme_id": plateforme["id"],
            "url_depot": plateforme["url_depot"],
            "etapes": guide,
        })
    except Exception as e:
        logger.error(f"Erreur guide soumission: {e}")
        return jsonify({"error": str(e)}), 500


# --- API Questions acheteur ---

@app.route("/api/ao/<path:ao_id>/questions", methods=["POST"])
def api_generer_questions(ao_id):
    """POST /api/ao/<id>/questions - Genere des questions a poser a l'acheteur."""
    appels = charger_ao()
    ao = next((a for a in appels if a.get("id") == ao_id), None)
    if not ao:
        return jsonify({"error": "AO non trouve"}), 404

    try:
        from questions_acheteur import generer_questions, formater_questions_email
        result = generer_questions(ao)

        # Ajouter l'email formate si des questions ont ete generees
        if result.get("questions"):
            result["email_formate"] = formater_questions_email(result["questions"], ao)

        return jsonify(result)
    except Exception as e:
        logger.error(f"Erreur generation questions: {e}")
        return jsonify({"error": str(e)}), 500


# --- Pre-informations ---

@app.route("/preinfos")
def preinfos_view():
    """Page listant les pre-informations detectees."""
    from veille_preinformation import preinfos_actives, recommander_actions
    preinfos = preinfos_actives()
    for p in preinfos:
        p["actions"] = recommander_actions(p)
    return render_template("preinfos.html", preinfos=preinfos)


@app.route("/api/preinfos")
def api_preinfos():
    """GET /api/preinfos - JSON des pre-infos actives."""
    from veille_preinformation import preinfos_actives, recommander_actions
    preinfos = preinfos_actives()
    for p in preinfos:
        p["actions"] = recommander_actions(p)
    return jsonify({"total": len(preinfos), "preinfos": preinfos})


# --- CRM Acheteurs ---

@app.route("/crm")
def crm_view():
    """Page CRM avec la liste des acheteurs."""
    from crm_acheteurs import lister_tous_acheteurs, acheteurs_a_relancer
    acheteurs = lister_tous_acheteurs()
    relancer = acheteurs_a_relancer()
    return render_template("crm.html", acheteurs=acheteurs, a_relancer=relancer)


@app.route("/crm/<path:nom>")
def crm_fiche_view(nom):
    """Fiche detaillee d'un acheteur."""
    from crm_acheteurs import get_fiche
    fiche = get_fiche(nom)
    if not fiche:
        return redirect(url_for("crm_view"))
    return render_template("crm_fiche.html", fiche=fiche, cle=nom)


@app.route("/crm/<path:nom>/note", methods=["POST"])
def crm_ajouter_note(nom):
    """Ajouter une note a un acheteur."""
    from crm_acheteurs import ajouter_note
    note = request.form.get("note", "").strip()
    if note:
        ajouter_note(nom, note)
    return redirect(url_for("crm_fiche_view", nom=nom))


@app.route("/crm/<path:nom>/contact", methods=["POST"])
def crm_ajouter_contact(nom):
    """Ajouter un contact a un acheteur."""
    from crm_acheteurs import ajouter_contact
    contact_info = {
        "nom": request.form.get("nom", "").strip(),
        "email": request.form.get("email", "").strip(),
        "telephone": request.form.get("telephone", "").strip(),
        "fonction": request.form.get("fonction", "").strip(),
    }
    if contact_info["nom"] or contact_info["email"]:
        ajouter_contact(nom, contact_info)
    return redirect(url_for("crm_fiche_view", nom=nom))


@app.route("/api/crm")
def api_crm():
    """GET /api/crm - JSON de tous les acheteurs."""
    from crm_acheteurs import lister_tous_acheteurs, acheteurs_a_relancer, top_acheteurs_actifs
    return jsonify({
        "acheteurs": lister_tous_acheteurs(),
        "a_relancer": acheteurs_a_relancer(),
        "top_actifs": top_acheteurs_actifs(20),
    })


# --- API Groupement ---

@app.route("/api/ao/<path:ao_id>/groupement", methods=["POST"])
def api_groupement(ao_id):
    """POST /api/ao/<id>/groupement - Genere les documents de groupement."""
    appels = charger_ao()
    ao = next((a for a in appels if a.get("id") == ao_id), None)
    if not ao:
        return jsonify({"error": "AO non trouve"}), 404

    try:
        from groupement import generer_documents_groupement, suggestions_partenaires
        data = request.get_json(force=True) if request.is_json else {}
        partenaire_info = data.get("partenaire") if data else None
        documents = generer_documents_groupement(ao, partenaire_info=partenaire_info)
        suggestions = suggestions_partenaires(ao)
        return jsonify({
            "status": "ok",
            "documents": documents,
            "suggestions_partenaires": suggestions,
        })
    except Exception as e:
        logger.error(f"Erreur generation groupement: {e}")
        return jsonify({"error": str(e)}), 500


# --- Objectifs CA ---

@app.route("/objectifs")
def objectifs_view():
    """Page objectifs CA avec progression et recommandations."""
    from objectifs_ca import progression as calc_progression, recommander_pipeline, alertes_objectif
    from datetime import date as _date

    prog = calc_progression()
    recommandation = recommander_pipeline()
    alertes = alertes_objectif()

    return render_template("objectifs.html",
                           prog=prog,
                           objectifs=prog["objectifs"],
                           recommandation=recommandation,
                           alertes=alertes,
                           stats_par_mois=prog["stats_par_mois"],
                           mois_actuel=_date.today().month)


@app.route("/api/objectifs")
def api_objectifs_get():
    """GET /api/objectifs - Donnees objectifs CA."""
    from objectifs_ca import progression as calc_progression, recommander_pipeline, alertes_objectif
    prog = calc_progression()
    recommandation = recommander_pipeline()
    alertes = alertes_objectif()
    return jsonify({
        "progression": prog,
        "recommandation": recommandation,
        "alertes": alertes,
    })


@app.route("/api/objectifs", methods=["POST"])
def api_objectifs_post():
    """POST /api/objectifs - Definir l'objectif annuel."""
    from objectifs_ca import definir_objectif
    data = request.get_json(force=True)
    annuel = data.get("objectif_annuel")
    if not annuel:
        return jsonify({"error": "objectif_annuel requis"}), 400
    try:
        annuel = float(annuel)
    except (ValueError, TypeError):
        return jsonify({"error": "objectif_annuel doit etre un nombre"}), 400
    marge = data.get("marge_cible_pct", 30)
    result = definir_objectif(annuel, marge_cible_pct=float(marge))
    return jsonify({"status": "ok", "objectifs": result})


@app.route("/api/memoires-gagnants")
def api_memoires_gagnants():
    """Liste des memoires techniques indexes (modeles gagnants)."""
    try:
        from memoire_adaptative import _charger_index
        index = _charger_index()
        return jsonify(index)
    except Exception as e:
        logger.error(f"Erreur chargement memoires gagnants: {e}")
        return jsonify([])


# --- Scoring predictif API ---

@app.route("/api/scoring/stats")
def api_scoring_stats():
    """GET /api/scoring/stats - Stats du modele de scoring predictif."""
    try:
        from scoring_predictif import stats_modele
        return jsonify(stats_modele())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/scoring/prediction/<path:ao_id>")
def api_scoring_prediction(ao_id):
    """GET /api/scoring/prediction/<ao_id> - Prediction pour un AO."""
    appels = charger_ao()
    ao = next((a for a in appels if a.get("id") == ao_id), None)
    if not ao:
        return jsonify({"error": "AO non trouve"}), 404
    try:
        from scoring_predictif import predire_victoire
        return jsonify(predire_victoire(ao))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# --- WebSocket ---

@socketio.on("connect")
def on_connect():
    logger.info("Client WebSocket connecte")


# --- Rapport hebdomadaire ---

@app.route("/api/rapport-hebdo", methods=["POST"])
def api_rapport_hebdo_forcer():
    """POST /api/rapport-hebdo - Forcer la generation manuelle du rapport hebdomadaire."""
    try:
        from rapport_hebdo import rapport_et_envoi
        result = rapport_et_envoi()
        return jsonify(result)
    except Exception as e:
        return jsonify({"erreur": str(e)}), 500


@app.route("/api/rapport-hebdo/dernier")
def api_rapport_hebdo_dernier():
    """GET /api/rapport-hebdo/dernier - Retourner le dernier rapport hebdomadaire."""
    try:
        from rapport_hebdo import _charger_rapports
        rapports = _charger_rapports()
        if not rapports:
            return jsonify({"erreur": "Aucun rapport disponible"}), 404
        return jsonify(rapports[-1])
    except Exception as e:
        return jsonify({"erreur": str(e)}), 500


# --- Sync endpoints ---

_app_start_time = datetime.now()


@app.route("/api/sync/status")
def api_sync_status():
    """GET /api/sync/status - Etat de synchronisation et sante du dashboard."""
    appels = charger_ao()
    uptime_seconds = (datetime.now() - _app_start_time).total_seconds()

    # Derniere synchro (depuis le fichier meta si present)
    derniere_synchro = None
    meta_file = DASHBOARD_DIR / ".sync_meta.json"
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            derniere_synchro = meta.get("dernier_pull")
        except Exception:
            pass

    return jsonify({
        "derniere_synchro": derniere_synchro,
        "nb_ao": len(appels),
        "version": "AO Hunter Dashboard v2",
        "uptime": round(uptime_seconds),
        "uptime_humain": f"{int(uptime_seconds // 3600)}h {int((uptime_seconds % 3600) // 60)}m",
        "environnement": "render" if os.environ.get("RENDER") else "local",
    })


@app.route("/api/sync/pull", methods=["POST"])
def api_sync_pull():
    """POST /api/sync/pull - Force un rechargement des donnees depuis les fichiers."""
    try:
        # Recharger les fichiers de donnees (utile apres un push externe)
        appels = charger_ao()
        concurrence_file = DASHBOARD_DIR / "concurrence.json"
        historique_file = DASHBOARD_DIR / "historique_veille.json"

        nb_concurrence = 0
        if concurrence_file.exists():
            try:
                data = json.loads(concurrence_file.read_text(encoding="utf-8"))
                nb_concurrence = len(data) if isinstance(data, list) else 0
            except Exception:
                pass

        nb_historique = 0
        if historique_file.exists():
            try:
                data = json.loads(historique_file.read_text(encoding="utf-8"))
                nb_historique = len(data) if isinstance(data, list) else 0
            except Exception:
                pass

        # Mettre a jour le timestamp de synchro
        meta_file = DASHBOARD_DIR / ".sync_meta.json"
        meta = {}
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        meta["dernier_pull"] = datetime.now().isoformat()
        meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        return jsonify({
            "status": "ok",
            "nb_ao": len(appels),
            "nb_concurrence": nb_concurrence,
            "nb_historique": nb_historique,
            "timestamp": meta["dernier_pull"],
        })
    except Exception as e:
        logger.error(f"Erreur sync pull: {e}")
        return jsonify({"status": "error", "erreur": str(e)}), 500


# Demarrer le scheduler sur Render (pas en local, la tache planifiee Windows s'en charge)
if os.environ.get("RENDER"):
    init_scheduler()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("RENDER") is None  # debug only in local
    print(f"AO Hunter Dashboard - http://localhost:{port}")
    socketio.run(app, debug=debug, use_reloader=False, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True)
