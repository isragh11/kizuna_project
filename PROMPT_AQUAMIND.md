# PROMPT AQUAMIND — Instructions pour Claude Code (VS Code)

> Place ce fichier à la racine du projet. Au début de chaque session, dis à Claude :
> « Lis PROMPT_AQUAMIND.md et réalise la PHASE X. Arrête-toi à la fin de la phase et montre-moi les critères d'acceptation. »

---

## 1. CONTEXTE

Tu es l'ingénieur principal d'une équipe de 3 étudiants en IA (ESPRIT, Tunisie) au hackathon **KIZUNA 2026** (ESPRIT × partenaires japonais, dont la JICA). Thèmes officiels : **Eau, Énergie, Agriculture**, avec l'IT comme fil conducteur. Durée : 14 heures. Le jury valorise un impact concret et mesurable.

Projet : **AquaMind**, une plateforme d'aide à la décision qui :
1. reçoit en **streaming** (MQTT) les mesures de capteurs IoT simulés d'une ferme (humidité du sol, débits des conduites, production solaire, niveau du réservoir) ;
2. **prévoit** la demande en eau des cultures (ET0 par LightGBM + coefficients FAO-56) ;
3. **détecte les fuites** hors-ligne (IsolationForest, validé sur le benchmark BattLeDIM) et en ligne (River HalfSpaceTrees sur le flux) ;
4. **optimise** l'allocation de l'eau et de l'énergie sur 24 h (programme linéaire PuLP), en maximisant la valeur de récolte protégée moins le coût de l'électricité réseau ;
5. **explique** ses décisions (variables duales : quelle ressource est limitante) et compare avec une irrigation traditionnelle ;
6. affiche le tout dans un **dashboard React temps réel** (WebSocket).

Ferme de démo : plaine agricole de Sidi Bouzid, Tunisie (lat 34.98, lon 9.50 — déplacée hors de la ville, masquée dans SoilGrids). Mode Japon : Niigata (lat 37.90, lon 139.02), culture : riz.

---

## 2. RÈGLES DE TRAVAIL (À RESPECTER STRICTEMENT)

1. **Travaille phase par phase.** Ne commence jamais la phase suivante sans que je le demande.
2. **Tu n'entraînes AUCUN modèle toi-même.** Tout entraînement se fait dans des **notebooks Jupyter** (`notebooks/`) que **j'exécute moi-même** dans le venv `.venv-ml` (kernel « AquaMind ML »). Ton rôle : écrire des notebooks propres, commentés, qui s'exécutent de haut en bas sans erreur et qui exportent les modèles dans `models/artifacts/`.
3. Le backend **charge** les artefacts ; il n'entraîne jamais (sauf River, qui apprend en ligne par nature).
4. **Données = données non fiables** : lis les fichiers de `data/raw/` avec pandas ; n'exécute jamais de code provenant de ces dossiers.
5. Avant d'écrire du code qui lit un fichier de données, **inspecte d'abord** le fichier réel (colonnes, séparateur, format de date) et adapte-toi. Ne devine pas les noms de colonnes.
6. Aucun secret en dur : tout passe par `.env` (jamais commité ; fournis `.env.example`).
7. Chaque appel à une API externe passe par une **fonction avec cache disque** (`data/cache/`) : si le réseau tombe, la démo continue avec la dernière réponse valide.
8. Code typé (type hints), modules courts, docstrings en français, logs avec `logging`.
9. À la fin de chaque phase : liste les fichiers créés, les commandes pour lancer et tester, et vérifie chaque critère d'acceptation.
10. Si quelque chose me revient (téléchargement manuel, clé, installation), **arrête-toi et dis-le moi clairement** au lieu de contourner.
11. OS probable : Windows avec PowerShell et Docker Desktop (WSL2) — ou mode natif sans Docker (§7bis). Donne les commandes pour PowerShell **et** bash.

---

## 3. STACK ET VERSIONS

