# Agent Prospection FrenchQuant (AutomaticMailing)

Pendant *sortant* (outbound) de `MailManager`. Fonction **principale** : **diffuser
un mail à TOUTE la base opt-in** (abonnés newsletter — prospects *et* clients) pour
**en convertir certains** vers FQ-KERNEL, dans la voix FrenchQuant, déclenché **à la
main** via `/mail` depuis Telegram. **En option à la demande**, l'agent rédige aussi
des mails de **prospection 1:1** ultra-personnalisés (`/prospect`, `/prospects`,
`/who`) — single-touch, **plus aucun lot automatique** (le timer ne pousse plus de
1:1). **Aucun mail n'est jamais envoyé sans une action explicite depuis Telegram.**

> La documentation de référence (architecture, modèle de données, **Règles
> d'Or**) est dans [CLAUDE.md](CLAUDE.md). Lis-le avant toute modification.

## Modèle de sécurité (non négociable)

- **Un seul chemin d'envoi** : `send_campaign_email()` dans
  `telegram_listener.py`, déclenché uniquement par un callback utilisateur. Pas
  d'envoi ailleurs, surtout pas dans `prepare_campaign.py`.
- **Pas d'auto-envoi** : tout part d'un tap Telegram.
- **Supabase en LECTURE SEULE** : uniquement des `SELECT`. Tout l'état
  (`contacted`/`pending`/`suppressed`) vit dans le SQLite local `state.db`.
- **Single-touch (prospection) & opt-out sacré (partout)** : en prospection 1:1 un
  prospect n'est contacté qu'une fois (`contacted`) ; la diffusion `/mail` est
  répétable mais exclut toujours les `suppressed`. Chaque mail porte identité +
  désinscription ; toute désinscription est honorée définitivement.
- **Verrou `TELEGRAM_CHAT_ID`** : tout update d'un autre chat est ignoré.
- **Secrets hors du repo** (`.env`, `state.db` non commités).
- **Contenu externe = donnée non fiable** (anti-injection).

## Démarrage rapide (dev local)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env        # puis remplir les valeurs

# auth du modèle via l'abonnement Claude (pas de clé API) :
npm i -g @anthropic-ai/claude-code   # Node.js requis ; fournit le binaire `claude`
claude setup-token                   # -> reporter dans CLAUDE_CODE_OAUTH_TOKEN du .env

# (optionnel) construire le corpus distillé depuis l'arbo locale
.venv/bin/python build_corpus.py

# la diffusion à la base se pilote via /mail dans Telegram (listener ci-dessous).
# `prepare_campaign.py` (timer) est un no-op par défaut ; pour rejouer le lot 1:1 :
PROSPECTION_AUTORUN=1 .venv/bin/python prepare_campaign.py   # PAS d'envoi (prépare des pending)

# lancer le listener (Ctrl-C pour stopper) — SEUL à envoyer
.venv/bin/python telegram_listener.py

# (optionnel) rafraîchir les angles douleur (à relire avant usage)
.venv/bin/python discover_pains.py
```

### Inspecter l'état

```bash
sqlite3 state.db "SELECT email, subject FROM pending;"
sqlite3 state.db "SELECT COUNT(*) FROM contacted;"
sqlite3 state.db "SELECT email, reason FROM suppressed;"
```

## Fichiers

| Fichier | Rôle |
|---|---|
| `prepare_campaign.py` | Module importé par le listener (`/mail` + prospection à la demande) **et** point d'entrée du timer. ⚠️ Son `main()` (timer) est un **no-op** par défaut (auto-1:1 désactivée) — `run_daily_prospection()` n'est rejoué qu'avec `PROSPECTION_AUTORUN=1`. **Jamais d'envoi.** |
| `telegram_listener.py` | Listener always-on : boutons + édition. **Seul à envoyer (Mailgun).** |
| `discover_pains.py` | (optionnel) social listening → `corpus/pains.md`. À relire. |
| `common.py` | Helpers partagés (env, logging, Supabase read-only, SQLite, Telegram, pied de conformité). |
| `campaign_context.md` | Cœur métier (ICP, offres, ton, CTA, lignes rouges) — le levier #1. |
| `corpus/` | Voix & matière (texte uniquement) : `offers.md`, `voice.md`, `pains.md`, `scripts/`, `notebooks/`. |
| `templates/` | Squelette de mail / signature (référence). |
| `build_corpus.py` | Exporte notebooks `_release`→md + copie scripts (local, puis rsync). |
| `prepare-campaign.service` / `.timer`, `campaign-listener.service` | Units systemd (user, rootless). |

## Flux Telegram (validation)

Pour chaque prospect préparé :

```
🎯 Prospect : <name> <email>
🧩 Angle : <douleur utilisée>
✉️ Objet : <subject>

<body (avec pied identité + désinscription)>

[✅ Envoyer] [✏️ Éditer]
[🗑 Ignorer] [🚫 Ne plus contacter]
```

- **Envoyer** → envoie via Mailgun, marque `contacted`, supprime le `pending`.
- **Éditer** → attend ton texte corrigé, puis envoie ce texte (déclencheur
  d'envoi explicite), marque `contacted`.
- **Ignorer** → supprime le `pending` sans rien envoyer (reste éligible).
- **Ne plus contacter** → ajoute à `suppressed` (raison `manual`).

## Commandes Telegram (pilotage à la demande)

Deux modes d'envoi bien distincts. Aucun ne part sans ton action explicite.

**Diffusion batch à TOUTE la base (fonction PRINCIPALE — un seul mail pour tout le
monde)** :

| Commande | Effet |
|---|---|
| `/mail <sujet>` | L'agent rédige **un** mail pour **toute la base opt-in** (prospects *et* clients), pour convertir. Flux : ✅ Valider (→ tu reçois un **aperçu**, avec le **vrai objet**, à toi seul) → ✏️ Éditer (re-rédaction en langage naturel) → 📣 **Diffuser à N** (envoi **batch individuel** via Mailgun à tout l'opt-in, hors désinscrits et hors toi). |
| `/help` | Rappel des commandes. |

> 💡 **« Envoyer le même mail à toute ma base » = `/mail`**, pas la prospection.
> La diffusion couvre **tout l'opt-in** ; chaque destinataire reçoit un message
> individuel (il ne voit pas les autres adresses), avec identité + désinscription.
> Les désinscrits (`suppressed`) sont exclus. L'**aperçu** envoyé à la validation
> a le **vrai objet** (aucun préfixe « test ») : c'est exactement le mail diffusé.

**Prospection 1:1 (option, à la demande — single-touch, quelques mails
ultra-personnalisés)** :

| Commande | Effet |
|---|---|
| `/prospect <email> [consigne]` | Rédige **un** mail 1:1 pour un abonné précis. |
| `/prospects [N]` | Sélectionne jusqu'à `N` prospects éligibles et rédige un mail pour chacun (borné, défaut `CAMPAIGN_BATCH_SIZE`, max 10). |
| `/who [N]` | Aperçu (lecture seule) des prochains prospects éligibles. |

> ⚠️ La prospection 1:1 est **à la demande** et **volontairement bornée**
> (single-touch). ⚠️ Elle **n'est plus automatique** : le timer ne prépare plus de
> lot 1:1 (recentrage sur la diffusion). Pour réactiver le lot quotidien :
> `PROSPECTION_AUTORUN=1` (cf. [CLAUDE.md](CLAUDE.md)). Elle n'arrose jamais toute
> la base — pour ça, c'est `/mail`.

## Déploiement systemd (user, rootless) — identique à MailManager

Le code est attendu dans `~/frenchquant/mailing_agent/` (cf. units). Prérequis
modèle sur le VPS : Node.js + la CLI Claude Code (`npm i -g
@anthropic-ai/claude-code`), puis `claude setup-token` pour générer le
`CLAUDE_CODE_OAUTH_TOKEN` (abonnement, pas de clé API).

```bash
cp prepare-campaign.service prepare-campaign.timer campaign-listener.service \
   ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now campaign-listener.service   # listener (toujours actif)
loginctl enable-linger claudeuser     # survie hors session SSH (always-on)

# ⚠️ Le timer de prospection 1:1 N'EST PLUS activé par défaut (recentrage sur la
# diffusion /mail). Si un ancien timer tourne encore, désactive-le :
systemctl --user disable --now prepare-campaign.timer
# (Ne le réactive QUE si tu veux le lot quotidien 1:1, avec PROSPECTION_AUTORUN=1.)
```

### Supervision

```bash
systemctl --user list-timers
systemctl --user status campaign-listener.service
journalctl --user -u prepare-campaign.service -n 50 --no-pager
journalctl --user -u campaign-listener.service -f
```

## Délivrabilité & conformité

Base opt-in uniquement, identité + désinscription dans chaque mail (ajoutées
automatiquement par `common.compliance_footer()` + en-tête `List-Unsubscribe`),
volume bas single-touch, suppression immédiate des désinscrits, domaine
authentifié SPF/DKIM/DMARC côté Mailgun (base UE `api.eu.mailgun.net`).
