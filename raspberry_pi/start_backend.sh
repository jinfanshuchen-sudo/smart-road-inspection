#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [ ! -f ".venv/bin/activate" ]; then
  echo "Missing .venv. Run: bash raspberry_pi/setup_rpi.sh"
  exit 1
fi

source .venv/bin/activate
export PYHULAX_HOST="${PYHULAX_HOST:-0.0.0.0}"
export PYHULAX_PORT="${PYHULAX_PORT:-5055}"
python drone_mission_service.py
