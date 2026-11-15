"""Admin exports of the whole team and of every candidate / student (Administration > Exports)."""
import io

import pandas as pd

from app.models import (Cohort, CohortCoach, Defense, Enrollment, Jury, JuryCandidate, Sas, ZeusClass, ZeusClassCoach,
                        ZeusClassMember)
from app.services import account_service
from app.utils import utcnow
from tests.conftest import PASSWORD, login, make_candidate, make_user


def _sheet(client, kind: str) -> pd.DataFrame:
    response = client.get(f"/admin/exports/{kind}?format=xlsx")
    assert response.status_code == 200
    return pd.read_excel(io.BytesIO(response.data), dtype=str).fillna("")


def test_team_export(app, db, client, admin):
    coach = make_user("coach", "coach@test.ci")
    cohort = Cohort(name="2026-2027")
    klass = ZeusClass(cohort=cohort, name="Web")
    db.session.add_all([CohortCoach(cohort=cohort, user=coach, role="Coach référent"),
                        ZeusClassCoach(klass=klass, user=coach, is_manager=True, accepted_at=utcnow())])
    account_service.sync_account(make_candidate())  # candidate accounts: in the students export, not the team
    db.session.commit()
    login(client, coach)  # the gender is set by the person, on "Mon profil"
    client.post("/profil", data={"action": "profile", "full_name": "KONE Awa", "gender": "F", "phone": "0707070707"})
    client.post("/logout")

    login(client, admin)
    assert "Équipe : utilisateurs" in client.get("/admin/exports").get_data(as_text=True)
    team = _sheet(client, "team")
    assert list(team["Email"]) == [admin.email, "coach@test.ci"]  # by role, then name; no candidate account
    row = team[team["Email"] == "coach@test.ci"].iloc[0]
    assert (row["Nom et prénoms"], row["Genre"], row["Rôle"], row["Compte actif"]) == ("KONE Awa", "F",
                                                                                     "Coach technique", "Oui")
    assert row["Cohortes de formation"] == "2026-2027 (Coach référent)"
    assert row["Classes Zeus"] == "Web (2026-2027) : gestionnaire"
    csv = client.get("/admin/exports/team?format=csv").data.decode("utf-8-sig")
    assert "pbkdf2" not in csv and "scrypt" not in csv and PASSWORD not in csv  # never a password or its hash


def test_students_export(app, db, client, admin):
    sas, cohort = Sas(name="SAS 2026"), Cohort(name="2026-2027")
    db.session.add_all([sas, cohort])
    db.session.commit()
    alpha = make_candidate(full_name="ALPHA Ama", gender="F", city="Bouaké", sas=sas, sas_class="Web",
                           sas_admitted_at=utcnow())
    make_candidate(full_name="=HACK()", email="h@test.ci", cni="H", phone="0101010101")  # formula: neutralised
    e = Enrollment(cohort=cohort, candidate=alpha, matricule="26DFS000001", validated_at=utcnow(), bedroom="A12")
    defense = Defense(sas=sas, name="Soutenance")
    jury = Jury(defense=defense, name="Jury 1")
    klass = ZeusClass(cohort=cohort, name="Web")
    db.session.add_all([e, defense, jury, klass])
    db.session.flush()
    db.session.add_all([JuryCandidate(jury=jury, defense_id=defense.id, candidate=alpha, sheet={"decision": "admitted"}),
                        ZeusClassMember(klass=klass, enrollment=e, joined_at=utcnow())])
    account_service.sync_account(alpha)
    db.session.commit()

    login(client, admin)
    students = _sheet(client, "students")
    assert list(students["Nom et prénoms"]) == ["'=HACK()", "ALPHA Ama"]
    row = students[students["Nom et prénoms"] == "ALPHA Ama"].iloc[0]
    assert (row["Matricule candidat"], row["Matricule DFS"], row["Genre"], row["Ville"]) == (
        alpha.matricule, "26DFS000001", "F", "Bouaké")
    assert (row["SAS"], row["Classe SAS"], row["Décision soutenance"]) == ("SAS 2026", "Web", "Admis")
    assert (row["Cohorte de formation"], row["Inscription cohorte"], row["Chambre (cohorte)"]) == (
        "2026-2027", "Inscrit", "A12")
    assert (row["Classes Zeus"], row["Compte en ligne"], row["Statut global"]) == ("Web", "Actif", "Planifié")
    assert "Code examen" in students.columns and "Statut test" in students.columns  # recruitment follow-up too
    client.post("/logout")
    login(client, make_user("supervisor"))
    assert client.get("/admin/exports/students?format=xlsx").status_code == 403  # admin only
