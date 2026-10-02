"""Tests run on SQLite in memory by default; set TEST_DATABASE_URL to run them on PostgreSQL."""
import os
import tempfile

import pytest

from app import create_app
from app.extensions import db as _db
from app.models import Candidate, Room, User, Workstation
from app.models.user import ROLE_ADMIN, ROLE_MOTIVATION, ROLE_TECHNICAL

PASSWORD = "Password123!"


@pytest.fixture()
def app():
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": os.environ.get("TEST_DATABASE_URL", "sqlite://"),
        "SECRET_KEY": "test-secret",
        "WTF_CSRF_ENABLED": False,
        "SESSION_COOKIE_SECURE": False,
        "UPLOAD_FOLDER": tempfile.mkdtemp(),
        "ADMIN_EMAIL": "admin@test.ci",
        "ADMIN_PASSWORD": PASSWORD,
    })
    with app.app_context():
        _db.drop_all()
        _db.create_all()
        yield app
        _db.session.remove()
        _db.drop_all()


@pytest.fixture()
def db(app):
    return _db


def make_user(role: str, email: str | None = None, active: bool = True) -> User:
    user = User(email=email or f"{role}@test.ci", full_name=role, role=role, active=active)
    user.set_password(PASSWORD)
    _db.session.add(user)
    _db.session.commit()
    return user


@pytest.fixture()
def admin(app):
    return make_user(ROLE_ADMIN)


@pytest.fixture()
def motivation_user(app):
    return make_user(ROLE_MOTIVATION)


@pytest.fixture()
def technical_user(app):
    return make_user(ROLE_TECHNICAL)


@pytest.fixture()
def client(app):
    return app.test_client()


def login(client, user: User):
    return client.post("/login", data={"email": user.email, "password": PASSWORD})


@pytest.fixture()
def room(app):
    room = Room(name="Salle A")
    room.workstations = [Workstation(computer_number=f"PC-{i:02d}") for i in range(1, 5)]
    _db.session.add(room)
    _db.session.commit()
    return room


def make_candidate(**kwargs) -> Candidate:
    values = {"full_name": "KOUAME Aya", "email": "aya@test.ci", "phone": "0707070707", "cni": "CI001"}
    values.update(kwargs)
    candidate = Candidate(**values)
    _db.session.add(candidate)
    _db.session.commit()
    return candidate


def eligible_candidate(**kwargs) -> Candidate:
    return make_candidate(motivation_completed=True, exam_code=kwargs.pop("exam_code", "DFS-2026-0001"),
                          piece_number="P1", **kwargs)
