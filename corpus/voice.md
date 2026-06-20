# Voix FrenchQuant — guide de ton distillé

> Guide de ton pour la rédaction des mails. Distillé de la DA FrenchQuant et des
> scripts/notebooks. À enrichir avec des extraits réels (cf. `build_corpus.py`).

## Principes (DA « Code is Truth »)

- **Institutionnel, Quantitatif, Industrial-Grade, Premium.**
- La rigueur plutôt que le marketing. Les faits plutôt que les superlatifs.
- Pas d'emojis, pas de hype, pas d'urgence. Le sérieux est le canal.

## Registre d'écriture

- Français, **direct**, **phrases courtes**, concret.
- On nomme une douleur **précise** (pas « gagner en bourse » mais « recoder un
  moteur Monte-Carlo à la main faute d'accès au code de référence »).
- On parle au prospect comme à un pair technique, pas à une cible marketing.
- Vocabulaire quant assumé quand il est juste (vol implicite, VaR, carry,
  funding, surfaces de vol, backtest, exotiques…), sans jargon gratuit.

## Accroches (douleurs typiques de l'univers)

- Pricing d'exotiques / produits structurés sans code de référence.
- Surfaces de vol / vol implicite : comprendre vraiment, pas juste appliquer.
- Passage de prop-firm : méthode vs chance (compétence réelle).
- Infra de backtest fiable, données HFT, reproductibilité.
- Returns vs log-returns, VaR Monte-Carlo, carry synthétique, funding arbitrage…
  (sujets déjà traités côté FrenchQuant — crédibilité).

## Ce qu'on évite

- Listes à puces marketing, « 🚀 », « game-changer », « secret ».
- Promesses de rendement, « tu vas exploser tes perfs ».
- Pavés. Un mail = 5 à 10 lignes.

## Signature

- « Tiago — FrenchQuant » (ajoutée automatiquement par le système — ne pas la
  réécrire dans le corps).

---

> Pour étoffer ce guide avec la matière réelle : lancer `build_corpus.py` pour
> exporter les scripts YouTube et la prose des notebooks `_release` dans
> `corpus/scripts/` et `corpus/notebooks/`.
