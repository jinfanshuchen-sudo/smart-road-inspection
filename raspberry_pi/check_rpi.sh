#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

echo "Project: $PROJECT_DIR"
echo
echo "[Python]"
python3 --version

echo
echo "[Network]"
hostname -I || true
ip route || true

echo
echo "[Files]"
test -f drone_mission_service.py && echo "drone_mission_service.py exists"
test -f dashboard/index.html && echo "dashboard/index.html exists"
test -f crack_detector.py && echo "crack_detector.py exists"

echo
echo "[Imports]"
if [ -f ".venv/bin/activate" ]; then
  source .venv/bin/activate
fi
python - <<'PY'
checks = ["flask", "cv2", "numpy", "pyhulax"]
for name in checks:
    try:
        module = __import__(name)
        version = getattr(module, "__version__", "ok")
        print(f"{name}: {version}")
    except Exception as exc:
        print(f"{name}: ERROR {exc}")
PY

echo
echo "[Drone ping]"
if ping -c 2 192.168.100.1 >/dev/null 2>&1; then
  echo "Drone IP 192.168.100.1 is reachable."
else
  echo "Drone IP 192.168.100.1 is not reachable. Connect Raspberry Pi Wi-Fi to the drone before flight tests."
fi
