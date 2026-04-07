#!/usr/bin/env bash
set -euo pipefail

STACK_DIR="/opt/stacks/bcferries"
CONTAINER="dockge"

FILES=(
    app.py
    ais.py
    data.py
    scraper.py
    requirements.txt
    Dockerfile
    compose.yaml
    www/index.html
    www/terminal.html
    www/route.html
    www/map.html
    www/css/style.css
    www/js/magic.js
)

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Deploying from $REPO_DIR to $CONTAINER:$STACK_DIR"

for f in "${FILES[@]}"; do
    if [ -f "$REPO_DIR/$f" ]; then
        incus file push "$REPO_DIR/$f" "$CONTAINER$STACK_DIR/$f"
    fi
done

echo "Files pushed. Rebuilding container..."
incus exec "$CONTAINER" -- bash -c "cd $STACK_DIR && docker compose up -d --build"

echo "Done."
