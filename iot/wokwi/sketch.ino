// AquaMind — ESP32 « pompe + vannes A/B/C » (Wokwi).
//
// SIMULATION UNIQUEMENT : broker MQTT PUBLIC (broker.hivemq.com, TLS 8883 vérifié), relayé vers le
// Mosquitto local par iot/bridge_public.py. Jamais pour piloter une vraie pompe.
//
// Rôle : appareil qui OBÉIT aux commandes de l'API (toutes passées par safety.py), CONFIRME son état
// réel, mesure les débits S1..S5 (potentiomètre 1 = débit de la pompe) et le sol de A (potentiomètre 2),
// et applique ses PROPRES sécurités. Même protocole et mêmes règles que iot/device_twin.py.
//
// Broches : pot débit D34 · pot sol D35 · relais pompe D26 · LED pompe D25 · vannes A/B/C : GPIO16 (RX2),
// GPIO17 (TX2), D18 · bouton pompe D27 · bouton « percer un tuyau » D14 · ARRÊT D'URGENCE D13 ·
// OLED SSD1306 I2C : SDA D21, SCL D22.
//
// Sécurité locale : 1) arrêt d'urgence (bouton ou commande) prioritaire, coupe tout par interruption,
// verrouillé jusqu'au relâchement par l'API (humain + motif) ; 2) sans « status/api » depuis 30 s :
// tout est coupé ; 3) pas de pompe sans vanne ouverte, pas de fermeture de la dernière vanne pompe en
// marche ; 4) durée maximale ; 5) cuve vide. Un refus est publié comme ÉTAT avec sa raison.

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <time.h>
#include "config.h"

// ------------------------------------------------------------------------------------ broches
#define PIN_POT_FLOW 34
#define PIN_POT_SOIL 35
#define PIN_RELAY    26
#define PIN_PUMP_LED 25
#define PIN_BTN_PUMP 27
#define PIN_BTN_LEAK 14
#define PIN_BTN_STOP 13
const int PIN_VALVE[3] = {16, 17, 18};
const char* VALVE_ID[3] = {"A", "B", "C"};
const char* VALVE_SECTION[3] = {"S2", "S3", "S4"};

// ------------------------------------------------------------------------------------ constantes
const float PUMP_MAX_M3H = 10.0;
const float RETURN_FRAC = 0.08, NOISE_REL = 0.02, NOISE_ABS = 0.05, LEAK_SHARE = 0.25;
const float DRY_TANK_M3 = 0.3, LEAK_RATE = 1.5;
const unsigned long MAX_PUMP_S = 7200;          // 2 h (= safety.max_pump_minutes)
const unsigned long API_SILENCE_MS = 30000;     // sans « status/api » : coupure
const unsigned long HEARTBEAT_MS = 5000;

// ------------------------------------------------------------------------------------ état
volatile bool estopIsr = false;                 // posé par l'interruption du bouton rouge
bool estop = false, pumpOn = false, valves[3] = {false, false, false};
bool apiOk = false;
String mode = "auto";
unsigned long pumpUntilMs = 0, apiLastMs = 0, lastHbMs = 0, lastOledMs = 0, msgUntilMs = 0;
int leakSection = -1;                           // -1 aucune, 0 = S1, 1..3 = S2..S4
float flushM3h = 0, tankM3 = -1;                // -1 : niveau inconnu
float potFlow = 0, potSoil = 0.25;
String oledMsg = "";
unsigned long reqCounter = 0;
String lastTlsError = "";

WiFiClientSecure net;
PubSubClient mqtt(net);
Adafruit_SSD1306 oled(128, 64, &Wire, -1);
bool oledOk = false;

// File des messages reçus : PubSubClient réutilise son tampon, on copie puis on traite dans loop()
struct Inbox { char rel[48]; char payload[512]; };
const int INBOX_N = 12;
Inbox inbox[INBOX_N];
volatile int inHead = 0, inTail = 0;

// ------------------------------------------------------------------------------------ sorties
void IRAM_ATTR onStopButton() {
  // Priorité absolue : coupure matérielle immédiate, même si le réseau ou le TLS bloque loop()
  estopIsr = true;
  digitalWrite(PIN_RELAY, LOW);
  digitalWrite(PIN_PUMP_LED, LOW);
  for (int i = 0; i < 3; i++) digitalWrite(PIN_VALVE[i], LOW);
}

