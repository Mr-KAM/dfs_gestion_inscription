"""Global jury page (filters, sort, batch decision) and its documents: all individual minutes, general minutes."""
import io
from datetime import datetime, timezone
from types import SimpleNamespace

from app.models import Defense, Evaluation, Jury, JuryCandidate, JuryMember, Notification, Sas
from app.models.campus import EVAL_CRITERIA
from app.models.defense import DECISIONS, RESULT_MESSAGES
from app.services import account_service
from app.services.defense_service import minutes_lists, surname_first
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
    assert f'href="{urls[0]}"' not in client.get(f"/soutenances/{d.id}/jury-global").get_data(as_text=True)
    for url in urls:
        assert "Délibération à valider" in client.get(url, follow_redirects=True).get_data(as_text=True)


def test_surname_first():
    assert surname_first("salia abdallah kamagate") == "Kamagate Salia Abdallah"
    assert surname_first("SALIA ABDALLAH KAMAGATE") == "Kamagate Salia Abdallah"
    assert surname_first("GOULI BI jean-marc") == "Gouli Bi Jean-Marc"  # capitals mark a compound surname
    assert surname_first("salia A kamagate") == "Kamagate Salia A"  # an initial is not a surname
    assert surname_first("awa n'guessan") == "N'Guessan Awa"
    assert surname_first("kouassi yao kouassi") == "Kouassi Kouassi Yao"  # no word lost
    assert surname_first("ALPHA") == "Alpha"


def test_minutes_lists_alphabetical_and_merit():
    def row(name, decision):
        return SimpleNamespace(candidate=SimpleNamespace(full_name=name), sheet={"decision": decision}), {}
    best_first = [row("awa zadi", "admitted"), row("yao élan", "admitted"), row("ali zoro", "waitlist"),
                  row("ben kone", "waitlist"), row("eve abo", "rejected"), row("ivan aka", "reserved")]
    admitted, waitlist = minutes_lists(best_first)
    assert [n for n, _c in admitted] == ["Élan Yao", "Zadi Awa"]  # alphabetical, accent ignored
    assert [n for n, _c in waitlist] == ["Zoro Ali", "Kone Ben"]  # merit, as they come


def test_general_minutes(app, db, client, admin):
    d, rows = _defense(db)
    rows["ALPHA"].candidate.full_name, rows["BRAVO"].candidate.full_name = "salia abdallah kamagate", "awa kone"
    rows["ALPHA"].sheet = rows["BRAVO"].sheet = {"decision": "admitted"}
    rows["CHARLIE"].sheet = {"decision": "rejected", "decision_note": "Orientation X"}
    d.starts_at = datetime(2026, 11, 6, 9, 0, tzinfo=timezone.utc)
    admin.full_name, admin.signature, admin.signature_type = "KAMBOU Anicet Cyrille", b"x", "image/png"
    member = d.juries[0].members[0].user
    member.signature, member.signature_type = b"x", "image/png"
    db.session.commit()
    login(client, admin)
    url = f"/soutenances/{d.id}/proces-verbal"
    assert f'href="{url}"' in client.get(f"/soutenances/{d.id}/jury-global").get_data(as_text=True)
    html = client.get(url).get_data(as_text=True)
    assert "PROCÈS-VERBAL DU JURY D’ADMISSION" in html and "data-autoprint" in html and "papier-en-tete.svg" in html
    assert "Vendredi 06 novembre 2026" in html and "session du vendredi 06 novembre 2026" in html
    assert "Fait à Yamoussoukro, le 06 novembre 2026." in html and "sous la présidence de Monsieur KAMBOU" in html
    assert html.index("Article 1") < html.index("Kamagate Salia Abdallah") < html.index("Kone Awa") \
        < html.index("Article 2") < html.index("Néant") < html.index("Fait à Yamoussoukro")
    assert "CHARLIE" not in html and "Charlie" not in html  # the rejected are not listed, nor their notes:
    for absent in ("Non admis", "réserve", "Orientation X", "Rang", "Note finale", "coef"):
        assert absent not in html
    # Signatures: the president also signs when he sits on no jury; the others without name nor role.
    assert f"/jury/signature/{admin.id}" in html and f"/jury/signature/{member.id}" in html
    assert member.full_name not in html and "Évaluateur" not in html


PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 10


