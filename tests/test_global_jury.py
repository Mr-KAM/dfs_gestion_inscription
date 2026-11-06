"""Global jury page (filters, sort, batch decision) and its documents: all individual minutes, general minutes."""
from datetime import datetime, timezone

from app.models import Defense, Evaluation, Jury, JuryCandidate, JuryMember, Notification, Sas
from app.models.campus import EVAL_CRITERIA
from app.services import account_service
from tests.conftest import login, make_candidate, make_user
from tests.test_portal import portal_login


def _defense(db, validated=True):
    """Two juries. ALPHA F follow 75, BRAVO M follow 50 (Jury 1), CHARLIE M no follow-up sheet (Jury 2)."""
    sas = Sas(name="SAS 2026")
    d = Defense(sas=sas, name="Soutenance finale")
    j1, j2 = Jury(defense=d, name="Jury 1", duration=20), Jury(defense=d, name="Jury 2", duration=20)
    db.session.add_all([sas, d, j1, j2])
    db.session.flush()
    first, second, third = (make_user(r, f"{r}@test.ci") for r in ("coach", "partner", "cme"))
    first.full_name, second.full_name, third.full_name = "FIRST Member", "SECOND Member", "THIRD Member"
    db.session.add_all([JuryMember(jury=j1, defense_id=d.id, user=first, role="technical"),
                        JuryMember(jury=j1, defense_id=d.id, user=second, role="president"),
                        JuryMember(jury=j2, defense_id=d.id, user=third, role="presentation")])
    rows = {}
    for i, (name, gender, jury, cote) in enumerate((("ALPHA", "F", j1, "3"), ("BRAVO", "M", j1, "2"),
                                                    ("CHARLIE", "M", j2, None))):
        c = make_candidate(full_name=name, gender=gender, sas=sas, sas_class="Web" if i else "SIG",
                           email=f"{name}@test.ci", cni=name, phone=f"010101010{i}")
        if cote:
            ratings = {k: cote for _s, k, *_r in EVAL_CRITERIA}
            db.session.add(Evaluation(candidate_id=c.id, ratings=ratings, evidence={}))
        rows[name] = JuryCandidate(jury=jury, defense_id=d.id, candidate=c, position=i)
        db.session.add(rows[name])
    if validated:
        j1.validated_at = j2.validated_at = d.created_at
    db.session.commit()
    return d, rows


def _names(client, url, query=""):
    html = client.get(f"{url}?{query}").get_data(as_text=True)
    return sorted((html.index(f">{n}</a>"), n) for n in ("ALPHA", "BRAVO", "CHARLIE") if f">{n}</a>" in html)


def _order(client, url, query=""):
    return [n for _i, n in _names(client, url, query)]


def test_filters_and_sort(app, db, client, admin):
    d, rows = _defense(db)
    rows["BRAVO"].sheet = {"decision": "waitlist"}
    db.session.commit()
    login(client, admin)
    url = f"/soutenances/{d.id}/jury-global"
    assert _order(client, url, "q=alph") == ["ALPHA"] and _order(client, url, "q=bravo@test") == ["BRAVO"]
    assert _order(client, url, "gender=M") == ["BRAVO", "CHARLIE"]
    assert _order(client, url, f"jury={rows['CHARLIE'].jury_id}") == ["CHARLIE"]
    assert _order(client, url, "class=SIG") == ["ALPHA"]
    assert _order(client, url, "decision=waitlist") == ["BRAVO"]
    assert _order(client, url, "decision=none") == ["ALPHA", "CHARLIE"]
    assert _order(client, url, "min_follow=60") == ["ALPHA"]  # no follow-up sheet: not above any minimum
    assert _order(client, url, "sort=follow&dir=asc") == ["BRAVO", "ALPHA", "CHARLIE"]  # no value: last either way
    assert _order(client, url, "sort=follow&dir=desc") == ["ALPHA", "BRAVO", "CHARLIE"]
    assert _order(client, url, "sort=name&dir=desc") == ["CHARLIE", "BRAVO", "ALPHA"]
    assert _order(client, url, "sort=gender&dir=asc&q=a") == ["ALPHA", "BRAVO", "CHARLIE"]
    assert "Aucun étudiant ne correspond" in client.get(f"{url}?q=zzz").get_data(as_text=True)