void writeOutputs() {
  bool run = pumpOn && !estop;
  digitalWrite(PIN_RELAY, run ? HIGH : LOW);
  digitalWrite(PIN_PUMP_LED, run ? HIGH : LOW);
  for (int i = 0; i < 3; i++) digitalWrite(PIN_VALVE[i], (valves[i] && !estop) ? HIGH : LOW);
}

bool anyValve() { return valves[0] || valves[1] || valves[2]; }
int openCount() { return (int)valves[0] + (int)valves[1] + (int)valves[2]; }
void cutAll() { pumpOn = false; pumpUntilMs = 0; for (int i = 0; i < 3; i++) valves[i] = false; writeOutputs(); }

String isoNow() {
  time_t now = time(nullptr);
  if (now < 1700000000) return String("1970-01-01T00:00:00Z");   // heure pas encore synchronisée
  struct tm t; gmtime_r(&now, &t);
  char b[25]; strftime(b, sizeof b, "%Y-%m-%dT%H:%M:%SZ", &t);
  return String(b);
}

// ------------------------------------------------------------------------------------ publication
void pub(const String& rel, JsonDocument& doc, bool retain) {
  if (!mqtt.connected()) return;
  char buf[640];
  size_t n = serializeJson(doc, buf, sizeof buf);
  String topic = String(AQ_PREFIX) + "/" + rel;
  mqtt.publish(topic.c_str(), (const uint8_t*)buf, n, retain);
}

void fillCommon(JsonDocument& d, const char* cid, const char* result, const char* reason, const char* source) {
  d["ts"] = isoNow();
  d["device"] = AQ_DEVICE;
  d["mode"] = mode;
  if (cid && *cid) d["command_id"] = cid; else d["command_id"] = nullptr;
  d["result"] = result;
  d["reason"] = reason;
  if (source && *source) d["source"] = source; else d["source"] = nullptr;
}

void pubPump(const char* cid = "", const char* result = "ok", const char* reason = "", const char* source = "") {
  JsonDocument d; fillCommon(d, cid, result, reason, source);
  d["on"] = pumpOn && !estop;
  if (pumpOn && pumpUntilMs) d["remaining_s"] = (long)((pumpUntilMs - millis()) / 1000); else d["remaining_s"] = nullptr;
  pub("actuator/pump/state", d, true);
}
void pubValve(int i, const char* cid = "", const char* result = "ok", const char* reason = "", const char* source = "") {
  JsonDocument d; fillCommon(d, cid, result, reason, source);
  d["open"] = valves[i] && !estop;
  pub(String("actuator/valve/") + VALVE_ID[i] + "/state", d, true);
}
void pubEstop(const char* cid = "", const char* result = "ok", const char* reason = "", const char* source = "") {
  JsonDocument d; fillCommon(d, cid, result, reason, source);
  d["engaged"] = estop;
  pub("actuator/estop/state", d, true);
}
void pubAll(const char* result, const char* reason, const char* source) {
  pubEstop("", result, reason, source); pubPump("", result, reason, source);
  for (int i = 0; i < 3; i++) pubValve(i, "", result, reason, source);
}
void request(const char* target, const char* action, const char* reason, int durationMin = 0) {
  JsonDocument d;
  d["request_id"] = String(AQ_DEVICE) + "-" + String(++reqCounter);
  d["device"] = AQ_DEVICE; d["target"] = target; d["action"] = action; d["reason"] = reason;
  if (durationMin > 0) d["duration_min"] = durationMin;
  pub("actuator/request", d, false);
}
void showMsg(const String& m, unsigned long ms = 4000) { oledMsg = m; msgUntilMs = millis() + ms; Serial.println(">> " + m); }

// ------------------------------------------------------------------------------------ mesures
float gauss() {   // Box-Muller (même méthode que iot/protocol.py)
  float u1 = (esp_random() + 1.0f) / 4294967297.0f, u2 = (esp_random() + 1.0f) / 4294967297.0f;
  return sqrtf(-2.0f * logf(u1)) * cosf(2.0f * PI * u2);
}

