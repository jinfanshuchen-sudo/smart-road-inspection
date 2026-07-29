# Starts the dashboard backend and local MQTT broker for the Hula-Battle LAN demo.
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendPython = Join-Path $root '.venv\Scripts\python.exe'

if (-not (Test-Path $backendPython)) {
  throw "Backend Python was not found: $backendPython"
}

$mqttListening = Get-NetTCPConnection -State Listen -LocalPort 1883 -ErrorAction SilentlyContinue
if (-not $mqttListening) {
  Start-Process -FilePath $backendPython `
    -ArgumentList 'offline_mqtt_broker.py' `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $env:TEMP 'road-warning-offline-mqtt.log') `
    -RedirectStandardError (Join-Path $env:TEMP 'road-warning-offline-mqtt.err.log')
}

$dashboardListening = Get-NetTCPConnection -State Listen -LocalPort 5055 -ErrorAction SilentlyContinue
if (-not $dashboardListening) {
  Start-Process -FilePath $backendPython `
    -ArgumentList 'drone_mission_service.py' `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $env:TEMP 'road-warning-backend.log') `
    -RedirectStandardError (Join-Path $env:TEMP 'road-warning-backend.err.log')
}

Write-Host 'Offline demo services are running.'
Write-Host 'Open: http://127.0.0.1:5055'
