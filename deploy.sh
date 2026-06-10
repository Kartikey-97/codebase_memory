#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# deploy.sh — Single-command deployment for Codebase Memory
#
# Usage:
#   ./deploy.sh                      # deploy everything
#   ./deploy.sh --frontend-only      # only frontend
#   ./deploy.sh --backend-only       # only backend
#
# Prerequisites:
#   - firebase CLI installed and authenticated
#   - gcloud CLI installed, authenticated, project set
#   - Node.js 18+ and npm
# ─────────────────────────────────────────────────────────────
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
FRONTEND_DIR="$ROOT_DIR/frontend"

# ── Colors ──────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}▸${NC} $1"; }
warn() { echo -e "${YELLOW}▸${NC} $1"; }
err()  { echo -e "${RED}✗${NC} $1" >&2; exit 1; }

# ── Parse flags ─────────────────────────────────────────────
DEPLOY_FRONTEND=true
DEPLOY_BACKEND=true

case "${1:-}" in
  --frontend-only) DEPLOY_BACKEND=false ;;
  --backend-only)  DEPLOY_FRONTEND=false ;;
esac

# ── Frontend: build + firebase deploy ───────────────────────
if [ "$DEPLOY_FRONTEND" = true ]; then
  log "Building frontend..."
  cd "$FRONTEND_DIR"
  npm ci --silent
  npm run build
  cd "$ROOT_DIR"

  log "Deploying frontend to Firebase Hosting..."
  firebase deploy --only hosting
  echo ""
  log "Frontend deployed ✓"
fi

# ── Backend: trigger Cloud Build ────────────────────────────
if [ "$DEPLOY_BACKEND" = true ]; then
  log "Triggering Cloud Build for backend..."

  # Resolve the current GCP project from gcloud config.
  PROJECT_ID=$(gcloud config get-value project 2>/dev/null)
  if [ -z "$PROJECT_ID" ]; then
    err "No GCP project set. Run: gcloud config set project <PROJECT_ID>"
  fi

  gcloud builds submit \
    --config=cloudbuild.yaml \
    --substitutions=SHORT_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo "manual-$(date +%s)")" \
    "$ROOT_DIR"

  echo ""
  log "Backend deployed to Cloud Run ✓"
fi

echo ""
log "Deployment complete 🚀"
