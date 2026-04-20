#!/usr/bin/env bash
# Quick setup script for local development.
set -e

echo "==> Creating virtual environment..."
python3.11 -m venv .venv
source .venv/bin/activate

echo "==> Installing dependencies..."
pip install -e ".[dev]"

echo "==> Creating .env from example..."
if [ ! -f .env ]; then
  cp .env.example .env
  echo "    .env created — edit it with your credentials"
fi

echo "==> Creating secrets/ directory..."
mkdir -p secrets
echo "    Place your Google service account JSON at secrets/google-service-account.json"

echo "==> Initialising database..."
efanxp status 2>/dev/null || true

echo ""
echo "Done. Next steps:"
echo "  1. Edit .env with your Google Calendar ID and API keys"
echo "  2. Place google-service-account.json in secrets/"
echo "  3. Run: efanxp sources list"
echo "  4. Run: efanxp sync --all --dry-run"
