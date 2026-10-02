"""CLI: flask create-admin | seed | sync-kobo | list-users (also via manage.py)."""
import random
from datetime import timedelta

import click
from flask import Flask, current_app
from sqlalchemy import func, select

from app.extensions import db
from app.models import Candidate, Room, User, Workstation
from app.models.user import ROLE_ADMIN, ROLE_MOTIVATION, ROLE_TECHNICAL


def ensure_admin() -> str:
    """Idempotent: create the .env admin only if no admin exists yet."""
    if db.session.scalar(select(User.id).where(User.role == ROLE_ADMIN)):
        return "Un administrateur existe déjà, rien à faire."
    email = current_app.config["ADMIN_EMAIL"].strip().lower()
    password = current_app.config["ADMIN_PASSWORD"]
    if not email or not password:
        raise click.ClickException("ADMIN_EMAIL et ADMIN_PASSWORD doivent être définis dans l'environnement.")
    if len(password) < 8:
        raise click.ClickException("ADMIN_PASSWORD doit contenir au moins 8 caractères.")
    if db.session.scalar(select(User.id).where(func.lower(User.email) == email)):
        raise click.ClickException(f"{email} existe déjà avec un autre rôle.")
    user = User(email=email, full_name="Administrateur", role=ROLE_ADMIN, active=True)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return f"Administrateur {email} créé."


def register_cli(app: Flask) -> None:
    @app.cli.command("create-admin")
    def create_admin():
        """Crée l'administrateur défini par ADMIN_EMAIL / ADMIN_PASSWORD."""
        click.echo(ensure_admin())

    @app.cli.command("list-users")
    def list_users():
        """Liste les utilisateurs."""
        for u in db.session.scalars(select(User).order_by(User.role, User.email)):
            click.echo(f"{u.id:>4}  {u.role:<18} {'actif' if u.active else 'inactif':<8} {u.email}")

    @app.cli.command("sync-kobo")
    def sync_kobo():
        """Synchronise les soumissions KoboToolbox."""
        from app.services.kobo_service import KoboError, sync
        try:
            click.echo(f"Synchronisation terminée : {sync()}")
        except KoboError as exc:
            raise click.ClickException(str(exc))

    @app.cli.command("seed")
    @click.option("--force", is_flag=True, help="Autoriser l'exécution en production.")
    @click.option("--password", default="Demo1234!", show_default=True, help="Mot de passe des comptes de démo.")
    def seed(force: bool, password: str):
        """Données de démonstration (jamais lancé automatiquement)."""
        if current_app.config["IS_PRODUCTION"] and not force:
            raise click.ClickException("FLASK_ENV=production : ajoutez --force si c'est vraiment voulu.")
        click.echo(ensure_admin())
        _seed(password)

    def _seed(password: str) -> None:
        from app.services.candidate_service import today

        for email, name, role in [
            ("motivation1@demo.local", "Awa Koné", ROLE_MOTIVATION),
            ("motivation2@demo.local", "Yao Kouassi", ROLE_MOTIVATION),
            ("technique1@demo.local", "Fatou Traoré", ROLE_TECHNICAL),
            ("technique2@demo.local", "Jean Bamba", ROLE_TECHNICAL),
        ]:
            if not db.session.scalar(select(User.id).where(User.email == email)):
                user = User(email=email, full_name=name, role=role)
                user.set_password(password)
                db.session.add(user)

        for room_name in ("Salle A", "Salle B"):
            if not db.session.scalar(select(Room.id).where(Room.name == room_name)):
                room = Room(name=room_name, description="Salle de démonstration")
                room.workstations = [Workstation(computer_number=f"PC-{i:02d}") for i in range(1, 11)]
                db.session.add(room)

        if not db.session.scalar(select(func.count(Candidate.id))):
            first = ["Konan", "Aya", "Ibrahim", "Mariam", "Serge", "Adjoua", "Moussa", "Christelle", "Koffi", "Salimata"]
            last = ["KOUAME", "DIALLO", "OUATTARA", "N'GUESSAN", "TOURE", "YAO", "COULIBALY", "KONE", "BROU", "SANOGO"]
            rng = random.Random(42)
            day = today()
            for i in range(20):
                db.session.add(Candidate(
                    candidate_number=str(i + 1), group_name=f"G{i // 10 + 1}", scheduled_date=day,
                    scheduled_time="08:00" if i < 10 else "10:00", passage_order=i % 10 + 1,
                    full_name=f"{last[i % 10]} {first[(i * 3) % 10]}",
                    birth_date=day - timedelta(days=rng.randint(18 * 365, 30 * 365)),
                    gender="F" if i % 2 else "M", email=f"candidat{i + 1}@demo.local",
                    phone=f"07{rng.randint(10_000_000, 99_999_999)}", city=rng.choice(["Abidjan", "Bouaké", "Yamoussoukro"]),
                    cni=f"CI{rng.randint(100_000_000, 999_999_999)}",
                ))
        db.session.commit()
        click.echo(f"Données de démo prêtes (mot de passe des testeurs : {password}).")
