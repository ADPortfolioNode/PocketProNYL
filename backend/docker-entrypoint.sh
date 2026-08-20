#!/bin/sh
set -e

# Fix permissions for data directory if it exists
if [ -d "/data" ]; then
    echo "Fixing permissions for /data directory..."
    # Ensure experiments directory exists and has correct permissions
    # Also ensure /home/appuser and the HF_HOME cache directory are writable
    mkdir -p /data/experiments /home/appuser /app/.cache/chroma
    chown -R appuser:appuser /data /data/experiments /home/appuser /app/.cache/chroma # Ensure appuser owns the data and its home
    chmod -R ug+rwX,o+rX /data /data/experiments /home/appuser /app/.cache/chroma # Ensure appuser can read/write and others can read
    echo "Permissions fixed for /data directory"
fi

# Ensure HOME is explicitly set for the appuser's session
export HOME=/app

# Execute the main command
exec "$@"
