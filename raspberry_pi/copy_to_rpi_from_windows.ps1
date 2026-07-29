param(
  [string]$PiUser = "afan",
  [string]$PiIp = "192.168.144.182",
  [string]$RemoteDir = "/home/afan/pyhulax-main"
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot

Write-Host "Project: $ProjectDir"
Write-Host "Target : ${PiUser}@${PiIp}:$RemoteDir"
Write-Host
Write-Host "This script uses scp. It does not delete files on the Raspberry Pi."

ssh "${PiUser}@${PiIp}" "mkdir -p '$RemoteDir'"
scp -r `
  "$ProjectDir\dashboard" `
  "$ProjectDir\pyhulax" `
  "$ProjectDir\raspberry_pi" `
  "$ProjectDir\crack_detector.py" `
  "$ProjectDir\drone_mission_service.py" `
  "$ProjectDir\requirements.txt" `
  "$ProjectDir\pyproject.toml" `
  "${PiUser}@${PiIp}:$RemoteDir/"

Write-Host
Write-Host "Copy finished."
Write-Host "Next on Raspberry Pi:"
Write-Host "  cd $RemoteDir"
Write-Host "  bash raspberry_pi/setup_rpi.sh"
