# AquaMind - demarre Mosquitto et InfluxDB en mode natif Windows (sans Docker).
# Usage (depuis la racine du projet) :  powershell -ExecutionPolicy Bypass -File scripts\start-services.ps1
# - Les processus tournent en arriere-plan (fenetre cachee), logs dans logs\
# - Premier lancement d'InfluxDB : "influx setup" non interactif avec les valeurs du .env
# - MQTT : reutilise le service Windows 'Mosquitto Broker' s'il ecoute deja sur 1883 ;
#   sinon tente de le demarrer, puis mosquitto.exe (config du projet), puis amqtt (.venv)
# - Si STORAGE_BACKEND=sqlite dans .env, InfluxDB n'est pas demarre.
# (Fichier enregistre en UTF-8 AVEC BOM : indispensable pour les accents sous Windows PowerShell 5.1.)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Logs = Join-Path $Root "logs"
New-Item -ItemType Directory -Force $Logs | Out-Null

# --- Lecture du .env (sans jamais afficher les secrets) ----------------------
$EnvFile = Join-Path $Root ".env"
if (-not (Test-Path $EnvFile)) {
    Write-Host "KO  .env absent. Lancer d'abord : .\.venv\Scripts\python scripts\init_env.py" -ForegroundColor Red
    exit 1
}
$Cfg = @{}
foreach ($line in Get-Content $EnvFile) {
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') { $Cfg[$Matches[1]] = $Matches[2] }
}
function Get-Cfg([string]$Key, [string]$Default) { if ($Cfg.ContainsKey($Key) -and $Cfg[$Key]) { $Cfg[$Key] } else { $Default } }

$MqttPort = [int](Get-Cfg "MQTT_PORT" "1883")
$InfluxUrl = Get-Cfg "INFLUXDB_URL" "http://localhost:8086"
$InfluxPort = ([Uri]$InfluxUrl).Port
$Storage = Get-Cfg "STORAGE_BACKEND" "influx"

# --- Utilitaires -------------------------------------------------------------
function Test-Port([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne(500)) { $client.EndConnect($iar); return $true }
        return $false
    } catch { return $false } finally { $client.Close() }
}

function Wait-Port([int]$Port, [int]$TimeoutSec = 30) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-Port $Port) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

$WingetLinks = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links"

function Find-Exe([string]$Name, [string[]]$Candidates) {
    $Candidates = @($Candidates) + @((Join-Path $WingetLinks "$Name.exe"))
    foreach ($c in $Candidates) { if ($c -and (Test-Path $c)) { return (Resolve-Path $c).Path } }
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    # Paquets portables winget (PATH pas encore recharge dans ce terminal)
    $pkgRoot = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
    if (Test-Path $pkgRoot) {
        $hit = Get-ChildItem -Path $pkgRoot -Filter "$Name.exe" -Recurse -Depth 3 -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    return $null
}

function Start-Bg([string]$Name, [string]$Exe, [string[]]$ArgList) {
    $out = Join-Path $Logs "$Name.log"
    $err = Join-Path $Logs "$Name.err.log"
    $p = Start-Process -FilePath $Exe -ArgumentList $ArgList -WorkingDirectory $Root `
        -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
    Set-Content -Path (Join-Path $Logs "$Name.pid") -Value $p.Id -Encoding ascii
    return $p
}

$AllOk = $true

# --- MQTT : service Windows "Mosquitto Broker" > mosquitto.exe > amqtt -------
# Le service installe par Mosquitto (config par defaut : 127.0.0.1:1883, anonyme en local)
# suffit : le frontend passe par FastAPI /ws/live, le port 9001 n'est pas necessaire.
$MosqService = Get-Service -Name "mosquitto" -ErrorAction SilentlyContinue
if (Test-Port $MqttPort) {
    if ($MosqService -and $MosqService.Status -eq "Running") {
        Write-Host "OK  Mosquitto : service Windows déjà actif (localhost:$MqttPort)" -ForegroundColor Green
    } else {
        Write-Host "OK  MQTT déjà actif sur localhost:$MqttPort" -ForegroundColor Green
    }
} else {
    $label = $null
    # 1) Service Windows installe mais arrete : tenter de le demarrer
    if ($MosqService) {
        try {
            Start-Service -Name "mosquitto" -ErrorAction Stop
            if (Wait-Port $MqttPort 30) { Write-Host "OK  Mosquitto : service Windows démarré (localhost:$MqttPort)" -ForegroundColor Green; $label = "service" }
        } catch {
            Write-Host "..  Impossible de démarrer le service Mosquitto (droits admin requis ?) -> secours" -ForegroundColor Yellow
        }
    }
}
if (-not (Test-Port $MqttPort)) {
    # 2) mosquitto.exe en processus utilisateur avec la config du projet, 3) amqtt en dernier recours
    $Mosq = Find-Exe "mosquitto" @("$env:ProgramFiles\mosquitto\mosquitto.exe", "${env:ProgramFiles(x86)}\mosquitto\mosquitto.exe", (Join-Path $Root "tools\mosquitto\mosquitto.exe"))
    $Amqtt = Join-Path $Root ".venv\Scripts\amqtt.exe"
    $label = $null
    if ($Mosq) {
        $conf = Join-Path $Root "infra\mosquitto\mosquitto.windows.conf"
        Start-Bg "mosquitto" $Mosq @("-c", "`"$conf`"", "-v") | Out-Null
        $label = "Mosquitto"
    } elseif (Test-Path $Amqtt) {
        Write-Host "..  mosquitto.exe introuvable -> secours amqtt (broker Python)" -ForegroundColor Yellow
        Start-Bg "amqtt" $Amqtt @() | Out-Null
        $label = "amqtt"
    } else {
        Write-Host "KO  Ni mosquitto.exe ni amqtt trouves. Installer Mosquitto (voir README) ou : .\.venv\Scripts\python -m pip install amqtt" -ForegroundColor Red
        $label = $null
        $AllOk = $false
    }
    if ($label) {
        if (Wait-Port $MqttPort 30) { Write-Host "OK  $label sur localhost:$MqttPort" -ForegroundColor Green }
        else { Write-Host "KO  $label ne repond pas sur $MqttPort (voir logs\)" -ForegroundColor Red; $AllOk = $false }
    }
}

