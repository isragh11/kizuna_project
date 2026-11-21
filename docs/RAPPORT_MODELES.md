# Rapport d'audit des modèles AquaMind (notebooks 01 → 04)

*Audit réalisé à partir des sorties enregistrées dans les `.ipynb` et des fichiers de `models/artifacts/`, sans rien réexécuter. Date : 21/11/2026.*

---

## 0. Synthèse en une minute

| Notebook | Exécution | Fuite de données | Résultat | Verdict |
|---|---|---|---|---|
| 01 EDA météo | ✅ complète (11/11 cellules, 9,6 s) | sans objet | ordres de grandeur réalistes | **OK** |
| 02 ET0 LightGBM | ✅ complète (11/11, 30 s) | ✅ aucune | MAE test 0,63 mm/j ; bat la persistance de 11 à 16 % | **OK**, 2 ajouts simples recommandés |
| 03 Fuites BattLeDIM | ✅ complète (12/12, 9,7 s) | ✅ aucune | ❌ **ne généralise pas à 2019** : F1 0,24, sous l'« alarme permanente » (0,27) ; alarme active 48 % du temps | **À corriger** avant la démo |
| 04 River | ✅ complète (11/11, 48 s) | ✅ aucune | F1 0,92 et délai < 1 s réelle, mais **sur données synthétiques faciles** : une règle simple fait aussi bien | **OK**, à présenter honnêtement |

**Message principal** : le notebook 02 est solide et défendable. Le notebook 04 est méthodologiquement propre, mais son score élevé vient de la facilité du flux simulé. **Le notebook 03 a un vrai problème** : ses chiffres « 19/19 fuites détectées » ne sont pas défendables devant un jury (explication en §2.2 et correction proposée en §3).

---

## 1. Vérification des résultats

### 1.1 Exécution

Les 4 notebooks ont des compteurs d'exécution séquentiels (1, 2, 3… sans trou ni répétition), **aucune sortie d'erreur**, et leur cellule finale « Résumé des métriques » est imprimée. Chacun a donc été exécuté de haut en bas en une seule passe. Les durées (10 s à 48 s) sont largement sous la limite de 10 min.

Seul message non bloquant : dans le notebook 04, cellule 7, des `INFO Using categorical units…` de matplotlib. C'est sans conséquence : les axes x du graphique de la grille sont traités comme des catégories, ce qui est voulu.

### 1.2 Artefacts

| Fichier | Présent | Cohérent avec l'affichage |
|---|---|---|
| `eda_weather_summary.json` | ✅ | ✅ mêmes valeurs que la cellule 10 du notebook 01 (ET0 4,479 / 2,698 mm/j, etc.) |
| `et0_lgbm.txt` | ✅ 344 Ko | ✅ 119 arbres (`Tree=` × 119 = `n_estimators`), 59 variables |
| `et0_features.json` | ✅ | ✅ 59 noms, dans le même ordre que dans le modèle ; aucune colonne `hargreaves`/`persistence`/`y` |
| `et0_metrics.json` | ✅ | ✅ MAE 0,6285 / 0,6135 / 0,6435 = cellule 7 du notebook 02 |
| `iforest.joblib` | ✅ 5,7 Mo | ✅ rechargement contrôlé par une assertion dans le notebook |
| `leak_features.json` / `leak_threshold.json` / `leak_metrics.json` | ✅ | ✅ seuil 0,4828, F1 0,2419, 19/19 = cellules 7–8 du notebook 03 |
| `river_config.json` / `river_metrics.json` | ✅ | ✅ (25, 10, 288), seuil 0,3317, 2 messages ; F1 0,9176 = cellule 8 du notebook 04 |

Les notebooks 02, 03 et 04 contiennent chacun une **assertion de rechargement** : on relit le fichier exporté et on vérifie qu'il donne les mêmes prédictions. Elles sont passées, donc les artefacts sont utilisables par le backend.

### 1.3 Contrôle de vraisemblance

