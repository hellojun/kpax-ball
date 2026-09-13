#!/usr/bin/env bash
# KPAX Ball — deploy to kpax (kpax.bout.network, Alibaba Cloud HK)
#
# Usage:
#   ./deploy.sh backend   # push code → ssh pull + pip install + alembic + restart
#   ./deploy.sh auth      # local vite build → rsync dist → restart
#   ./deploy.sh all       # backend then auth
#
# Server layout:
#   ssh alias : kpax        (~/.ssh/config; root user)
#   repo      : ~/kpax-ball  (= /root/kpax-ball)
#   service   : systemd unit kpax-ball (uvicorn, bare metal venv, runs as root)
#   nginx     : kpax.bout.network → 127.0.0.1:8000 (TLS via certbot)
#
# Migration history: 2026-05-10 moved off AWS bout (US Ohio) to Alibaba HK
# because PM CLOB write endpoints (/order, /cancel) geoblock US-region IPs.
# HK is on PM's allowlist + better latency for CN users. See git log.

set -euo pipefail

REMOTE=kpax
REMOTE_DIR='~/kpax-ball'

deploy_backend() {
  echo "==> push to origin"
  git push

  echo "==> ssh: pull + pip install + alembic upgrade + restart"
  # Order matters: migrate BEFORE restart so uvicorn comes up against the
  # right schema (postgres ALTER TABLE ADD COLUMN is non-locking on small
  # tables, so it's safe to run while the old uvicorn is still serving).
  # alembic must run from backend/ with PYTHONPATH=. so env.py can do
  # `from app.config import settings` without a packaging install.
  ssh "$REMOTE" "set -e; cd $REMOTE_DIR && \
    git pull --ff-only && \
    backend/.venv/bin/pip install -q -r backend/requirements.txt && \
    ( cd backend && PYTHONPATH=. .venv/bin/alembic upgrade head ) && \
    systemctl restart kpax-ball && \
    sleep 2 && systemctl is-active kpax-ball"
}

deploy_auth() {
  echo "==> build auth-page"
  ( cd auth-page && npm run build )

  echo "==> rsync dist/ to $REMOTE"
  rsync -avz --delete auth-page/dist/ "$REMOTE:$REMOTE_DIR/auth-page/dist/"

  echo "==> restart backend (re-mount /auth)"
  ssh "$REMOTE" 'systemctl restart kpax-ball && sleep 2 && systemctl is-active kpax-ball'
}

case "${1:-}" in
  backend) deploy_backend ;;
  auth)    deploy_auth ;;
  all)     deploy_backend; deploy_auth ;;
  *)       echo "usage: $0 {backend|auth|all}" >&2; exit 1 ;;
esac