void publishFlows(const char* ts) {
  float plan[5] = {0, 0, 0, 0, 0}, meas[5];
  bool pumping = pumpOn && anyValve() && !estop && potFlow > 0;
  if (pumping) {
    float delivered = potFlow * (1 - RETURN_FRAC);
    for (int i = 0; i < 3; i++) if (valves[i]) plan[1 + i] = delivered / openCount();
    plan[4] = potFlow * RETURN_FRAC;
    plan[0] = potFlow;
  }
  for (int s = 0; s < 5; s++) meas[s] = plan[s] * (1 + NOISE_REL * gauss()) + NOISE_ABS * gauss();
  float leak = 0;
  if (pumping && leakSection >= 0 && (leakSection == 0 || plan[leakSection] > 0.1)) {
    meas[0] += LEAK_RATE;
    if (leakSection > 0) meas[leakSection] -= LEAK_SHARE * LEAK_RATE;
    leak = LEAK_RATE;
  }
  if (pumping) meas[0] += flushM3h;
  float pressure = pumping ? 2.5 - 0.25 * leak + 0.03 * gauss() : 0.2;
  const char* names[5] = {"S1", "S2", "S3", "S4", "S5"};
  for (int s = 0; s < 5; s++) {
    JsonDocument d;
    d["ts"] = ts;
    d["flow_m3h"] = roundf(max(0.0f, meas[s]) * 10000) / 10000.0;
    d["plan_m3h"] = roundf(plan[s] * 10000) / 10000.0;
    d["pressure_bar"] = roundf(pressure * 1000) / 1000.0;
    pub(String("pipe/") + names[s] + "/flow", d, false);
  }
  JsonDocument d;
  d["ts"] = ts;
  d["moisture_m3m3"] = roundf(potSoil * 10000) / 10000.0;
  pub("plot/A/soil", d, false);
}

// ------------------------------------------------------------------------------------ commandes reçues
void handle(const char* rel, const char* payload) {
  JsonDocument p;
  if (deserializeJson(p, payload)) return;
  const char* cid = p["command_id"] | "";
  const char* src = p["source"] | "";
  bool fromPlan = strcmp(src, "scheduler") == 0;

  if (!strcmp(rel, "status/api")) { apiLastMs = millis(); apiOk = true; return; }
  if (!strcmp(rel, "sim/tick")) { String ts = p["ts"] | isoNow().c_str(); publishFlows(ts.c_str()); return; }
  if (!strcmp(rel, "tank/level")) { if (p["level_m3"].is<float>()) tankM3 = p["level_m3"]; return; }
  if (!strcmp(rel, "cmd/leak")) {
    String s = p["section"] | "S3";
    leakSection = (s == "S1") ? 0 : (s == "S2") ? 1 : (s == "S3") ? 2 : (s == "S4") ? 3 : -1;
    showMsg("Fuite (demo) sur " + s); return;
  }
  if (!strcmp(rel, "cmd/reset")) { leakSection = -1; flushM3h = 0; showMsg("Fuites reparees"); return; }
  if (!strcmp(rel, "cmd/flush")) { flushM3h = max(0.0f, (float)(p["rate_m3h"] | 0.6)); return; }
  if (!strcmp(rel, "actuator/request/result")) {
    bool ok = p["accepted"] | false;
    String why = p["reason"] | "";
    showMsg(String(ok ? "OK: " : "REFUS: ") + why.substring(0, 40), 6000); return;
  }

  if (!strcmp(rel, "actuator/estop/cmd")) {
    bool engaged = p["engaged"] | true;
    if (engaged) { estop = true; cutAll(); mode = "manual"; showMsg("ARRET D'URGENCE (API)"); }
    else { estop = false; estopIsr = false; showMsg("Arret d'urgence relache"); }
    writeOutputs();
    pubEstop(cid, "ok", "", src); pubPump("", "ok", "", src);
    for (int i = 0; i < 3; i++) pubValve(i, "", "ok", "", src);
    return;
  }
  if (!strcmp(rel, "actuator/mode/cmd")) {
    if (estop) { pubPump(cid, "refused", "arrêt d'urgence actif", src); return; }
    String m = p["mode"] | "";
    if (m == "auto") mode = "auto";
    pubPump(cid, "ok", "", src); return;
  }
  if (!strcmp(rel, "actuator/pump/cmd")) {
    bool on = p["on"] | false;
    if (on) {
      const char* why = estop ? "arrêt d'urgence actif"
                      : !anyValve() ? "aucune vanne ouverte"
                      : (tankM3 >= 0 && tankM3 < DRY_TANK_M3) ? "cuve vide : marche à sec interdite" : "";
      if (*why) { pubPump(cid, "refused", why, src); showMsg(String("Pompe refusee: ") + why); return; }
      unsigned long dur = p["duration_s"] | 1800UL;
      if (dur > MAX_PUMP_S) dur = MAX_PUMP_S;
      pumpOn = true; pumpUntilMs = millis() + dur * 1000UL;
    } else { pumpOn = false; pumpUntilMs = 0; }
    mode = fromPlan ? "auto" : "manual";
    writeOutputs(); pubPump(cid, "ok", "", src); return;
  }
  if (!strncmp(rel, "actuator/valve/", 15)) {
    int i = rel[15] - 'A';
    if (i < 0 || i > 2) return;
    bool open = p["open"] | false;
    if (open && estop) { pubValve(i, cid, "refused", "arrêt d'urgence actif", src); return; }
    if (!open && pumpOn && valves[i] && openCount() == 1) {
      pubValve(i, cid, "refused", "dernière vanne ouverte avec la pompe en marche", src); return;
    }
    valves[i] = open;
    mode = fromPlan ? "auto" : "manual";
    writeOutputs(); pubValve(i, cid, "ok", "", src); return;
  }
}