def test_minutes_info_defaults_edit_and_stamp(app, db, client, admin):
    d, rows = _defense(db)
    for row in rows.values():
        row.sheet = {"decision": "admitted"}
    d.starts_at = datetime(2026, 11, 6, 9, 0, tzinfo=timezone.utc)
    db.session.commit()
    login(client, admin)
    page, pv = f"/soutenances/{d.id}/jury-global", f"/soutenances/{d.id}/proces-verbal"
    html = client.get(page).get_data(as_text=True)
    for default in ('value="KAMBOU ANICET CYRILLE"', 'value="Directeur Pédagogique et Académique"',
                    'value="2026-11-06"', 'value="Digital Farming School"', 'value="Yamoussoukro"', "sans cachet"):
        assert default in html

    form = {"civility": "Madame", "president_name": "ATSE Laure", "president_title": "Directrice", "date": "2026-11-09",
            "site": "INP-HB", "city": "Bouaké"}
    resp = client.post(pv, data={**form, "stamp": (io.BytesIO(PNG), "cachet.png")}, content_type="multipart/form-data",
                       follow_redirects=True)
    assert "Informations du procès-verbal enregistrées" in resp.get_data(as_text=True)
    html = client.get(pv).get_data(as_text=True)
    assert "Lundi 09 novembre 2026" in html and "Lieu :</strong> INP-HB, Bouaké" in html
    assert "site « INP-HB » à Bouaké, sous la présidence de Madame ATSE Laure, Directrice de la DFS" in html
    assert "Fait à Bouaké, le 09 novembre 2026." in html and "La Présidente de la session" in html
    # The stamp comes right under the president's signature, in the closing block only.
    assert html.count(f"/soutenances/{d.id}/cachet") == 1
    assert html.index("Directrice</div>") < html.index("/cachet") < html.index('flex-fill')
    resp = client.get(f"/soutenances/{d.id}/cachet")
    assert resp.data == PNG and resp.mimetype == "image/png" and "no-store" in resp.headers["Cache-Control"]

    # A new save without a file keeps the stamp; an SVG is refused and changes nothing.
    client.post(pv, data=form, content_type="multipart/form-data")
    assert client.get(f"/soutenances/{d.id}/cachet").status_code == 200
    resp = client.post(pv, data={"stamp": (io.BytesIO(b"<svg></svg>"), "c.svg")}, content_type="multipart/form-data",
                       follow_redirects=True)
    assert "Image invalide" in resp.get_data(as_text=True)
    db.session.expire_all()
    assert d.minutes_info["city"] == "Bouaké"
    assert "Date invalide" in client.post(pv, data={**form, "date": "x"}, follow_redirects=True).get_data(as_text=True)

    # Emptied fields go back to the defaults, the date follows the defense again; the stamp can be removed.
    client.post(pv, data={"remove_stamp": "1"})
    db.session.expire_all()
    assert d.minutes_info == {} and d.stamp_type is None
    assert client.get(f"/soutenances/{d.id}/cachet").status_code == 404
    html = client.get(pv).get_data(as_text=True)
    assert "site de la Digital Farming School à Yamoussoukro, sous la présidence de Monsieur KAMBOU" in html
    assert "Vendredi 06 novembre 2026" in html and "Le Président de la session" in html

    client.post("/logout")
    login(client, make_user("coach", "other@test.ci"))
    assert client.post(pv, data=form).status_code == 403
    assert client.get(f"/soutenances/{d.id}/cachet").status_code == 403


def test_minutes_fixed_wording_can_change_and_returns_to_default(app, db, client, admin):
    d, rows = _defense(db)
    for row in rows.values():
        row.sheet = {"decision": "admitted"}
    d.starts_at = datetime(2026, 11, 6, 9, 0, tzinfo=timezone.utc)
    db.session.commit()
    login(client, admin)
    pv = f"/soutenances/{d.id}/proces-verbal"
    html = client.get(pv).get_data(as_text=True)
    assert "Académique de la DFS, a procédé" in html and "<div>Digital Farming School (DFS)</div>" in html
    assert "de la première promotion de la DFS, au titre de l’année académique 2026-2027." in html
    assert "au titre de la première promotion, année académique 2026-2027," in html

    client.post(pv, data={"title_suffix": "de l’INP-HB", "organization": "INP-HB", "promotion": "deuxième promotion",
                          "academic_year": "2027-2028"})
    html = client.get(pv).get_data(as_text=True)
    assert "Académique de l’INP-HB, a procédé" in html and "<div>INP-HB</div>" in html
    assert "de la deuxième promotion de la DFS, au titre de l’année académique 2027-2028." in html
    assert "au titre de la deuxième promotion, année académique 2027-2028," in html
    assert "au titre de l’année académique 2027-2028, les candidats" in html
    assert "2026-2027" not in html and "première promotion" not in html

    client.post(pv, data={"title_suffix": "", "organization": " ", "promotion": "", "academic_year": ""})
    html = client.get(pv).get_data(as_text=True)
    assert "Académique de la DFS, a procédé" in html and "<div>Digital Farming School (DFS)</div>" in html
    assert "au titre de la première promotion, année académique 2026-2027," in html


