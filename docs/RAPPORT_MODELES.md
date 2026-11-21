# Rapport de vérification des modèles AquaMind — version 3

*Vérification du 21/11/2026, après les corrections des notebooks 02, 03 et 04, puis de l'essai v3 du notebook 03 (§7) et de la règle adaptative du notebook 04 (§8). Sources : sorties enregistrées dans les `.ipynb`, fichiers de `models/artifacts/`, runs MLflow (`mlruns/`). Aucun notebook n'a été exécuté par moi ; seule correction hors notebook : la clé `_location` retirée de `data/static/farm.json` (§1.1).*

---

## 0. En bref

| Notebook | Exécuté avec le code actuel ? | Bat sa référence ? | Généralise ? | Utile en pratique ? | Statut |
|---|---|---|---|---|---|
| 01 EDA | ✅ (inchangé) | sans objet | sans objet | ✅ | **Prêt** |
| 02 ET0 LightGBM | ✅ réexécuté jusqu'au bout, artefacts réécrits (20:02) | ✅ persistance battue sur les 2 sites | ✅ test stable (0,605 → 0,628) ; écart train/test ×1,6, attendu (§2.1 b) | ✅ 11,0 m³/j d'erreur = 18,3 % du quota | **Prêt** |
| 03 Fuites BattLeDIM (v3, §7) | ✅ | ❌ **ne bat pas des alertes régulières** (p = 0,29) | ❌ 0,98 FA/sem. en validation → 1,61 en test | ❌ 82 % des alertes sont fausses | **Pas utilisable pour alerter** (critère non atteint, essai clos) ; indicateur lent seulement |
| 04 River + règle adaptative (§8) | ✅ (5 graines) | ❌ River ne bat pas la règle ; ✅ la **règle adaptative** bat la règle fixe sous dérive (6,1 → 0,8 FA/j) | ✅ 5 graines × 2 scénarios | ✅ 97–100 % des fuites, < 1,1 s | **Phase 5 : règle adaptative (alerte) + River (indice de confirmation)** |

---

## 1. Intégrité

### 1.1 Exécution après les dernières modifications

Le code de chaque notebook a été comparé cellule par cellule à la dernière version générée : **identique pour les 4**.

| Notebook | Compteurs d'exécution | Erreurs | Verdict |
|---|---|---|---|
| 01 | 1 → 11, séquentiels | 0 | ✅ exécuté (code inchangé depuis la 1re exécution) |
| 02 | 10 → 18, puis **vides** | **1** | ⚠️ **réexécuté jusqu'à la cellule « m³ »**, qui plante : `ValidationError … Farm _location: Extra inputs are not permitted`. J'avais ajouté une clé de commentaire `_location` dans `data/static/farm.json` en déplaçant la ferme, et le schéma `Farm` interdit les clés inconnues. **Corrigé** : clé retirée (la position est documentée dans PROMPT_AQUAMIND.md) ; `load_farm()` revérifié dans les deux venvs. **Troisième exécution (compteurs 19 → 31, 0 erreur, 18,4 s)** : tout passe ; artefacts `et0_*` réécrits à 20:02, avec `mae_train_val_test` et `farm_translation` ; assertion de rechargement ✅. |
| 03 | 27 → 40, séquentiels | 0 | ✅ exécuté jusqu'au bout (17,8 s). Deux runs MLflow (16:34 et 16:35 UTC) aux **résultats identiques** : le calcul est déterministe. |
| 04 | 12 → 24, séquentiels | 0 | ✅ exécuté jusqu'au bout (43,1 s). La cellule 5 n'a pas de sortie : elle ne fait que définir des fonctions, c'est normal. |

> Les modèles et métriques du **notebook 02 restent valides** : la réexécution redonne exactement les mêmes chiffres de test (MAE 0,614 / 0,643 / 0,628, même meilleur essai, 119 arbres), ce qui confirme le déterminisme. Il reste à la relancer **une fois** (Run All, ~1 min) pour que `et0_metrics.json` contienne `mae_train_val_test` et `farm_translation`, et que l'assertion de rechargement repasse.

### 1.2 Artefacts vs chiffres affichés

| Artefact | Contrôle | Verdict |
|---|---|---|
| `leak_metrics.json` | IF 13/19, 1,17 FA/sem., délai médian 43 h, p = 0,039 ; CUSUM 3/19, 0,06 FA/sem. = cellules 9 et 14 du notebook 03 | ✅ |
| `leak_threshold.json` | méthode recommandée IsolationForest ; seuils 0,4277 (IF) et 22,12 (CUSUM) ; pause de 72 h = cellule 8 | ✅ |
| `iforest.joblib` | réécrit à 17:35 | ✅ |
| `river_config.json` (20:09) | (25, 10, 288), seuil 0,3317, 2 messages consécutifs, `rule_baseline` 0,5 m³/h, `adaptive_rule` (36 / 6 / 48 messages), **`recommended_mode = rule_adaptive`** = sorties de la section 6 ter | ✅ |
| `river_metrics.json` (20:09) | F1 test 0,9176, hybride 0,9231, `river_better_than_rule = false`, `multi_seed.adaptive_fixes_drift = true` | ✅ |
| `et0_*` | réécrits à 20:02 ; MAE test, MAE train/val/test et traduction m³ = sorties de la cellule de résumé | ✅ |

