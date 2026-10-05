#!/usr/bin/env bash
# Production entrypoint: web server + background worker in ONE service (so only one Render service is needed).
#   RUN_WORKER=false  -> web only (use this when the worker runs as its own Render "Background Worker")
# If either process dies the script exits, so Render restarts the whole service instead of leaving a zombie.
set -o errexit

PORT="${PORT:-8000}"

pids=()
cleanup() { kill "${pids[@]}" 2>/dev/null || true; wait 2>/dev/null || true; }
trap cleanup SIGTERM SIGINT

if [ "${RUN_WORKER:-true}" != "false" ]; then
  python manage.py qcluster &
  pids+=($!)
fi

gunicorn config.wsgi:application \
  --bind "0.0.0.0:${PORT}" \
  --workers "${WEB_CONCURRENCY:-2}" --threads "${WEB_THREADS:-2}" --worker-class gthread \
  --timeout "${WEB_TIMEOUT:-120}" --graceful-timeout 30 \
  --worker-tmp-dir /dev/shm \
  --access-logfile - --error-logfile - &
pids+=($!)

# wait for whichever exits first, then take everything down
wait -n "${pids[@]}" || true
status=$?
cleanup
exit "$status"
