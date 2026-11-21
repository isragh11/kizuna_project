# AquaMind 💧⚡🌱

Plateforme d'aide à la décision **eau × énergie × agriculture** — hackathon KIZUNA 2026 (ESPRIT × JICA).

- Capteurs IoT simulés en streaming (MQTT → InfluxDB)
- Prévision de l'ET0 (LightGBM) + besoins des cultures (FAO-56)
- Détection de fuites : IsolationForest (validé sur BattLeDIM) + River HalfSpaceTrees (en ligne)
- Optimisation eau/énergie sur 24 h (PuLP), explications par les variables duales
- Dashboard React temps réel

> ⚠️ Les valeurs économiques (`data/static/economics.json`) sont des **hypothèses** ; tout chiffre d'impact affiché est une **estimation simulée**.

Spécification complète : [PROMPT_AQUAMIND.md](PROMPT_AQUAMIND.md).

---

## Prérequis

- Python 3.11, Node.js 20+
- **Soit** Docker Desktop (WSL2), **soit** le mode natif Windows (Mosquitto + InfluxDB installés localement)
- VS Code + extensions Python et **Jupyter**

## Installation

### 1. Fichier `.env`

Génère `.env` depuis `.env.example` et remplit `INFLUXDB_TOKEN` / `INFLUXDB_ADMIN_PASSWORD` avec `secrets.token_hex(32)` (jamais affichés). À lancer après avoir créé `.venv` (étape 2) :

```powershell
.\.venv\Scripts\python scripts\init_env.py
```
```bash
.venv/bin/python scripts/init_env.py
```

> Ne changez plus le token une fois InfluxDB initialisé : il garde l'ancien (voir « Réinitialiser » plus bas).

### 2. Environnements Python

**`.venv` (backend, simulateur, consumer)**

PowerShell :
```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements.txt
```
bash :
```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

**`.venv-ml` (notebooks) + kernel Jupyter « AquaMind ML »**

PowerShell :
```powershell
py -3.11 -m venv .venv-ml
.\.venv-ml\Scripts\python -m pip install --upgrade pip
.\.venv-ml\Scripts\python -m pip install -r requirements-ml.txt
.\.venv-ml\Scripts\python -m ipykernel install --user --name aquamind-ml --display-name "AquaMind ML"
```
bash :
```bash
python3.11 -m venv .venv-ml
.venv-ml/bin/python -m pip install --upgrade pip
.venv-ml/bin/python -m pip install -r requirements-ml.txt
.venv-ml/bin/python -m ipykernel install --user --name aquamind-ml --display-name "AquaMind ML"
```

> **Erreur `CERTIFICATE_VERIFY_FAILED` avec pip ?** (antivirus/proxy qui inspecte le HTTPS)
> Le pip fourni avec Python 3.11 est trop ancien pour utiliser le magasin de certificats Windows.
> Récupérer un pip récent via un autre Python (ex. 3.13, pip ≥ 24.2) puis l'installer hors-ligne :
> ```powershell
> py -3.13 -m pip download --no-deps -d $env:TEMP\pipwhl pip==24.3.1
> .\.venv\Scripts\python -m pip install --no-index (Get-ChildItem $env:TEMP\pipwhl\pip-*.whl).FullName
> ```
> Ne pas utiliser `--trusted-host` (désactive la vérification TLS).

### 3. Infrastructure (Mosquitto + InfluxDB) — deux modes, même code

Le code Python ne lit que `.env` : il ne sait pas si les services tournent sous Docker ou en natif.

#### Démarrer avec Docker

```bash
docker compose up -d
docker compose ps          # les deux services doivent être "healthy"
```
- MQTT : `localhost:1883` (WebSocket : `9001`)
- InfluxDB : http://localhost:8086 (identifiants du `.env`)

> Si vous changez `INFLUXDB_TOKEN` après le premier démarrage : `docker compose down -v` puis `docker compose up -d` (efface les données).

#### Démarrer sans Docker (Windows natif)

**Installation (une seule fois)**

```powershell
# InfluxDB 2.7 serveur + CLI (paquets portables, pas de droits admin)
winget install --id InfluxData.InfluxDB.OSS --exact --source winget
winget install --id InfluxData.InfluxDB.CLI --exact --source winget
# Mosquitto 2 (installeur officiel Eclipse : accepter la fenêtre UAC)
winget install --id EclipseFoundation.Mosquitto --exact --source winget --accept-package-agreements --accept-source-agreements
```

> `--source winget` évite l'erreur `0x8a15005e` (certificat) causée par l'inspection HTTPS de l'antivirus sur la source `msstore`.
> Si le téléchargement échoue quand même pour une raison de certificat : suspendre l'agent HTTPS d'Avast quelques minutes.

Pas de winget ? Utilisez les binaires officiels : InfluxDB 2.7 pour Windows (`influxd.exe` + `influx.exe`) à dézipper dans `tools\influxdb\`, et l'installeur Windows de mosquitto.org.

**MQTT = le service Windows « Mosquitto Broker », utilisé tel quel.** L'installeur crée ce service (démarrage automatique). Sans fichier de config, Mosquitto 2 écoute sur `127.0.0.1:1883` en acceptant les clients anonymes locaux : c'est tout ce dont le projet a besoin. Le port WebSocket 9001 n'est pas nécessaire, car le frontend passe par FastAPI `/ws/live`. Ne pas arrêter ce service.

`start-services.ps1` choisit le broker MQTT dans cet ordre :
1. le port 1883 répond déjà → « Mosquitto : service Windows déjà actif », rien n'est lancé ;
2. sinon, il démarre le service `mosquitto` (il peut falloir des droits admin) ;
3. sinon, il lance `mosquitto.exe -c infra\mosquitto\mosquitto.windows.conf` en processus utilisateur ;
4. en dernier recours, il lance le broker Python `amqtt` s'il est installé dans `.venv`.

**À chaque session**

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-services.ps1   # démarre en arrière-plan, OK/KO par service
.\.venv\Scripts\python scripts\check_services.py                     # /health, écriture+lecture Influx, pub/sub MQTT
powershell -ExecutionPolicy Bypass -File scripts\stop-services.ps1    # arrête InfluxDB (le service Windows Mosquitto reste actif)
```

