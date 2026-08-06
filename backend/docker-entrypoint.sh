#!/bin/sh
set -e

# Fix permissions for data directory if it exists
if [ -d "/data" ]; then
    echo "Fixing permissions for /data directory..."
    # Ensure experiments directory exists and has correct permissions
    mkdir -p /data/experiments
    chown -R appuser:appuser /data /data/experiments # Ensure appuser owns the data
    chmod -R ug+rwX,o+rX /data /data/experiments # Ensure appuser can read/write and others can read
    echo "Permissions fixed for /data directory"
fi

# Execute the main command
exec "$@"