void onMqtt(char* topic, byte* payload, unsigned int len) {
  size_t pl = strlen(AQ_PREFIX);
  if (strncmp(topic, AQ_PREFIX, pl) != 0 || topic[pl] != '/') return;
  int next = (inHead + 1) % INBOX_N;
  if (next == inTail) { Serial.println("File pleine : message ignore"); return; }
  strncpy(inbox[inHead].rel, topic + pl + 1, sizeof inbox[inHead].rel - 1);
  inbox[inHead].rel[sizeof inbox[inHead].rel - 1] = 0;
  unsigned int n = min(len, (unsigned int)(sizeof inbox[inHead].payload - 1));
  memcpy(inbox[inHead].payload, payload, n);
  inbox[inHead].payload[n] = 0;
  inHead = next;
}

// ------------------------------------------------------------------------------------ boutons
struct Button { int pin; bool last; unsigned long at; };
Button bPump = {PIN_BTN_PUMP, HIGH, 0}, bLeak = {PIN_BTN_LEAK, HIGH, 0};
bool pressed(Button& b) {
  bool v = digitalRead(b.pin);
  bool edge = (b.last == HIGH && v == LOW && millis() - b.at > 60);
  if (v != b.last) { b.at = millis(); b.last = v; }
  return edge;
}

void onEstopPressed() {
  // Le matériel est déjà coupé par l'interruption ; on aligne l'état, on l'annonce, on le fait journaliser.
  estop = true; cutAll(); mode = "manual";
  showMsg("ARRET D'URGENCE (bouton)", 8000);
  pubAll("local", "arrêt d'urgence physique", "esp32_local");
  request("estop", "engage", "bouton d'arrêt d'urgence de l'appareil");
}

void onPumpButton() {
  if (pumpOn) {                               // arrêter est toujours sûr : immédiat et local
    pumpOn = false; pumpUntilMs = 0; mode = "manual"; writeOutputs();
    pubPump("", "local", "arrêt par le bouton de l'appareil", "esp32_local");
    request("pump", "off", "bouton pompe de l'appareil");
    showMsg("Pompe arretee (bouton)");
  } else if (!mqtt.connected() || !apiOk) {
    showMsg("API injoignable : demarrage impossible");
  } else {                                     // démarrer passe par l'API (safety.py)
    request("pump", "on", "bouton pompe de l'appareil", 30);
    showMsg("Demande de demarrage envoyee...");
  }
}

void onLeakButton() {
  JsonDocument d; d["ts"] = isoNow(); d["device"] = AQ_DEVICE;
  if (leakSection >= 0) { leakSection = -1; d["type"] = "repair"; showMsg("Tuyau repare"); }
  else {
    leakSection = 0;                           // tuyau principal, sauf si une vanne est ouverte
    for (int i = 0; i < 3; i++) if (valves[i]) { leakSection = i + 1; break; }
    const char* names[4] = {"S1", "S2", "S3", "S4"};
    d["type"] = "leak"; d["section"] = names[leakSection]; d["rate_m3h"] = LEAK_RATE;
    showMsg(String("Tuyau perce : ") + names[leakSection]);
  }
  pub("device/event", d, false);
}