def test_director_signature_on_every_individual_minutes(app, db, client, admin):
    d, rows = _defense(db)
    db.session.commit()
    login(client, admin)
    page, pv = f"/soutenances/{d.id}/jury-global", f"/soutenances/{d.id}/proces-verbal"
    image, all_pv = f"/soutenances/{d.id}/signature-directeur", f"/jury/soutenances/{d.id}/proces-verbaux"
    one_pv = f"/jury/sheets/{rows['ALPHA'].id}/print"

    html = client.get(all_pv).get_data(as_text=True)
    assert html.count("KAMBOU ANICET CYRILLE") == 3 and image not in html  # default name, room to sign by hand
    assert "Nom et signature" not in html and client.get(image).status_code == 404
    assert "sans signature du directeur" in client.get(page).get_data(as_text=True)

    client.post(pv, data={"director_name": "ATSE Laure", "director_signature": (io.BytesIO(PNG), "s.png")},
                content_type="multipart/form-data")
    html = client.get(all_pv).get_data(as_text=True)
    assert html.count(image) == 3 and html.count("ATSE Laure") >= 3  # one per student's sheet
    assert image in client.get(one_pv).get_data(as_text=True)
    resp = client.get(image)
    assert resp.data == PNG and resp.mimetype == "image/png" and "no-store" in resp.headers["Cache-Control"]
    assert "signature du directeur chargée" in client.get(page).get_data(as_text=True)

    # An SVG is refused and keeps the signature; the stamp and the signature live apart.
    resp = client.post(pv, data={"director_signature": (io.BytesIO(b"<svg></svg>"), "s.svg")},
                       content_type="multipart/form-data", follow_redirects=True)
    assert "Image invalide" in resp.get_data(as_text=True) and client.get(image).data == PNG
    assert client.get(f"/soutenances/{d.id}/cachet").status_code == 404

    client.post("/logout")
    login(client, make_user("coach", "other@test.ci"))
    assert client.get(image).status_code == 403
    assert client.post(pv, data={"remove_director_signature": "1"}).status_code == 403
    client.post("/logout")
    login(client, admin)
    client.post(pv, data={"remove_director_signature": "1"})
    assert client.get(image).status_code == 404 and image not in client.get(all_pv).get_data(as_text=True)


def test_individual_minutes_surname_first_and_no_reserved_option(app, db, client, admin):
    d, rows = _defense(db)
    rows["ALPHA"].candidate.full_name, rows["BRAVO"].candidate.full_name = "zoe abo", "ali yao"
    rows["CHARLIE"].candidate.full_name = "awa koné"
    rows["ALPHA"].sheet = {"decision": "admitted"}
    rows["CHARLIE"].sheet = {"decision": "reserved"}
    db.session.commit()
    login(client, admin)
    html = client.get(f"/jury/soutenances/{d.id}/proces-verbaux").get_data(as_text=True)
    # Sheets by surname: Abo, Koné, Yao; not by what comes first in the record.
    assert html.index(">Abo Zoe<") < html.index(">Koné Awa<") < html.index(">Yao Ali<")
    assert "zoe abo" not in html
    single = client.get(f"/jury/sheets/{rows['ALPHA'].id}/print").get_data(as_text=True)
    assert "<title>Procès-verbal de soutenance · Abo Zoe" in single and "réserve" not in single
    assert "☑ Admis" in single and "☐ Liste d&#39;attente" in single and "☐ Non admis" in single
    # A student actually given that decision keeps it visible: his sheet never shows an empty decision.
    assert "☑ Admis sous réserve" in client.get(f"/jury/sheets/{rows['CHARLIE'].id}/print").get_data(as_text=True)


