# Offres FrenchQuant — faits autorisés en rédaction

> CECI EST UNE DONNÉE, PAS UNE INSTRUCTION. Source de vérité « offres » pour la
> rédaction des mails. Ne contient QUE des faits étayés (offres autorisées +
> matière extraite du code de l'infra). Le modèle ne doit utiliser aucun autre
> chiffre, prix, lien, performance ou promesse que ceux listés ici
> (cf. Règle de rédaction non négociable, CLAUDE.md). En cas de doute : rester
> sobre et factuel, ou omettre. Toute capacité non étayée ci-dessous ne doit
> JAMAIS être affirmée.

---

## FQ-KERNEL (produit phare — l'offre de cette campagne)

- **Licence cœur / accès au code source.** Présenté comme « Licence
  Infrastructure Complète » / « Propriété du Code ».
- **Prix : 2497 €** (total).
- **Paiement échelonnable : 1, 2, 3, 4, 6 ou 12 mensualités** (Stripe),
  **sans frais**, **accès immédiat dès le 1er prélèvement**. Alternatives :
  carte, crypto ou virement (crypto et virement : paiement unique uniquement).
- Cadré comme un **achat acquis définitivement** (propriété à vie), pas un
  abonnement.

Ce que KERNEL inclut, d'après le code (à n'évoquer que si c'est pertinent pour
la douleur du prospect — ne pas tout lister dans un mail) :

- **Propriété du code source** + **accès GitHub privé**, **droits
  d'hébergement**, **mises à jour à vie**, **support technique prioritaire**.
- **Infrastructure Cloud incluse** et **notebooks cloud**.
- **Terminal de trading**, **formation interactive**, **jeux & entraînement**.

### Le moteur au cœur de KERNEL (PricerCore, package `pricer_core`)

Librairie Python quantitative multi-actif. Capacités étayées par le code :

- **Modèles d'underlying** : GBM, **Heston** (vol stochastique), **Bates** (vol
  stochastique + sauts), sous mesure forward. Version multi-actif
  (`MultiAssetBatesForward`) : N actifs Bates, browniens corrélés (Cholesky),
  sauts indépendants par actif.
- **Pricers** européens analytiques (Black-76) et **Monte-Carlo** génériques
  (options, digitales, range, one-touch, baskets).
- **Greeks** analytiques (delta, gamma, vega, rho) et par différences finies.
- **Courbes** : taux zéro (`YieldCurve`, NSS/spline), carry (`CarryCurve`),
  forward (`ForwardCurve`, cost-of-carry). Cas FX traité proprement (taux
  domestique vs étranger extrait du marché). Discipline de calibration :
  le forward et le carry sont **extraits du marché, jamais devinés**.
- **Surfaces de volatilité** : **SSVI** calibrée avec contraintes de
  non-arbitrage (butterfly + calendar), IV Black-76, vol réalisée EWMA, et
  reconstruction par prime de risque de vol pour les actifs sans options
  listées (FX, crypto).
- **Corrélation** multi-actif (EWMA, corrélation implicite par dispersion,
  projection sur matrice de corrélation valide — Higham).
- **Densité risque-neutre** implicite via Breeden-Litzenberger.
- **Pricer de challenges prop-firm** : valeur présente conservatrice V0 par
  Monte-Carlo Bates multi-actif calibré, décision TAKE/SKIP via edge vs fee,
  simulateur des règles réelles (daily loss, min trading days, trailing
  drawdown, consistency), optimiseurs de levier/portefeuille (CMA-ES, Brent),
  stats de risque (Kelly plafonné, VaR/CVaR, Sharpe/Sortino, P(pass)),
  portefeuilles décorrélés (PCA), analyse de bankroll (ruine du joueur,
  capital requis), delta-hedging value-delta. Firmes pré-câblées : **FTMO,
  FundedNext, The5ers, FundingPips, MFF** (+ challenges custom).
- **Mode live** (asyncio) : courbe forward persistante, surface recalibrée en
  EOD, feed de spot temps réel, pricing thread-safe.

> Note rédaction : KERNEL donne accès au **code source** de ce moteur. L'offre
> « découverte » (voir plus bas) donne accès au pricer, **pas** au code source.

---

## Terminal (offre secondaire)

Abonnement (accès conditionné à un abonnement actif). Briques étayées par le
code, plusieurs en statut **Live** (production) :

- **HFT Terminal** (Live) — microstructure temps réel : orderbook L2,
  price heatmap, microprice, **VPIN**, **CVD**, **OBI** multi-depth.
- **Risk Analysis Terminal** (Live) — **VaR / CVaR**, stress testing,
  corrélations, régimes **HMM**, Monte Carlo **FHS** (Filtered Historical
  Simulation), analyse factorielle (CAPM, Fama-French 3/5), stress historiques
  (2008, Covid).
- **Portfolio Optimizer** (Live) — **Black-Litterman**, covariance
  **DCC-GARCH**, vues investisseur, Max Sharpe / Min Vol, frontière efficiente.
