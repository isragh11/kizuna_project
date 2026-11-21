# Scénarios du moteur de décision (Phase 4)

*Généré par `python -m aquamind_core.scenarios --markdown docs/SCENARIOS.md`. Horizon 48 h, journées réelles de l'archive Open-Meteo. Valeurs de récolte, paliers, calendrier et tarifs : hypothèses — **estimation simulée**.*

## Méthode en bref

- **Programme linéaire** (PuLP/CBC) sur 48 h : pompage puits → réservoir limité par l'énergie (solaire + réseau), irrigation réservoir → parcelles, bilan de la cuve heure par heure, niveau final ≥ niveau initial, quota cumulé sur l'horizon (`quota_mode="daily"` pour un quota strict par jour).
- **Valeur de l'eau par paliers** : 0–30 % du besoin = survie (50 % de la valeur du jour), 30–70 % = moyen (35 %), 70–100 % = confort (15 %). Pour l'olivier (pérenne), la survie vaut 5 fois plus. L'eau est donc partagée : chaque culture reçoit sa survie avant le confort d'une autre.
- **Calendrier cultural** : culture et stade dépendent de la date (piment en été, blé en hiver-printemps, tomate d'avril à août, jachère hors saison).
- **ET0** : J1 = modèle LightGBM (7 jours précédents), J2 = Open-Meteo. **Marge de sécurité** = MAE du modèle (0,61 mm/j), ×1,5 les jours où Tmax > 35 °C (sous-estimation moyenne mesurée de 0,32 mm/j ces jours-là sur 2024–2025).
- **Référence traditionnelle** : pompe dès 7h à plein débit, doses égales jusqu'au quota du jour, mêmes panneaux solaires.

## Synthèse (optimisé, entre parenthèses : irrigation traditionnelle)

| KPI (48 h) | Canicule à Sidi Bouzid | Journée voilée annoncée | Quota d'eau divisé par deux | Riz à Niigata (mode Japon) |
|---|---|---|---|---|
| Besoin prévu (m³) | 269 (269) | 199 (199) | 206 (206) | 227 (227) |
| Quota (m³) | 120 (120) | 120 (120) | 60 (60) | 300 (300) |
| Eau pompée (m³) | 120 (120) | 120 (120) | 60 (60) | 260 (300) |
| kWh solaire | 48 (37) | 37 (29) | 24 (14) | 21 (24) |
| kWh réseau | 0 (11) | 11 (19) | 0 (10) | 0 (0) |
| Part solaire (%) | 100 (77) | 77 (60) | 100 (57) | 100 (98) |
| kWh réseau évités vs trad. | 11 | 8 | 10 | 0 |
| Satisfaction A (%) | 48 (35) | 70 (46) | 30 (21) | 100 (100) |
| Satisfaction B (%) | 30 (57) | 30 (66) | 27 (35) | 100 (100) |
| Satisfaction C (%) | 52 (48) | 80 (77) | 30 (37) | 100 (100) |
| Valeur protégée (TND) | 340 (306) | 414 (370) | 256 (240) | 550 (550) |
| Gain de valeur vs trad. (TND) | 34 | 44 | 17 | 0 |
| Facteur limitant | water | water | water | none |

## Canicule à Sidi Bouzid (`tunisia_heatwave`)

20–21 juillet 2025 : les deux jours consécutifs à plus forte ET0 de l'été 2025 dans l'archive (Tmax 42,9 et 44,8 °C, ET0 9,8 et 10,0 mm). Le besoin dépasse largement le quota.

```
SCÉNARIO tunisia_heatwave — Canicule à Sidi Bouzid
20–21 juillet 2025 : les deux jours consécutifs à plus forte ET0 de l'été 2025 dans l'archive (Tmax 42,9 et 44,8 °C, ET0 9,8 et 10,0 mm). Le besoin dépasse largement le quota.
ET0 utilisée : [7.991, 9.96] mm/j (modèle LightGBM (7 jours précédents) ; Open-Meteo (archive)) | pluie [0.0, 0.0] mm | ensoleillement [1.025, 0.968] | marge [0.92, 0.92] mm/j | 0.3997 kWh/m³
Cultures : A Tomate (mi-saison, jour 102/135, Kc 1.15) ; B Olivier (saison d'irrigation, Kc 0.7) ; C Piment (développement, jour 50/125, Kc 0.844)
Facteur limitant : water | résolu en 140 ms

KPI (48 h)                          Optimisé  Traditionnel
Besoin prévu (m³)                        269           269
Quota (m³)                               120           120
Eau pompée (m³)                          120           120
Énergie de pompage (kWh)                48.0          48.0
  dont solaire (kWh)                    48.0          37.0
  dont réseau (kWh)                      0.0          10.9
Part solaire (%)                         100            77
Coût réseau (TND)                       0.00          2.41
Satisfaction A (%)                        48            35
Satisfaction B (%)                        30            57
Satisfaction C (%)                        52            48
Satisfaction minimale (%)                 30            35
Valeur protégée (TND)                    340           306
Valeur perdue (TND)                      138           172
Réservoir max (m³)                        65            40

kWh réseau évités vs traditionnel : +10.9 kWh (+2.41 TND) | valeur protégée : +34 TND | eau économisée : +0 m³
Horizon 48 h vs deux plans de 24 h : +0.0 kWh réseau évités, gain net +4.69 TND

Explications :
  1. Besoin prévu sur 48 h : 269 m³ (ET0 J1 (20/07) 7,99 mm (modèle LightGBM (7 jours précédents)) ; J2 (21/07) 9,96 mm (Open-Meteo (archive))) pour un quota de 120 m³ : il manque 149 m³, soit 55 % du besoin. Il faut rationner.
  2. Cultures du jour (calendrier cultural) : A Tomate (mi-saison, jour 102/135, Kc 1,15, goutte-à-goutte) ; B Olivier (saison d'irrigation, Kc 0,70, goutte-à-goutte) ; C Piment (développement, jour 50/125, Kc 0,84, goutte-à-goutte).
  3. Facteur limitant : l'EAU. Les 120 m³ du quota sont utilisés ; 1 m³ de quota en plus protégerait 1,65 TND de récolte (prix fictif de l'eau, variable duale).
  4. Valeur de l'eau par paliers du besoin (survie 0–30 % / moyen 30–70 % / confort 70–100 %), en TND par m³ : C Piment 3,53 / 1,85 / 1,06 ; A Tomate 3,43 / 1,80 / 1,03 ; B Olivier 2,76 (arbre) / 0,29 / 0,17. L'eau va d'abord aux paliers les plus précieux, toutes parcelles confondues : chaque culture reçoit d'abord sa part de survie avant que la plus rentable ne reçoive son confort. Pour l'olivier (culture pérenne), le palier de survie vaut 5 fois plus : sans eau, l'arbre dépérit et il faut plusieurs saisons pour retrouver la production.
  5. Le dernier m³ du quota vaut 1,65 TND : il complète le palier « moyen » de A (Tomate). Tout palier qui vaut moins par m³ reste sans eau.
  6. Parcelle C (Piment) RATIONNÉE à 53 % de son besoin (44,2 sur 84,2 m³, il manque 40,0 m³ ; paliers : survie 100 %, moyen 56 %, confort 0 %). Pourquoi : palier(s) survie servi(s) en priorité ; le palier « moyen » n'est servi qu'en partie (56 %) : c'est là que le quota s'épuise, en concurrence avec les autres parcelles (dernier m³ : 1,65 TND). Perte de récolte estimée : 51 TND sur 48 h.
  7. Parcelle A (Tomate) RATIONNÉE à 48 % de son besoin (54,8 sur 114,7 m³, il manque 59,9 m³ ; paliers : survie 100 %, moyen 44 %, confort 0 %). Pourquoi : palier(s) survie servi(s) en priorité ; le palier « moyen » n'est servi qu'en partie (44 %) : c'est là que le quota s'épuise, en concurrence avec les autres parcelles (dernier m³ : 1,65 TND). Perte de récolte estimée : 76 TND sur 48 h.
  8. Parcelle B (Olivier) RATIONNÉE à 30 % de son besoin (20,9 sur 69,8 m³, il manque 48,9 m³ ; paliers : survie 100 %, moyen 0 %, confort 0 %). Pourquoi : palier(s) survie servi(s) en priorité ; le palier « moyen » vaut 0,29 TND/m³, moins que le dernier m³ d'eau disponible (1,65 TND/m³) ; la culture tolère mieux le manque d'eau (Ky 0,80 < 1). Perte de récolte estimée : 11 TND sur 48 h.
  9. Marge de sécurité : +0,61 mm/j d'ET0 (erreur moyenne mesurée du modèle), portée à 0,92 mm/j (×1,5) les jours chauds (Tmax > 35 °C : J1 (20/07) 42,9 °C, J2 (21/07) 44,8 °C), car le modèle sous-estime les extrêmes : sur 2024–2025 en Tunisie, quand Tmax > 35 °C, le modèle sous-estime l'ET0 de 0,32 mm/j en moyenne (0,08 mm/j les autres jours) ; ex. 8,0 mm prévus contre 9,8 mm observés le 20/07/2025. Soit 27,5 m³ sur l'horizon. Non servie : l'eau manque déjà pour couvrir les besoins prévus.
  10. Pompage : 120 m³ (J1 (20/07) 6h–20h ; J2 (21/07) 6h–17h), 48,0 kWh dont 100 % solaire.
  11. Réservoir : 40 m³ au départ, entre 0 et 65 m³ pendant l'horizon, 40 m³ à la fin (≥ niveau initial). Il découple le pompage solaire de l'irrigation : 72 m³ sont irrigués à des heures sans pompage.
  12. Comparé à l'irrigation traditionnelle (pompe dès 7h à plein débit, doses égales jusqu'au quota du jour, mêmes panneaux solaires) : réseau 10,9 → 0,0 kWh (-10,9 kWh, -2,41 TND), part solaire 77 → 100 %, satisfaction A/B/C 35/57/48 → 48/30/53 %, valeur protégée 306 → 340 TND (+34 TND), eau pompée 120 → 120 m³.
  13. Valeurs de récolte, paliers de valeur, calendrier cultural, durées de saison et tarifs électriques : hypothèses (economics.json, crops.json, crop_calendar.json) — estimation simulée.
```

## Journée voilée annoncée (`tunisia_cloudy`)

8–9 juin 2024 : le 9 juin, le rayonnement tombe à 52 % de la veille (vent de sable, 33 km/h) alors que l'ET0 reste forte. Le plan doit anticiper avec le réservoir.

```
SCÉNARIO tunisia_cloudy — Journée voilée annoncée
8–9 juin 2024 : le 9 juin, le rayonnement tombe à 52 % de la veille (vent de sable, 33 km/h) alors que l'ET0 reste forte. Le plan doit anticiper avec le réservoir.
ET0 utilisée : [8.105, 7.47] mm/j (modèle LightGBM (7 jours précédents) ; Open-Meteo (archive)) | pluie [0.0, 0.0] mm | ensoleillement [1.026, 0.538] | marge [0.92, 0.92] mm/j | 0.3997 kWh/m³
Cultures : A Tomate (développement, jour 60/135, Kc 0.999) ; B Olivier (saison d'irrigation, Kc 0.7) ; C Piment (initial, jour 8/125, Kc 0.6)
Facteur limitant : water | résolu en 127 ms

KPI (48 h)                          Optimisé  Traditionnel
Besoin prévu (m³)                        199           199
Quota (m³)                               120           120
Eau pompée (m³)                          120           120
Énergie de pompage (kWh)                48.0          48.0
  dont solaire (kWh)                    36.9          28.5
  dont réseau (kWh)                     11.1          19.4
Part solaire (%)                          77            60
Coût réseau (TND)                       1.66          4.27
Satisfaction A (%)                        70            46
Satisfaction B (%)                        30            66
Satisfaction C (%)                        80            77
Satisfaction minimale (%)                 30            46
Valeur protégée (TND)                    414           370
Valeur perdue (TND)                       64           108
Réservoir max (m³)                        80            40

kWh réseau évités vs traditionnel : +8.4 kWh (+2.61 TND) | valeur protégée : +44 TND | eau économisée : +0 m³
Horizon 48 h vs deux plans de 24 h : +0.2 kWh réseau évités, gain net +0.75 TND

Explications :
  1. Besoin prévu sur 48 h : 199 m³ (ET0 J1 (08/06) 8,11 mm (modèle LightGBM (7 jours précédents)) ; J2 (09/06) 7,47 mm (Open-Meteo (archive))) pour un quota de 120 m³ : il manque 79 m³, soit 40 % du besoin. Il faut rationner.
  2. Cultures du jour (calendrier cultural) : A Tomate (développement, jour 60/135, Kc 1,00, goutte-à-goutte) ; B Olivier (saison d'irrigation, Kc 0,70, goutte-à-goutte) ; C Piment (initial, jour 8/125, Kc 0,60, goutte-à-goutte).
  3. Facteur limitant : l'EAU. Les 120 m³ du quota sont utilisés ; 1 m³ de quota en plus protégerait 1,71 TND de récolte (prix fictif de l'eau, variable duale).
  4. Valeur de l'eau par paliers du besoin (survie 0–30 % / moyen 30–70 % / confort 70–100 %), en TND par m³ : C Piment 5,66 / 2,97 / 1,70 ; A Tomate 4,51 / 2,37 / 1,35 ; B Olivier 3,15 (arbre) / 0,33 / 0,19. L'eau va d'abord aux paliers les plus précieux, toutes parcelles confondues : chaque culture reçoit d'abord sa part de survie avant que la plus rentable ne reçoive son confort. Pour l'olivier (culture pérenne), le palier de survie vaut 5 fois plus : sans eau, l'arbre dépérit et il faut plusieurs saisons pour retrouver la production.
  5. Le dernier m³ du quota vaut 1,71 TND : il complète le palier « confort » de C (Piment). Tout palier qui vaut moins par m³ reste sans eau.
  6. Parcelle C (Piment) RATIONNÉE à 80 % de son besoin (41,3 sur 51,9 m³, il manque 10,6 m³ ; paliers : survie 100 %, moyen 100 %, confort 32 %). Pourquoi : palier(s) survie, moyen servi(s) en priorité ; le palier « confort » n'est servi qu'en partie (32 %) : c'est là que le quota s'épuise, en concurrence avec les autres parcelles (dernier m³ : 1,71 TND). Perte de récolte estimée : 18 TND sur 48 h.
  7. Parcelle A (Tomate) RATIONNÉE à 70 % de son besoin (60,5 sur 86,4 m³, il manque 25,9 m³ ; paliers : survie 100 %, moyen 100 %, confort 0 %). Pourquoi : palier(s) survie, moyen servi(s) en priorité ; le palier « confort » vaut 1,35 TND/m³, moins que le dernier m³ d'eau disponible (1,71 TND/m³). Perte de récolte estimée : 35 TND sur 48 h.
  8. Parcelle B (Olivier) RATIONNÉE à 30 % de son besoin (18,2 sur 60,6 m³, il manque 42,4 m³ ; paliers : survie 100 %, moyen 0 %, confort 0 %). Pourquoi : palier(s) survie servi(s) en priorité ; le palier « moyen » vaut 0,33 TND/m³, moins que le dernier m³ d'eau disponible (1,71 TND/m³) ; la culture tolère mieux le manque d'eau (Ky 0,80 < 1). Perte de récolte estimée : 11 TND sur 48 h.
  9. Marge de sécurité : +0,61 mm/j d'ET0 (erreur moyenne mesurée du modèle), portée à 0,92 mm/j (×1,5) les jours chauds (Tmax > 35 °C : J1 (08/06) 40,7 °C, J2 (09/06) 39,5 °C), car le modèle sous-estime les extrêmes : sur 2024–2025 en Tunisie, quand Tmax > 35 °C, le modèle sous-estime l'ET0 de 0,32 mm/j en moyenne (0,08 mm/j les autres jours) ; ex. 8,0 mm prévus contre 9,8 mm observés le 20/07/2025. Soit 23,5 m³ sur l'horizon. Non servie : l'eau manque déjà pour couvrir les besoins prévus.
  10. Pompage : 120 m³ (J1 (08/06) 0h–4h, 6h–20h ; J2 (09/06) 6h–20h), 48,0 kWh dont 77 % solaire ; 11,1 kWh réseau (1,66 TND, 0,150 TND/kWh en moyenne : heures creuses), aux heures J1 (08/06) 0h–4h.
  11. Anticipation : J2 (09/06) s'annonce peu ensoleillé (54 % d'une journée normale). Le plan pompe 28,4 m³ de plus que l'irrigation du J1, avec le solaire du J1 et 11,1 kWh réseau (0h–4h), et les stocke dans le réservoir (niveau fin J1 : 68,4 m³) pour les utiliser le J2 au lieu d'acheter du réseau en journée.
  12. Réservoir : 40 m³ au départ, entre 16 et 80 m³ pendant l'horizon, 40 m³ à la fin (≥ niveau initial). Il découple le pompage solaire de l'irrigation : 59 m³ sont irrigués à des heures sans pompage.
  13. Comparé à l'irrigation traditionnelle (pompe dès 7h à plein débit, doses égales jusqu'au quota du jour, mêmes panneaux solaires) : réseau 19,4 → 11,1 kWh (-8,4 kWh, -2,61 TND), part solaire 60 → 77 %, satisfaction A/B/C 46/66/77 → 70/30/80 %, valeur protégée 370 → 414 TND (+44 TND), eau pompée 120 → 120 m³.
  14. Valeurs de récolte, paliers de valeur, calendrier cultural, durées de saison et tarifs électriques : hypothèses (economics.json, crops.json, crop_calendar.json) — estimation simulée.
```

## Quota d'eau divisé par deux (`water_cut_50`)

6–7 juillet 2025 (ET0 proche de la médiane de juillet 2025, 7,7 mm/j) avec un quota de 30 m³/j au lieu de 60 (coupure, tour d'eau réduit).

```
SCÉNARIO water_cut_50 — Quota d'eau divisé par deux
6–7 juillet 2025 (ET0 proche de la médiane de juillet 2025, 7,7 mm/j) avec un quota de 30 m³/j au lieu de 60 (coupure, tour d'eau réduit).
ET0 utilisée : [6.879, 7.86] mm/j (modèle LightGBM (7 jours précédents) ; Open-Meteo (archive)) | pluie [0.0, 0.0] mm | ensoleillement [1.002, 0.995] | marge [0.92, 0.92] mm/j | 0.3997 kWh/m³
Cultures : A Tomate (mi-saison, jour 88/135, Kc 1.15) ; B Olivier (saison d'irrigation, Kc 0.7) ; C Piment (développement, jour 36/125, Kc 0.664)
Facteur limitant : water | résolu en 123 ms

KPI (48 h)                          Optimisé  Traditionnel
Besoin prévu (m³)                        206           206
Quota (m³)                                60            60
Eau pompée (m³)                           60            60
Énergie de pompage (kWh)                24.0          24.0
  dont solaire (kWh)                    24.0          13.8
  dont réseau (kWh)                      0.0          10.2
Part solaire (%)                         100            57
Coût réseau (TND)                       0.00          2.25
Satisfaction A (%)                        30            21
Satisfaction B (%)                        27            35
Satisfaction C (%)                        30            37
Satisfaction minimale (%)                 27            21
Valeur protégée (TND)                    256           240
Valeur perdue (TND)                      222           238
Réservoir max (m³)                        79            40

kWh réseau évités vs traditionnel : +10.2 kWh (+2.25 TND) | valeur protégée : +17 TND | eau économisée : +0 m³
Horizon 48 h vs deux plans de 24 h : +0.0 kWh réseau évités, gain net +0.63 TND

Explications :
  1. Besoin prévu sur 48 h : 206 m³ (ET0 J1 (06/07) 6,88 mm (modèle LightGBM (7 jours précédents)) ; J2 (07/07) 7,86 mm (Open-Meteo (archive))) pour un quota de 60 m³ : il manque 146 m³, soit 71 % du besoin. Il faut rationner.
  2. Cultures du jour (calendrier cultural) : A Tomate (mi-saison, jour 88/135, Kc 1,15, goutte-à-goutte) ; B Olivier (saison d'irrigation, Kc 0,70, goutte-à-goutte) ; C Piment (développement, jour 36/125, Kc 0,66, goutte-à-goutte).
  3. Facteur limitant : l'EAU. Les 60 m³ du quota sont utilisés ; 1 m³ de quota en plus protégerait 3,11 TND de récolte (prix fictif de l'eau, variable duale).
  4. Valeur de l'eau par paliers du besoin (survie 0–30 % / moyen 30–70 % / confort 70–100 %), en TND par m³ : C Piment 5,42 / 2,84 / 1,62 ; A Tomate 4,15 / 2,18 / 1,24 ; B Olivier 3,34 (arbre) / 0,35 / 0,20. L'eau va d'abord aux paliers les plus précieux, toutes parcelles confondues : chaque culture reçoit d'abord sa part de survie avant que la plus rentable ne reçoive son confort. Pour l'olivier (culture pérenne), le palier de survie vaut 5 fois plus : sans eau, l'arbre dépérit et il faut plusieurs saisons pour retrouver la production.
  5. Le dernier m³ du quota vaut 3,11 TND : il complète le palier « survie » de B (Olivier). Tout palier qui vaut moins par m³ reste sans eau.
  6. Parcelle C (Piment) RATIONNÉE à 30 % de son besoin (16,3 sur 54,4 m³, il manque 38,1 m³ ; paliers : survie 100 %, moyen 0 %, confort 0 %). Pourquoi : palier(s) survie servi(s) en priorité ; le palier « moyen » vaut 2,84 TND/m³, moins que le dernier m³ d'eau disponible (3,11 TND/m³). Perte de récolte estimée : 88 TND sur 48 h.
  7. Parcelle A (Tomate) RATIONNÉE à 30 % de son besoin (28,2 sur 94,2 m³, il manque 65,9 m³ ; paliers : survie 100 %, moyen 0 %, confort 0 %). Pourquoi : palier(s) survie servi(s) en priorité ; le palier « moyen » vaut 2,18 TND/m³, moins que le dernier m³ d'eau disponible (3,11 TND/m³). Perte de récolte estimée : 117 TND sur 48 h.
  8. Parcelle B (Olivier) RATIONNÉE à 27 % de son besoin (15,4 sur 57,3 m³, il manque 41,9 m³ ; paliers : survie 90 %, moyen 0 %, confort 0 %). Pourquoi : le palier « survie » n'est servi qu'en partie (90 %) : c'est là que le quota s'épuise, en concurrence avec les autres parcelles (dernier m³ : 3,11 TND) ; la culture tolère mieux le manque d'eau (Ky 0,80 < 1). Perte de récolte estimée : 17 TND sur 48 h.
  9. Marge de sécurité : +0,61 mm/j d'ET0 (erreur moyenne mesurée du modèle), portée à 0,92 mm/j (×1,5) les jours chauds (Tmax > 35 °C : J1 (06/07) 37,1 °C, J2 (07/07) 39,9 °C), car le modèle sous-estime les extrêmes : sur 2024–2025 en Tunisie, quand Tmax > 35 °C, le modèle sous-estime l'ET0 de 0,32 mm/j en moyenne (0,08 mm/j les autres jours) ; ex. 8,0 mm prévus contre 9,8 mm observés le 20/07/2025. Soit 25,7 m³ sur l'horizon. Non servie : l'eau manque déjà pour couvrir les besoins prévus.
  10. Pompage : 60 m³ (J1 (06/07) 6h–17h ; J2 (07/07) aucune heure), 24,0 kWh dont 100 % solaire.
  11. Réservoir : 40 m³ au départ, entre 27 et 79 m³ pendant l'horizon, 40 m³ à la fin (≥ niveau initial). Il découple le pompage solaire de l'irrigation : 52 m³ sont irrigués à des heures sans pompage.
  12. Comparé à l'irrigation traditionnelle (pompe dès 7h à plein débit, doses égales jusqu'au quota du jour, mêmes panneaux solaires) : réseau 10,2 → 0,0 kWh (-10,2 kWh, -2,25 TND), part solaire 57 → 100 %, satisfaction A/B/C 21/35/37 → 30/27/30 %, valeur protégée 240 → 256 TND (+17 TND), eau pompée 60 → 60 m³.
  13. Valeurs de récolte, paliers de valeur, calendrier cultural, durées de saison et tarifs électriques : hypothèses (economics.json, crops.json, crop_calendar.json) — estimation simulée.
```

## Riz à Niigata (mode Japon) (`japan_rice`)

22–23 août 2025 à Niigata : 3 rizières de 0,5 ha en submersion, quota de 150 m³/j, puits de 15 m. Tarifs et valeurs de récolte : mêmes hypothèses que la Tunisie (en TND).

```
SCÉNARIO japan_rice — Riz à Niigata (mode Japon)
22–23 août 2025 à Niigata : 3 rizières de 0,5 ha en submersion, quota de 150 m³/j, puits de 15 m. Tarifs et valeurs de récolte : mêmes hypothèses que la Tunisie (en TND).
ET0 utilisée : [4.036, 4.98] mm/j (modèle LightGBM (7 jours précédents) ; Open-Meteo (archive)) | pluie [0.0, 0.3] mm | ensoleillement [1.195, 1.209] | marge [0.643, 0.643] mm/j | 0.0818 kWh/m³
Cultures : A Riz (mi-saison, jour 114/150, Kc 1.2) ; B Riz (mi-saison, jour 114/150, Kc 1.2) ; C Riz (mi-saison, jour 114/150, Kc 1.2)
Facteur limitant : none | résolu en 130 ms

KPI (48 h)                          Optimisé  Traditionnel
Besoin prévu (m³)                        227           227
Quota (m³)                               300           300
Eau pompée (m³)                          260           300
Énergie de pompage (kWh)                21.2          24.5
  dont solaire (kWh)                    21.2          24.1
  dont réseau (kWh)                      0.0           0.4
Part solaire (%)                         100            98
Coût réseau (TND)                       0.00          0.09
Satisfaction A (%)                       100           100
Satisfaction B (%)                       100           100
Satisfaction C (%)                       100           100
Satisfaction minimale (%)                100           100
Valeur protégée (TND)                    550           550
Valeur perdue (TND)                        0             0
Réservoir max (m³)                       100            50

kWh réseau évités vs traditionnel : +0.4 kWh (+0.09 TND) | valeur protégée : +0 TND | eau économisée : +40 m³
Horizon 48 h vs deux plans de 24 h : +0.0 kWh réseau évités, gain net -0.03 TND

Explications :
  1. Besoin prévu sur 48 h : 227 m³ (ET0 J1 (22/08) 4,04 mm (modèle LightGBM (7 jours précédents)) ; J2 (23/08) 4,98 mm (Open-Meteo (archive))) pour un quota de 300 m³ : le quota couvre les besoins prévus (73 m³ de reste).
  2. Cultures du jour (calendrier cultural) : A Riz (mi-saison, jour 114/150, Kc 1,20, submersion) ; B Riz (mi-saison, jour 114/150, Kc 1,20, submersion) ; C Riz (mi-saison, jour 114/150, Kc 1,20, submersion).
  3. Aucune ressource n'est limitante : chaque parcelle reçoit 100 % de son besoin prévu.
  4. Parcelle A (Riz) : 100 % du besoin servi (75,6 m³), valeur protégée 183 TND.
  5. Parcelle B (Riz) : 100 % du besoin servi (75,6 m³), valeur protégée 183 TND.
  6. Parcelle C (Riz) : 100 % du besoin servi (75,6 m³), valeur protégée 183 TND.
  7. Marge de sécurité : +0,64 mm/j d'ET0 (erreur moyenne mesurée du modèle). Soit 33,1 m³ sur l'horizon. Servie pour : A (11,0 m³), B (11,0 m³), C (11,0 m³).
  8. Pompage : 260 m³ (J1 (22/08) 5h–16h ; J2 (23/08) 5h–12h), 21,2 kWh dont 100 % solaire.
  9. Réservoir : 50 m³ au départ, entre 0 et 100 m³ pendant l'horizon, 50 m³ à la fin (≥ niveau initial). Il découple le pompage solaire de l'irrigation : 142 m³ sont irrigués à des heures sans pompage.
  10. Comparé à l'irrigation traditionnelle (pompe dès 7h à plein débit, doses égales jusqu'au quota du jour, mêmes panneaux solaires) : réseau 0,4 → 0,0 kWh (-0,4 kWh, 0,09 TND), part solaire 98 → 100 %, satisfaction A/B/C 100/100/100 → 100/100/100 %, valeur protégée 550 → 550 TND (+0 TND), eau pompée 300 → 260 m³.
  11. Valeurs de récolte, paliers de valeur, calendrier cultural, durées de saison et tarifs électriques : hypothèses (economics.json, crops.json, crop_calendar.json) — estimation simulée.
```