def test_minutes_president_signature_follows_the_name(app, db, client, admin):
    d, rows = _defense(db)
    for row in rows.values():
        row.sheet = {"decision": "admitted"}
    d.starts_at = datetime(2026, 11, 6, 9, 0, tzinfo=timezone.utc)
    member = d.juries[1].members[0].user  # "THIRD Member" sits on a jury and becomes president of the session
    member.signature, member.signature_type = PNG, "image/png"
    d.minutes_info = {"president_name": "Member Third"}
    db.session.commit()
    login(client, admin)
    html = client.get(f"/soutenances/{d.id}/proces-verbal").get_data(as_text=True)
    url = f"/jury/signature/{member.id}"
    # In the president's block, not again next to it, and on the foot of the sheets.
    assert html.count(url) == 2 and html.index("pv-president") < html.index(url) < html.index("flex-fill")


def test_general_minutes_needs_a_date_and_a_place_for_everyone(app, db, client, admin):
    d, rows = _defense(db)
    for row in rows.values():
        row.sheet = {"decision": "admitted"}
    db.session.commit()
    login(client, admin)
    url = f"/soutenances/{d.id}/proces-verbal"
    assert "Renseignez d&#39;abord la date" in client.get(url, follow_redirects=True).get_data(as_text=True)
    d.starts_at = datetime(2026, 11, 6, 9, 0, tzinfo=timezone.utc)
    rows["ALPHA"].sheet = {"decision": "reserved"}
    rows["BRAVO"].sheet = {}
    db.session.commit()
    assert "2 étudiant(s) sans décision ou" in client.get(url, follow_redirects=True).get_data(as_text=True)
    rows["ALPHA"].sheet = rows["BRAVO"].sheet = {"decision": "waitlist"}
    db.session.commit()
    assert "PROCÈS-VERBAL" in client.get(url).get_data(as_text=True)


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
    assert not any("/ 100" in n.body for n in notes)
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
    # ... with the corrected decision
    assert "admise sous réserve" in Notification.query.filter_by(candidate_id=student.id).one().body


def test_result_message_per_decision(app, db, client, admin):
    assert set(RESULT_MESSAGES) == set(DECISIONS)
    d, rows = _defense(db)  # ALPHA F, BRAVO M
    charlie = rows["CHARLIE"].candidate
    charlie.gender, charlie.full_name = None, "yao jean kouame"
    rows["ALPHA"].candidate.full_name = "salia kamagate"
    rows["ALPHA"].sheet, rows["BRAVO"].sheet = {"decision": "admitted"}, {"decision": "waitlist"}
    rows["CHARLIE"].sheet = {"decision": "rejected", "decision_note": "Note interne"}
    db.session.commit()
    login(client, admin)
    client.post(f"/soutenances/{d.id}/resultats")

    def note(name):
        return Notification.query.filter_by(candidate_id=rows[name].candidate_id).one()
    intro = "Le jury a délibéré : à l'issue de la soutenance du SAS, vous avez été"
    assert note("ALPHA").body == f"Bonjour Kamagate Salia,\n{intro} déclarée admise."
    assert note("ALPHA").level == "success"
    assert note("BRAVO").body == f"Bonjour Bravo,\n{intro} inscrit sur la liste d'attente."
    assert note("BRAVO").level == "warning"
    assert note("CHARLIE").body == f"Bonjour Kouame Yao Jean,\n{intro} déclaré(e) non admis(e)."
    assert note("CHARLIE").level == "danger"
    assert not any(ch.isdigit() for n in Notification.query.all() for ch in n.body)  # no grade

    # The student sees it in color, on his home page and his notifications; his message only.
    home = _student_logs_in(client, db, rows["ALPHA"].candidate)
    assert "list-group-item-success" in home and "déclarée admise" in home and "list-group-item-danger" not in home
    html = client.get("/candidat/notifications").get_data(as_text=True)
    assert "list-group-item-success" in html and "déclarée admise" in html

    # The coach of the class sees the title, never the decision.
    client.post("/logout")
    coach = make_user("coach", "c@test.ci")
    coach.coach_classes = [f"{d.sas_id}:SIG"]
    db.session.commit()
    login(client, coach)
    for url in ("/campus/notifications", f"/campus/students/{rows['ALPHA'].candidate_id}"):
        html = client.get(url).get_data(as_text=True)
        assert "Résultat de soutenance" in html and "réservé à l'étudiant" in html, url
        assert "déclarée admise" not in html, url


