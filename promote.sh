#!/bin/bash
# Release gate: point :latest at an already-built, already-smoke-tested version tag.
# This RETAGS the existing manifest (docker buildx imagetools) — no rebuild, no new
# bytes, so prod runs the exact multi-arch image you tested. Run ONLY after deploy.sh
# published the tag and you smoke-tested it via docker-compose.staging.yml.
set -euo pipefail

TAG="${1:-}"
if [ -z "$TAG" ]; then
  echo "Usage: ./promote.sh vX.Y.Z" >&2
  echo "  Retags caddismaster/budget-buddy:<tag> -> :latest (no rebuild)." >&2
  exit 1
fi

echo "Promoting caddismaster/budget-buddy:${TAG} -> :latest (retag, no rebuild)..."
docker buildx imagetools create -t caddismaster/budget-buddy:latest "caddismaster/budget-buddy:${TAG}"

echo "Done! :latest now points at ${TAG}."
echo
echo "NOTE: this script is the FALLBACK path. The normal way to ship is to publish a"
echo "GitHub Release — Actions builds, pushes to ghcr, and deploys after you approve."
echo "Use this only if Actions is unavailable, or to get back onto the Docker Hub image."
echo
echo "Deploy on the Droplet:"
echo "  ssh deploy@147.182.219.112"
echo "  cd /opt/budget-buddy && docker compose pull web && docker compose up -d"