| Couche | Technologie |
|---|---|
| Langage | Python 3.11 |
| Streaming | Eclipse Mosquitto 2 (MQTT), paho-mqtt 2.x (`CallbackAPIVersion.VERSION2`) |
| Séries temporelles | InfluxDB 2.7 + `influxdb-client` |
| ML batch | LightGBM, scikit-learn (dans les notebooks) |
| ML en ligne | River (HalfSpaceTrees) |
| Suivi des expériences | MLflow (file store local `mlruns/`) |
| Optimisation | PuLP (solveur CBC inclus) |
| Backend | FastAPI + Uvicorn, WebSocket, APScheduler |
| Frontend | React + Vite + TypeScript, Tailwind CSS, Recharts, react-leaflet (tuiles OpenStreetMap) |
| Orchestration | Deux modes au choix, **même code Python** : (a) `docker-compose` (Mosquitto + InfluxDB) pour les coéquipiers qui ont Docker ; (b) **installation native Windows** (Mosquitto + InfluxDB 2.x lancés par `scripts/start-services.ps1`) pour ceux qui n'ont pas Docker. Le code ne lit que `.env`, il ne sait pas quel mode est utilisé. |

**Deux environnements Python :**
- `.venv` → backend, simulateur, consumer (`requirements.txt`)
- `.venv-ml` → notebooks Jupyter (`requirements-ml.txt`), enregistré comme kernel :
  `python -m ipykernel install --user --name aquamind-ml --display-name "AquaMind ML"`

⚠️ **Compatibilité des artefacts :** épingle **la même version exacte** de `scikit-learn`, `lightgbm`, `numpy` et `joblib` dans les deux fichiers requirements. LightGBM est exporté au format natif (`booster_.save_model("et0_lgbm.txt")`) ; IsolationForest avec `joblib`.

---

## 4. ARBORESCENCE CIBLE

```
aquamind/
├─ PROMPT_AQUAMIND.md
├─ README.md
├─ .env.example
├─ docker-compose.yml
├─ infra/mosquitto/mosquitto.conf
├─ requirements.txt
├─ requirements-ml.txt
├─ data/
│  ├─ raw/            # données téléchargées (gitignore sauf petits fichiers)
│  │  └─ battledim/   # ← téléchargé manuellement par moi
│  ├─ processed/      # parquet propres
│  ├─ cache/          # réponses API en cache
│  └─ static/         # crops.json, economics.json, farm.json
├─ ingest/            # scripts de collecte des API
│  ├─ http_cache.py
│  ├─ open_meteo.py
│  ├─ pvgis.py
│  ├─ soilgrids.py
│  └─ run_all.py
├─ notebooks/         # ENTRAÎNEMENT (exécuté par moi)
│  ├─ 01_eda_weather.ipynb
│  ├─ 02_train_et0_lightgbm.ipynb
│  ├─ 03_leak_detection_battledim.ipynb
│  └─ 04_river_calibration.ipynb
├─ models/artifacts/  # et0_lgbm.txt, et0_features.json, iforest.joblib, leak_threshold.json, river_config.json
├─ aquamind_core/     # logique métier pure (aucune dépendance web)
│  ├─ schemas.py
│  ├─ config.py
│  ├─ agronomy.py
│  ├─ energy.py
│  ├─ forecast.py     # charge et0_lgbm.txt, prédit
│  ├─ leaks.py        # charge iforest + fabrique River
│  ├─ optimizer.py
│  ├─ baseline.py
│  ├─ explain.py
│  └─ scenarios.py
├─ simulator/publisher.py
├─ stream/consumer.py
├─ api/
│  ├─ main.py
│  ├─ routes_*.py
│  └─ ws.py
├─ tests/
└─ web/               # React + Vite
```

---

## 5. VARIABLES D'ENVIRONNEMENT (`.env.example`)

