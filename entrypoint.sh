#!/bin/sh
# Idempotent startup: migrate, ensure admin + candidate logins, serve.
set -e
export FLASK_APP=wsgi.py
echo "Applying database migrations..."
flask db upgrade
flask create-admin
flask create-candidate-accounts
exec gunicorn -c gunicorn.conf.py wsgi:app