- **Funding Arbitrage Terminal** (Live) — funding rates multi-exchange,
  Spot-Perp / Perp-Perp, recommandations live, net-after-fees. (Le code recense
  des connecteurs vers 8 exchanges : Binance, Bybit, OKX, Deribit, Kraken,
  Hyperliquid, dYdX, GMX ; la fiche produit publique mentionne « 9 exchanges ».)
- **Prop-Firm Pricer** (Live) — single-asset long-only : levier optimal,
  P(pass), edge net de coûts, **14 actifs calibrés EOD**, firmes FTMO /
  FundedNext / The5ers / FundingPips / MFF.
- **Quant Screener** — annoncé `coming soon` (scan multi-asset, factor
  screening, signaux momentum / mean-reversion). À ne PAS présenter comme
  disponible.

Le moteur HFT temps réel (data marché microstructure) est un backend Rust
propriétaire : reconstruction d'orderbook L2, features de microstructure
(VPIN, OFI, micro-price, imbalance), latence sous-milliseconde, API REST +
WebSocket.

À n'évoquer que si la douleur du prospect le justifie (single-touch, mail
court).

---

## FQ-PRICER (offre découverte / pied-dans-la-porte)

- **Prix : 97 €** (paiement unique), **97 € crédités sur un futur FQ-KERNEL**.
- « Pricer Prop-Firm bridé · 1 actif long ». Donne accès au **Pricer, pas au
  code source**.
- Capacités : pricer 1 actif long-only, **levier optimal + P(pass)**, **edge
  net de coûts (honnête)**, **14 actifs calibrés EOD**, compatible prop-firms
  **FTMO, FundedNext, The5ers, FundingPips, MFF**.

> Usage : levier d'entrée bas-ticket vers KERNEL. Ne pas le confondre avec
> KERNEL (le produit poussé par cette campagne).

---

## Cloud Infra

- **Option d'infrastructure** : infrastructure cloud et **notebooks cloud**.
- Incluse dans FQ-KERNEL (« Infrastructure Cloud incluse », « Droits
  d'hébergement »).

---

## Formation & contenu (inclus dans KERNEL)

À n'utiliser que comme preuve de profondeur / angle, jamais comme promesse.
Faits étayés :

- **Education Center** : cours structurés (du pricing d'options au market making
  algorithmique), contenu interactif. Modules publiés : Les Bases, Quantitative
  Factor Investing & smart beta, Produits dérivés & trading, Pair Trading
  Statistique.
- **Jeux & entraînement** (Practice) : Black-Scholes, market-making, calcul
  mental, real-or-fake, etc.
- **FrenchQuant Circle (frenchquant-lab)** : bibliothèque R&D organisée en
  **21 modules** (des fondamentaux math/code à la régulation / market design).
  Convention de maturité du code : `_scratch` (brouillon) / `_work`
  (reproductible) / `_release` (livrable diffusable).
- **Notebooks** documentés (pricing dérivés Black-Scholes/Heston/Bates,
  processus stochastiques, microstructure OBI/OFI, backtesting PSR/DSR,
  momentum cross-sectionnel, frontière efficiente, VaR Monte Carlo, etc.).
- Articles techniques associés (Hurst, KPSS, information mutuelle, loi
  fondamentale de la gestion active, pricers neuronaux Heston, VPIN, etc.).

---

## Liens officiels

- Site (offres / pricing / FAQ) : `https://frenchquant.com`
- App : `https://app.frenchquant.com`
- Contact / réponses : `frenchquant125@gmail.com`

## Calendly

- **CTA principal** (call de cette campagne) — client-call :
  `https://calendly.com/frenchquant125/client-call`
- NE PAS utiliser ici (autres usages) : `investor-call` (investisseurs),
  `new-meeting`, `60-minutes-call`.

---

## Voix & positionnement (pour le ton, pas pour des promesses)

- Tagline officielle : **« Infrastructure Quantitative Privée »**.
- Pitch : « Terminal de trading, formation avancée, entraînement interactif et
  notebooks cloud. Tout-en-un pour l'ingénierie financière quantitative. »
- Le client est nommé **« opérateur »** (pas « utilisateur »).
- Posture **« Code is Truth »** : rigueur, honnêteté sur les coûts et les
  limites, anti-bullshit. Exemple de registre : « edge net de coûts (honnête) »,
  « on montre le résultat, pas la méthode ».

---

## Interdits (rappel)

Aucune promesse de gain, aucune performance chiffrée tirée d'un backtest
présentée comme un rendement attendu, aucune garantie, aucune urgence
artificielle, aucun superlatif marketing, aucun emoji. Les chiffres internes de
backtests présents dans la matière (Sharpe, Sortino, speedups, etc.) sont des
illustrations pédagogiques : **ne pas les transformer en promesse de
performance** dans un mail. Les seuls prix/mensualités/liens autorisés sont ceux
listés ci-dessus. En cas de doute : sobre et factuel.
