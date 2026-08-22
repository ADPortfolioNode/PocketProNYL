#!/bin/sh
set -e

# Permission repair only works as root. Compose often starts the backend as
# root then drops to appuser; CI smoke tests run the image as appuser.
if [ "$(id -u)" = "0" ] && [ -d "/data" ]; then
    echo "Fixing permissions for /data directory..."
    mkdir -p /data/experiments /home/appuser /app/.cache/chroma
    chown -R appuser:appuser /data /data/experiments /home/appuser /app/.cache/chroma
    chmod -R ug+rwX,o+rX /data /data/experiments /home/appuser /app/.cache/chroma
    echo "Permissions fixed for /data directory"
fi

export HOME=/app
exec "$@"