| Grandeur | Valeur obtenue | Référence attendue | Avis |
|---|---|---|---|
| ET0 moyenne Sidi Bouzid | 4,48 mm/j, 1 636 mm/an | 1 400–1 700 mm/an en Tunisie centrale semi-aride | ✅ réaliste |
| Pluie Sidi Bouzid | 228 mm/an | ≈ 200–250 mm/an | ✅ |
| Déficit hydrique Sidi Bouzid | 1 408 mm/an | climat semi-aride, irrigation indispensable | ✅ |
| ET0 Niigata | 2,70 mm/j, 986 mm/an | 800–1 000 mm/an au Japon tempéré humide | ✅ |
| Pluie Niigata | 1 969 mm/an | ≈ 1 800–2 000 mm/an | ✅ |
| Biais Hargreaves | −0,41 à −0,46 mm/j | Hargreaves sous-estime en climat venté et sec | ✅ plausible |
| MAE ET0 J+1 | 0,61–0,64 mm/j | 0,4–0,8 mm/j sans prévision météo du lendemain | ✅ |
| F1 fuites BattLeDIM (2019) | 0,24 | les meilleures équipes BattLeDIM utilisent le modèle hydraulique ; un IsolationForest seul est bien moins bon | ⚠️ faible, voir §2.2 |
| Délais fuites BattLeDIM | médiane 5 h, moyenne 46 h | ordre de grandeur plausible pour des fuites brusques | ⚠️ non significatif (§2.2) |
| F1 River (simulé) | 0,92, délai 0,7 message | données synthétiques, fuites ≥ 0,4 m³/h pour un bruit de 0,05 m³/h | ⚠️ optimiste par construction |

---

## 2. Surapprentissage et fuite de données

### 2.1 Notebook 02 — ET0 LightGBM

**a) Aucune variable n'utilise J+1 : ✅ OK**
- *Preuve 1* (cellule 3, aperçu du tableau) : la cible de la ligne du 07/01/2015 vaut `y = 0,97`, et c'est exactement `et0_lag1` de la ligne du 08/01. La cible d'aujourd'hui devient donc la variable « dernier jour connu » de demain, sans recouvrement.
- *Preuve 2* (`et0_features.json`) : les 59 variables sont des `lag1…lag7` (jours J à J−6), des moyennes `mean3/mean7`, `amp_*`, `doy_*` et `site`. `hargreaves` (calculé avec les températures de J+1) et `persistence` sont dans `NOT_FEATURES`, absents du fichier de variables et du modèle (`feature_names` de `et0_lgbm.txt`).
- `doy_sin/doy_cos` utilisent la **date** de J+1 : c'est le calendrier, connu à l'avance, pas une fuite.

**b) Moyennes glissantes : ✅ OK, pas besoin de `shift` supplémentaire**
`rolling(3)` et `rolling(7)` se terminent au jour J (fenêtres « vers le passé » de pandas). Comme la cible est J+1, aucune moyenne n'inclut la cible. Un `shift(1)` ne serait nécessaire que si la cible était le jour J lui-même.

**c) Découpage temporel : ✅ OK**
- Cellule 4 : train = cibles du 08/01/2015 au 31/12/2022 (2 915 jours/site), validation = 2023 (365), test = 2024–2025 (731). Le découpage se fait sur la **date cible**, sans mélange aléatoire.
- Les premières lignes du test utilisent fin 2023 (la validation) comme historique. Ce n'est pas une fuite : c'est le passé, disponible en conditions réelles.
- Le test n'a servi ni à la recherche d'hyperparamètres (cellule 5, validation uniquement) ni au réentraînement (cellule 6 : train + validation).

**d) Train / validation / test : ✅ pas d'écart suspect, mais l'erreur sur le train n'est pas affichée**

| Jeu | MAE (mm/j) | Modèle |
|---|---|---|
| Train | *non calculée* | — |
| Validation 2023 | 0,605 | entraîné sur ≤ 2022, meilleur des 20 essais |
| Test 2024–2025 | 0,628 | réentraîné sur ≤ 2023 |

- Validation → test : +3,9 %. C'est normal (le test est plus loin dans le temps), donc pas de surapprentissage de la validation.
- Les 10 meilleurs essais ont tous une MAE comprise entre 0,605 et 0,614 (cellule 5). La courbe est très plate : choisir le meilleur des 20 n'a presque pas d'effet de « chance ».
- L'hyperparamètre `min_child_samples = 80` (le plus grand de la grille) et l'arrêt à 119 arbres indiquent un modèle **régularisé**, peu propice au surapprentissage.
- ⚠️ **À ajouter** : la MAE sur le train n'est pas calculée, alors que le jury peut la demander. Je propose une cellule qui l'affiche (§3). Attendu : un peu plus basse que la validation (≈ 0,5), sans écart énorme.

**e) Point à connaître (pas une fuite)** : le modèle est entraîné sur la **réanalyse** ERA5 (météo « observée »). En production, les derniers jours viendront en partie de la **prévision** Open-Meteo, car l'archive a quelques jours de retard. Ce léger décalage entraînement/production est à mentionner comme limite.

### 2.2 Notebook 03 — Fuites BattLeDIM