# --- InfluxDB ----------------------------------------------------------------
if ($Storage -eq "sqlite") {
    Write-Host "--  STORAGE_BACKEND=sqlite : InfluxDB non demarre (secours SQLite)" -ForegroundColor Cyan
} elseif (Test-Port $InfluxPort) {
    Write-Host "OK  InfluxDB deja actif sur $InfluxUrl" -ForegroundColor Green
} else {
    $InfluxDir = Join-Path $Root "tools\influxdb"
    $Influxd = Find-Exe "influxd" @((Join-Path $InfluxDir "influxd.exe"))
    $InfluxCli = Find-Exe "influx" @((Join-Path $InfluxDir "influx.exe"))
    if (-not $Influxd) {
        Write-Host "KO  influxd.exe introuvable (attendu dans tools\influxdb\). Voir README, ou STORAGE_BACKEND=sqlite." -ForegroundColor Red
        $AllOk = $false
    } else {
        $DataDir = Join-Path $Root "data\influx"
        New-Item -ItemType Directory -Force $DataDir | Out-Null
        $Bolt = Join-Path $DataDir "influxd.bolt"
        $FirstRun = -not (Test-Path $Bolt)
        Start-Bg "influxd" $Influxd @("--bolt-path", "`"$Bolt`"", "--engine-path", "`"$(Join-Path $DataDir 'engine')`"",
            "--http-bind-address", "127.0.0.1:$InfluxPort", "--reporting-disabled") | Out-Null  # 127.0.0.1 : pas d'alerte pare-feu
        if (Wait-Port $InfluxPort 30) {
            Write-Host "OK  InfluxDB sur $InfluxUrl" -ForegroundColor Green
            if ($FirstRun) {
                if (-not $InfluxCli) {
                    Write-Host "KO  influx.exe (CLI) introuvable : setup initial impossible." -ForegroundColor Red; $AllOk = $false
                } else {
                    Start-Sleep -Seconds 2
                    & $InfluxCli setup --host $InfluxUrl --org (Get-Cfg "INFLUXDB_ORG" "aquamind") --bucket (Get-Cfg "INFLUXDB_BUCKET" "farm") `
                        --username (Get-Cfg "INFLUXDB_ADMIN_USER" "admin") --password $Cfg["INFLUXDB_ADMIN_PASSWORD"] `
                        --token $Cfg["INFLUXDB_TOKEN"] --retention 30d --force | Out-Null
                    if ($LASTEXITCODE -eq 0) { Write-Host "OK  InfluxDB initialise (org/bucket/token du .env)" -ForegroundColor Green }
                    else { Write-Host "KO  'influx setup' a echoue (voir ci-dessus)" -ForegroundColor Red; $AllOk = $false }
                }
            }
        } else {
            Write-Host "KO  InfluxDB ne repond pas sur $InfluxPort apres 30 s (voir logs\influxd.err.log)" -ForegroundColor Red
            $AllOk = $false
        }
    }
}

if ($AllOk) { Write-Host "`nServices prets. Verification complete : .\.venv\Scripts\python scripts\check_services.py" -ForegroundColor Green; exit 0 }
else { Write-Host "`nAu moins un service est KO (voir ci-dessus et logs\)." -ForegroundColor Red; exit 1 }
