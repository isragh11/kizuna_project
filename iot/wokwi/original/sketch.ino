#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

// ====== CONFIG ======
#define USE_AGENT false   // false = local rule, true = AI agent
const char* AGENT_URL = "https://abcd-1234.ngrok-free.app/decide";
const unsigned long ORDER_INTERVAL = 20000;  // ms between irrigation decisions
// ====================

#define PIN_TEMP   34   // pot 1: temperature
#define PIN_RAIN   35   // pot 2: rain probability
#define PIN_PUMP   25   // LED = pump
#define PIN_RELAY  26   // relay = pump on/off
#define PIN_BTN    27   // button: change crop

// ----- Crops -----
struct Culture { const char* nom; float humMin; float humMax; };
Culture cultures[3] = {
  {"Olivier", 25, 40},
  {"Tomate",  60, 80},
  {"Ble",     40, 60}
};
int cultureIdx = 0;

// ----- Simulation parameters -----
const float DEBIT_POMPE    = 30.0;   // pump flow, L/min
const float RESERVOIR_MAX  = 500.0;  // tank capacity, L
const float LITRES_PAR_PCT = 2.0;    // liters needed to raise soil moisture by 1 %
const float EVAP_BASE      = 6.0;    // % moisture lost per min at 25 C (accelerated for demo)

float reservoir = 300.0;   // L
float humSol    = 40.0;    // soil moisture, %
float cibleLitres = 0, livreLitres = 0;
unsigned long dernierCalcul = 0, dernierAffichage = 0, dernierOrdre = 0, dernierBtn = 0;
bool btnAvant = HIGH;

void pwmInit() {
#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcAttach(PIN_PUMP, 5000, 8);
#else
  ledcSetup(0, 5000, 8);
  ledcAttachPin(PIN_PUMP, 0);
#endif
}

void pwmWrite(int value) {
#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(PIN_PUMP, value);
#else
  ledcWrite(0, value);
#endif
}

// Local rule (fallback): bring soil to the middle of the crop's range
void decisionLocale(float hum, float pluie) {
  Culture c = cultures[cultureIdx];
  float cible = (c.humMin + c.humMax) / 2.0;
  float litres = 0;
  if (hum < c.humMin && pluie < 60) litres = (cible - hum) * LITRES_PAR_PCT;
  cibleLitres = constrain(litres, 0, 50);
  livreLitres = 0;
  Serial.printf(">> Local order: %.1f L\n", cibleLitres);
}

// AI agent decision
void demanderAgent(float hum, float temp, float pluie, float resPct) {
  if (WiFi.status() != WL_CONNECTED) { decisionLocale(hum, pluie); return; }

  Culture c = cultures[cultureIdx];
  StaticJsonDocument<300> req;
  req["culture"] = c.nom;
  req["humidite_sol_pct"] = hum;
  req["humidite_min_pct"] = c.humMin;
  req["humidite_max_pct"] = c.humMax;
  req["temperature_c"] = temp;
  req["pluie_prevue_pct"] = pluie;
  req["reservoir_pct"] = resPct;
  String body;
  serializeJson(req, body);

  WiFiClientSecure client;
  client.setInsecure();
  HTTPClient http;
  http.begin(client, AGENT_URL);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("ngrok-skip-browser-warning", "1");
  int code = http.POST(body);

  if (code == 200) {
    StaticJsonDocument<200> res;
    deserializeJson(res, http.getString());
    cibleLitres = res["volume_litres"] | 0.0;
    livreLitres = 0;
    Serial.printf(">> AI agent order: %.1f L\n", cibleLitres);
  } else {
    Serial.printf("HTTP error: %d -> local fallback\n", code);
    decisionLocale(hum, pluie);
  }
  http.end();
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_RELAY, OUTPUT);
  pinMode(PIN_BTN, INPUT_PULLUP);
  pwmInit();

#if USE_AGENT
  WiFi.begin("Wokwi-GUEST", "", 6);
  Serial.print("WiFi connecting");
  while (WiFi.status() != WL_CONNECTED) { delay(250); Serial.print("."); }
  Serial.println(" OK");
#endif
  Serial.println("Irrigation simulation started");
  Serial.printf("Crop: %s (press the button to change)\n", cultures[cultureIdx].nom);
}

void loop() {
  unsigned long now = millis();

  // Sensors
  float temp   = 10.0 + analogRead(PIN_TEMP) * 35.0 / 4095.0;   // 10-45 C
  float pluie  = analogRead(PIN_RAIN) * 100.0 / 4095.0;         // 0-100 %
  float resPct = reservoir * 100.0 / RESERVOIR_MAX;

  // Button: change crop
  bool btn = digitalRead(PIN_BTN);
  if (btnAvant == HIGH && btn == LOW && now - dernierBtn > 250) {
    dernierBtn = now;
    cultureIdx = (cultureIdx + 1) % 3;
    dernierOrdre = 0;
    livreLitres = cibleLitres;   // stop the current order
    Serial.printf("*** Crop changed: %s (target %.0f-%.0f %%)\n",
                  cultures[cultureIdx].nom, cultures[cultureIdx].humMin, cultures[cultureIdx].humMax);
  }
  btnAvant = btn;

  // New decision only if the pump is not already running
  bool pompeEnCours = (livreLitres < cibleLitres);
  if (!pompeEnCours && (now - dernierOrdre > ORDER_INTERVAL || (dernierOrdre == 0 && now > 2000))) {
    dernierOrdre = now;
#if USE_AGENT
    demanderAgent(humSol, temp, pluie, resPct);
#else
    decisionLocale(humSol, pluie);
#endif
  }

  // Physics (every 100 ms)
  if (now - dernierCalcul >= 100) {
    float dt = (now - dernierCalcul) / 60000.0;   // minutes
    dernierCalcul = now;

    if (livreLitres < cibleLitres && reservoir > 0) {
      digitalWrite(PIN_RELAY, HIGH);
      pwmWrite(255);
      float apport = min(DEBIT_POMPE * dt, reservoir);
      livreLitres += apport;
      reservoir   -= apport;
      humSol      += apport / LITRES_PAR_PCT;
    } else {
      digitalWrite(PIN_RELAY, LOW);
      pwmWrite(0);
      livreLitres = cibleLitres;   // order finished (or tank empty)
    }

    // Evaporation: hotter = drier
    humSol -= EVAP_BASE * (temp / 25.0) * dt;
    humSol = constrain(humSol, 0.0, 100.0);
  }

  // Serial display every second
  if (now - dernierAffichage >= 1000) {
    dernierAffichage = now;
    Serial.printf("%s | Soil %.1f%% | Temp %.0fC | Rain %.0f%% | Tank %.0f%% | Order %.1f/%.1f L | Pump %s\n",
                  cultures[cultureIdx].nom, humSol, temp, pluie, resPct,
                  livreLitres, cibleLitres,
                  (livreLitres < cibleLitres) ? "ON" : "OFF");
  }
}