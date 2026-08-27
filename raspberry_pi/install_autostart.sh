#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="${SUDO_USER:-$USER}"

if [ "$RUN_USER" = "root" ]; then
  echo "Run this script as the normal Raspberry Pi user, without sudo."
  exit 1
fi

if [ ! -x "$PROJECT_DIR/.venv/bin/python" ]; then
  echo "Missing Python environment. Run: bash raspberry_pi/setup_rpi.sh"
  exit 1
fi

install_service() {
  local template="$1"
  local target="$2"
  sed \
    -e "s|__PYHULAX_USER__|$RUN_USER|g" \
    -e "s|__PYHULAX_DIR__|$PROJECT_DIR|g" \
    "$template" | sudo tee "$target" >/dev/null
}

install_service \
  "$PROJECT_DIR/raspberry_pi/pyhulax-mqtt.service.example" \
  /etc/systemd/system/pyhulax-mqtt.service
install_service \
  "$PROJECT_DIR/raspberry_pi/pyhulax-dashboard.service.example" \
  /etc/systemd/system/pyhulax-dashboard.service

sudo systemctl daemon-reload
sudo systemctl enable --now pyhulax-mqtt.service pyhulax-dashboard.service
sudo systemctl status pyhulax-mqtt.service pyhulax-dashboard.service --no-pager