**a) Le seuil n'a pas été choisi sur 2019 : ✅ OK**
Cellule 7 : le seuil (0,4828) est choisi en maximisant le F1 sur `period == "val"` (septembre–décembre 2018). `leak_threshold.json` → `chosen_on` le confirme. 2019 n'est utilisé qu'en cellule 8 (évaluation).

**b) Normalisation calculée sur l'entraînement uniquement : ✅ OK**
Les échelles des z-scores (MAD) sont calculées sur `REF = period == "fit" & y_onset == 0`, soit janvier–août 2018 hors fenêtres de nouvelle fuite (cellule 4). 2019 n'intervient jamais dans les échelles.

**c) Profils hebdomadaires : ✅ OK (calcul causal, ce qui est volontaire)**
Le profil d'un instant t est la médiane des mêmes heures sur les 4 semaines **précédentes** (`shift(168 × k)`, k = 1…4). Pour une heure de 2019, on utilise donc les 4 semaines d'avant, y compris des données de 2019. Ce n'est **pas** une fuite : c'est exactement ce qu'un système en ligne connaît à cet instant. Un profil figé, calculé une fois sur 2018, serait moins réaliste (la consommation évolue).

**d) Les voisins corrélés ne regardent pas 2019 : ✅ OK**
`corr = pres_h[fit_mask].corr()`, puis `LinearRegression().fit` sur `fit_mask` (janvier–août 2018). Les médianes glissantes (`rolling(336).median().shift(1)`), le débit nocturne (`shift(1)` puis disponible à 5h) et le lissage du score (fenêtre glissante vers le passé) sont tous causaux.

**e) ❌ Problème : le détecteur ne généralise pas à 2019**

Il n'y a pas de fuite de données. En revanche, **le résultat sur 2019 n'est pas bon**, et deux chiffres affichés sont trompeurs.

| Indicateur | Validation 2018 | Test 2019 | Lecture |
|---|---|---|---|
| F1 point par point | 0,591 | **0,242** | forte chute |
| F1 « alarme permanente » | — | 0,273 | **le modèle fait moins bien qu'une alarme toujours allumée** |
| Précision | — | 0,161 | ≈ part des heures positives (0,158) → **aucun pouvoir de discrimination** |
| Heures en alarme | — | **48 %** | l'alarme est allumée une heure sur deux |
| Fausses alarmes | — | 70 h/semaine | inutilisable en exploitation |

**Pourquoi le « 19/19 détectées » n'est pas défendable** : si l'alarme est allumée 48 % du temps, presque toute fenêtre de 14 jours contient une alarme, même tirée au hasard. De plus, pour **6 fuites sur 19**, l'alarme était **déjà active avant le début** de la fuite (délais négatifs : p280, p514, p193, p123, p455, p426). Ces cas ne sont pas des détections. Le « délai médian 5 h » hérite du même défaut.

**Diagnostic** (graphiques des cellules 5 et 10) :
- La variable `p_resid_absmax` (variation du résidu entre capteurs voisins) sépare très bien les fenêtres de fuite en validation : médiane ≈ 34 contre ≈ 3 (boîtes à moustaches de la cellule 5). C'est donc un bon signal.
- Mais sa référence est la médiane des **14 jours** précédents, alors que l'étiquette ne dure que **72 h**. Après chaque nouvelle fuite, le score reste haut pendant environ 14 jours.
- En 2019, il y a **une nouvelle fuite tous les 19 jours** en moyenne, et elles s'accumulent : le débit total de fuite passe de 25 à 140 m³/h (courbe bleue de la cellule 10). Le score ne redescend presque jamais sous le seuil : il oscille autour de 0,45–0,60 alors que le seuil est à 0,48.
- Le seuil choisi sur 2018 (moins de fuites simultanées) est donc trop bas pour 2019 : c'est un **changement de distribution** entre les deux années.

**Verdict notebook 03 : à corriger.** La méthode est saine (pas de fuite de données), mais le détecteur doit raisonner en **événements** (« une nouvelle anomalie vient d'apparaître ») plutôt qu'en **état** (« le réseau est anormal »). Correction proposée en §3.

### 2.3 Notebook 04 — River

**a) Configuration choisie sur d'autres données que le score annoncé : ✅ OK**
- La grille, le seuil et `n_consec` sont choisis sur le flux de **validation** (graine 42, cellule 6). Le score final est annoncé sur un flux de **test indépendant** (graine 43, cellule 8).
- Validation F1 0,911 → test F1 0,918 : pas d'écart, donc pas de surapprentissage de la configuration.
- `river_config.json` → `calibrated_on` documente cette séparation.