### 1.3 Assertions de rechargement

- **03**, cellule 13 : le message « Artefacts écrits » s'affiche **après** l'assertion `allclose` du modèle rechargé → ✅ passée.
- **04**, cellule 12 : « config rechargée → scores identiques » → ✅ passée.
- **02** : « Artefacts écrits et rechargés sans écart » à la 3e exécution → ✅ passée.

---

## 2. Verdict par notebook

### 2.1 Notebook 02 — ET0 J+1 (chiffres officiels de la réexécution)

**(a) Bat-il sa référence ? ✅ Oui** (cellule 7)

| Site | LightGBM MAE | Persistance MAE | Gain | Hargreaves MAE (indicatif) |
|---|---|---|---|---|
| Tunisie | **0,614** mm/j | 0,687 | **−10,7 %** | 0,694 |
| Japon | **0,643** | 0,767 | **−16,1 %** | 0,707 |
| Global | **0,628** | 0,727 | **−13,6 %** | 0,700 |

Au Japon, Hargreaves a un RMSE (0,852 contre 0,874) et un R² (0,650 contre 0,632) légèrement meilleurs. Il utilise cependant les températures **réelles** de J+1 : la comparaison l'avantage.

**(b) Généralise-t-il ? ✅ Oui**
- MAE validation 2023 = 0,605 → test 2024–2025 = 0,628 (+3,9 %), écart normal.
- Les 10 meilleurs réglages sont entre 0,605 et 0,614 : le choix est robuste.
- **MAE d'entraînement mesurée** (cellule 8 de la réexécution) :

  | MAE (mm/j) | Global | Tunisie | Japon |
  |---|---|---|---|
  | train ≤ 2022 (en-échantillon) | 0,375 | 0,362 | 0,389 |
  | validation 2023 | 0,605 | 0,556 | 0,654 |
  | train + val ≤ 2023 (modèle final, en-échantillon) | 0,390 | 0,376 | 0,404 |
  | **test 2024–2025** | **0,628** | **0,613** | **0,644** |

  L'erreur est 1,6 fois plus grande hors échantillon, et le notebook affiche « écart important : le modèle apprend en partie par cœur ». **Mon interprétation : c'est attendu et ce n'est pas un problème de généralisation.**
  - Un boosting ajuste toujours mieux ses propres jours, car une partie de la variation d'ET0 d'un jour à l'autre (front, nuages) est imprévisible à partir des 7 jours passés. Le modèle en apprend une partie sur l'entraînement, sans que cela se transfère.
  - Ce qui compte est **stable** : validation 0,605 → test 0,628 (+4 %), sur deux années jamais vues.
  - Régulariser davantage n'aiderait guère : les 10 meilleurs essais, y compris les plus régularisés (`min_child_samples` 80, `reg_lambda` 5), sont tous entre 0,605 et 0,614 en validation.

  Le message automatique du notebook est donc trop alarmiste. À dire au jury : « 0,38 sur l'entraînement, 0,63 sur deux années jamais vues ; l'écart est habituel, et la performance hors échantillon est stable entre 2023 et 2024–2025. »
- Fuite de données : **aucune** (re-vérifié : variables arrêtées au jour J, `hargreaves`/`persistence` exclues, découpage sur la date cible). Le code d'entraînement n'a pas changé.

**(c) Utile en pratique ? ✅ Oui**

Chiffres confirmés par la 3e exécution (cellule 9 et `farm_translation` dans `et0_metrics.json`) :
- 1 mm/j d'ET0 = Σ Kc × surface × 10 / efficience = 1,15×5/0,9 + 0,70×5/0,9 + 1,15×5/0,75 = **17,94 m³/j** pour la ferme de démo.
- Erreur LightGBM = 0,6135 × 17,94 = **11,0 m³/j, soit 18,3 % du quota de 60 m³/j** ; persistance : 12,3 m³/j (20,5 %).
- **Besoin brut moyen 2024–2025 : 83 m³/j, au-dessus du quota de 60 m³/j** (sans pluie, parcelles au stade « mid »). La ferme de démo est donc structurellement en pénurie : le moteur de la Phase 4 devra **arbitrer entre parcelles**, et pas seulement planifier. C'est un bon argument pour la démo.
- Gain par rapport à la persistance : ≈ **1,3 m³/j**. C'est un gain réel mais modeste : la prévision de demain reste une source d'incertitude importante pour le plan.

### 2.2 Notebook 03 — fuites BattLeDIM (nouvelle évaluation par incident)

**(a) Bat-il ses références ?** (cellule 9)

| | IsolationForest | CUSUM pression | Hasard (même nombre d'alertes que l'IF) |
|---|---|---|---|
| Fuites 2019 détectées (≤ 72 h) | **13/19** | 3/19 | 8,8 en moyenne |
| dont brusques / progressives | 5/9 · 8/10 | 3/9 · 0/10 | — |
| Alertes émises | 74 | 6 | 74 |
| Fausses alertes | **1,17/sem.** (61 sur 74) | 0,06/sem. (3 sur 6) | — |
| Précision des alertes | **18 %** | 50 % | — |
| Délai médian | 43 h | 23 h | ≈ 36 h attendu au hasard |
| p-valeur vs hasard | 0,039 | 0,043 | — |
| % du temps au-dessus du seuil | **65 %** | 61 % | — |