def test_saving_a_filtered_page_keeps_the_hidden_decisions(app, db, client, admin):
    d, rows = _defense(db)
    rows["CHARLIE"].sheet = {"decision": "rejected", "decision_note": "Absent"}
    db.session.commit()
    login(client, admin)
    a = rows["ALPHA"].id
    resp = client.post(f"/soutenances/{d.id}/jury-global?gender=F", data={f"decision_{a}": "admitted", f"note_{a}": ""})
    assert resp.headers["Location"].endswith("gender=F")  # stays on the filtered list
    db.session.expire_all()
    assert rows["ALPHA"].sheet["decision"] == "admitted" and rows["CHARLIE"].sheet["decision"] == "rejected"
    assert rows["CHARLIE"].sheet["decision_note"] == "Absent" and "decision" not in (rows["BRAVO"].sheet or {})


def test_batch_decision(app, db, client, admin):
    d, rows = _defense(db)
    login(client, admin)
    url = f"/soutenances/{d.id}/jury-global"
    a, b, c = rows["ALPHA"].id, rows["BRAVO"].id, rows["CHARLIE"].id
    shown = {f"decision_{a}": "", f"decision_{b}": "", f"decision_{c}": "rejected", f"note_{c}": "Absent"}
    for bad in ({"bulk_decision": "waitlist"}, {"selected": [a]}, {"selected": [a], "bulk_decision": "hack"}):
        html = client.post(url, data={**shown, "apply_bulk": "1", **bad}, follow_redirects=True).get_data(as_text=True)
        assert "Cochez au moins un étudiant" in html
    db.session.expire_all()
    assert "decision" not in (rows["CHARLIE"].sheet or {})  # refused: nothing saved
    client.post(url, data={**shown, "apply_bulk": "1", "selected": [a, b], "bulk_decision": "waitlist"})
    db.session.expire_all()
    assert rows["ALPHA"].sheet["decision"] == rows["BRAVO"].sheet["decision"] == "waitlist"
    assert rows["CHARLIE"].sheet["decision"] == "rejected" and rows["CHARLIE"].sheet["decision_note"] == "Absent"
    client.post(url, data={**shown, f"decision_{a}": "admitted", "selected": [b], "bulk_decision": "admitted"})
    db.session.expire_all()  # the plain save button ignores the ticks
    assert rows["ALPHA"].sheet["decision"] == "admitted" and rows["BRAVO"].sheet["decision"] == ""


def test_documents_are_for_the_admin_once_validated(app, db, client, admin):
    d, _rows = _defense(db, validated=False)
    urls = (f"/soutenances/{d.id}/proces-verbal", f"/jury/soutenances/{d.id}/proces-verbaux")
    member = make_user("coach", "other@test.ci")
    login(client, member)
    assert all(client.get(url).status_code == 403 for url in urls)
    client.post("/logout")
    login(client, admin)
    assert "proces-verbal" not in client.get(f"/soutenances/{d.id}/jury-global").get_data(as_text=True)
    for url in urls:
        assert "Délibération à valider" in client.get(url, follow_redirects=True).get_data(as_text=True)


def test_general_minutes(app, db, client, admin):
    d, rows = _defense(db)
    rows["ALPHA"].sheet = {"decision": "admitted"}
    rows["BRAVO"].sheet = {"decision": "waitlist", "decision_note": "Rang 1"}
    d.starts_at, d.location = datetime(2026, 10, 5, 17, 30, tzinfo=timezone.utc), "Salle 3"
    db.session.commit()
    login(client, admin)
    assert "proces-verbal" in client.get(f"/soutenances/{d.id}/jury-global").get_data(as_text=True)
    html = client.get(f"/soutenances/{d.id}/proces-verbal").get_data(as_text=True)
    assert "PROCÈS-VERBAL JURY ADMISSION À LA DFS COHORTE 1 2026-2027" in html and "data-autoprint" in html
    assert "Note finale =" not in html  # the formula stays on the global jury page
    assert "Sous la présidence de SECOND Member, le jury" in html
    assert "s’est réuni le 05 octobre 2026 à 17h30" in html and "Yamoussoukro (Salle 3)" in html
    assert "– SECOND Member, Président du jury (Jury 1)" in html
    assert "– THIRD Member, Membre du jury, Évaluateur présentation (Jury 2)" in html
    assert html.index("Le jury était composé") < html.index("Admis (1)")  # the text, then the tables
    for title in ("Admis (1)", "Liste d&#39;attente (1)", "Sans décision (1)"):
        assert title in html
    assert "Non admis (" not in html and "Rang 1" in html  # empty lists only appear in the summary
    assert html.index("Admis (1)") < html.index("ALPHA") < html.index("Liste d&#39;attente (1)") < html.index("BRAVO")
    # the president signs first
    assert html.index(">SECOND Member<") < html.index(">FIRST Member<") < html.index(">THIRD Member<")
    assert "Évaluateur technique · Jury 1" in html and "Évaluateur présentation · Jury 2" in html


