# Agent Prospection FrenchQuant (AutomaticMailing)

Pendant *sortant* (outbound) de `MailManager`. De façon planifiée, l'agent
**identifie des prospects tièdes** dans Supabase (abonnés newsletter qui n'ont
pas acheté FQ-KERNEL), **rédige un mail de prospection 1:1 ultra-personnalisé**
dans la voix FrenchQuant, et **pousse un récap sur Telegram avec des boutons de
validation**. **Aucun mail n'est jamais envoyé sans une action explicite depuis
Telegram.**

> La documentation de référence (architecture, modèle de données, **Règles
> d'Or**) est dans [CLAUDE.md](CLAUDE.md). Lis-le avant toute modification.

## Modèle de sécurité (non négociable)

- **Un seul chemin d'envoi** : `send_campaign_email()` dans
  `telegram_listener.py`, déclenché uniquement par un callback utilisateur. Pas
  d'envoi ailleurs, surtout pas dans `prepare_campaign.py`.
- **Pas d'auto-envoi** : tout part d'un tap Telegram.
- **Supabase en LECTURE SEULE** : uniquement des `SELECT`. Tout l'état
  (`contacted`/`pending`/`suppressed`) vit dans le SQLite local `state.db`.
- **Single-touch & opt-out sacré** : un prospect contacté une seule fois, chaque
  mail porte identité + désinscription, désinscription honorée définitivement.
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

# préparer une campagne à la main (cible + rédige + notifie, PAS d'envoi)
.venv/bin/python prepare_campaign.py

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
| `prepare_campaign.py` | Préparation (timer) : cible Supabase, rédige, notifie. **Jamais d'envoi.** |
| `telegram_listener.py` | Listener always-on : boutons + édition. **Seul à envoyer (Mailgun).** |
| `discover_pains.py` | (optionnel) social listening → `corpus/pains.md`. À relire. |
| `common.py` | Helpers partagés (env, logging, Supabase read-only, SQLite, Telegram, pied de conformité). |
| `campaign_context.md` | Cœur métier (ICP, offres, ton, CTA, lignes rouges) — le levier #1. |
| `corpus/` | Voix & matière (texte uniquement) : `offers.md`, `voice.md`, `pains.md`, `scripts/`, `notebooks/`. |
| `templates/` | Squelette de mail / signature (référence). |
| `build_corpus.py` | Exporte notebooks `_release`→md + copie scripts (local, puis rsync). |
| `prepare-campaign.service` / `.timer`, `mail-listener.service` | Units systemd (user, rootless). |

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

## Déploiement systemd (user, rootless) — identique à MailManager

Le code est attendu dans `~/frenchquant/mailing_agent/` (cf. units). Prérequis
modèle sur le VPS : Node.js + la CLI Claude Code (`npm i -g
@anthropic-ai/claude-code`), puis `claude setup-token` pour générer le
`CLAUDE_CODE_OAUTH_TOKEN` (abonnement, pas de clé API).

```bash
cp prepare-campaign.service prepare-campaign.timer mail-listener.service \
   ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now prepare-campaign.timer
systemctl --user enable --now mail-listener.service
loginctl enable-linger claudeuser     # survie hors session SSH (always-on)
```

### Supervision

```bash
systemctl --user list-timers
systemctl --user status mail-listener.service
journalctl --user -u prepare-campaign.service -n 50 --no-pager
journalctl --user -u mail-listener.service -f
```

## Délivrabilité & conformité

Base opt-in uniquement, identité + désinscription dans chaque mail (ajoutées
automatiquement par `common.compliance_footer()` + en-tête `List-Unsubscribe`),
volume bas single-touch, suppression immédiate des désinscrits, domaine
authentifié SPF/DKIM/DMARC côté Mailgun (base UE `api.eu.mailgun.net`).