- **IF contre CUSUM** : l'IF détecte plus de fuites (13 contre 3), mais émet 12 fois plus d'alertes. Ils ne se situent pas au même point de fonctionnement, ce qui rend la comparaison peu équitable.
- **IF contre hasard** : 13 contre 8,8, p = 0,039. C'est mieux que des alertes tirées au hasard, **mais le résultat est fragile** :
  1. Le score dépasse le seuil **65 % du temps** (graphique de la cellule 12). Les alertes sont donc espacées **presque régulièrement**, au rythme de la pause de 72 h : environ une tous les 5 jours.
  2. Des alertes régulières couvrent mieux les fenêtres de 72 h que des alertes aléatoires (qui se regroupent parfois). Une **alerte périodique tous les 5 jours** détecterait en moyenne 19 × 3/4,9 ≈ **11,6 fuites**, presque autant que l'IF (13). Notre référence aléatoire ne teste pas ce cas.
  3. Le délai médian de 43 h est proche du délai d'une alerte tombée au hasard dans la fenêtre (≈ 36 h). De plus, les fuites progressives (8/10) sont « mieux » détectées que les brusques (5/9), ce qui est physiquement improbable : signe de **coïncidences**.
- **CUSUM** : peu d'alertes, mais son signal est **physiquement lisible** (cellule 12). Il monte nettement après chaque grosse fuite brusque (janvier, avril, juin, octobre), avec un retard de plusieurs jours, d'où peu de détections dans la fenêtre de 72 h.

**(b) Généralise-t-il ? ⚠️ Fragile**
- Les fausses alertes sont stables entre la validation et le test (IF : 0,98 → 1,17/sem.). Le calage sur le budget a donc fonctionné.
- La détection, elle, est imprévisible. La validation ne contient que **2 fuites** (IF 1/2, CUSUM 2/2). Le tableau de sensibilité au budget (cellule 10) est **non monotone** : 18/19 avec un budget de 0,5, 13/19 avec 1, 10/19 avec 2, pour presque le même taux de fausses alertes en test (1,30 / 1,17 / 1,11). Les résultats dépendent fortement du seuil, et ce seuil est mal déterminé avec 2 fuites.
- **Fuite de données : aucune** (re-vérifié après les modifications). Les résidus sont comparés à `rolling(48).median().shift(1)` (passé uniquement), les seuils sont calés sur `period_mask("val")`, les échelles et les voisins viennent de janvier–août 2018. ⚠️ Seule réserve : la « méthode recommandée » est choisie en regardant 2019. C'est une légère sélection sur le test, sans effet sur les seuils.

**(c) Utile en pratique ? ❌ Non, pas pour déclencher des alertes**
Environ 1,4 alerte par semaine, dont **4 sur 5 fausses**, avec un délai médian de presque 2 jours. Aucun exploitant ne garderait ce système. Le CUSUM est sobre (1 alerte tous les 2 mois) mais rate 16 fuites sur 19.

### 2.3 Notebook 04 — River vs règle fixe

**(a) Bat-il la règle ? ❌ Non** (cellules 8 et 10)

| Flux | Méthode | F1 | Fuites | Délai | Fausses alertes/jour |
|---|---|---|---|---|---|
| Test nominal (graine 43) | River | 0,918 | 12/12 | 0,67 msg (0,56 s) | 0,91 |
| | **Règle** \|déséquilibre\| > 0,5 | **0,923** | 12/12 | 1,0 msg (0,83 s) | **0** |
| | **Hybride** (règle ET River) | **0,923** | 12/12 | 1,0 msg (0,83 s) | **0** |
| Dérive, après le jour 7 (graine 44) | River | 0,475 | 6/6 | 0,33 msg | 4,4 |
| | Règle | 0,618 | 6/6 | 0,83 msg | 5,9 |
| | **Hybride** | **0,631** | 6/6 | 0,83 msg | 5,6 |
| Dérive, après adaptation (+288 msg) | River | 0,563 | 5/5 | 0,4 msg | **4,2** |
| | Règle | 0,677 | 5/5 | 0,8 msg | 5,4 |
| | Hybride | 0,693 | 5/5 | 0,8 msg | 5,0 |

**River ne s'adapte pas à la dérive** en 2 jours : 4,2 fausses alertes par jour même après adaptation (graphique de la cellule 11 : River déclenche à chaque créneau d'irrigation, comme la règle). Explication probable :
- le rinçage n'a lieu **que pendant le pompage** (≈ 40 % des messages) ;
- la valeur de 0,6 m³/h, bruitée, se répartit sur de nombreuses « cases » d'arbres profonds (`height = 10`) ;
- chaque case reste donc peu peuplée, et ces messages restent « rares » aux yeux de River.

À noter aussi : la valeur du rinçage (0,6) est **à peine au-dessus du seuil de la règle (0,5)**. Le bruit fait passer le déséquilibre de part et d'autre du seuil, ce qui multiplie les fronts montants : un cas défavorable pour la règle comme pour River.

