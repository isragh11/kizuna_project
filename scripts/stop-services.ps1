# AquaMind - arrete les services natifs lances par start-services.ps1 (PID dans logs\*.pid).
# Le service Windows "Mosquitto Broker" n'est pas touche : il est utilise tel quel.
# Usage :  powershell -ExecutionPolicy Bypass -File scripts\stop-services.ps1
$Root = Split-Path -Parent $PSScriptRoot
$Logs = Join-Path $Root "logs"

foreach ($name in @("mosquitto", "amqtt", "influxd")) {
    $pidFile = Join-Path $Logs "$name.pid"
    if (-not (Test-Path $pidFile)) { continue }
    $procId = [int](Get-Content $pidFile -TotalCount 1)
    $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
    if ($proc) {
        Stop-Process -Id $procId -Force
        Write-Host "OK  $name arrete (PID $procId)" -ForegroundColor Green
    } else {
        Write-Host "--  $name ne tournait plus (PID $procId)"
    }
    Remove-Item $pidFile -Force
}
