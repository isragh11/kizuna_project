# ESP32 « pompe + vannes A/B/C » dans Wokwi

> ⚠️ **MODE RÉSERVÉ À LA SIMULATION — jamais pour piloter une vraie pompe.**
> L'ESP32 de Wokwi passe par un broker MQTT **public** (broker.hivemq.com) : n'importe qui connaissant le
> préfixe de topic peut lire ou publier. Le préfixe aléatoire évite les collisions, il ne protège pas.
> Pour du vrai matériel : broker privé, authentification, TLS mutuel, et un arrêt d'urgence câblé.

## Rôle de l'ESP32
- Il **obéit** aux commandes de l'API (pompe, vannes A/B/C, arrêt d'urgence, retour au plan) ; toutes sont
  d'abord vérifiées par `aquamind_core/safety.py` et journalisées avec leur source.
- Il **confirme** son état réel (`.../state`, avec l'identifiant de la commande, « ok » ou « refusée + raison »)
  et envoie un **battement de cœur** toutes les 5 s.
- Il **mesure** les débits S1…S5 (potentiomètre 1 = débit de la pompe) et le sol de la parcelle A
  (potentiomètre 2), au même format que le simulateur Python.
- Il applique ses **propres sécurités** : arrêt d'urgence prioritaire (coupure matérielle par interruption),
  coupure si l'API se tait 30 s, pas de pompe sans vanne ouverte, durée maximale 2 h, cuve vide.

## Montage (`diagram.json`)

| Élément | Broche | Rôle |
|---|---|---|
| Potentiomètre 1 | D34 | débit de la pompe, 0–10 m³/h |
| Potentiomètre 2 | D35 | humidité du sol de la parcelle A |
| Relais + LED bleue | D26, D25 | pompe |
| LED verte / jaune / orange | GPIO16 (`RX2`), GPIO17 (`TX2`), D18 | vannes A / B / C |
| Bouton vert | D27 | pompe : **arrêter** = immédiat et local ; **démarrer** = demande à l'API (safety.py) |
| Bouton bleu | D14 | « percer un tuyau » (fuite de démo sur la branche ouverte), un 2e appui répare |
| Bouton **rouge** | D13 | **ARRÊT D'URGENCE** : coupe tout tout de suite, prioritaire, relâché seulement depuis le dashboard |
| Écran OLED SSD1306 | SDA D21, SCL D22 | état : MQTT, API, pompe, vannes, fuite, dernier message |

Le montage d'origine (règle d'arrosage autonome et appel HTTP non vérifié) est conservé pour mémoire dans
`original/` ; il n'est plus utilisé.

## Lancer le tout, pas à pas

**0. Une seule fois : préfixe et `config.h`**
```powershell
.\.venv\Scripts\python scripts\wokwi_config.py
```
Crée `WOKWI_TOPIC_PREFIX` dans `.env` s'il manque et écrit `iot/wokwi/config.h` (préfixe, broker, certificats
racines publics servant à **vérifier** le certificat TLS du broker). `config.h` n'est pas commité.

**1. Wokwi (navigateur)** — sur https://wokwi.com, ouvrir votre projet ESP32 (ou « New project → ESP32 ») :
1. onglet `diagram.json` : remplacer tout le contenu par `iot/wokwi/diagram.json` ;
2. onglet `sketch.ino` : remplacer par `iot/wokwi/sketch.ino` ;
3. onglet `libraries.txt` (ou « Library Manager ») : `ArduinoJson`, `PubSubClient`, `Adafruit SSD1306`,
   `Adafruit GFX Library` ;
4. « + » → nouveau fichier **`config.h`** → coller le contenu de `iot/wokwi/config.h` ;
5. ▶ démarrer la simulation. Le moniteur série doit afficher `MQTT broker.hivemq.com:8883 (TLS verifie)...`
   puis l'écran « Connecte au broker (TLS) ».

**2. Terminaux (dossier du projet)**