**(b) Généralise-t-il ? ✅ en régime nominal** (F1 validation 0,911 → test 0,918). Le scénario de dérive est un test **hors distribution** volontaire. Fuite de données : aucune (configuration et seuils figés avant les flux de test et de dérive). Le mode recommandé est choisi sur le flux de dérive lui-même (légère sélection).

**(c) Utile en pratique ?**
- **Régime nominal** : oui, délai ≤ 1 message, soit **< 1 s réelle** en démo (critère Phase 5 : < 10 s).
- **Après un changement de plan légitime** : non, environ 5 fausses alertes par jour pour **toutes** les méthodes. Il faut un mécanisme de recalage (voir §3.2).

---

## 3. Analyse détaillée

### 3.1 Notebook 03 — fuite par fuite (2019, IsolationForest, cellule 11)

| Fuite | Type | Débit max (m³/h) | Détectée ≤ 72 h | Délai (h) |
|---|---|---|---|---|
| p523 | brusque | 28,4 | ✅ | 2 |
| p827 | brusque | 26,5 | ❌ | — |
| p280 | brusque | 5,3 | ✅ | 10 |
| p653 | progressive | 18,3 | ✅ | 43 |
| p710 | brusque | 5,6 | ✅ | 53 |
| p514 | brusque | 15,6 | ✅ | 34 |
| p331 | brusque | 10,9 | ❌ | — |
| p193 | progressive | 10,4 | ✅ | 60 |
| p277 | progressive | 7,4 | ✅ | 56 |
| p142 | brusque | 27,0 | ✅ | 1 |
| p680 | brusque | 5,4 | ❌ | — |
| p586 | progressive | 20,5 | ✅ | 38 |
| p721 | progressive | 13,2 | ✅ | 51 |
| p800 | progressive | 22,0 | ❌ | — |
| p123 | progressive | 9,2 | ❌ | — |
| p455 | progressive | 11,1 | ✅ | 22 |
| p762 | progressive | 15,7 | ✅ | 43 |
| p426 | brusque | 13,6 | ❌ | — |
| p879 | progressive | 10,9 | ✅ | 43 |

Seules **3 détections** sont rapides et crédibles physiquement : **p523 (2 h), p142 (1 h), p280 (10 h)**, des ruptures brusques. Les autres arrivent entre 22 et 60 h après le début, ce qu'on obtiendrait aussi avec des alertes régulières.

**Méthode recommandée pour l'application** :
- **Pour le réseau de la ferme** : **ni l'IF BattLeDIM ni le CUSUM comme déclencheurs d'alerte**. La ferme a un compteur sur chaque tronçon (S1–S5), donc le **bilan de masse** est disponible et beaucoup plus fiable (notebook 04). BattLeDIM n'a que 3 débitmètres pour 905 conduites : c'est un problème bien plus difficile.
- **Rôle de l'IF** : indice d'anomalie **lent, affiché sans alerter** (« santé du réseau »), et preuve que la méthode a été confrontée à un benchmark public.
- **Rôle du CUSUM** : à garder comme indicateur **lisible** de chute de pression cumulée (« la pression baisse depuis X jours »), utile pour les fuites qui s'installent.

### 3.2 Notebook 04 — River apporte-t-il quelque chose ?

**Mesuré : non.** La règle fait au moins aussi bien partout et ne coûte rien. River n'apporte qu'un délai légèrement plus court en nominal (0,67 contre 1,0 message), au prix de 0,9 fausse alerte par jour. L'hybride « règle ET River » obtient le meilleur F1 partout (0,923 nominal, 0,631 sous dérive), car River filtre quelques déclenchements de la règle.

**Architecture proposée pour la Phase 5** (alertes à niveaux) :

```
message débit (S1..S5) ──► déséquilibre = S1 − (S2+S3+S4+S5)
                          │
                          ├─► RÈGLE ADAPTATIVE : |déséquilibre − référence| > 0,5 m³/h pendant 2 messages
                          │     référence = médiane du déséquilibre sur les dernières heures de pompage
                          │     (+ bouton « nouveau normal » pour recaler après un changement voulu)
                          │
                          └─► RIVER HalfSpaceTrees (score_one puis learn_one)

  règle adaptative → 🔴 « Fuite détectée »  (alerte + estimation m³/j, kWh, TND)
     + River d'accord → mention « confirmée par l'IA » (n'empêche jamais l'alerte)
  River seul       → ⚪ indice affiché, pas d'alerte
  (mis à jour après le §8 : sur 5 graines, River ne filtre aucune fausse alerte de la règle adaptative,
   donc l'exiger n'apporte rien ; recommended_mode = rule_adaptive)
  IF BattLeDIM / CUSUM → indicateurs lents de santé du réseau (tableau de bord)
```

- La **référence adaptative** de la règle traite la dérive simplement : un rinçage constant de 0,6 m³/h est absorbé dans la référence après quelques heures. Une fuite reste un **saut** par rapport à cette référence.
- Le **bouton « nouveau normal »** est la solution d'exploitation la plus fiable : l'agriculteur sait quand il change son installation.
- ⚠️ La règle adaptative n'est **pas encore testée** : correction n°2 (§6).

