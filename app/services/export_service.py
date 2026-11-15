"""Rows of the admin exports (Administration > Exports): recruitment lists, the team, every candidate / student.
Never exported: password hashes, photos and signatures (only whether there is one), defense grades."""
from flask import current_app
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import (Candidate, Defense, Jury, JuryCandidate, Sas, TechnicalTestSession, User, ZeusClassMember)
from app.models.candidate import FILE_STATUSES
from app.models.defense import DECISIONS
from app.models.user import ROLE_CANDIDATE


def _dt(value) -> str:
    return current_app.jinja_env.filters["dt"](value)


def _date(value) -> str:
    return value.strftime("%d/%m/%Y") if value else ""


def _yes(value) -> str:
    return "Oui" if value else "Non"


def recruitment_row(c: Candidate) -> dict:
    """Recruitment follow-up of a candidate: file, motivation, technical test."""
    s = c.latest_session
    return {
        "Matricule": c.matricule, "N°": c.candidate_number, "Nom": c.full_name, "Email": c.email, "Téléphone": c.phone,
        "Groupe": c.group_name, "Date": c.scheduled_date, "Ordre": c.passage_order, "CNI": c.cni,
        "Kobo": c.kobo_badge[1], "Dossier physique": FILE_STATUSES.get(c.physical_file_status, ""),
        "Numéro pièce": c.piece_number, "Code examen": c.exam_code,
        "Motivation": _yes(c.motivation_completed),
        "Motivation le": _dt(c.motivation_completed_at),
        "Motivation validée": _yes(c.motivation_passed),
        "Salle": s.room.name if s else "", "Ordinateur": s.workstation.name if s else "",
        "Début test": _dt(s.started_at) if s else "", "Fin test": _dt(s.finished_at) if s else "",
        "Durée prévue (min)": s.duration_seconds // 60 if s else "",
        "Durée effective (min)": round(s.elapsed_seconds() / 60, 1) if s else "",
        "Statut test": s.status_badge[1] if s else "",
        "Technique terminé": _yes(c.technical_completed),
        "Technique validé": _yes(c.technical_passed),
        "Statut global": c.global_status,
    }


def team_rows() -> list[dict]:
    """Every staff account (candidate accounts are in the students export), with its places in the SAS and Zeus."""
    sas_names = {s.id: s.name for s in db.session.scalars(select(Sas))}
    users = db.session.scalars(select(User).where(User.role != ROLE_CANDIDATE)
                               .options(selectinload(User.cohort_roles), selectinload(User.zeus_class_links))
                               .order_by(User.role, User.full_name, User.email)).all()

    def sas_class(key: str) -> str:
        sas_id, _, name = key.partition(":")
        return f"{sas_names.get(int(sas_id), '?') if sas_id.isdigit() else '?'} · {name}"

    def zeus_class(link) -> str:
        notes = [n for n in (link.role.name if link.role else "", "gestionnaire" if link.is_manager else "",
                             "" if link.accepted_at else "invitation en attente") if n]
        return f"{link.klass.name} ({link.klass.cohort.name}) : {', '.join(notes) or 'coach'}"

    return [{
        "Nom et prénoms": u.full_name, "Genre": u.gender or "", "Email": u.email, "Téléphone": u.phone or "",
        "Rôle": u.role_label, "Compte actif": _yes(u.active),
        "Mot de passe à changer": _yes(u.must_change_password),
        "Photo": _yes(u.photo_type), "Signature numérique": _yes(u.signature_type),
        "Créé le": _dt(u.created_at), "Dernière connexion": _dt(u.last_login_at),
        "Classes SAS (coach)": ", ".join(sas_class(k) for k in u.coach_classes or []),
        "Cohortes de formation": ", ".join(f"{cc.cohort.name} ({cc.role})" for cc in u.cohort_roles),
        "Classes Zeus": " | ".join(zeus_class(link) for link in u.zeus_class_links),
    } for u in users]


def student_rows() -> list[dict]:
    """Every candidate, from recruitment to the training cohort: identity, file, tests, SAS, defense decision
    (never the grades), cohort registration, Zeus classes, online account."""
    candidates = db.session.scalars(select(Candidate).options(
        selectinload(Candidate.sessions).selectinload(TechnicalTestSession.room),
        selectinload(Candidate.sessions).selectinload(TechnicalTestSession.workstation),
        selectinload(Candidate.sas), selectinload(Candidate.enrollment), selectinload(Candidate.account),
        selectinload(Candidate.kobo_submissions), selectinload(Candidate.motivation_tester),
    ).order_by(Candidate.full_name)).all()
    # Last defense decision of each student (defenses in creation order: the latest wins).
    decisions = {row.candidate_id: (row.sheet or {}).get("decision") for row in db.session.scalars(
        select(JuryCandidate).join(Jury).join(Defense, Defense.id == Jury.defense_id).order_by(Defense.id))}
    zeus_classes: dict[int, list[str]] = {}
    for m in db.session.scalars(select(ZeusClassMember).where(ZeusClassMember.joined_at.is_not(None))):
        zeus_classes.setdefault(m.enrollment_id, []).append(m.klass.name)
    duplicates = ("Matricule", "N°", "Nom", "Email", "Téléphone", "Groupe", "Date", "Ordre", "CNI")

    rows = []
    for c in candidates:
        e, account = c.enrollment, c.account
        rows.append({
            "Matricule candidat": c.matricule, "Matricule DFS": e.matricule if e and e.matricule else "",
            "Nom et prénoms": c.full_name, "Genre": c.gender or "", "Date de naissance": _date(c.birth_date),
            "Lieu de naissance": c.birth_place or "", "Email": c.email or "", "Téléphone": c.phone or "",
            "Ville": c.city or "", "CNI": c.cni or "", "N° candidat": c.candidate_number or "",
            "Groupe": c.group_name or "", "Date convocation": _date(c.scheduled_date),
            "Heure convocation": c.scheduled_time or "", "Ordre de passage": c.passage_order or "",
            **{k: v for k, v in recruitment_row(c).items() if k not in duplicates},
            "Testeur motivation": c.motivation_tester.display_name if c.motivation_tester else "",
            "SAS": c.sas.name if c.sas else "", "Admis au SAS le": _date(c.sas_admitted_at),
            "Classe SAS": c.sas_class or "", "Chambre SAS": c.sas_bedroom or "", "Matériel SAS": c.sas_equipment or "",
            "Décision soutenance": DECISIONS.get(decisions.get(c.id), ""),
            "Cohorte de formation": e.cohort.name if e else "",
            "Inscription cohorte": ("Inscrit" if e.matricule else "Éligible") if e else "",
            "Inscrit le": _date(e.validated_at) if e else "", "Date d'arrivée": _date(e.arrival_date) if e else "",
            "Chambre (cohorte)": e.bedroom or "" if e else "", "Matériel (cohorte)": e.equipment or "" if e else "",
            "Signature d'inscription": _yes(e.signature_type) if e else "",
            "Classes Zeus": ", ".join(zeus_classes.get(e.id, [])) if e else "",
            "Compte en ligne": ("Actif" if account.active else "Désactivé") if account else "Aucun",
            "Photo": _yes(c.photo_type), "Créé le": _dt(c.created_at),
        })
    return rows