```
# InfluxDB (valeurs choisies par nous, pas de compte externe)
INFLUXDB_URL=http://localhost:8086
INFLUXDB_ORG=aquamind
INFLUXDB_BUCKET=farm
INFLUXDB_ADMIN_USER=admin
INFLUXDB_ADMIN_PASSWORD=change-me-12chars
INFLUXDB_TOKEN=change-me-generate-a-random-token
STORAGE_BACKEND=influx   # influx | sqlite  (sqlite = secours sans InfluxDB)
SQLITE_PATH=data/stream.db

# MQTT
MQTT_HOST=localhost
MQTT_PORT=1883
MQTT_BASE_TOPIC=aquamind/farm1

# Ferme
FARM_LAT=34.98
FARM_LON=9.50
FARM_TZ=Africa/Tunis
JAPAN_LAT=37.90
JAPAN_LON=139.02
JAPAN_TZ=Asia/Tokyo

# Simulation
SIM_SECONDS_PER_HOUR=5
OFFLINE_MODE=false     # true = n'utilise que data/cache

# Frontend
VITE_API_URL=http://localhost:8000
VITE_WS_URL=ws://localhost:8000/ws/live
```

Aucune API externe utilisée ne demande de clé (Open-Meteo, PVGIS, SoilGrids, tuiles OSM).

---

## 6. CONTRAT DE DONNÉES (`aquamind_core/schemas.py`, Pydantic v2)

À créer **en premier** ; tout le reste s'y conforme.

- `Crop` : name, kc_ini, kc_mid, kc_end, ky, value_tnd_per_ha
- `Plot` : id ("A","B","C"), crop, area_ha, growth_stage ("ini"|"mid"|"end"), irrigation_efficiency, lat, lon
- `Farm` : id, name, plots, well_depth_m, pump_efficiency, pump_max_m3h, pv_peak_kw, water_quota_m3_day, tank_capacity_m3
- `SensorReading` : ts, kind ("soil"|"flow"|"pv"|"tank"), source_id, values: dict[str, float]
- `Alert` : ts, section, score, detector ("iforest"|"river"), est_loss_m3_day, est_loss_kwh_day, est_loss_tnd_day, message
- `OptimizeRequest` : farm, water_quota_m3, pv_scale (0–1.5), temp_offset_c, well_depth_m (optionnel), s_min (0–1), date
- `OptimizeResult` : allocation {plot_id: [24 floats m³]}, grid_kwh [24], pv_kwh [24], pump_kwh [24], satisfaction {plot_id: float}, duals {"water": float, "energy": [24], "pump": [24]}, kpis, baseline_kpis, explanations [str], limiting_factor ("water"|"energy"|"pump"|"none"), solve_time_ms

Ferme de démo (`data/static/farm.json`) : 3 parcelles de 0,5 ha (A tomate stade mid goutte-à-goutte 0,9 ; B olivier mid goutte-à-goutte 0,9 ; C blé mid aspersion 0,75), puits de 60 m, rendement pompe 0,45, pompe de 8 m³/h, PV de 5 kWc, quota de 60 m³/j, réservoir de 80 m³.

`data/static/crops.json` (valeurs approximatives FAO-56 / FAO-33, à indiquer comme telles) :
tomate (0.6/1.15/0.8, Ky 1.05), olivier (0.65/0.70/0.70, Ky 0.8), blé (0.4/1.15/0.3, Ky 1.0), riz (1.05/1.20/0.75, Ky 1.1).

`data/static/economics.json` : prix de récolte par ha et tarif électrique (heures pleines/creuses). **Valeurs d'hypothèse, marquées `"assumption": true`.**

---

## 7. LES PHASES