// ------------------------------------------------------------------------------------ connexion
void connectWifi() {
  if (WiFi.status() == WL_CONNECTED) return;
  WiFi.begin(AQ_WIFI_SSID, AQ_WIFI_PASS, AQ_WIFI_CHANNEL);
  Serial.print("WiFi");
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 15000) {
    if (estopIsr && !estop) onEstopPressed();
    delay(200); Serial.print(".");
  }
  Serial.println(WiFi.status() == WL_CONNECTED ? " OK" : " echec");
}

unsigned long lastTry = 0;
void connectMqtt() {
  if (mqtt.connected() || millis() - lastTry < 3000) return;
  lastTry = millis();
  String id = String("aqm-esp32-") + String((uint32_t)esp_random(), HEX);
  String will = String(AQ_PREFIX) + "/device/status";
  const char* willMsg = "{\"online\":false,\"device\":\"" AQ_DEVICE "\"}";
  Serial.printf("MQTT %s:%d (TLS verifie)...\n", AQ_BROKER, AQ_PORT);
  if (mqtt.connect(id.c_str(), will.c_str(), 1, true, willMsg)) {
    lastTlsError = "";
    String base = String(AQ_PREFIX) + "/";
    const char* subs[] = {"actuator/pump/cmd", "actuator/valve/+/cmd", "actuator/estop/cmd", "actuator/mode/cmd",
                          "actuator/request/result", "status/api", "sim/tick", "tank/level",
                          "cmd/leak", "cmd/reset", "cmd/flush"};
    for (const char* s : subs) mqtt.subscribe((base + s).c_str(), 1);
    String online = "{\"online\":true,\"device\":\"" AQ_DEVICE "\"}";
    mqtt.publish(will.c_str(), online.c_str(), true);
    pubAll("ok", "", "esp32_local");          // état actuel (sans notification côté dashboard)
    showMsg("Connecte au broker (TLS)");
  } else {
    char err[100] = "";
    net.lastError(err, sizeof err);
    lastTlsError = String(err);
    Serial.printf("MQTT echec (etat %d) TLS : %s\n", mqtt.state(), err);
    // AUCUN repli sans TLS : si le certificat est refusé, on le dit et on réessaie (toujours en TLS)
    showMsg(strlen(err) ? "TLS : certificat refuse ?" : "Broker injoignable", 3000);
  }
}

// ------------------------------------------------------------------------------------ écran
void drawOled() {
  if (!oledOk || millis() - lastOledMs < 300) return;
  lastOledMs = millis();
  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);
  oled.setTextSize(1);
  oled.setCursor(0, 0);
  if (estop) {
    oled.fillRect(0, 0, 128, 22, SSD1306_WHITE);
    oled.setTextColor(SSD1306_BLACK);
    oled.setTextSize(2); oled.setCursor(4, 3); oled.print("ARRET URG.");
    oled.setTextColor(SSD1306_WHITE); oled.setTextSize(1);
  } else {
    oled.printf("AquaMind %s", mode == "auto" ? "AUTO" : "MANUEL");
  }
  oled.setCursor(0, 24);
  oled.printf("MQTT %s  API %s", mqtt.connected() ? "ok" : "--", apiOk ? "ok" : "--");
  oled.setCursor(0, 34);
  oled.printf("Pompe %s %.1fm3/h", (pumpOn && !estop) ? "ON " : "off", potFlow);
  oled.setCursor(0, 44);
  oled.printf("A:%s B:%s C:%s %s", valves[0] ? "o" : "-", valves[1] ? "o" : "-", valves[2] ? "o" : "-",
              leakSection >= 0 ? "FUITE" : "");
  oled.setCursor(0, 54);
  if (millis() < msgUntilMs) oled.print(oledMsg.substring(0, 21));
  else oled.printf("Sol A %.2f Cuve %s", potSoil, tankM3 < 0 ? "?" : String(tankM3, 0).c_str());
  oled.display();
}