| # | Commande | Rôle |
|---|---|---|
| 1 | `.\scripts\start-services.ps1` | Mosquitto + InfluxDB (comme d'habitude) |
| 2 | `.\.venv\Scripts\python -m uvicorn api.main:app --host 127.0.0.1 --port 8000` | API |
| 3 | `.\.venv\Scripts\python -m simulator.publisher --plan api --device wokwi` | ferme simulée : B, C, soleil, cuve, heure de la ferme |
| 4 | `.\.venv\Scripts\python -m iot.bridge_public` | pont broker public ⇄ Mosquitto (TLS vérifié) |
| 5 | `cd web; npm run dev` | dashboard http://127.0.0.1:5173 |

Le bandeau du dashboard doit afficher **« Appareil : ESP32 Wokwi »** avec un point vert.
Sans Wokwi, le **jumeau Python** joue le même rôle : `.\.venv\Scripts\python -m iot.device_twin`
(touches `p` pompe, `f` fuite, `e` arrêt d'urgence). Pour revenir au simulateur seul : terminal 3 sans
`--device wokwi`, et arrêter le pont.

**3. Vérification automatique (< 2 s mesurées)**
```powershell
.\.venv\Scripts\python scripts\e2e_demo.py --appareil     # santé + pompe ON/OFF confirmée
.\.venv\Scripts\python scripts\e2e_demo.py                # parcours complet
```

## Parcours de démo
1. **Dashboard → Commandes** : ouvrir la vanne A → « ⏳ envoyé… » puis « ✅ Confirmé par ESP32 Wokwi en 0,x s »,
   la LED verte s'allume. Démarrer la pompe → le relais et la LED bleue s'allument, « Tourne ».
2. Fermer la vanne A pompe en marche → « ❌ Refusé : arrêtez d'abord la pompe ».
3. Tourner le **potentiomètre 1** → le débit change sur « L'eau sort-elle bien jusqu'aux cultures ? ».
   Tourner le **potentiomètre 2** → l'humidité du sol des tomates (A) change.
4. **Bouton bleu** (percer un tuyau) → « 💦 Tuyau percé depuis le boîtier », puis alerte de fuite sur la
   branche ouverte (quelques secondes). Rappuyer : réparé, l'alerte se ferme.
5. **Bouton vert** : arrête la pompe aussitôt (« arrêt par le bouton du boîtier ») ; un nouvel appui demande
   le démarrage à l'API (refusé si aucune vanne n'est ouverte : la raison s'affiche sur l'OLED).
6. **Bouton rouge** → coupure immédiate, OLED « ARRÊT URG. », dashboard en arrêt d'urgence avec
   « déclenché SUR LE BOÎTIER ». Le relâcher : bouton rouge du dashboard + motif (un humain seulement).
7. « Rendre la main à l'IA » → le plan pilote l'ESP32 (commandes « Plan d'arrosage (IA) » dans le journal).
8. Arrêter la simulation Wokwi → bandeau **« Appareil hors-ligne »** (≤ 15 s, souvent immédiat).
   Arrêter l'API 30 s → l'ESP32 coupe tout seul (« API muette depuis 30 s »).

## Dépannage
- **OLED « TLS : certificat refusé ? »** : le certificat du broker n'est signé par aucune des racines de
  `config.h`. Il n'y a volontairement **aucun repli sans TLS** : signalez-le avant toute autre option.
- **« Broker injoignable »** : vérifier le WiFi Wokwi-GUEST (moniteur série) ; le broker public peut être lent.
- **Bandeau « Appareil hors-ligne »** alors que Wokwi tourne : le pont (terminal 4) est-il lancé ? Le préfixe
  de `config.h` est-il celui du `.env` (relancer `scripts\wokwi_config.py` et recoller `config.h`) ?
- **Antivirus qui inspecte le TLS (Avast…)** : le pont vérifie le certificat avec le magasin Windows (où se
  trouve la racine de l'antivirus) ; l'ESP32, lui, passe par les serveurs de Wokwi et voit le vrai certificat.