**b) ⚠️ Limites qui ne sont pas des erreurs, mais à dire au jury**
1. **Tâche facile par construction** : une fuite de 0,4 à 3 m³/h rompt le bilan de masse de 0,5 à 3,75 m³/h, alors que le bruit vaut environ 0,05 m³/h. La **règle simple** « |déséquilibre| > 0,5 m³/h pendant 2 messages » obtient F1 0,923 et **0 fausse alerte**, contre F1 0,918 et 0,91 fausse alerte par jour pour HalfSpaceTrees (cellule 8). River n'apporte pas de gain de performance ici ; son intérêt est ailleurs (voir les questions du jury, §5.3).
2. **Mêmes hypothèses en entraînement et en test** : validation et test viennent du même générateur, seule la graine change. Le score mesure la cohérence avec *notre* simulateur, pas la réalité.
3. **Délai < 1 message** (0,67 en moyenne) : avec 2 messages consécutifs exigés, un délai inférieur à 1 signifie que l'alarme était parfois déjà active au début de la fuite. C'est probablement dû au bruit de transition quand la consigne change. Le chiffre est donc un peu flatteur ; parler plutôt de « détection en 1 à 2 messages (< 2 s réelles) ».
4. Le flux de test ne contient **aucun pic isolé** (8 en validation) : la robustesse aux pics n'a pas été testée sur le jeu de test.
5. La meilleure `window_size` (288) est au **bord** de la grille testée : une valeur plus grande ferait peut-être mieux. Effet faible, non bloquant.

---

## 3. Les notebooks sont-ils suffisants pour le hackathon ?

### Corrections et ajouts proposés (en attente de ton accord ; rien n'a été modifié)

| # | Ajout | Notebook | Classement | Effort |
|---|---|---|---|---|
| 1 | **Refonte de l'évaluation et du seuil de détection de fuites** (détails ci-dessous) | 03 | 🔴 **Indispensable** | ~45 min d'écriture + 2 min d'exécution |
| 2 | Afficher la MAE sur le **train**, à côté de la validation et du test | 02 | 🟠 Utile (question jury quasi certaine) | 5 min |
| 3 | **Erreur par saison/mois** (MAE par mois et par site), surtout l'été en Tunisie, pic de besoin d'irrigation | 02 | 🟠 Utile | 10 min |
| 4 | **Intervalle d'incertitude** simple : quantiles 10 %/90 % des résidus de validation, par site → « ET0 demain = 6,1 mm/j ± 0,8 » | 02 | 🟠 Utile (l'optimiseur peut prendre une marge) | 15 min |
| 5 | Traduction de la MAE en **m³ d'eau** pour la ferme de démo | 02 (ou rapport) | 🟠 Utile pour le pitch | 5 min |
| 6 | Flux de test River avec **pics isolés** et **petites fuites** (0,2–0,4 m³/h), proches du seuil de la règle | 04 | 🟡 Utile si du temps reste | 15 min |
| 7 | Étendre `window_size` au-delà de 288 | 04 | ⚪ Inutile pour 14 h | — |
| 8 | Courbe d'apprentissage (MAE selon la taille du train) | 02 | ⚪ Inutile pour 14 h | — |
| 9 | Comparer à la prévision ET0 d'Open-Meteo pour J+1 (nécessite l'API *Historical Forecast*) | 02 | ⚪ Inutile pour 14 h (à citer comme perspective) | — |
| 10 | SHAP / importance des variables | 02 | ✅ **Déjà présent** (cellule 9) | — |

### Détail de la correction n°1 (notebook 03)

1. **Alerter sur les fronts montants** : une alerte = le score franchit le seuil vers le haut, suivie d'une **période réfractaire** de 72 h (pas de nouvelle alerte pour le même incident). C'est le fonctionnement réel d'une supervision : l'opérateur reçoit *une* alerte par incident.
2. **Référence courte pour les résidus** : médiane des **48 h** précédentes au lieu de 14 jours. Le signal monte à l'apparition d'une fuite puis s'efface en environ 2 jours, ce qui correspond à l'étiquette de 72 h.
3. **Seuil sous contrainte de fausses alertes** : choisir sur la validation le seuil qui maximise la détection **avec au plus 1 à 2 fausses alertes par semaine**, au lieu du F1 point par point.
4. **Métriques par événement honnêtes** : une fuite est détectée seulement si une **nouvelle** alerte démarre après son début, dans les 72 h. Ajouter les fausses alertes par semaine et une **référence aléatoire** : des alertes tirées au hasard au même rythme. Le détecteur doit faire mieux que le hasard pour qu'un chiffre soit annoncé.
5. Ajouter un graphique du **taux d'alarme par mois** en 2019, pour rendre visible la dérive.