def test_all_individual_minutes_and_waitlist_label(app, db, client, admin):
    d, rows = _defense(db)
    rows["ALPHA"].sheet = {"decision": "waitlist", "decision_note": "Rang 2"}
    rows["BRAVO"].sheet = rows["CHARLIE"].sheet = {"decision": "rejected"}
    db.session.commit()
    login(client, admin)
    html = client.get(f"/jury/soutenances/{d.id}/proces-verbaux").get_data(as_text=True)
    assert html.count("data-fit-page") == 3 and html.index("Alpha") < html.index("Bravo") < html.index("Charlie")
    assert "☑ Liste d&#39;attente" in html and "Chaque membre du jury" not in html
    single = client.get(f"/jury/sheets/{rows['ALPHA'].id}/print").get_data(as_text=True)
    assert single.count("data-fit-page") == 1 and "Alpha" in single and "Bravo" not in single
    client.post(f"/soutenances/{d.id}/resultats")
    assert "Liste d&#39;attente : Rang 2" in _student_logs_in(client, db, rows["ALPHA"].candidate)


def test_public_results_link(app, db, client, admin):
    d, rows = _defense(db)
    for name, decision in (("ALPHA", "admitted"), ("BRAVO", "waitlist"), ("CHARLIE", "rejected")):
        rows[name].sheet = {"decision": decision}
    db.session.commit()
    login(client, admin)
    page, link = f"/soutenances/{d.id}/jury-global", f"/soutenances/{d.id}/lien-public"
    assert "Générer le lien public" in client.get(page).get_data(as_text=True)
    client.post(link, data={"action": "create"})
    db.session.refresh(d)
    url = f"/resultats/{d.public_token}"
    assert d.public_enabled and "Actif" in client.get(page).get_data(as_text=True)
    rows["CHARLIE"].sheet = {}  # no decision yet: open before sending, but nothing to show for him
    db.session.commit()
    charlie = {"matricule": rows["CHARLIE"].candidate.matricule}
    assert "pas encore disponible" in client.get(url, query_string=charlie).get_data(as_text=True)
    assert "result-admitted" in client.get(url, query_string={"matricule": rows["ALPHA"].candidate.matricule}).get_data(as_text=True)
    rows["CHARLIE"].sheet = {"decision": "rejected"}
    db.session.commit()
    client.post(f"/soutenances/{d.id}/resultats")
    assert url in client.get(page).get_data(as_text=True)
    client.post("/logout")

    assert client.get(f"/resultats/{d.id}").status_code == 404 and client.get(url + "x").status_code == 404
    alpha = client.get(url, query_string={"matricule": rows["ALPHA"].candidate.matricule.lower()}).get_data(as_text=True)
    assert "result-admitted" in alpha and "déclarée" in alpha and "mercredi 11 novembre 2026" in alpha
    assert "Photo non fournie" in alpha and "Classe SAS" in alpha
    bravo = client.get(url, query_string={"matricule": rows["BRAVO"].candidate.matricule}).get_data(as_text=True)
    assert "result-waitlist" in bravo and "rappelé," in bravo and "mercredi 11" not in bravo
    assert "result-rejected" in client.get(url, query_string={"matricule": rows["CHARLIE"].candidate.matricule}).get_data(as_text=True)
    assert "Aucun candidat" in client.get(url, query_string={"matricule": "26CAND999999"}).get_data(as_text=True)

    login(client, admin)
    client.post(link, data={"action": "disable"})
    assert client.get(url).status_code == 404
    client.post(link, data={"action": "enable"})
    assert client.get(url).status_code == 200
    client.post(f"/soutenances/{d.id}/resultats", data={"withdraw": "1"})
    assert client.get(url).status_code == 200  # independent of sending the results to the students
    client.post(link, data={"action": "delete"})
    assert client.get(url).status_code == 404
    client.post(link, data={"action": "create"})
    db.session.refresh(d)
    assert f"/resultats/{d.public_token}" != url and client.get(f"/resultats/{d.public_token}").status_code == 200
