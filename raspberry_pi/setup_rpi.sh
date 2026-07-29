#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

echo "[1/5] Installing Raspberry Pi system packages..."
sudo apt update
sudo apt install -y \
  python3-venv \
  python3-pip \
  python3-opencv \
  python3-av \
  libgl1 \
  libglib2.0-0

echo "[2/5] Creating Python virtual environment..."
python3 -m venv --system-site-packages .venv

echo "[3/5] Installing Python packages..."
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install -r raspberry_pi/requirements-rpi.txt

echo "[4/5] Preparing media folders..."
mkdir -p media/photos media/crack_results

echo "[5/5] Running basic import check..."
python - <<'PY'
import flask
import cv2
import numpy
import pyhulax
print("Flask:", flask.__version__)
print("OpenCV:", cv2.__version__)
print("NumPy:", numpy.__version__)
print("pyhulax import ok")
PY

echo
echo "Setup finished."
echo "Start backend with: bash raspberry_pi/start_backend.sh"