Résultat attendu (à confirmer à l'exécution) : la plupart des **fuites brusques** détectées en quelques heures, avec peu de fausses alertes, et des **fuites progressives** majoritairement manquées. C'est une limite connue et défendable.

---

## 4. Explication pédagogique des notebooks

### 4.1 Notebook 01 — Analyse exploratoire météo

**But.** Connaître les données avant de modéliser : sont-elles complètes ? Quels ordres de grandeur ? Quelles saisons ? Qu'est-ce qui est prévisible ?

**Données.** 11 ans (2015–2025) de météo journalière Open-Meteo pour Sidi Bouzid et Niigata, soit 4 018 jours par site. L'**ET0** (évapotranspiration de référence, en mm/jour) est la quantité d'eau qu'un gazon bien arrosé perdrait par évaporation et transpiration. C'est la « demande en eau de l'atmosphère », calculée par la formule FAO-56 Penman-Monteith. La demande d'une culture vaut ensuite ET0 × Kc (coefficient cultural).

**Méthode.** Statistiques descriptives et graphiques, sans modèle.

**Comment lire les graphiques.**
- *Distributions* (8 histogrammes) : chaque courbe est la répartition des valeurs journalières d'une variable. Les courbes tunisiennes sont décalées vers des ET0, températures et rayonnements plus élevés, et vers une humidité plus faible.
- *Climatologie mensuelle* : les barres bleues sont la pluie moyenne, la courbe l'ET0 moyenne. En Tunisie, la courbe passe très au-dessus des barres d'avril à octobre : c'est le besoin d'irrigation. À Niigata, la pluie dépasse l'ET0 presque toute l'année.
- *Saisonnalité journalière* : la ligne est la médiane de l'ET0 pour chaque jour de l'année, la bande va du 10e au 90e centile. Une bande large signifie des jours très variables d'une année à l'autre.
- *Bilan annuel* : ET0 et pluie année par année. Déficit tunisien moyen de **1 408 mm/an**, contre un excédent de 984 mm/an à Niigata.
- *Corrélations de Spearman* : de −1 à +1. L'ET0 est très liée au rayonnement et à la température, et anti-corrélée à l'humidité.
- *Autocorrélation* : corrélation de l'ET0 avec elle-même k jours plus tôt. Elle vaut **0,92** à 1 jour en Tunisie et 0,74 au Japon. La Tunisie est plus « prévisible » ; au Japon, les passages nuageux rendent l'ET0 plus erratique.
- *Hargreaves vs Penman-Monteith* : nuage de points autour de la diagonale. Les points sous la diagonale montrent que Hargreaves sous-estime d'environ 0,4 mm/j.

**Ce que signifient les résultats.** La **persistance** (« demain = aujourd'hui ») se trompe déjà de 0,61 mm/j en moyenne en Tunisie et de 0,73 au Japon. C'est la barre à franchir pour le modèle. **Bon résultat ?** Oui : les données sont complètes, réalistes, et justifient le projet (déficit massif en Tunisie).

### 4.2 Notebook 02 — Prévision de l'ET0 du lendemain (LightGBM)

**But.** Prévoir l'ET0 de demain pour calculer les besoins en eau de chaque parcelle et planifier l'irrigation.

**Données.** Les mêmes séries journalières. Une ligne = un jour J d'un site ; la **cible** est l'ET0 de J+1.

**Méthode.**
- **Variables** (59) : les 7 derniers jours de 6 variables météo (*lags*), leurs moyennes sur 3 et 7 jours, l'amplitude thermique, la saison codée en sinus/cosinus (pour que le 31 décembre soit « proche » du 1er janvier), et le site.
- **LightGBM** : une suite d'arbres de décision construits l'un après l'autre. Chaque nouvel arbre corrige les erreurs des précédents (*gradient boosting*). C'est très efficace sur des données tabulaires.
- **Découpage temporel** : on apprend sur le passé (≤ 2022), on règle sur 2023, on note sur 2024–2025. Mélanger les dates au hasard tricherait, car des jours voisins se ressemblent beaucoup.
- **Early stopping** : on ajoute des arbres tant que l'erreur de validation baisse, puis on s'arrête (ici 119 arbres), pour éviter le surapprentissage.
- **Recherche aléatoire** de 20 jeux d'hyperparamètres, puis réentraînement final sur 2015–2023.

**Comment lire les graphiques.**
- *Séries temporelles du test* : la courbe noire est l'ET0 réelle, la couleur la prédiction, le gris la persistance. La prédiction suit bien la saison et les tendances de quelques jours, mais **lisse les pics** : elle ne peut pas savoir qu'il y aura un orage ou un ciel couvert demain.
- *Nuage prédit/réel* : idéalement sur la diagonale. Les erreurs sont plus grandes quand l'ET0 est forte (été).
- *Importance (gain)* : `et0_lag1` (ET0 d'aujourd'hui) représente environ 65 % du gain, puis `et0_mean7` et `et0_mean3`. Le modèle s'appuie surtout sur l'inertie de l'ET0, corrigée par la saison et le rayonnement.
- *SHAP* : contribution moyenne de chaque variable à la prédiction, en mm/j. `et0_lag1` pèse environ 0,9 mm/j, puis `et0_mean7`, `doy_cos` et `site`. C'est cohérent avec la physique.

**Métriques.**
- **MAE** (erreur absolue moyenne) : 0,61 mm/j en Tunisie, soit environ 14 % de l'ET0 moyenne ; 0,64 mm/j au Japon (environ 24 %).
- **RMSE** : pénalise davantage les grosses erreurs.
- **R²** : part de la variance expliquée (0,85 en Tunisie, 0,63 au Japon).
- **Gain sur la persistance** : −10,7 % d'erreur en Tunisie, −16,1 % au Japon.
- **Hargreaves** : le modèle fait mieux en MAE sur les deux sites. Au Japon, Hargreaves a un RMSE et un R² légèrement meilleurs (0,852 contre 0,874 ; 0,650 contre 0,632). C'est normal : notre Hargreaves utilise les températures **réelles** de demain, comme si on avait une prévision parfaite. Ce n'est pas une comparaison à armes égales.

**Bon résultat ?** **Oui, solide et honnête.** Le gain est modeste mais réel, sans fuite de données, et l'ordre de grandeur est typique d'une prévision sans données de prévision météo. Pour aller plus loin, il faudrait ajouter la prévision météo de demain en entrée (perspective).

### 4.3 Notebook 03 — Détection de fuites sur BattLeDIM (IsolationForest)

**But.** Montrer qu'on sait détecter des fuites **sur un benchmark public**, avec de vraies dates de fuites, avant de transposer la méthode à la ferme.

**Données.** BattLeDIM (L-Town, réseau urbain chypriote simulé de façon réaliste), 2018–2019, au pas de 5 min ramené à 1 h : 33 capteurs de pression, 3 débitmètres, et 33 fuites documentées (14 en 2018, 19 en 2019). Il y a des fuites **brusques** (rupture) et **progressives** (qui grossissent pendant des semaines).

**Méthode.**
- **Étiquettes** : l'étiquette « fuite active » vaut 1 pendant 98–100 % du temps, car des fuites ne sont jamais réparées. On étiquette donc l'**apparition** : 72 h après chaque début déclaré.
- **Variables** (8 indicateurs sans unité) : baisse de pression par rapport au profil habituel de la même heure et du même jour, rupture de cohérence entre capteurs voisins, excès de débit d'entrée, débit nocturne minimal. Elles sont exprimées en **z-scores robustes** (écart divisé par la dispersion habituelle, mesurée par la médiane des écarts absolus, peu sensible aux valeurs extrêmes).
- **IsolationForest** (non supervisé) : il construit des arbres aléatoires qui « isolent » chaque point. Un point anormal s'isole en peu de coupes, ce qui lui donne un score élevé. Le modèle apprend la normalité sur janvier–août 2018 ; le seuil est réglé sur septembre–décembre 2018 ; le test porte sur 2019.

**Comment lire les graphiques.**
- *Boîtes à moustaches* (validation) : orange = fenêtres de nouvelle fuite, gris = le reste. `p_resid_absmax` sépare très nettement les deux (≈ 34 contre ≈ 3) ; les pressions baissent et le débit monte pendant une fuite. Les variables contiennent donc un vrai signal.
- *F1 selon le seuil* : courbe en cloche, maximum 0,59 pour un seuil de 0,483.
- *Matrice de confusion 2019* : beaucoup de faux positifs (alarmes hors fenêtre).
- *Délais par fuite* : les barres courtes en rouge (fuites brusques) sont encourageantes, mais voir la limite ci-dessous.
- *Chronologie 2019* (le graphique le plus important) : la courbe noire (score) passe **constamment** au-dessus de la ligne rouge (seuil). Les bandes orange (fuites) ne se distinguent pas nettement du reste. La courbe bleue (débit total de fuite) monte de 25 à 140 m³/h, car les fuites s'accumulent.

**Métriques.** Précision 0,16 (16 % des heures en alarme sont dans une fenêtre de fuite), rappel 0,49, F1 0,24. Une alarme **toujours allumée** ferait F1 0,27.

**Bon résultat ?** **Non, pas en l'état.** Les variables sont bonnes et la méthode est propre, mais le score reste élevé environ 14 jours après chaque fuite. En 2019, avec une nouvelle fuite tous les 19 jours, l'alarme est allumée la moitié du temps. Le « 19/19 détectées » est un artefact. Il faut la correction n°1 (§3), puis présenter honnêtement : « fuites brusques détectées en quelques heures, fuites progressives difficiles ».

### 4.4 Notebook 04 — Calibration de River HalfSpaceTrees (en ligne)

**But.** Régler le détecteur **en temps réel** qui tournera dans le consumer MQTT : un message toutes les 10 minutes simulées, soit 0,83 s réelle.

**Données.** Un flux **synthétique** de 14 jours généré dans le notebook, au format du simulateur : débits des tronçons S1–S5, plan d'irrigation, bruit, pics isolés, et 14 fuites injectées. Il y a un flux de validation et un flux de test, avec des graines différentes.

**Méthode.**
- **Variables** : déséquilibre du bilan de masse (S1 − S2 − S3 − S4 − S5) et écarts mesure − plan, ramenés entre 0 et 1.
- **HalfSpaceTrees** : des arbres qui découpent l'espace en « demi-espaces » aléatoires et comptent combien de points récents tombent dans chaque case. Un point qui tombe dans une case habituellement vide est anormal. Le modèle apprend **à chaque message** (`learn_one`) et oublie le passé par fenêtres (`window_size`).
- **Grille** de 27 combinaisons (nombre d'arbres, profondeur, fenêtre) ; pour chacune, on choisit le seuil et le nombre de messages consécutifs exigés (anti-bruit).

**Comment lire les graphiques.**
- *Flux synthétique* : en haut, les débits par tronçon (créneaux d'irrigation de jour) ; en bas, le déséquilibre, nul hors fuite et en saut pendant une fuite (zones rouges).
- *Boîtes F1 de la grille* : la fenêtre (`window_size`) compte le plus ; les fenêtres longues (288 = 2 jours) sont meilleures.
- *Flux de test* : le score monte nettement à chaque fuite ; les petites barres rouges sont les alarmes.

**Métriques.** F1 0,92, 12 fuites détectées sur 12, en 0,7 message en moyenne (≈ 0,6 s réelle), 0,9 fausse alerte par jour (simulé). La règle simple fait F1 0,92 avec 0 fausse alerte.

**Bon résultat ?** **Oui pour la démo, à relativiser.** La méthode est propre (validation et test séparés). Mais les données sont simulées et la tâche facile : une règle de bon sens fait aussi bien. L'intérêt de River est de n'avoir aucun seuil physique à régler à la main et de s'adapter si le comportement normal change.

---

## 5. Ce que tu diras au jury

### 5.1 Chiffres clés défendables

1. **« Notre modèle prévoit la demande en eau de l'atmosphère du lendemain avec une erreur moyenne de 0,61 mm/jour à Sidi Bouzid. »**
   Mesuré sur 2024–2025, deux années jamais vues pendant l'entraînement, soit environ 14 % de l'ET0 moyenne.
2. **« C'est 11 à 16 % plus précis que de supposer que demain ressemblera à aujourd'hui. »**
   Gain mesuré par rapport à la persistance, la référence standard en prévision, sur les deux sites.
3. **« À Sidi Bouzid, l'atmosphère réclame 1 636 mm d'eau par an pour seulement 228 mm de pluie : un déficit de 1 400 mm à combler par l'irrigation. »**
   Données Open-Meteo sur 11 ans ; c'est ce qui justifie l'optimisation de chaque m³.
4. **« Une erreur de 0,6 mm/jour représente environ 6 m³ d'eau par hectare et par jour : c'est la marge que notre optimiseur doit gérer. »**
   Conversion : 1 mm sur 1 ha = 10 m³ ; ordre de grandeur à multiplier par le Kc de la culture et à diviser par l'efficience d'irrigation.
5. **(Seulement après la correction du notebook 03)** « Sur le benchmark international BattLeDIM, notre détecteur repère *X* fuites brusques sur 9 en *Y* heures, avec *Z* fausses alertes par semaine. » Ne pas annoncer « 19/19 » en l'état.
   **Pour River**, toujours avec l'étiquette « simulé » : « En simulation, une fuite qui rompt le bilan de masse est signalée en moins de 2 secondes de démo. »

### 5.2 Limites à annoncer honnêtement

- **Pas de prévision météo en entrée** : le modèle ET0 n'utilise que le passé. Il lisse les jours exceptionnels (orage, vent chaud). Perspective : ajouter la prévision météo J+1.
- **Données de réanalyse**, pas de station météo sur la ferme ; en production, les derniers jours viendront de la prévision Open-Meteo.
- **BattLeDIM est un réseau urbain**, pas un réseau d'irrigation : nous validons la *méthode* (profils, résidus, z-scores), pas la performance sur une vraie ferme.
- **Les fuites progressives** (qui grossissent lentement) sont difficiles à détecter avec des données de débit et de pression seules ; c'est aussi le cas dans la littérature.
- **River est calibré sur un flux simulé** : les chiffres en temps réel sont des **estimations simulées**, et une règle simple sur le bilan de masse fait aussi bien sur ce flux.
- **Les coefficients cultures (Kc, Ky) et les prix** sont des valeurs FAO ou des hypothèses, pas des mesures locales.

### 5.3 Questions probables du jury et réponses

**« Pourquoi LightGBM et pas un réseau de neurones (LSTM) ? »**
Avec environ 6 500 lignes tabulaires, le gradient boosting est l'état de l'art : rapide, interprétable (SHAP), sans GPU. Un LSTM demanderait plus de données pour un gain incertain. Contrainte du hackathon : pas de deep learning.

**« Comment savez-vous que vous ne surapprenez pas ? »**
Découpage temporel strict (train ≤ 2022, validation 2023, test 2024–2025), early stopping à 119 arbres, MAE de validation 0,605 contre 0,628 en test : écart faible. Les 10 meilleurs réglages sont à 1,5 % les uns des autres, donc le résultat ne dépend pas d'un réglage chanceux.

**« Votre modèle triche-t-il avec des données du futur ? »**
Non : toutes les variables s'arrêtent au jour J (preuve : la cible d'un jour est la variable « lag1 » du lendemain). Hargreaves, qui utilise les températures de J+1, est seulement une **référence de comparaison**, pas une entrée du modèle.

**« Pourquoi ne pas utiliser directement l'ET0 prévue par Open-Meteo ? »**
Le backend l'utilise en secours. Notre modèle fonctionne même **hors-ligne**, à partir de l'historique, ce qui sécurise la démo et les zones mal connectées. La comparaison avec les prévisions archivées d'Open-Meteo est notre prochaine étape.

**« Comment passez-vous d'un réseau urbain (BattLeDIM) à un réseau d'irrigation ? »**
Le détecteur ne voit que des indicateurs **sans unité** (z-scores) : chute de pression, excès de débit, rupture du bilan de masse, débit hors irrigation. Ces indicateurs existent sur les deux réseaux ; la table de correspondance est dans `leak_features.json`.

**« Pourquoi deux détecteurs (IsolationForest et River) ? »**
IsolationForest est **validé hors-ligne** sur un benchmark avec de vraies fuites. River **apprend en continu** sur le flux de la ferme, sans entraînement préalable, et s'adapte quand le comportement normal change (nouvelle culture, nouveau plan d'irrigation).

**« Une règle simple fait aussi bien que River, pourquoi l'utiliser ? »**
Sur notre simulateur, oui, et nous l'affichons. La règle exige de connaître à l'avance le bon seuil en m³/h pour chaque ferme. River apprend la normalité de chaque installation et détecte aussi des anomalies sur les écarts au plan, sans seuil physique. En production, on garderait les deux : la règle pour les ruptures nettes, River pour les dérives.

**« Que se passe-t-il si le modèle se trompe sur l'ET0 ? »**
Une erreur de 0,6 mm/j se traduit par quelques m³ d'eau par jour sur la ferme de démo, et le plan est recalculé chaque heure avec les nouvelles données. Avec l'intervalle d'incertitude (ajout n°4), l'optimiseur pourra prévoir une marge les jours où la prévision est incertaine.

**« Quel est le F1 de votre détection de fuites ? »**
Dire la vérité : le F1 point par point est un indicateur mal adapté quand des fuites restent actives toute l'année. Nous mesurons plutôt les **fuites détectées, le délai et les fausses alertes par semaine**, plus parlants pour un exploitant. Annoncer les chiffres corrigés.

---

## 6. Prochaine étape proposée

1. Ton accord pour appliquer la **correction n°1** (notebook 03, indispensable), et éventuellement les ajouts n°2 à 5 (notebook 02, environ 35 min au total).
2. Tu réexécutes les notebooks modifiés.
3. Je mets à jour ce rapport avec les chiffres corrigés, puis on passe au test de chargement (`forecast.py`, `leaks.py`) demandé dans la Phase 3.
