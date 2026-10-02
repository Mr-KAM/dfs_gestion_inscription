#!/bin/sh
# Idempotent startup: migrate, ensure admin, serve.
set -e
export FLASK_APP=wsgi.py
echo "Applying database migrations..."
flask db upgrade
flask create-admin
exec gunicorn -c gunicorn.conf.py wsgi:app
