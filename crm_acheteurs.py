"""
CRM acheteurs integre - Gere une base de fiches acheteurs enrichie automatiquement.
"""

import json
import re
import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("ao_hunter.crm_acheteurs")

DASHBOARD_DIR = Path(__file__).parent
CRM_FILE = DASHBOARD_DIR / "crm_acheteurs.json"


def _normaliser_nom(nom: str) -> str:
    """Normalise le nom d'un acheteur pour servir de cle unique."""
    if not nom:
        return "inconnu"
    # Minuscules, supprimer accents simples, garder alphanum et espaces
    n = nom.strip().lower()
    n = re.sub(r"[^a-z0-9\s]", "", n)
    n = re.sub(r"\s+", "_", n)
    return n[:100] if n else "inconnu"


def _charger_crm() -> dict:
    """Charge la base CRM."""
    if CRM_FILE.exists():
        try:
            return json.loads(CRM_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _sauvegarder_crm(data: dict):
    """Sauvegarde la base CRM."""
    CRM_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def enrichir_acheteur(ao: dict):
    """Cree ou met a jour la fiche acheteur depuis les donnees d'un AO.

    Appele automatiquement a chaque veille pour chaque AO detecte.
    """
    acheteur_nom = ao.get("acheteur", "")
    if not acheteur_nom or acheteur_nom == "Inconnu":
        return

    if isinstance(acheteur_nom, list):
        acheteur_nom = ", ".join(str(a) for a in acheteur_nom)

    cle = _normaliser_nom(acheteur_nom)
    crm = _charger_crm()

    if cle not in crm:
        crm[cle] = {
            "nom": str(acheteur_nom),
            "type": _deviner_type(str(acheteur_nom)),
            "adresse": "",
            "contacts": [],
            "historique_ao": [],
            "notes": "",
            "tags": [],
            "score_relation": 0,
            "prochaine_action": "",
            "date_dernier_contact": "",
            "date_creation": datetime.now().strftime("%Y-%m-%d"),
        }

    fiche = crm[cle]

    # Mettre a jour le nom si plus complet
    if len(str(acheteur_nom)) > len(fiche.get("nom", "")):
        fiche["nom"] = str(acheteur_nom)

    # Ajouter l'AO a l'historique (eviter les doublons par ao_id)
    ao_id = ao.get("id", "")
    ids_existants = {h.get("ao_id") for h in fiche.get("historique_ao", [])}
    if ao_id and ao_id not in ids_existants:
        fiche["historique_ao"].append({
            "ao_id": ao_id,
            "titre": ao.get("titre", "")[:150],
            "date": ao.get("date_publication", "") or datetime.now().strftime("%Y-%m-%d"),
            "statut": ao.get("statut", "nouveau"),
            "montant": ao.get("budget_estime"),
        })

    # Ajouter le contact s'il y en a un dans l'AO
    contact_email = ao.get("contact_email", "")
    contact_nom = ao.get("contact_nom", "")
    if contact_email or contact_nom:
        emails_existants = {c.get("email", "").lower() for c in fiche.get("contacts", []) if c.get("email")}
        if contact_email and contact_email.lower() not in emails_existants:
            fiche["contacts"].append({
                "nom": contact_nom or "",
                "email": contact_email,
                "telephone": "",
                "fonction": "",
            })

    # Region
    region = ao.get("region", "")
    if region and not fiche.get("adresse"):
        if isinstance(region, list):
            region = ", ".join(str(r) for r in region)
        fiche["adresse"] = str(region)

    # Recalculer le score de relation
    fiche["score_relation"] = _calculer_score_relation(fiche)

    crm[cle] = fiche
    _sauvegarder_crm(crm)


def _deviner_type(nom: str) -> str:
    """Devine le type d'acheteur a partir du nom."""
    nom_lower = nom.lower()
    if any(t in nom_lower for t in ["commune", "mairie", "ville de", "metropole", "communaute"]):
        return "Collectivite territoriale"
    if any(t in nom_lower for t in ["departement", "conseil departemental", "conseil general"]):
        return "Departement"
    if any(t in nom_lower for t in ["region", "conseil regional"]):
        return "Region"
    if any(t in nom_lower for t in ["universite", "ecole", "lycee", "college", "academie", "education"]):
        return "Education"
    if any(t in nom_lower for t in ["hopital", "chu", "chru", "centre hospitalier", "ars", "sante"]):
        return "Sante"
    if any(t in nom_lower for t in ["ministere", "direction", "dgfip", "prefecture", "etat"]):
        return "Etat"
    if any(t in nom_lower for t in ["chambre", "cci", "cma"]):
        return "Chambre consulaire"
    if any(t in nom_lower for t in ["opco", "pole emploi", "france travail", "caisse"]):
        return "Organisme paritaire"
    if any(t in nom_lower for t in ["sem", "spl", "epl", "office"]):
        return "Entreprise publique"
    return "Autre"


def _calculer_score_relation(fiche: dict) -> int:
    """Calcule un score de relation 0-100 base sur l'historique."""
    score = 0
    historique = fiche.get("historique_ao", [])

    # Nombre d'AO (max 30 pts)
    score += min(len(historique) * 5, 30)

    # Contacts connus (max 20 pts)
    score += min(len(fiche.get("contacts", [])) * 10, 20)

    # Dernier contact recent (max 20 pts)
    if fiche.get("date_dernier_contact"):
        try:
            dt = datetime.fromisoformat(fiche["date_dernier_contact"])
            jours = (datetime.now() - dt).days
            if jours < 30:
                score += 20
            elif jours < 90:
                score += 10
            elif jours < 180:
                score += 5
        except (ValueError, TypeError):
            pass

    # Notes renseignees (10 pts)
    if fiche.get("notes"):
        score += 10

    # AO gagnes (max 20 pts)
    gagnes = sum(1 for h in historique if h.get("statut") == "gagne")
    score += min(gagnes * 10, 20)

    return min(score, 100)


def get_fiche(nom_acheteur: str, is_cle: bool = False) -> dict:
    """Retourne la fiche complete d'un acheteur.

    Args:
        nom_acheteur: Nom ou cle normalisee de l'acheteur.
        is_cle: Si True, nom_acheteur est deja une cle normalisee (pas de re-normalisation).
    """
    crm = _charger_crm()
    cle = nom_acheteur if is_cle else _normaliser_nom(nom_acheteur)

    # Recherche exacte
    if cle in crm:
        fiche = crm[cle]
        fiche["_cle"] = cle
        return fiche

    # Recherche partielle
    for k, v in crm.items():
        if cle in k or k in cle:
            v["_cle"] = k
            return v

    return {}


def ajouter_contact(nom_acheteur: str, contact_info: dict):
    """Ajoute un contact a la fiche d'un acheteur."""
    crm = _charger_crm()
    cle = _normaliser_nom(nom_acheteur)

    if cle not in crm:
        return False

    fiche = crm[cle]
    # Eviter les doublons par email
    if contact_info.get("email"):
        emails = {c.get("email", "").lower() for c in fiche.get("contacts", [])}
        if contact_info["email"].lower() in emails:
            return False

    fiche.setdefault("contacts", []).append({
        "nom": contact_info.get("nom", ""),
        "email": contact_info.get("email", ""),
        "telephone": contact_info.get("telephone", ""),
        "fonction": contact_info.get("fonction", ""),
    })

    fiche["date_dernier_contact"] = datetime.now().strftime("%Y-%m-%d")
    fiche["score_relation"] = _calculer_score_relation(fiche)
    crm[cle] = fiche
    _sauvegarder_crm(crm)
    return True


def ajouter_note(nom_acheteur: str, note: str):
    """Ajoute une note a la fiche d'un acheteur."""
    crm = _charger_crm()
    cle = _normaliser_nom(nom_acheteur)

    if cle not in crm:
        return False

    fiche = crm[cle]
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    ancienne = fiche.get("notes", "")
    fiche["notes"] = f"[{timestamp}] {note}\n{ancienne}".strip()
    fiche["date_dernier_contact"] = datetime.now().strftime("%Y-%m-%d")
    fiche["score_relation"] = _calculer_score_relation(fiche)
    crm[cle] = fiche
    _sauvegarder_crm(crm)
    return True


def top_acheteurs_actifs(n: int = 20) -> list[dict]:
    """Retourne les acheteurs avec le plus d'AO recents."""
    crm = _charger_crm()
    acheteurs = []

    for cle, fiche in crm.items():
        historique = fiche.get("historique_ao", [])
        # Compter les AO des 6 derniers mois
        seuil = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")
        recents = [h for h in historique if (h.get("date", "") or "") >= seuil]

        acheteurs.append({
            "_cle": cle,
            "nom": fiche.get("nom", cle),
            "type": fiche.get("type", "Autre"),
            "nb_ao_total": len(historique),
            "nb_ao_recents": len(recents),
            "nb_contacts": len(fiche.get("contacts", [])),
            "score_relation": fiche.get("score_relation", 0),
            "date_dernier_contact": fiche.get("date_dernier_contact", ""),
            "tags": fiche.get("tags", []),
        })

    # Trier par nb AO recents puis total
    acheteurs.sort(key=lambda a: (a["nb_ao_recents"], a["nb_ao_total"]), reverse=True)
    return acheteurs[:n]


def acheteurs_a_relancer() -> list[dict]:
    """Retourne les acheteurs sans contact depuis 3+ mois avec AO en cours."""
    crm = _charger_crm()
    seuil_relance = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    a_relancer = []

    for cle, fiche in crm.items():
        dernier_contact = fiche.get("date_dernier_contact", "")
        # Sans contact depuis 3 mois (ou jamais contacte)
        if dernier_contact and dernier_contact > seuil_relance:
            continue

        # Verifier s'il y a des AO recents (actifs)
        historique = fiche.get("historique_ao", [])
        ao_recents = [
            h for h in historique
            if h.get("statut") in ("nouveau", "analyse", "candidature")
        ]

        if ao_recents or len(historique) >= 2:
            a_relancer.append({
                "_cle": cle,
                "nom": fiche.get("nom", cle),
                "type": fiche.get("type", "Autre"),
                "nb_ao": len(historique),
                "ao_actifs": len(ao_recents),
                "date_dernier_contact": dernier_contact or "Jamais",
                "score_relation": fiche.get("score_relation", 0),
            })

    a_relancer.sort(key=lambda a: a["ao_actifs"], reverse=True)
    return a_relancer


def lister_tous_acheteurs() -> list[dict]:
    """Retourne la liste complete des acheteurs pour le CRM."""
    crm = _charger_crm()
    acheteurs = []

    for cle, fiche in crm.items():
        acheteurs.append({
            "_cle": cle,
            "nom": fiche.get("nom", cle),
            "type": fiche.get("type", "Autre"),
            "nb_ao": len(fiche.get("historique_ao", [])),
            "nb_contacts": len(fiche.get("contacts", [])),
            "score_relation": fiche.get("score_relation", 0),
            "date_dernier_contact": fiche.get("date_dernier_contact", ""),
            "tags": fiche.get("tags", []),
            "adresse": fiche.get("adresse", ""),
        })

    acheteurs.sort(key=lambda a: a["score_relation"], reverse=True)
    return acheteurs
