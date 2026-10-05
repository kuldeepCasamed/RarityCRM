#!/usr/bin/env bash
# Render build step (also fine to run by hand). Fails fast on any error, so a bad migration blocks the deploy
# and the previous version keeps running. Doing this at build time (not start) keeps wake-ups from sleep fast.
# Works on the free plan, which has no pre-deploy command and no shell.
set -o errexit

pip install --upgrade pip
pip install -r requirements.txt
python manage.py collectstatic --noinput

if [ "${RUN_MIGRATIONS_ON_BUILD:-true}" != "false" ]; then
  python manage.py migrate --noinput
  python manage.py setup_schedules
  python manage.py bootstrap_admin
fi