### 3.3 Stabilité des résultats

| Notebook | Ce qui a été testé | Stabilité |
|---|---|---|
| 02 | 1 découpage temporel (train ≤ 2022 / val 2023 / test 2024–25), 1 graine, 20 réglages | ✅ bonne : réglages à ±1,5 %, écart validation/test faible. Pas de validation croisée temporelle (plusieurs années de test). |
| 03 | 1 découpage (2018 / 2019), 1 graine IF, seuil calé sur **2 fuites** | ⚠️ **instable** : la sensibilité au budget est non monotone (18 / 13 / 10 détections). Pas de test multi-graines. |
| 04 | 1 graine par flux (42 validation, 43 test, 44 dérive), 12 fuites en test, **0 pic isolé** dans le flux de test, 1 seul scénario de dérive | ⚠️ moyenne : chiffres plausibles, mais un seul tirage. La robustesse aux pics n'est pas testée sur le test. |

---

## 4. Avant / après

| Notebook | Indicateur | 1re exécution | Maintenant |
|---|---|---|---|
| **02** | MAE test Tunisie / Japon / global | 0,614 / 0,643 / 0,628 | **identique (non réexécuté)** |
| | MAE train | non mesurée | non mesurée (cellule prête) |
| | Erreur en m³/j (% quota) | — | ≈ 11,0 m³/j (18 %), calcul manuel |
| **03** | Évaluation | point par point (F1) + événements sur 14 j | **par incident** (alerte + pause de 72 h, détection ≤ 72 h) |
| | Détections 2019 (IF) | 19/19 *(artefact : alarme 48 % du temps, dont 6 déjà actives avant la fuite)* | **13/19** (5/9 brusques, 8/10 progressives) |
| | Fausses alarmes | 70 h/semaine | **1,17 alerte/semaine** |
| | Délai médian | 5 h *(non significatif)* | 43 h |
| | Temps en alarme / au-dessus du seuil | 48 % | 65 % au-dessus du seuil |
| | F1 point par point | 0,242 (< 0,273 en alarme permanente) | supprimé (inadapté) |
| | Référence simple | aucune | CUSUM : 3/19, 0,06 FA/sem. |
| | Comparaison au hasard | aucune | IF p = 0,039 (fragile) ; CUSUM p = 0,043 |
| **04** | Test nominal River | F1 0,918, 12/12, 0,67 msg, 0,91 FA/j | **identique** |
| | Règle fixe | F1 0,923, 0 FA/j | identique |
| | Hybride | — | F1 0,923, 0 FA/j |
| | Dérive (après jour 7) | — | River 0,475 (4,4 FA/j) · Règle 0,618 (5,9) · **Hybride 0,631 (5,6)** |

---

## 5. Pour le jury

### 5.1 Chiffres que tu PEUX annoncer

1. **« Notre modèle prévoit la demande en eau de l'atmosphère du lendemain avec 0,61 mm/j d'erreur moyenne à Sidi Bouzid, testé sur 2024–2025. »** Ce sont deux années jamais vues à l'entraînement.
2. **« C'est 11 à 16 % plus précis que de supposer que demain ressemblera à aujourd'hui. »** Gain mesuré contre la persistance, la référence standard, sur les deux sites.
3. **« Sidi Bouzid a besoin de 1 636 mm d'eau par an et n'en reçoit que 228 par la pluie. »** 11 ans de données Open-Meteo : le déficit justifie d'optimiser chaque m³.
4. **« Sur 10 flux simulés, notre bilan de masse adaptatif détecte 107 fuites sur 109, en environ 1 seconde de démo. Après un changement d'installation, il divise les fausses alertes par 8 par rapport à un seuil fixe (6,1 → 0,8 par jour). »** Toujours dire : *estimation simulée, 5 graines* (§8).
4bis. *(chiffre précédent, 1 graine)* « Sur la ferme simulée, le bilan de masse détecte 12 fuites sur 12 en moins d'une seconde de démo, sans fausse alerte en fonctionnement normal. » Toujours dire : *estimation simulée* (règle + River, notebook 04).
5. **« Notre erreur de prévision représente environ 11 m³/j, soit 18 % du quota ; c'est pourquoi le plan est recalculé à chaque nouvelle prévision. »** Ce chiffre honnête montre que vous maîtrisez l'incertitude.

### 5.2 Chiffres à NE PAS annoncer

| Chiffre | Pourquoi |
|---|---|
| « 19/19 fuites détectées » | artefact de la 1re version (alarme allumée la moitié du temps) |
| « 18/19 » (budget 0,5 dans le tableau de sensibilité) | choisi en regardant le test et instable : c'est de la sélection a posteriori |
| « 13/19 fuites détectées » **seul** | à donner seulement avec « 4 alertes sur 5 sont fausses » et « à peine mieux que des alertes régulières » |
| « p = 0,039, donc significatif » | la référence aléatoire ne couvre pas le cas d'alertes régulières, et 2 méthodes × 3 budgets ont été testés |
| « 18/19 fuites détectées » (notebook 03 v3) | 102 alertes en 2019, dont 82 % fausses ; des alertes régulières au même rythme en détectent 16 en moyenne et 18 dans 5 % des cas (§7) |
| « F1 0,92 » comme performance réelle | données simulées par nous-mêmes |
| « River s'adapte aux changements » | **mesuré faux** dans notre scénario de dérive (4,0 ± 0,9 fausses alertes/jour sur 5 graines) |
| « River améliore la détection » | sur 5 graines, l'hybride adaptatif fait exactement comme la règle adaptative seule |
| « Meilleur que Hargreaves au Japon » | faux en RMSE/R², et Hargreaves y utilisait les températures réelles de demain |