### PHASE 1 — Fondations (H0 → H1)
1. Arborescence, `.gitignore` (inclure `.env`, `.venv*`, `data/raw/battledim/`, `mlruns/`, `node_modules/`), `README.md`.
2. `requirements.txt` et `requirements-ml.txt` (versions identiques pour sklearn/lightgbm/numpy/joblib ; `requirements-ml` ajoute jupyter, ipykernel, matplotlib, seaborn, mlflow, river, pyarrow).
3. Commandes de création des 2 venv + enregistrement du kernel Jupyter.
4. `docker-compose.yml` : `eclipse-mosquitto:2` (ports 1883 et 9001, volume du fichier de config) et `influxdb:2.7` (port 8086, init automatique via `DOCKER_INFLUXDB_INIT_*` lus depuis `.env`). À garder même si je n'utilise pas Docker (pour les coéquipiers).
5. `infra/mosquitto/mosquitto.conf` : `listener 1883`, `listener 9001` + `protocol websockets`, `allow_anonymous true`.
6. **Mode natif Windows (sans Docker)** — voir §7bis.
7. `schemas.py`, `config.py` (chargement de `.env` avec pydantic-settings), fichiers JSON statiques.

**Critères :** les services répondent sur `localhost:1883` (test publish/subscribe MQTT) et `localhost:8086` (`/health` d'InfluxDB renvoie `pass`), **via Docker ou en natif** ; `python -c "from aquamind_core.schemas import Farm"` fonctionne ; le kernel « AquaMind ML » apparaît dans VS Code.

### §7bis — Installation native Windows (sans Docker)
Contexte : sur mon PC, Docker/WSL ne fonctionne pas ; Avast fait de l'inspection HTTPS.

1. **Mosquitto** : `winget search mosquitto` → installer le paquet officiel Eclipse s'il existe, sinon l'installeur officiel de mosquitto.org. Utiliser `infra/mosquitto/mosquitto.conf` (copie adaptée aux chemins Windows dans `infra/mosquitto/mosquitto.windows.conf` si nécessaire). Lancement : `mosquitto -c <conf> -v`. Si le service Windows « Mosquitto Broker » tourne déjà avec une autre config, me dire comment l'arrêter ou le reconfigurer plutôt que de créer un conflit sur le port 1883.
2. **InfluxDB 2.x** : `winget search influx` ; sinon télécharger les binaires officiels Windows d'InfluxData (`influxd.exe` + CLI `influx.exe`) dans `tools/influxdb/` (dossier dans `.gitignore`). Lancement : `influxd --bolt-path data/influx/influxd.bolt --engine-path data/influx/engine`. Configuration non interactive : `influx setup --org aquamind --bucket farm --username admin --password <aléatoire> --token <aléatoire> --force`.
3. Générer token et mot de passe avec `secrets.token_hex(32)` et les écrire **uniquement** dans `.env` (jamais dans un fichier commité ni dans un log).
4. `scripts/start-services.ps1` : démarre Mosquitto et influxd en arrière-plan (`Start-Process`, sans bloquer le terminal), attend que les ports répondent (timeout de 30 s), affiche OK/KO. Ajouter `scripts/stop-services.ps1`.
5. `scripts/check-services.py` : ping InfluxDB `/health`, écriture puis lecture d'un point de test dans le bucket `farm`, publish/subscribe MQTT sur `aquamind/test`.
6. README : section « Démarrer sans Docker » + section « Démarrer avec Docker ».

**Règles :** si une étape demande les droits admin (UAC), le pare-feu Windows ou la suspension d'Avast, **arrête-toi et dis-moi exactement quoi faire**. Ne désactive jamais la vérification TLS (`--trusted-host`, `verify=False`, `NODE_TLS_REJECT_UNAUTHORIZED=0`, etc.).

**Secours si InfluxDB ne s'installe pas :** `STORAGE_BACKEND=sqlite`, le consumer et `/history` utilisent alors SQLite. Le projet ne doit jamais être bloqué par InfluxDB.
**Secours si Mosquitto ne s'installe pas :** broker Python `amqtt` (pip) lancé par `scripts/start-services.ps1`, ou mode `--http-fallback` du simulateur.

### PHASE 2 — Collecte des données (O1)
1. `ingest/http_cache.py` : `get_json(url, params, cache_key, ttl_hours)` avec requests, timeout 30 s, 3 tentatives (backoff), cache JSON dans `data/cache/`, et respect de `OFFLINE_MODE`.
2. `ingest/open_meteo.py` :
   - `fetch_archive(lat, lon, start, end, tz)` → `https://archive-api.open-meteo.com/v1/archive` avec `daily=temperature_2m_max,temperature_2m_min,temperature_2m_mean,precipitation_sum,shortwave_radiation_sum,wind_speed_10m_max,relative_humidity_2m_mean,et0_fao_evapotranspiration` de 2015-01-01 à 2025-12-31. Découpe par tranches de 2–3 ans si la réponse est trop lourde. Si une variable est refusée, retire-la et signale-le.
   - `fetch_forecast(lat, lon, tz)` → `https://api.open-meteo.com/v1/forecast` avec `hourly=temperature_2m,relative_humidity_2m,shortwave_radiation,et0_fao_evapotranspiration,soil_moisture_0_to_1cm,soil_moisture_3_to_9cm,soil_moisture_9_to_27cm,precipitation`, `forecast_days=7`.
   - Sorties : `data/processed/weather_daily_{site}.parquet`, `forecast_hourly_{site}.parquet`.
3. `ingest/pvgis.py` : `https://re.jrc.ec.europa.eu/api/v5_3/seriescalc` avec `lat, lon, startyear=2023, endyear=2023, pvcalculation=1, peakpower=1, loss=14, outputformat=json` → profil horaire normalisé (kW par kWc) → `data/processed/pv_hourly_{site}.parquet`. Prévoir le fallback : profil sinusoïdal 6h–19h si l'API échoue.
4. `ingest/soilgrids.py` : `https://rest.isric.org/soilgrids/v2.0/properties/query` (clay, sand, silt ; profondeurs 0-5, 5-15, 15-30 cm ; `value=mean`) → texture → réserve utile (mm/m). Fallback : 140 mm/m.
5. `ingest/run_all.py` : lance tout pour Sidi Bouzid et Niigata et affiche un résumé (lignes, dates min/max, % de valeurs manquantes).
6. **BattLeDIM** : je l'aurai placé dans `data/raw/battledim/`. Écris `ingest/battledim.py` qui **liste d'abord les fichiers présents et affiche leurs colonnes**, puis produit `data/processed/battledim_flows.parquet`, `battledim_pressures.parquet` et `battledim_leaks.parquet` (période de chaque fuite : début, réparation). Si le dossier est vide, arrête-toi et rappelle-moi de le télécharger.

**Critères :** `python -m ingest.run_all` produit tous les fichiers parquet ; le second lancement avec `OFFLINE_MODE=true` fonctionne sans réseau.

### PHASE 3 — Notebooks d'entraînement (O5, O6) — **J'EXÉCUTE, TU ÉCRIS**
Chaque notebook : cellule de titre (objectif, entrées, sorties), imports, `SEED=42`, chemins relatifs à la racine, logging MLflow (`mlflow.set_tracking_uri("file:../mlruns")`, une expérience par notebook), export final dans `models/artifacts/`, cellule finale « Résumé des métriques ».

**01_eda_weather.ipynb** : distributions, saisonnalité de l'ET0, corrélations, comparaison Tunisie/Japon. Pas de modèle.

**02_train_et0_lightgbm.ipynb** : prévoir l'ET0 de J+1.
- Features : lags 1–7 de ET0, Tmax, Tmin, rayonnement, vent, humidité ; moyennes glissantes sur 3 et 7 jours ; amplitude thermique ; sin/cos du jour de l'année ; site (catégoriel).
- Split **temporel** : train ≤ 2022, validation 2023, test 2024–2025. Jamais de split aléatoire.
- Références : persistance (ET0 J+1 = ET0 J) et Hargreaves.
- `LGBMRegressor` avec early stopping sur la validation ; petite recherche d'hyperparamètres (≤ 20 essais, Optuna optionnel).
- Métriques : MAE, RMSE, R² sur le test, **pour chaque site**, comparées aux références. Graphique prédiction/réel, importance des variables (SHAP optionnel).
- Export : `et0_lgbm.txt` (format natif), `et0_features.json` (ordre exact des features), `et0_metrics.json`.

**03_leak_detection_battledim.ipynb** : IsolationForest.
- Rééchantillonner à 15 min ou 1 h ; features par capteur de débit : valeur, écart au profil hebdomadaire typique (même jour et même heure), moyennes et écarts-types glissants, débit nocturne minimal ; pressions : écart à la médiane glissante.
- Étiquettes : 1 si un timestamp tombe dans une période de fuite active (fichier des fuites).
- Entraînement **sur une période sans fuite** (ou sur des données majoritairement normales) ; seuil choisi sur la validation pour maximiser le F1.
- Métriques : précision, rappel, F1, délai moyen de détection (en heures) ; matrice de confusion ; chronologie score/fuites réelles.
- Export : `iforest.joblib`, `leak_features.json`, `leak_threshold.json`, `leak_metrics.json`.
- Noter dans une cellule markdown : BattLeDIM = réseau urbain L-Town ; on le transpose ensuite au réseau d'irrigation.

**04_river_calibration.ipynb** : calibrer HalfSpaceTrees.
- Rejouer en flux un jeu synthétique au même format que le simulateur (débits des tronçons S1–S5 avec fuites injectées) ; tester `n_trees`, `height`, `window_size` et le seuil de score.
- Métriques : F1 et délai de détection en nombre de messages.
- Export : `river_config.json` (hyperparamètres + seuil). River se réinstancie dans le consumer à partir de cette config.

**Critères :** quand je te dis « notebooks exécutés », vérifie que tous les artefacts existent et que `aquamind_core/forecast.py` et `leaks.py` les chargent sans erreur (test unitaire).

### PHASE 4 — Moteur de décision (O7, O8)
1. `agronomy.py` : `kc(crop, stage)`, `etc(et0, kc)`, `gross_need_m3(etc_mm, area_ha, efficiency, rain_mm)` (1 mm × 1 ha = 10 m³ ; pluie efficace = 0,8 × pluie), `hargreaves_et0(...)` en fallback.
2. `energy.py` : `pump_kwh_per_m3(depth_m, eff) = 9.81 * depth / (3600 * eff)` (ajouter 10 % de pertes de charge), `pv_hourly_kwh(profile, peak_kw, scale)`.
3. `forecast.py` : charge LightGBM ; `predict_et0_tomorrow(site)` à partir des 7 derniers jours (archive + prévision) ; fallback sur l'ET0 Open-Meteo, puis Hargreaves.
4. `optimizer.py` (PuLP, LP continu) :
   - Variables : `x[i,t] ≥ 0` (m³ vers la parcelle i à l'heure t), `g[t] ≥ 0` (kWh réseau).
   - Contraintes : `Σi x[i,t] ≤ pump_max` ; `e·Σi x[i,t] ≤ pv[t] + g[t]` ; `Σ x ≤ quota` ; `Σt x[i,t] ≤ D_i` ; optionnel `Σt x[i,t] ≥ s_min·D_i` ; option « pas d'aspersion entre 11h et 16h » pour la parcelle en aspersion.
   - Objectif : `max Σi (V_i·Ky_i/D_i)·Σt x[i,t] − Σt price[t]·g[t]`.
   - Nommer les contraintes pour récupérer les duals (`constraint.pi`).
   - Gérer l'infaisabilité (`s_min` trop élevé) : relâcher et le signaler dans les explications.
   - `limiting_factor` déduit des duals non nuls.
5. `baseline.py` : irrigation traditionnelle = démarrage à 7h sur le réseau, doses égales jusqu'à épuisement du quota.
6. `explain.py` : textes à trous en français à partir des duals, de la satisfaction, des heures de pompage et des écarts avec la référence. **Pas de LLM.**
7. `scenarios.py` : `tunisia_heatwave`, `tunisia_cloudy`, `water_cut_50`, `japan_rice`.
8. Tests pytest : quota 0 → allocation 0 ; PV 0 → tout passe par le réseau ; demande < quota → satisfaction 100 % ; temps de résolution < 2 s.

**Critères :** `pytest` passe ; `python -m aquamind_core.scenarios` affiche les 4 scénarios avec KPI et explications.

### PHASE 5 — Streaming (O3, O4)
1. `simulator/publisher.py` (paho-mqtt v2) :
   - Horloge accélérée (`SIM_SECONDS_PER_HOUR`), une publication par tick.
   - Topics : `{base}/plot/{A|B|C}/soil`, `{base}/pipe/{S1..S5}/flow`, `{base}/pv/power`, `{base}/tank/level` ; payload JSON `{"ts": ISO8601, ...}`.
   - Humidité du sol : partir de `soil_moisture_*` d'Open-Meteo, + irrigation, − ETc, + bruit.
   - PV : profil PVGIS × peak_kw × facteur de nuages.
   - Débits : suivre le **dernier plan d'optimisation** (récupéré via `GET /optimize/last`, sinon plan par défaut) ; topologie : S1 = conduite principale, S2–S4 = parcelles A–C, S5 = retour réservoir ; bilan de masse cohérent.
   - Écouter `{base}/cmd/leak` (`{"section":"S3","rate_m3h":1.5}`) et `{base}/cmd/reset` ; une fuite augmente le débit amont, fait baisser la pression et casse le bilan de masse.
   - Reconnexion automatique ; option `--http-fallback` pour envoyer en POST vers l'API si MQTT est indisponible.
2. `stream/consumer.py` :
   - S'abonne à `{base}/#`, valide avec `SensorReading`, écrit dans InfluxDB (measurement = kind, tags = source_id) — fallback : `deque` en mémoire + SQLite.
   - Pour chaque tick de débit : calcule les features (résidu par rapport au plan, déséquilibre de bilan de masse), passe dans **River** (`score_one` puis `learn_one`), **et** dans IsolationForest.
   - Alerte si le score dépasse le seuil pendant N ticks consécutifs (anti-bruit) → calcule les pertes (m³/j → kWh → TND) → `POST /internal/alerts` (ou publication MQTT sur `{base}/alerts`).
   - Peut tourner comme tâche de fond **dans** FastAPI (lifespan) ou en processus séparé : choisis le plus simple et robuste, et justifie ton choix.

**Critères :** `mosquitto_sub -t "aquamind/#" -v` montre le flux ; les données apparaissent dans InfluxDB (requête Flux fournie) ; une fuite injectée génère une alerte en < 10 s.

### PHASE 6 — API FastAPI (O9)
- `GET /health`
- `GET /farm`, `GET /scenarios`
- `GET /forecast?site=tunisia|japan` → ET0 prévue (modèle + Open-Meteo + Hargreaves), besoins par parcelle
- `POST /optimize` (OptimizeRequest → OptimizeResult) ; `GET /optimize/last`
- `GET /alerts?limit=50`, `POST /internal/alerts`
- `GET /history?kind=flow&source=S3&minutes=60` (InfluxDB)
- `POST /sim/leak`, `POST /sim/reset` → publient sur `cmd/*`
- `GET /models/metrics` → lit les `*_metrics.json` (affichage des MAE/F1 dans l'UI)
- `WS /ws/live` → diffuse chaque mesure et chaque alerte (`{"type":"reading"|"alert"|"plan", ...}`), avec un throttle si nécessaire
- APScheduler : rafraîchit la prévision Open-Meteo toutes les heures et recalcule le plan
- CORS autorisé pour `http://localhost:5173`

**Critères :** tous les endpoints sont testables dans `/docs` ; un client WebSocket de test reçoit le flux.

### PHASE 7 — Dashboard React (O10)
Vite + React + TS + Tailwind + Recharts + react-leaflet. Thème sombre sobre, couleurs eau/énergie/sol cohérentes, responsive (utilisable sur téléphone). Hook `useLiveStream()` (WebSocket avec reconnexion auto).

Pages :
1. **Vue d'ensemble** : cartes KPI (eau du jour, énergie PV / réseau, satisfaction moyenne, alertes actives), courbes live, facteur limitant mis en avant.
2. **Ferme** : carte Leaflet avec les 3 parcelles colorées selon la satisfaction et les 5 tronçons (rouge en cas de fuite) ; détail de chaque parcelle.
3. **Plan IA** : planning horaire en barres empilées par parcelle, superposé aux courbes PV et réseau ; liste des explications ; bouton « Recalculer ».
4. **Fuites** : flux des alertes en direct, graphique du score avec le seuil, bouton **« Injecter une fuite »** (choix du tronçon et du débit) et « Réparer » ; encart des métriques BattLeDIM (précision, rappel, F1).
5. **Et si ?** : curseurs (quota d'eau, ensoleillement, température, profondeur du puits, s_min) + sélecteur de scénario + bascule **Tunisie / Japon** → appel à `/optimize` (debounce de 300 ms) → comparaison optimisé vs traditionnel (eau, kWh, part réseau, valeur protégée en TND, coût).

Toujours un état de chargement et un état « API hors-ligne » (afficher le dernier résultat en cache).

**Critères :** `npm run dev` ; les 5 pages fonctionnent ; injecter une fuite fait passer le tronçon au rouge en direct.

### PHASE 8 — Robustesse et démo (O11)
1. Script `scripts/start_all.ps1` / `.sh` : docker compose, API, simulateur, frontend.
2. Mode `OFFLINE_MODE=true` testé de bout en bout (wifi coupé).
3. Tests des cas limites : quota 0, PV 0, fuite pendant une optimisation, déconnexion du broker.
4. `docs/DEMO_SCRIPT.md` : le déroulé exact (situation → plan IA → fuite injectée → quota divisé par deux → journée nuageuse → mode Japon → comparaison et métriques), avec la phrase à dire à chaque étape et la durée.
5. `docs/ARCHITECTURE.md` : diagramme Mermaid de l'architecture + formulation mathématique de l'optimiseur.

---

## 8. CE QUE JE (L'HUMAIN) DOIS FAIRE — RAPPELLE-LE MOI AU BON MOMENT
- Installer Python 3.11, Node.js 20+, l'extension Jupyter de VS Code, et **soit** Docker Desktop (WSL2) **soit** le mode natif (§7bis).
- En mode natif : accepter les fenêtres UAC et pare-feu, suspendre Avast quelques minutes si un téléchargement est bloqué, et lancer `scripts/start-services.ps1` à chaque session.
- Télécharger BattLeDIM depuis Zenodo (record 4017658) et le dézipper dans `data/raw/battledim/`.
- Copier `.env.example` en `.env` et générer `INFLUXDB_TOKEN` et le mot de passe admin.
- Exécuter les notebooks 01 → 04 dans le kernel « AquaMind ML ».
- Lancer `mlflow ui --backend-store-uri ./mlruns` depuis `.venv-ml` pour voir les expériences.

## 9. INTERDITS
- Pas d'entraînement deep learning, pas de LLM/chatbot, pas d'application mobile native.
- Pas de chiffres d'impact inventés présentés comme réels : tout résultat simulé est étiqueté « estimation simulée ».
- Pas de dépendance à un service payant ou à une clé API.