def _student_logs_in(client, db, student):
    client.post("/logout")
    (account_service.sync_account(student) or student.account).must_change_password = False
    db.session.commit()
    portal_login(client, student.email)
    return client.get("/candidat/").get_data(as_text=True)


def test_results_reach_the_students_only_when_sent(app, db, client, admin):
    d, rows = _defense(db, validated=False)
    url, send = f"/soutenances/{d.id}/jury-global", f"/soutenances/{d.id}/resultats"
    student = rows["ALPHA"].candidate
    rows["ALPHA"].sheet = {"decision": "admitted"}
    db.session.commit()

    coach = make_user("coach", "other@test.ci")
    login(client, coach)
    assert client.post(send).status_code == 403
    client.post("/logout")
    login(client, admin)
    assert "Délibération à valider" in client.post(send, follow_redirects=True).get_data(as_text=True)
    for jury in d.juries:
        client.post(f"/soutenances/juries/{jury.id}/validate")
    assert "2 étudiant(s) sans décision" in client.post(send, follow_redirects=True).get_data(as_text=True)
    db.session.expire_all()
    assert d.results_sent_at is None and Notification.query.count() == 0
    assert "Résultat de soutenance" not in _student_logs_in(client, db, student)  # validated and decided: not sent

    client.post("/logout")
    login(client, admin)
    client.post(url, data={f"decision_{rows['BRAVO'].id}": "waitlist", f"decision_{rows['CHARLIE'].id}": "rejected"})
    html = client.post(send, follow_redirects=True).get_data(as_text=True)
    assert "Résultats envoyés à 3 étudiant(s)" in html and "Retirer les résultats" in html
    notes = Notification.query.all()
    assert {n.candidate_id for n in notes} == {r.candidate_id for r in rows.values()} and len(notes) == 3
    assert not any("Admis" in n.body or "/ 100" in n.body for n in notes)  # the coaches can read them
    assert "déjà envoyés" in client.post(send, follow_redirects=True).get_data(as_text=True)
    assert Notification.query.count() == 3  # a double click does not notify twice

    # Sent: everything is locked until the results are withdrawn.
    a = rows["ALPHA"].id
    assert "retirez-les d" in client.post(url, data={f"decision_{a}": "rejected"},
                                          follow_redirects=True).get_data(as_text=True)
    jury = rows["ALPHA"].jury
    assert "retirez-les d" in client.post(f"/soutenances/juries/{jury.id}/validate", data={"reopen": "1"},
                                          follow_redirects=True).get_data(as_text=True)
    db.session.expire_all()
    assert rows["ALPHA"].sheet["decision"] == "admitted" and jury.validated_at is not None
    assert "Résultat de soutenance" in _student_logs_in(client, db, student)

    client.post("/logout")
    login(client, admin)
    client.post(send, data={"withdraw": "1"})
    client.post(url, data={f"decision_{a}": "reserved"})
    db.session.expire_all()
    assert d.results_sent_at is None and rows["ALPHA"].sheet["decision"] == "reserved"
    assert Notification.query.count() == 0  # no "your result is available" left behind
    assert "Résultat de soutenance" not in _student_logs_in(client, db, student)
    client.post("/logout")
    login(client, admin)
    client.post(send)
    assert Notification.query.count() == 3  # sent again, once each


def test_all_individual_minutes_and_waitlist_label(app, db, client, admin):
    d, rows = _defense(db)
    rows["ALPHA"].sheet = {"decision": "waitlist", "decision_note": "Rang 2"}
    rows["BRAVO"].sheet = rows["CHARLIE"].sheet = {"decision": "rejected"}
    db.session.commit()
    login(client, admin)
    html = client.get(f"/jury/soutenances/{d.id}/proces-verbaux").get_data(as_text=True)
    assert html.count("data-fit-page") == 3 and html.index("ALPHA") < html.index("BRAVO") < html.index("CHARLIE")
    assert "☑ Liste d&#39;attente" in html and "Chaque membre du jury" not in html
    single = client.get(f"/jury/sheets/{rows['ALPHA'].id}/print").get_data(as_text=True)
    assert single.count("data-fit-page") == 1 and "ALPHA" in single and "BRAVO" not in single
    client.post(f"/soutenances/{d.id}/resultats")
    assert "Liste d&#39;attente : Rang 2" in _student_logs_in(client, db, rows["ALPHA"].candidate)