- Logs : `logs\influxd.log` (+ `.err.log`), et `logs\mosquitto.log` seulement si le script lance Mosquitto lui-même
- Données InfluxDB : `data\influx\` (ignoré par git). Au premier démarrage, le script exécute `influx setup` avec l'org, le bucket, l'utilisateur, le mot de passe et le token du `.env`.
- **Réinitialiser InfluxDB** (ex. token changé) : `stop-services.ps1`, supprimer `data\influx\`, relancer `start-services.ps1`.

**Solutions de secours**

| Problème | Secours |
|---|---|
| InfluxDB ne s'installe pas | `STORAGE_BACKEND=sqlite` dans `.env` → le consumer et `/history` utilisent `data/stream.db` |
| Mosquitto ne s'installe pas | `.\.venv\Scripts\python -m pip install amqtt` → `start-services.ps1` lance le broker Python en dernier recours ; ou `--http-fallback` du simulateur |
| Antivirus (Avast) bloque un téléchargement | Suspendre l'agent HTTPS quelques minutes. **Ne jamais** désactiver la vérification TLS (`--trusted-host`, `verify=False`…) |

## Vérifications rapides

```powershell
.\.venv\Scripts\python scripts\check_services.py
```

```powershell
.\.venv\Scripts\python -c "from aquamind_core.config import load_farm; print(load_farm())"
```
```bash
.venv/bin/python -c "from aquamind_core.config import load_farm; print(load_farm())"
```

## Collecte des données (Phase 2)

```powershell
.\.venv\Scripts\python -m ingest.run_all                                   # en ligne (cache disque réutilisé)
$env:OFFLINE_MODE="true"; .\.venv\Scripts\python -m ingest.run_all; Remove-Item Env:OFFLINE_MODE   # cache uniquement
```
```bash
.venv/bin/python -m ingest.run_all
OFFLINE_MODE=true .venv/bin/python -m ingest.run_all
```

- Sources sans clé : Open-Meteo (archive 2015–2025 + prévision 7 j), PVGIS (profil PV 2023), SoilGrids (texture → réserve utile), BattLeDIM (`data/raw/battledim/`, record Zenodo 4017658/4017659).
- Chaque appel passe par `ingest/http_cache.py` : cache JSON dans `data/cache/`, 3 tentatives, dernière réponse valide servie si le réseau tombe.
- **TLS et antivirus** : `truststore` fait vérifier les certificats par le magasin Windows, ce qui rend compatible l'inspection HTTPS d'Avast. La vérification n'est jamais désactivée. Il est activé à l'import de `aquamind_core`.
- Sorties : `data/processed/*.parquet` (météo, prévision, PV, sol, BattLeDIM).

## Entraînement (Phase 3) : notebooks exécutés à la main

Ouvrir chaque notebook dans VS Code, choisir le kernel **« AquaMind ML »**, puis *Run All*, dans l'ordre :

| Notebook | Produit dans `models/artifacts/` | Durée |
|---|---|---|
| `01_eda_weather.ipynb` | `eda_weather_summary.json` (pas de modèle) | < 1 min |
| `02_train_et0_lightgbm.ipynb` | `et0_lgbm.txt`, `et0_features.json`, `et0_metrics.json` | 1–3 min |
| `03_leak_detection_battledim.ipynb` | `iforest.joblib`, `leak_features.json`, `leak_threshold.json`, `leak_metrics.json` | 1–2 min |
| `04_river_calibration.ipynb` | `river_config.json`, `river_metrics.json` | 2–5 min |

Prérequis : `python -m ingest.run_all` (fichiers parquet). Expériences MLflow : `mlflow ui --backend-store-uri ./mlruns` (depuis `.venv-ml`).

## Arborescence

```
aquamind_core/   logique métier (schémas, config, agronomie, optimiseur…)
ingest/          collecte des API (Open-Meteo, PVGIS, SoilGrids) avec cache disque
notebooks/       entraînement des modèles (exécutés dans le kernel « AquaMind ML »)
models/artifacts modèles exportés, chargés par le backend
simulator/       capteurs simulés (MQTT)
stream/          consumer MQTT → InfluxDB + détection de fuites
api/             FastAPI + WebSocket
web/             dashboard React
data/static/     farm.json, farm_japan.json, crops.json, economics.json
```

## Suivi des expériences

```bash
mlflow ui --backend-store-uri ./mlruns   # depuis .venv-ml
```
