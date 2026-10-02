"""python manage.py <command>  ==  flask <command>  (create-admin, seed, sync-kobo, list-users, db ...)."""
from flask.cli import FlaskGroup

from app import create_app

cli = FlaskGroup(create_app=create_app)

if __name__ == "__main__":
    cli()
