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


def lister_dossiers() -> list[dict]:
    # D'abord essayer le scan local (PC)
    dossiers = []
    if RESULTATS_DIR.exists():
        for d in sorted(RESULTATS_DIR.iterdir(), reverse=True):
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
    # Fallback: index JSON (Render)
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

    return render_template(
        "ao_liste.html",
        appels=page_appels,
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
    return render_template("kanban.html", colonnes=colonnes, statuts=STATUTS_KANBAN,
                           taux_conversion=taux_conversion, total_soumis=total_soumis,
                           gagnes=gagnes)


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
            return jsonify({"status": "ok", "id": ao_id, "statut": nouveau_statut})
    return jsonify({"error": "AO non trouve"}), 404


@app.route("/ao/<path:ao_id>")
def detail_ao(ao_id):
    appels = charger_ao()
    ao = next((a for a in appels if a.get("id") == ao_id), None)
    if not ao:
        return redirect(url_for("liste_ao"))

    # Dossier genere
    dossier_genere = None
    clean_id = ao_id.replace("BOAMP-", "").replace("PLACE-", "").replace("TED-", "").replace("MSEC-", "").replace("AWS-", "")
    if RESULTATS_DIR.exists():
        for d in RESULTATS_DIR.iterdir():
            if d.is_dir() and clean_id in d.name:
                dossier_genere = {
                    "nom": d.name,
                    "fichiers": sorted([f.name for f in d.glob("*") if f.is_file()]),
                }
                break
    # Fallback: check dossiers index
    if not dossier_genere and DOSSIERS_INDEX.exists():
        for d in json.loads(DOSSIERS_INDEX.read_text(encoding="utf-8")):
            if clean_id in d.get("nom", ""):
                dossier_genere = d
                break

    # Notes
    notes = charger_notes()
    note = notes.get(ao_id, "")

    prestations = detecter_prestations(ao)
    return render_template("ao_detail.html", ao=ao, dossier=dossier_genere, note=note,
                           prestations=prestations)


@app.route("/ao/<path:ao_id>/statut", methods=["POST"])
def changer_statut(ao_id):
    appels = charger_ao()
    nouveau_statut = request.form.get("statut", "nouveau")
    for ao in appels:
        if ao.get("id") == ao_id:
            ao["statut"] = nouveau_statut
            break
    sauvegarder_ao(appels)
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


@app.route("/ao/<path:ao_id>/generer", methods=["POST"])
def generer_dossier(ao_id):
    """Lance la generation du dossier en arriere-plan via WebSocket."""
    appels = charger_ao()
    ao_dict = next((a for a in appels if a.get("id") == ao_id), None)
    if not ao_dict:
        return jsonify({"error": "AO non trouve"}), 404

    def _generer_en_bg():
        try:
            from veille import AppelOffre
            from generateur import GenerateurMemoire
            from dce_downloader import telecharger_dce

            config = charger_config()
            ao = AppelOffre(**{k: v for k, v in ao_dict.items()
                              if k in AppelOffre.__dataclass_fields__})

            socketio.emit("veille_log", {"msg": f"Generation dossier: {ao.titre[:60]}..."})

            # DCE
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
    # Chercher le dossier local
    dossier_path = RESULTATS_DIR / nom
    if not dossier_path.exists():
        return "Fichier non disponible (dossier distant)", 404
    fichier_path = dossier_path / fichier
    if not fichier_path.exists() or not fichier_path.is_file():
        return "Fichier non trouve", 404
    # Securite : verifier que le chemin reste dans RESULTATS_DIR
    try:
        fichier_path.resolve().relative_to(RESULTATS_DIR.resolve())
    except ValueError:
        return "Acces refuse", 403
    from flask import send_file
    return send_file(str(fichier_path), as_attachment=False)


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


@app.route("/api/veille", methods=["POST"])
def api_lancer_veille():
    """POST /api/veille - Lance une veille en arriere-plan."""
    def _veille_bg():
        try:
            config = charger_config()
            from veille import Veilleur
            from filtre import FiltreAO

            socketio.emit("veille_log", {"msg": "Recherche en cours..."})
            veilleur = Veilleur(config)
            tous_ao = veilleur.lancer_recherche()
            socketio.emit("veille_log", {"msg": f"{len(tous_ao)} AO bruts trouves"})

            filtre = FiltreAO(config)
            pertinents = filtre.filtrer(tous_ao)
            socketio.emit("veille_log", {"msg": f"{len(pertinents)} AO pertinents"})

            # Sauvegarder
            existants = charger_ao()
            ids_existants = {ao["id"] for ao in existants}
            nouveaux = 0
            for ao in pertinents:
                d = ao.to_dict()
                if d["id"] not in ids_existants:
                    existants.append(d)
                    nouveaux += 1
            sauvegarder_ao(existants)

            socketio.emit("veille_log", {"msg": f"Termine: {nouveaux} nouveaux AO ajoutes"})
            socketio.emit("veille_complete", {"nouveaux": nouveaux, "total": len(existants)})
        except Exception as e:
            socketio.emit("veille_log", {"msg": f"Erreur: {e}"})
            socketio.emit("veille_complete", {"error": str(e)})

    thread = threading.Thread(target=_veille_bg, daemon=True)
    thread.start()
    return jsonify({"status": "started"})


# --- WebSocket ---

@socketio.on("connect")
def on_connect():
    logger.info("Client WebSocket connecte")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("RENDER") is None  # debug only in local
    print(f"AO Hunter Dashboard - http://localhost:{port}")
    socketio.run(app, debug=debug, use_reloader=False, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True)