// ------------------------------------------------------------------------------------ setup / loop
void setup() {
  Serial.begin(115200);
  pinMode(PIN_RELAY, OUTPUT); pinMode(PIN_PUMP_LED, OUTPUT);
  for (int i = 0; i < 3; i++) pinMode(PIN_VALVE[i], OUTPUT);
  cutAll();
  pinMode(PIN_BTN_PUMP, INPUT_PULLUP); pinMode(PIN_BTN_LEAK, INPUT_PULLUP); pinMode(PIN_BTN_STOP, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(PIN_BTN_STOP), onStopButton, FALLING);
  if (digitalRead(PIN_BTN_STOP) == LOW) estopIsr = true;       // bouton déjà enfoncé au démarrage

  Wire.begin(21, 22);
  oledOk = oled.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  if (oledOk) { oled.clearDisplay(); oled.setTextColor(SSD1306_WHITE); oled.setCursor(0, 0); oled.print("AquaMind ESP32..."); oled.display(); }

  Serial.println("AquaMind ESP32 — SIMULATION UNIQUEMENT (broker public)");
  connectWifi();
  configTime(0, 0, "pool.ntp.org", "time.google.com");
  net.setCACert(AQ_ROOT_CA);                  // vérification du certificat du broker (jamais setInsecure)
  mqtt.setServer(AQ_BROKER, AQ_PORT);
  mqtt.setBufferSize(1024);
  mqtt.setKeepAlive(15);
  mqtt.setCallback(onMqtt);
}

void loop() {
  unsigned long now = millis();

  // 1) Arrêt d'urgence physique : avant TOUT le reste
  if (estopIsr && !estop) onEstopPressed();

  // 2) Réseau
  if (WiFi.status() != WL_CONNECTED) connectWifi();
  connectMqtt();
  mqtt.loop();

  // 3) Messages reçus (copiés par onMqtt)
  while (inTail != inHead) {
    handle(inbox[inTail].rel, inbox[inTail].payload);
    inTail = (inTail + 1) % INBOX_N;
    if (estopIsr && !estop) onEstopPressed();
  }

  // 4) Potentiomètres
  potFlow = roundf(PUMP_MAX_M3H * analogRead(PIN_POT_FLOW) / 4095.0 * 100) / 100.0;
  potSoil = 0.05 + 0.40 * analogRead(PIN_POT_SOIL) / 4095.0;

  // 5) Boutons
  if (pressed(bPump)) onPumpButton();
  if (pressed(bLeak)) onLeakButton();

  // 6) Sécurités locales
  if (pumpOn && pumpUntilMs && (long)(now - pumpUntilMs) >= 0) {
    pumpOn = false; pumpUntilMs = 0; writeOutputs();
    pubPump("", "local", "durée maximale atteinte : arrêt", "esp32_local");
    showMsg("Duree max : pompe arretee");
  }
  if (pumpOn && tankM3 >= 0 && tankM3 < DRY_TANK_M3) {
    pumpOn = false; pumpUntilMs = 0; writeOutputs();
    pubPump("", "local", "cuve vide : pompe arrêtée", "esp32_local");
    showMsg("Cuve vide : pompe arretee");
  }
  if (apiOk && now - apiLastMs > API_SILENCE_MS) {
    apiOk = false;
    if (pumpOn || anyValve()) {
      cutAll();
      pubAll("local", "API muette depuis 30 s : coupure de sécurité", "esp32_local");
    }
    showMsg("API muette 30 s : tout coupe", 8000);
  }
  writeOutputs();

  // 7) Battement de cœur
  if (now - lastHbMs >= HEARTBEAT_MS) {
    lastHbMs = now;
    JsonDocument d;
    d["ts"] = isoNow(); d["device"] = AQ_DEVICE; d["uptime_s"] = now / 1000; d["rssi"] = WiFi.RSSI();
    d["api_ok"] = apiOk; d["pump"] = pumpOn && !estop; d["estop"] = estop; d["flow_m3h"] = potFlow;
    JsonObject v = d["valves"].to<JsonObject>();
    for (int i = 0; i < 3; i++) v[VALVE_ID[i]] = valves[i];
    const char* names[4] = {"S1", "S2", "S3", "S4"};
    d["leak"] = leakSection >= 0 ? names[leakSection] : "";
    d["fw"] = "aquamind-esp32-1";
    pub("device/heartbeat", d, false);
  }

  drawOled();
  delay(10);
}
