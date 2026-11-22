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

## Moteur de décision (Phase 4)

```powershell
.\.venv\Scripts\python -m pytest -q                              # 53 tests (phases 4 à 7)
.\.venv\Scripts\python -m aquamind_core.scenarios                # 4 scénarios : KPI + explications
.\.venv\Scripts\python -m aquamind_core.scenarios --live         # + plan de demain (prévision Open-Meteo)
.\.venv\Scripts\python -m aquamind_core.scenarios --markdown docs/SCENARIOS.md   # rapport complet
```
```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m aquamind_core.scenarios --live
```

- **Optimiseur** (`aquamind_core/optimizer.py`, PuLP/CBC, programme linéaire) sur **48 h** : pompage puits → réservoir contraint par l'énergie (solaire + réseau), irrigation réservoir → parcelles, bilan de la cuve heure par heure, niveau final ≥ niveau initial, quota (cumulé sur l'horizon par défaut, ou strict par jour avec `quota_mode="daily"`), pas d'aspersion de 11h à 16h.
- **Priorités par paliers** (`crops.json` → `value_tranches`) : 0–30 % du besoin = survie, 30–70 % = moyen, 70–100 % = confort, avec une valeur par m³ décroissante (FAO-33 rendu concave, toujours un LP). Culture pérenne (olivier) : survie ×5. L'eau est partagée entre parcelles, et l'explication dit quel palier de quelle parcelle est rationné et pourquoi.
- **Calendrier cultural** (`data/static/crop_calendar.json`) : culture active et Kc du jour selon la date (FAO-56 : Kc interpolé pendant le développement et l'arrière-saison). Parcelle C : blé (aspersion) l'hiver, piment (goutte-à-goutte) l'été ; tomate d'avril à août, jachère sinon. Désactivable : `use_crop_calendar=False`.
- **Marge de sécurité** : +MAE du modèle ET0 du site (0,61 mm/j en Tunisie, soit 11 m³/j sur la ferme), ×1,5 les jours où Tmax > 35 °C (le modèle y sous-estime l'ET0 de 0,32 mm/j en moyenne). Réglable : `safety_margin_mm`, `hot_day_tmax_c`, `hot_day_margin_factor`.
- **Quota** : cumulé sur l'horizon par défaut (`quota_mode="horizon"`), strict par jour avec `quota_mode="daily"` (champ de `OptimizeRequest`, exposé à l'interface).
- **Météo** : J1 = modèle LightGBM (7 jours précédents), J2 = ET0 Open-Meteo ; ensoleillement de chaque jour = rayonnement prévu / moyenne du mois. Secours : ET0 Open-Meteo, puis Hargreaves.
- **Référence** (`baseline.py`) : pompe dès 7h à plein débit, doses égales jusqu'au quota du jour, mêmes panneaux solaires.
- **Explications** (`explain.py`) : textes à trous à partir des duals (prix fictifs), sans LLM.
- Valeurs de récolte, durées de saison et tarifs : hypothèses (`economics.json`, `crops.json`) — estimation simulée.

## Streaming (Phase 5)

Trois terminaux PowerShell, depuis la racine du projet :

```powershell
# 0. Services (une fois par session) puis vérification
powershell -ExecutionPolicy Bypass -File scripts\start-services.ps1
.\.venv\Scripts\python scripts\check_services.py

# Terminal 1 — consumer : stockage + détection (River préchauffé ~5 s)
.\.venv\Scripts\python -m stream.consumer

# Terminal 2 — simulateur (1 message = 10 min simulées = 0,83 s réelles)
.\.venv\Scripts\python -m simulator.publisher --plan scenario:tunisia_heatwave --start-hour 8   # démo
.\.venv\Scripts\python -m simulator.publisher                                                   # plan du jour

# Terminal 3 — observer et piloter
& "C:\Program Files\mosquitto\mosquitto_sub.exe" -h localhost -t "aquamind/farm1/#" -v
.\.venv\Scripts\python scripts\sim_cmd.py leak S3 1.5     # fuite → alerte en quelques secondes
.\.venv\Scripts\python scripts\sim_cmd.py reset           # réparation
.\.venv\Scripts\python scripts\sim_cmd.py flush 0.6       # rinçage des filtres (dérive légitime)
.\.venv\Scripts\python scripts\sim_cmd.py new_normal      # bouton « nouveau normal »
.\.venv\Scripts\python scripts\show_history.py flow S3 10 # mesures stockées (InfluxDB ou SQLite)
.\.venv\Scripts\python scripts\show_history.py alert      # alertes stockées

# Test automatique de bout en bout (~3 min) : fuite < 10 s, rinçage sans alerte en boucle
.\.venv\Scripts\python scripts\stream_e2e.py
```

- **Simulateur** (`simulator/`) : physique pure dans `physics.py` (cuve, débits S1–S5, PV, sols ; bruit, rampes et pics isolés du notebook 04), culture et stade du jour selon `crop_calendar.json`, débits qui suivent le dernier plan (`plan.py` : API `/optimize/last` → optimiseur local → `data/plan_last.json` → plan par défaut). Une fuite de branche ne coule que si la branche est en eau.
- **Topics** : `aquamind/farm1/pipe/{S1..S5}/flow`, `plot/{A,B,C}/soil`, `plot/{A,B,C}/info` (retenu : culture, stade), `pv/power`, `tank/level` ; commandes `cmd/leak`, `cmd/flush`, `cmd/reset`, `cmd/speed`, `cmd/replan`, `cmd/new_normal` ; alertes sur `aquamind/farm1/alerts` ; statut du consumer sur `status/consumer`.
- **Consumer** (`stream/`), processus séparé de l'API (redémarre seul, testable isolément) : mode d'alerte lu dans `river_config.json` (`rule_adaptive`), « nouveau normal » automatique après 8 h d'écart persistant ou sur commande, River = indice « confirmé par l'IA » qui ne bloque jamais une alerte. IsolationForest et CUSUM ne tournent pas en direct.
- **Alerte** : tronçon, m³ perdus/jour, kWh gaspillés/jour, TND perdus/jour (eau 0,20 TND/m³ + tarif électrique de l'heure : hypothèses), confirmation IA.
- **Stockage** : InfluxDB (measurement = kind, tag `source_id`), horodaté à l'heure réelle de réception (l'heure simulée est dans le champ `sim_epoch`) ; secours SQLite automatique (`data/stream.db`) si InfluxDB ne répond pas ou si `STORAGE_BACKEND=sqlite`.
- Requête Flux (explorateur http://localhost:8086) :
  ```
  from(bucket: "farm") |> range(start: -10m)
    |> filter(fn: (r) => r._measurement == "flow" and r.source_id == "S3" and r._field == "flow_m3h")
  ```

## API FastAPI (Phase 6)

```powershell
# Terminal 1 — API (le consumer tourne DANS l'API : ne pas lancer stream.consumer en plus)
.\.venv\Scripts\python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
# Terminal 2 — simulateur, qui suit le plan publié par l'API (/optimize/last)
.\.venv\Scripts\python -m simulator.publisher --plan api --start-hour 8
# Navigateur : http://127.0.0.1:8000/docs
```

| Endpoint | Rôle |
|---|---|
| `GET /health` | broker, stockage, consumer, plan, planificateur, clients WebSocket |
| `GET /farm?site=&day=` | ferme du jour (culture et stade selon le calendrier) |
| `GET /scenarios`, `GET /scenarios/{clé}?apply=` | scénarios ; `apply=true` : la ferme suit ce plan |
| `GET /forecast?site=` | ET0 (modèle, Open-Meteo, Hargreaves) et besoins par parcelle, J+1 et J+2 |
| `POST /optimize?apply=` | plan « Et si ? » (n'est appliqué que si `apply=true`) ; `GET /optimize/last` ; `POST /optimize/refresh` |
| `GET /alerts`, `POST /internal/alerts` | alertes de fuite |
| `GET /history?kind=&source=&minutes=` | mesures stockées (InfluxDB ou SQLite) |
| `POST /sim/leak`, `/sim/reset`, `/sim/flush`, `/sim/new_normal` | pilotage du simulateur et bouton « nouveau normal » |
| `GET /models/metrics` | MAE ET0, verdict BattLeDIM, détection en direct (5 graines) |
| `WS /ws/live` (`?types=alert,plan`) | mesures, alertes, plan, actionneurs en direct |
| `POST /actuators/pump`, `POST /actuators/valve/{A,B,C}`, `POST /actuators/auto` | actionneurs (garde-fous) |
| `POST /emergency-stop`, `GET /actuators/state`, `GET /actuators/log` | arrêt d'urgence, état, journal |

- **APScheduler** : prévision Open-Meteo et plan recalculés toutes les heures ; un plan appliqué à la main (`apply=true`) n'est pas écrasé (retour au plan automatique : `POST /optimize/refresh`).
- **Actionneurs** : `aquamind/farm1/actuator/pump/cmd`, `actuator/valve/{A|B|C}/cmd`, `actuator/estop/cmd`, `actuator/mode/cmd` ; l'appareil répond sur `.../state` (retenu). Aujourd'hui le simulateur joue l'appareil ; demain un ESP32 (Wokwi ou réel) s'abonne aux mêmes topics.
- **Garde-fous** (`aquamind_core/safety.py`, fonction unique `check_command`) : liste blanche d'actions et d'origines, arrêt d'urgence prioritaire (relâché seulement par un humain avec motif), durée de pompage ≤ 120 min avec arrêt automatique, pas de pompage si la cuve est vide ou son niveau inconnu, pas de pompe sans vanne ouverte, anti-court-cycle. Mêmes règles pour `source` = dashboard, agent ou esp32. Chaque décision est journalisée dans `data/actuator_log.jsonl` (qui, quoi, quand, acceptée/refusée, pourquoi). Réponses : 200 acceptée, 409 refusée (raison dans le corps), 503 acceptée mais broker absent.
- CORS : `http://localhost:5173` (variable `CORS_ORIGINS`).
- `POST /actuators/isolate/{A|B|C}` : **isoler une parcelle** de façon atomique (arrêt pompe → fermeture vanne → alerte), possible même sur la dernière vanne ouverte.
- `GET /health` : `last_reading_age_s` et `simulator.alive` ; statut `degraded` si aucune mesure depuis plus de 30 s.
- Simulateur : une erreur sur un pas est journalisée (trace) sans arrêter la démo ; ligne « Vivant » à chaque journée simulée ; à la fin de l'horizon (48 h), il recharge le plan suivant ou rejoue le même.

## Dashboard React (Phase 7)

```powershell
cd web
npm install          # une seule fois
npm run dev          # http://127.0.0.1:5173
```

- Vite + React 18 + TypeScript + Tailwind + Recharts + react-leaflet ; l'URL de l'API vient de `VITE_API_URL` / `VITE_WS_URL` du `.env` racine (seules les variables `VITE_*` sont exposées au navigateur).
- **5 pages** : Vue d'ensemble, Ferme (carte OSM), Plan IA, Fuites, Et si ? ; **bandeau d'état** permanent (API, MQTT, simulateur vivant / arrêté depuis X s, temps réel, stockage, plan) ; bouton **ARRÊT D'URGENCE** toujours visible ; panneau **Pilotage** (pompe, vannes, isolement, journal).
- API coupée : bandeau « hors-ligne » et dernières réponses en cache (`localStorage`), reconnexion automatique du WebSocket.
- Build de production : `npm run build` (vérifie les types puis construit `web/dist/`).

## Mes terres agricoles (cartographie des parcelles)

Page **« Mes terres »** du dashboard (`#terres`) : définir le contour de chaque champ sur la photo satellite
**Esri World Imagery**, le corriger puis l'enregistrer en **GeoJSON** (SQLite `data/parcels.db` en local, ignoré par git).

| Méthode | Comment | Code |
|---|---|---|
| Dessiner | outils Geoman (polygone, rectangle), croisements interdits pendant le dessin | `web/src/pages/MapPage.tsx` |
| Marcher | relevé GPS du téléphone, seuil de précision réglable (25 m par défaut), points à moins de 3 m ignorés, pause | `web/src/components/GpsRecorder.tsx` |
| Dimensions | rectangle, carré, ou côté par côté (longueur + direction) | `web/src/components/ManualDimensions.tsx` |
| Plan cadastral | JPG/PNG/PDF ≤ 10 Mo, contour détecté par OpenCV **en pixels**, puis mise à l'échelle et placement **vérifiés par l'agriculteur** | `web/src/components/PlanUpload.tsx`, `aquamind_core/plan_processing.py` |

Toute forme se modifie ensuite sur la carte (coins, déplacement, rotation, **Annuler** / Ctrl+Z), avec la longueur
de chaque côté affichée. Recherche d'un village ou de coordonnées (« 34.98, 9.49 », degrés-minutes-secondes, lien
Google Maps), fond Satellite / Plan, brouillon conservé si l'on quitte la page.

Mêmes règles dans le navigateur (`web/src/lib/parcels.ts`) et sur le serveur (`aquamind_core/parcel_geo.py`) :
bords qui se croisent refusés (bouton **Démêler**), surface entre 10 m² et 10 000 ha, étendue ≤ 20 km, noms uniques ;
chevauchement avec un champ existant signalé. Les surfaces utilisent le rayon terrestre moyen (6 371 008,8 m) des
deux côtés : « 2,5 ha » saisis s'affichent « 2,5 ha ». Un tour GPS est nettoyé à la fin (chemin d'accès, dépassement
du départ, bruit, petites boucles) et l'écran reste allumé pendant le relevé. Un champ redessiné repasse
« à vérifier » ; un plan cadastral ne s'enregistre qu'après la case « J'ai vérifié ».

Plans cadastraux : le texte (cotes, numéros) est retiré avant la détection, le cadre de page et les blocs de
parcelles voisines sont écartés, et jusqu'à 5 contours sont proposés (« Autre contour »). L'échelle se donne par
un côté connu ou par la surface inscrite sur le plan.

API (`api/parcels.py`, documentée dans `/docs`, rubrique « mes terres agricoles ») :

| Route | Rôle |
|---|---|
| `POST /api/parcels` · `GET /api/parcels` | créer (statut `pending`) · lister |
| `GET` · `PUT` · `DELETE /api/parcels/{parcel_id}` | lire · renommer / redessiner / valider · supprimer |
| `GET /api/parcels/{parcel_id}/geojson` · `GET /api/parcels/export.geojson` | Feature · FeatureCollection |
| `POST /api/parcels/validate` | surface (m², ha), périmètre, refus si auto-intersection |
| `POST /api/parcels/rectangle` · `POST /api/parcels/polygon-segments` | formes à partir de dimensions (écart de fermeture renvoyé) |
| `POST /api/parcels/upload-plan` · `POST /api/parcels/extract-plan` | envoi (type vérifié sur le contenu, 10 Mo) · contour `pending_human_verification` |

La lecture du texte des plans (PaddleOCR) est **optionnelle** et non installée : sans elle, le contour est détecté
et `ocr_status` vaut `engine_unavailable`. Le GPS du navigateur exige `https` ou `localhost` (pas une IP du réseau local en http).

### Profil d'un champ et équipement conseillé

Bouton **« Profil et équipement »** d'un champ (`#terres?champ=<id>`) :

| Partie | Contenu | Source |
|---|---|---|
| Localisation | surface, tour, centre | contour enregistré |
| Sol | texture, % sable/limon/argile, réserve utile, conseil d'arrosage ; correction possible | SoilGrids (ISRIC) + Saxton & Rawls |
| Soleil | kWh/kWc/an et par mois, inclinaison idéale, ombre déclarée | PVGIS (JRC) « PVcalc » |
| Climat | ET0 et pluie moyennes par mois | Open-Meteo, archive 2023-2025 |
| Eau | position du puits (toucher la carte), profondeur, cuve | saisie |
| Cultures | zones : tout le champ, bandes de même surface, ou zone dessinée (ramenée dans le champ, sans recouvrement) ; culture + arrosage par zone | saisie |

Recommandations (`aquamind_core/land_reco.py`, prix d'**hypothèse** dans `data/static/equipment.json`) :
- **Panneaux solaires (pompage)** : besoin en eau mois par mois (ET0 × Kc du calendrier cultural − pluie efficace,
  ÷ efficience), énergie zone par zone (puits + pression de l'arrosage), dimensionnés pour le mois le plus exigeant
  (+10 %, et ≥ 1,3 × la puissance de la pompe). Emplacement : partie non cultivée, du côté où l'ombre ne tombe pas
  sur les cultures (nord dans l'hémisphère nord), au plus près du puits ; sinon juste à l'extérieur du champ.
  Rangées espacées pour ne pas se faire d'ombre au solstice d'hiver. Coût détaillé, économie, rentabilité.
- **Capteurs** : sonde d'humidité par zone (+1 par 2 ha, profondeur selon la culture), compteurs et pression à la
  pompe et par secteur, station météo et passerelle radio partagées entre champs à moins de 5 / 3 km, niveau de cuve.
  Chaque capteur a sa position sur la carte, sa raison et son coût.

API : `GET /api/parcels/crops`, `GET|PUT /api/parcels/{id}/profile`, `POST /api/parcels/{id}/environment`,
`GET /api/parcels/{id}/recommendations` (`api/parcel_profile.py`). Les données sol/soleil/climat sont mises en cache
dans la base avec le champ ; un contour déplacé de plus de 100 m les fait recalculer, un contour modifié marque le
découpage « à revérifier ». Tests : `tests/test_land_profile.py`.

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
