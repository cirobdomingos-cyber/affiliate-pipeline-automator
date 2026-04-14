#!/bin/sh
# Container entrypoint.
#
# Railway injects $PORT at runtime. Render the nginx config template by
# substituting $PORT (the only variable in the template), then hand off
# to supervisord to manage uvicorn + streamlit + nginx.
set -eu

: "${PORT:=8080}"
export PORT

echo "[entrypoint] PORT=$PORT"
echo "[entrypoint] DATA_DIR=${DATA_DIR:-<unset>}"

# Write directly to conf.d/ which is always included by Debian's default
# nginx.conf. Nuke the stock sites-enabled/default so there's no listener
# conflict on port 80.
rm -f /etc/nginx/sites-enabled/default /etc/nginx/conf.d/default.conf

envsubst '${PORT}' \
    < /etc/nginx/templates/default.conf.template \
    > /etc/nginx/conf.d/default.conf

echo "[entrypoint] nginx config rendered:"
cat /etc/nginx/conf.d/default.conf

echo "[entrypoint] nginx -t"
nginx -t

echo "[entrypoint] launching supervisord"
exec supervisord -c /etc/supervisor/conf.d/supervisord.conf