### 5.3 Limites à dire honnêtement

- Le modèle ET0 n'utilise **pas de prévision météo** : il lisse les journées exceptionnelles.
- La détection de fuites sur BattLeDIM (réseau urbain, 3 débitmètres) reste **difficile** : notre détecteur ne fait guère mieux que des alertes régulières. Notre ferme, elle, a un compteur par tronçon, et c'est ce qui rend le bilan de masse efficace.
- Tous les résultats temps réel viennent d'un **simulateur que nous avons écrit** : ce sont des estimations simulées.
- Un **changement d'installation légitime** (rinçage, nouvelle vanne) provoque des fausses alertes tant que la référence n'est pas recalée : d'où le bouton « nouveau normal ».
- Une seule graine et un seul découpage temporel par expérience : pas d'intervalle de confiance sur les métriques.

### 5.4 Cinq questions probables et réponses courtes

1. **« Votre IA de détection de fuites fonctionne-t-elle vraiment ? »**
   Sur la ferme, la détection repose d'abord sur la physique : l'eau qui entre doit égaler l'eau qui sort. L'IA (River) confirme l'alerte. Sur le benchmark urbain BattLeDIM, nous avons testé honnêtement : c'est difficile avec 3 capteurs de débit, et nous le montrons.
2. **« Pourquoi garder River si une règle fait aussi bien ? »**
   *(Réponse mise à jour après le §8.)* Mesuré sur 5 graines : la règle adaptative fait aussi bien seule, et c'est elle qui déclenche l'alerte. River sert de second avis indépendant, sans modèle physique du réseau : il confirme presque toutes les alertes, et il est le seul des deux à fonctionner sans connaître la topologie des compteurs. Nous l'affichons comme indice, sans lui donner le pouvoir de bloquer une alerte. *(Ancienne réponse :)* Combinées, une alerte « confirmée » demande l'accord des deux : le F1 est le meilleur et les fausses alertes sont nulles en régime normal. River est aussi un indice continu, sans seuil physique à régler pour chaque ferme.
3. **« Comment évitez-vous le surapprentissage ? »**
   Découpage par dates (jamais aléatoire), test sur 2024–2025, early stopping. L'erreur passe de 0,605 en validation à 0,628 en test : écart faible.
4. **« Que se passe-t-il si l'agriculteur modifie son installation ? »**
   C'est notre scénario de dérive. Une règle fixe et River déclenchent tous les deux à tort (6 et 4 fausses alertes par jour). Avec une référence adaptative et un bouton « nouveau normal », on tombe à 0,8 par jour sans manquer une fuite (41/41, 5 graines).
5. **« 0,6 mm/j d'erreur, c'est beaucoup ? »**
   Environ 11 m³/j sur la ferme de démo (18 % du quota). C'est pourquoi le plan est recalculé à chaque nouvelle prévision, et une marge de sécurité peut être ajoutée.

---

## 6. Décision

**Phase 4 (moteur de décision) : ✅ prête.** Elle n'utilise que le modèle ET0 (notebook 02), qui est valide et défendable.

**Phases 5 à 7 : ✅ prêtes, avec l'architecture du §3.2 mise à jour par le §8.** La détection temps réel s'appuie sur le bilan de masse avec référence adaptative (`recommended_mode = rule_adaptive`) ; River est affiché comme indice de confirmation ; l'IF BattLeDIM et le CUSUM ne servent que d'indicateurs lents, sans alerte.

