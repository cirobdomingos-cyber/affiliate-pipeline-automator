#!/bin/sh
# Container entrypoint.
#
# Railway injects $PORT at runtime. Render the nginx config template by
# substituting $PORT (the only variable in the template), then hand off
# to supervisord to manage uvicorn + streamlit + nginx.
set -eu

: "${PORT:=8080}"
export PORT

envsubst '${PORT}' \
    < /etc/nginx/templates/default.conf.template \
    > /etc/nginx/sites-available/default

# Debian's default nginx.conf already includes sites-enabled/default which
# symlinks to sites-available/default, so we don't need to touch the main
# config. Verify syntax before handing off so bad configs fail fast with a
# clear error in Railway logs rather than a supervisord restart loop.
nginx -t

exec supervisord -c /etc/supervisor/conf.d/supervisord.conf