Corrections, par priorité (**rien n'est appliqué sans ton accord**) :

| # | Correction | Pourquoi | Temps |
|---|---|---|---|
| 1 | ~~Réexécuter le notebook 02~~ | **fait** : chiffres officiels et artefacts à jour | — |
| 2 | ✅ **fait (§8)** — ~~Notebook 04 : tester la « règle adaptative »~~ (déséquilibre − médiane des dernières heures de pompage) dans le scénario de dérive, et évaluer **sur 5 graines** | valider l'architecture de Phase 5 avant de la coder ; donner des chiffres stables | 25 min d'écriture + 2 min d'exécution |
| 3 | ~~Notebook 03 : référence « alertes régulières »~~ | **fait en v3** (§7) : l'IF ne la bat pas | — |
| 4 | ✅ couvert par les 5 graines (§8) — ~~pics isolés dans le flux de test~~ | | |

**Toutes les corrections sont faites.** Les notebooks 01 à 04 sont à jour et leurs artefacts cohérents. **On ne retouche plus le notebook 03** : l'essai de 1 h est clos (§7).

---

## 7. Notebook 03 v3 — essai d'une logique d'alerte (résultat : critère non atteint)

*Sorties enregistrées du notebook 03 v3, exécuté le 21/11/2026. Essai limité à 1 h, sans changer d'algorithme.*

**Ce qui a été essayé**, pour IsolationForest et pour CUSUM :
- une référence **à la même heure sur les 7 jours précédents** ;
- une remise à zéro (« nouveau normal ») après chaque alerte : référence reconstruite avec les seules données postérieures, CUSUM remis à 0 ;
- une persistance de **3 dépassements sur 4 h** ou **4 sur 6 h**, puis une pause de 72 h ;
- un seuil choisi sur la validation sept.–déc. 2018 : le plus bas tel que tous les seuils au-dessus donnent ≤ 1 fausse alerte par semaine.

**Critère fixé à l'avance** : battre des alertes régulières (p < 0,05) avec ≤ 1 fausse alerte par semaine en 2019.

### 7.1 Résultats 2019 (19 nouvelles fuites, fenêtre de 72 h)

| | IF 3/4 | **IF 4/6** | CUSUM 3/4 | CUSUM 4/6 |
|---|---|---|---|---|
| Fausses alertes/sem. en validation 2018 | 0,98 | 0,98 | 0,98 | 0,80 |
| Détectées /19 | 18 | **18** | 14 | 14 |
| Brusques /9 · progressives /10 | 9 · 9 | 9 · 9 | 7 · 7 | 7 · 7 |
| Délai médian | 44,5 h | **25 h** | 37 h | 32 h |
| Alertes émises | 105 | 102 | 86 | 85 |
| **Fausses alertes/sem.** | 1,67 | **1,61** | 1,38 | 1,36 |
| Part d'alertes justes | 17 % | 18 % | 16 % | 16 % |
| Alertes aléatoires, même nombre (moyenne) | 11,1 | 10,9 | 9,8 | 9,7 |
| p contre aléatoires | 0,001 | < 0,001 | 0,033 | 0,029 |
| **Alertes régulières, même nombre (moyenne · p95)** | 16,7 · 18 | **16,2 · 18** | 13,6 · 17 | 13,4 · 17 |
| **p contre régulières** | 0,27 | **0,29** | 0,42 | 0,54 |
| Critère atteint | ❌ | ❌ | ❌ | ❌ |

### 7.2 Interprétation

1. **Le 18/19 est un effet du rythme d'alerte, pas de la détection.** Avec une pause de 72 h, on ne peut pas émettre plus d'environ 120 alertes par an. L'IF en émet 102, soit **84 % du maximum** : il alerte presque dès que la pause se termine. Des alertes régulières au même rythme couvrent déjà 16 des 19 fenêtres de 72 h en moyenne.
2. **Le score reste au-dessus du seuil la majeure partie du temps** (29 à 73 % des heures selon le mois, plus de 50 % de février à juillet). La remise à zéro ne calme donc pas l'alarme : dès que la pause se termine, la persistance est de nouveau satisfaite.
3. **Le seuil ne se transfère pas de 2018 à 2019** : 0,98 fausse alerte par semaine en validation, 1,61 en test. En 2019, le réseau accumule des fuites jamais réparées (12 des 19 fuites de 2019 restent actives jusqu'au 31/12, en plus de 4 fuites de 2018). Notre hypothèse, non vérifiée : les variables de dispersion (écart-type du débit sur 24 h, plus grand résidu de pression) ne sont pas exprimées par rapport à la référence glissante, et elles montrent un réseau globalement plus « bruité ».
4. **Seul signal réel : la vitesse sur les fuites brusques.** p523, p827, p280, p331 et p680 sont détectées en 3 à 10 h par l'IF. Sur 5 fuites, ce n'est cependant pas statistiquement établi : des alertes régulières tous les 3,6 jours tombent aussi parfois dans les 10 h.
5. **CUSUM** est moins bon partout (14/19) et ne bat pas non plus les alertes régulières.
6. **Ce qu'on garde** : la validation 2018 ne contient que 2 fuites. « 2/2 détectées » n'y dit rien, et le seuil n'est contraint que par les fausses alertes, ce qui était voulu.

### 7.3 Décision

- **Critère non atteint → essai clos.** On ne lance pas de v4 (autres variables, autres algorithmes) : ce serait de la recherche hors du temps du hackathon.
- **On garde l'architecture hybride** (§3.2, §6) : le bilan de masse par tronçon (référence adaptative, §8) pour les alertes, River en indice de confirmation, et un bouton « nouveau normal ». L'IF BattLeDIM n'est affiché que comme **indice lent, sans alerte** (`use_for_alerting: false` dans `leak_threshold.json`).
- **À dire au jury** : « Sur un réseau urbain avec seulement 3 débitmètres, nous avons montré qu'un détecteur non supervisé ne fait pas mieux que des inspections régulières. C'est pour cela que notre ferme mesure chaque tronçon, et que la détection repose sur un bilan de masse. »

---

## 8. Notebook 04 — règle adaptative sur 5 graines (section 6 ter)

*Sorties enregistrées du notebook 04, exécuté le 21/11/2026 (compteurs 14 → 28, 0 erreur, 38,8 s). Estimations simulées.*

**Protocole.**
- 5 flux normaux (graines 43, 101–104) et 5 flux avec un rinçage légitime de 0,6 m³/h à partir du jour 7 (graines 44, 201–204).
- Normal : évalué après le préchauffage. Dérive : évalué sur **tout ce qui suit le début du rinçage**, y compris le temps d'adaptation.
- Paramètres fixés à l'avance, sans recalage : seuil 0,5 m³/h, 2 messages consécutifs, référence = médiane des 36 derniers messages de pompage normaux, « nouveau normal » après 48 messages de pompage (8 h) en écart.
- Chaque flux contient 0 à 5 pics isolés, ce qui couvre aussi la correction n°4 (robustesse aux pics).

### 8.1 Résultats (moyenne ± écart-type sur 5 graines)

| Scénario | Méthode | Fuites détectées | Délai (s réelles) | Fausses alertes / jour |
|---|---|---|---|---|
| **Normal** | River seul | 67/68 (0,99 ± 0,03) | 0,64 ± 0,12 | 0,98 ± 0,63 |
| | Règle fixe | 66/68 (0,97 ± 0,04) | 0,92 ± 0,18 | **0,00 ± 0,00** |
| | **Règle adaptative** | 66/68 (0,97 ± 0,04) | 1,02 ± 0,28 | 0,11 ± 0,20 |
| | Hybride fixe (règle ET River) | 66/68 (0,97 ± 0,04) | 0,94 ± 0,17 | 0,00 ± 0,00 |
| | Hybride adaptatif | 66/68 (0,97 ± 0,04) | 1,08 ± 0,20 | 0,13 ± 0,28 |
| **Dérive** | River seul | 41/41 (1,00) | 0,26 ± 0,14 | 4,03 ± 0,89 |
| | Règle fixe | 41/41 (1,00) | 0,56 ± 0,09 | 6,11 ± 0,54 |
| | **Règle adaptative** | 41/41 (1,00) | 0,90 ± 0,14 | **0,77 ± 0,37** |
| | Hybride fixe | 41/41 (1,00) | 0,56 ± 0,09 | 6,00 ± 0,58 |
| | Hybride adaptatif | 41/41 (1,00) | 0,90 ± 0,14 | 0,77 ± 0,37 |

Graine par graine, sous dérive, la règle adaptative fait 0,29 à 1,29 fausse alerte par jour ; la règle fixe 5,3 à 6,6. En temps normal, la règle adaptative ne fait des fausses alertes que sur une graine sur cinq (103 : 0,45/jour).

### 8.2 Interprétation

1. **La règle adaptative résout la dérive** : fausses alertes divisées par 8 (6,1 → 0,8 par jour) sans perdre une seule fuite (41/41). C'est le seul changement qui fonctionne : River seul (4,0/jour) et les deux variantes fixes restent inutilisables après un changement d'installation.
2. **Son coût en temps normal est faible** : 0,11 fausse alerte par jour au lieu de 0, et environ 0,1 s de délai en plus. Son F1 point par point est un peu plus bas (0,86 ± 0,05 contre 0,92). Hypothèse non vérifiée : les petites fuites proches du seuil entrent en partie dans la référence et raccourcissent les alarmes, sans faire manquer de fuite (66/68 dans les deux cas).
3. **Les 0,77 fausse alerte par jour sous dérive sont une moyenne sur 7 jours.** Elles se concentrent vraisemblablement pendant les 8 h de pompage avant l'acceptation automatique : chaque heure sans pompage coupe l'alarme et en crée une nouvelle. Ce n'est pas vérifié jour par jour. Dans l'application, l'agriculteur appuie sur **« nouveau normal »** dès qu'il a changé son installation, ce qui supprime cette phase.
4. **River n'apporte rien de mesurable dans ces scénarios.** L'hybride adaptatif détecte exactement les mêmes fuites que la règle adaptative, avec les mêmes fausses alertes sous dérive (et 0,13 contre 0,11 en normal). River confirme donc presque toujours la règle. Seul, il est le plus rapide (0,3 à 0,6 s), mais avec 1 fausse alerte par jour en temps normal.
5. Les écarts-types restent larges, car il n'y a qu'environ 8 fuites par flux après la dérive. Les conclusions 1 et 4 sont nettes (pas de chevauchement) ; la 2 ne l'est pas.

### 8.3 Décision

- `river_config.json` → **`recommended_mode: "rule_adaptive"`**, choisi automatiquement sur les 5 graines : la méthode avec le moins de fausses alertes sous dérive parmi celles qui détectent ≥ 90 % des fuites dans les deux scénarios. Les paramètres de la règle sont dans `adaptive_rule`.
- **Phase 5** :
  - la **règle adaptative** déclenche l'alerte 🔴, avec le bouton « nouveau normal » ;
  - **River** est affiché comme indice de confirmation (« confirmée par l'IA ») et comme score continu, sans pouvoir bloquer une alerte ;
  - l'IF et le CUSUM de BattLeDIM restent des indicateurs lents.
- Correction n°2 : ✅ faite. Correction n°4 (pics isolés) : ✅ couverte par les 5 graines (0 à 5 pics par flux, aucune fausse alerte de la règle fixe en temps normal).
