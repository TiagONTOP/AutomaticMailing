"""Helpers partagés entre prepare_campaign.py et telegram_listener.py.

Centralise : chargement du .env, logging, accès Supabase (LECTURE SEULE), accès
SQLite local (state.db), appels HTTP à l'API Telegram, et la fabrication du pied
de mail de conformité (signature + désinscription).

AUCUN envoi de mail n'est défini ici — l'envoi (POST Mailgun .../messages)
n'existe QUE dans telegram_listener.py (cf. Règle d'Or #1). De même, toute lecture
Supabase faite ici est strictement en SELECT (cf. Règle d'Or #3) : jamais
d'INSERT/UPDATE/DELETE sur la base de production.
"""

import html as _html
import logging
import os
import re as _re
import sqlite3
import sys
from pathlib import Path

import requests

# Répertoire du projet (= dossier de ce fichier). Tous les chemins en découlent.
BASE_DIR = Path(__file__).resolve().parent

ENV_PATH = BASE_DIR / ".env"
DB_PATH = BASE_DIR / "state.db"
AWAITING_EDIT_PATH = BASE_DIR / "awaiting_edit.json"
CAMPAIGN_CONTEXT_PATH = BASE_DIR / "campaign_context.md"
CORPUS_DIR = BASE_DIR / "corpus"

# Modèle utilisé pour la rédaction (cohérent avec MailManager, cf. CLAUDE.md).
ANTHROPIC_MODEL = "claude-opus-4-8"

# Longueur du token court servant de clé pour les callback_data Telegram.
# Contrainte Telegram : callback_data <= 64 octets. "block:" + 32 = 38 octets.
TOKEN_LEN = 32

# Purge des brouillons jamais validés plus vieux que ce nombre de jours.
PENDING_TTL_DAYS = 14

# Taille de lot par défaut (nb de prospects préparés par run). Surchargée par
# la variable d'environnement CAMPAIGN_BATCH_SIZE.
DEFAULT_BATCH_SIZE = 5

# Identité expéditeur reprise dans le pied de mail. Faits, pas de secret.
SENDER_SIGNATURE = "Tiago — FrenchQuant"
SITE_URL = "https://frenchquant.com"
CONTACT_EMAIL = "frenchquant125@gmail.com"

# Phrase-sentinelle du pied de désinscription. Sert à rendre l'ajout du pied
# IDEMPOTENT : si elle est déjà présente dans le corps (cas nominal préparé par
# prepare_campaign.py), on ne ré-ajoute pas le pied au moment de l'envoi.
UNSUB_SENTINEL = "répondez « STOP »"

# Lien Calendly du CTA principal (call client). Fait, pas un secret (cf. offers).
CALENDLY_CALL = "https://calendly.com/frenchquant125/client-call"

# DA FrenchQuant (cf. frenchquant_website/CLAUDE.md) — tokens couleur du mail HTML.
DA_BG = "#000000"          # noir pur
DA_CARD = "#09090b"        # zinc-950 (carte)
DA_PRIMARY = "#7c3aed"     # violet électrique
DA_TEXT = "#e4e4e7"        # zinc-200 (corps)
DA_MUTED = "#71717a"       # zinc-500 (pied)
DA_BORDER = "#27272a"      # zinc-800
DA_FONT = "'Space Mono','SFMono-Regular',Menlo,Consolas,'Courier New',monospace"

EMAIL_TEMPLATE_PATH = BASE_DIR / "templates" / "email.html"


# --------------------------------------------------------------------------- #
# Environnement
# --------------------------------------------------------------------------- #
def load_env(path: Path = ENV_PATH) -> None:
    """Charge le .env en dev local. En prod, systemd fournit déjà les variables
    via EnvironmentFile, donc on utilise setdefault : l'environnement réel gagne.
    Ne jamais logger ces valeurs (Règle d'Or #6)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, val)


def require_env(*names: str) -> dict:
    """Récupère les variables d'environnement requises, ou lève une erreur
    explicite si l'une manque."""
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise RuntimeError(
            "Variables d'environnement manquantes : " + ", ".join(missing)
        )
    return {n: os.environ[n] for n in names}


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
def get_logger(name: str) -> logging.Logger:
    """Logger simple vers stdout (récupéré par journalctl en prod)."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


_logger = get_logger("common")


# --------------------------------------------------------------------------- #
# Normalisation des emails (insensible casse + trim) — cf. Ciblage CLAUDE.md
# --------------------------------------------------------------------------- #
def norm_email(email: str | None) -> str:
    """Normalise un email pour comparaison/déduplication : trim + minuscules."""
    return (email or "").strip().lower()


# --------------------------------------------------------------------------- #
# Supabase — LECTURE SEULE (Règle d'Or #3)
# --------------------------------------------------------------------------- #
def supabase_client():
    """Construit un client Supabase service-role.

    *** Ce client ne doit JAMAIS servir qu'à des SELECT (Règle d'Or #3). ***
    Aucune fonction de ce module n'appelle .insert()/.update()/.delete()/.upsert()
    ni un rpc mutant. L'état applicatif vit dans le SQLite local, jamais dans
    Supabase. C'est l'équivalent du scope gmail.readonly de MailManager : même en
    cas de bug, le périmètre de dégât reste borné à de la lecture.
    """
    from supabase import create_client  # import local : dépendance optionnelle

    env = require_env("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")
    return create_client(env["SUPABASE_URL"], env["SUPABASE_SERVICE_ROLE_KEY"])


def fetch_newsletter_contacts(client) -> list[dict]:
    """Liste opt-in : `SELECT email, name FROM public.email` (Règle d'Or #3,
    #4 — base opt-in uniquement). NE JAMAIS écrire dans cette table.

    Renvoie une liste de dicts {email, name} (email tels quels, normalisés en
    aval par le ciblage)."""
    res = client.table("email").select("email, name").execute()
    rows = getattr(res, "data", None) or []
    return [r for r in rows if r.get("email")]


def find_newsletter_contact(client, email: str) -> dict | None:
    """Cherche UN contact précis dans la base opt-in (SELECT, Règle d'Or #3) par
    email, insensible à la casse. Renvoie {email, name} (valeurs d'origine) ou
    None si l'adresse n'est pas un abonné.

    Sert au /prospect ad-hoc piloté depuis Telegram : on ne rédige QUE pour un
    abonné de la table `email` (Règle d'Or #4 — base opt-in uniquement). On ne
    fabrique jamais un destinataire à partir d'une adresse tapée à la main."""
    key = norm_email(email)
    if not key:
        return None
    for c in fetch_newsletter_contacts(client):
        if norm_email(c.get("email")) == key:
            return {
                "email": (c.get("email") or "").strip(),
                "name": (c.get("name") or "").strip(),
            }
    return None


def _auth_users_email_map(client) -> dict:
    """Construit un mapping user_id -> email via l'API admin auth (source
    autoritative : auth.users.email est toujours rempli, contrairement à
    profiles.email qui peut être NULL — cf. note prod CLAUDE.md). Lecture seule.

    Best effort : en cas d'échec (droits, version SDK), renvoie ce qui a pu être
    lu ; le rapprochement basculera alors sur profiles.email."""
    mapping: dict = {}
    try:
        page = 1
        per_page = 1000
        while True:
            resp = client.auth.admin.list_users(page=page, per_page=per_page)
            # Selon la version de supabase-py : liste d'User, ou objet .users.
            users = resp if isinstance(resp, list) else getattr(resp, "users", []) or []
            if not users:
                break
            for u in users:
                uid = getattr(u, "id", None) or (u.get("id") if isinstance(u, dict) else None)
                mail = getattr(u, "email", None) or (u.get("email") if isinstance(u, dict) else None)
                if uid and mail:
                    mapping[uid] = mail
            if len(users) < per_page:
                break
            page += 1
    except Exception:
        _logger.warning(
            "auth.admin.list_users indisponible ; rapprochement via profiles.email."
        )
    return mapping


def fetch_kernel_client_emails(client) -> set[str]:
    """Emails des clients KERNEL actifs, à EXCLURE du ciblage (Règle d'Or #3 —
    SELECT uniquement).

    Logique :
      SELECT user_id FROM subscriptions
      WHERE plan_type='core_license' AND status='active'
    puis rapprochement user_id -> email via auth.users (autoritative) avec
    complément profiles.email (cf. note prod CLAUDE.md, point 1).
    Renvoie un set d'emails normalisés (minuscules, trim)."""
    subs = (
        client.table("subscriptions")
        .select("user_id")
        .eq("plan_type", "core_license")
        .eq("status", "active")
        .execute()
    )
    sub_rows = getattr(subs, "data", None) or []
    kernel_ids = {r["user_id"] for r in sub_rows if r.get("user_id")}
    if not kernel_ids:
        return set()

    id_to_email = _auth_users_email_map(client)

    # Complément profiles.email pour les ids non résolus par l'API auth.
    missing_ids = [uid for uid in kernel_ids if uid not in id_to_email]
    if missing_ids:
        try:
            profs = (
                client.table("profiles")
                .select("id, email")
                .in_("id", missing_ids)
                .execute()
            )
            for r in getattr(profs, "data", None) or []:
                if r.get("id") and r.get("email"):
                    id_to_email.setdefault(r["id"], r["email"])
        except Exception:
            _logger.warning("Lecture profiles.email échouée (complément ignoré).")

    return {
        norm_email(id_to_email[uid]) for uid in kernel_ids if id_to_email.get(uid)
    }


# --------------------------------------------------------------------------- #
# Pied de mail de conformité (RGPD / LCEN / CAN-SPAM — Règle d'Or #4)
# --------------------------------------------------------------------------- #
def compliance_footer() -> str:
    """Pied standard : identité de l'expéditeur + lien de désinscription visible.
    Chaque mail DOIT le contenir (Règle d'Or #4). Texte identique pour tous les
    destinataires : la désinscription se fait par réponse « STOP » (relevée par
    MailManager via le Reply-To), sans dépendre d'un endpoint web."""
    return (
        "\n\n—\n"
        f"{SENDER_SIGNATURE}\n"
        f"{CONTACT_EMAIL} · {SITE_URL}\n\n"
        "Vous recevez ce message en tant qu'abonné à la newsletter FrenchQuant. "
        f"Pour ne plus être contacté, {UNSUB_SENTINEL} à ce mail."
    )


def ensure_footer(body: str) -> str:
    """Garantit la présence du pied de conformité (idempotent). prepare_campaign
    l'ajoute déjà à la rédaction ; mais en cas d'édition manuelle qui l'aurait
    retiré, le listener le ré-ajoute avant envoi (Règle d'Or #4)."""
    return body if UNSUB_SENTINEL in body else body + compliance_footer()


def list_unsubscribe_header(reply_to: str) -> str:
    """Valeur de l'en-tête List-Unsubscribe (Règle d'Or #4). Désinscription par
    mailto, sans serveur web : conforme et sans infra supplémentaire."""
    return f"<mailto:{reply_to}?subject=unsubscribe>"


# --------------------------------------------------------------------------- #
# Rendu HTML du mail dans la DA FrenchQuant (noir / violet / Space Mono)
# --------------------------------------------------------------------------- #
# Le corps stocké/édité reste du TEXTE BRUT (ce que montre Telegram, et la part
# `text` de Mailgun). Le HTML est dérivé de ce texte au moment de l'envoi : une
# coquille de marque, sobre (mail 1:1 premium, pas une newsletter), avec le pied
# identité + désinscription OBLIGATOIRE (Règle d'Or #4). Le contenu reste une
# DONNÉE : il est échappé avant insertion (anti-injection HTML, Règle d'Or #7).

_URL_RE = _re.compile(r'(https?://[^\s<>"\)]+)')
_EMAIL_RE = _re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')

# Coquille de repli si templates/email.html est absent (jetons {{...}}).
_DEFAULT_EMAIL_SHELL = (
    '<!doctype html><html lang="fr"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
    # dark-only : empêche l'inversion automatique (Gmail/Outlook dark mode).
    '<meta name="color-scheme" content="dark">'
    '<meta name="supported-color-schemes" content="dark">'
    '<style>:root{color-scheme:dark;supported-color-schemes:dark;}</style></head>'
    f'<body style="margin:0;padding:0;background-color:{DA_BG};">'
    '<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{{PREHEADER}}</div>'
    f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{DA_BG}" style="background-color:{DA_BG};"><tr><td align="center" style="padding:32px 12px;">'
    f'<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" bgcolor="{DA_CARD}" style="width:600px;max-width:600px;background-color:{DA_CARD};border:1px solid {DA_BORDER};border-top:3px solid {DA_PRIMARY};">'
    f'<tr><td style="padding:28px 36px 6px 36px;font-family:{DA_FONT};">'
    f'<span style="font-size:15px;letter-spacing:3px;color:#ffffff;font-weight:bold;">FRENCHQUANT</span><span style="color:{DA_PRIMARY};font-weight:bold;">_</span>'
    '<div style="font-size:10px;letter-spacing:2px;color:#52525b;margin-top:5px;">// CODE IS TRUTH</div></td></tr>'
    f'<tr><td style="padding:18px 36px 0 36px;font-family:{DA_FONT};font-size:14px;line-height:1.75;color:{DA_TEXT};">{{BODY}}{{CTA}}</td></tr>'
    f'<tr><td style="padding:6px 36px 30px 36px;"><div style="border-top:1px solid {DA_BORDER};font-size:1px;line-height:1px;height:1px;">&nbsp;</div>'
    f'<div style="padding-top:16px;font-family:{DA_FONT};font-size:11px;line-height:1.7;color:{DA_MUTED};">{{FOOTER}}</div></td></tr>'
    '</table></td></tr></table></body></html>'
)


def _linkify(escaped_text: str, color: str = DA_PRIMARY) -> str:
    """Transforme les URLs et emails (déjà échappés HTML) en liens cliquables."""
    out = _URL_RE.sub(
        lambda m: f'<a href="{m.group(1)}" style="color:{color};text-decoration:underline;">{m.group(1)}</a>',
        escaped_text,
    )
    out = _EMAIL_RE.sub(
        lambda m: f'<a href="mailto:{m.group(0)}" style="color:{color};text-decoration:underline;">{m.group(0)}</a>',
        out,
    )
    return out


def _split_message_footer(text_body: str) -> tuple[str, str]:
    """Sépare le message du pied de conformité. Le pied (compliance_footer) est
    introduit par une ligne contenant uniquement « — »."""
    lines = text_body.split("\n")
    for i, line in enumerate(lines):
        if line.strip() == "—":
            return "\n".join(lines[:i]).rstrip(), "\n".join(lines[i + 1:]).strip()
    return text_body.strip(), ""


def strip_footer(text_body: str) -> str:
    """Renvoie le message sans le pied de conformité (utile pour redonner au
    modèle un brouillon « propre » à retravailler)."""
    return _split_message_footer(text_body)[0]


def _paragraphs_html(text: str) -> str:
    """Texte brut -> paragraphes HTML (ligne vide = nouveau <p>, retour simple =
    <br>), échappé puis liens cliquables. Anti-injection : on échappe AVANT."""
    blocks = _re.split(r"\n\s*\n", text.strip())
    parts = []
    for block in blocks:
        esc = _linkify(_html.escape(block)).replace("\n", "<br>")
        parts.append(
            f'<p style="margin:0 0 16px 0;font-family:{DA_FONT};font-size:14px;'
            f'line-height:1.75;color:{DA_TEXT};">{esc}</p>'
        )
    return "\n".join(parts)


def _cta_button_html(url: str, label: str) -> str:
    """Bouton CTA (DA : violet plein, majuscules espacées, léger glow). Table-based
    pour Outlook. Le violet plein reste lisible quel que soit le thème du client."""
    if not url:
        return ""
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'style="margin:20px 0 6px 0;"><tr>'
        f'<td bgcolor="{DA_PRIMARY}" style="border-radius:4px;'
        'box-shadow:0 0 20px rgba(124,58,237,0.35);">'
        f'<a href="{url}" style="display:inline-block;padding:13px 28px;'
        f'font-family:{DA_FONT};font-size:12px;letter-spacing:1.5px;'
        'text-transform:uppercase;color:#ffffff;border:1px solid '
        f'{DA_PRIMARY};border-radius:4px;">{_html.escape(label)}</a>'
        '</td></tr></table>'
    )


def render_html_email(
    text_body: str,
    cta_url: str = CALENDLY_CALL,
    cta_label: str = "Réserver un échange",
) -> str:
    """Rend le corps texte (message + pied) dans la coquille HTML de la DA.

    `text_body` est exactement la part texte envoyée (déjà passée par
    ensure_footer). On en dérive le HTML pour la part `html` du multipart.
    Le pied de désinscription est toujours présent (Règle d'Or #4)."""
    message, footer = _split_message_footer(text_body)
    if not footer:  # corps sans pied (cas limite) : on régénère le pied standard
        _, footer = _split_message_footer(message + compliance_footer())

    body_html = _paragraphs_html(message)
    cta_html = _cta_button_html(cta_url, cta_label)
    footer_html = _linkify(_html.escape(footer), color=DA_MUTED).replace("\n", "<br>")
    preheader = _html.escape(" ".join(message.split())[:140])

    shell = (
        EMAIL_TEMPLATE_PATH.read_text(encoding="utf-8")
        if EMAIL_TEMPLATE_PATH.exists()
        else _DEFAULT_EMAIL_SHELL
    )
    return (
        shell.replace("{{PREHEADER}}", preheader)
        .replace("{{BODY}}", body_html)
        .replace("{{CTA}}", cta_html)
        .replace("{{FOOTER}}", footer_html)
    )


# --------------------------------------------------------------------------- #
# Base de données locale (SQLite) — tout l'état applicatif vit ici (Règle d'Or #3)
# --------------------------------------------------------------------------- #
_SCHEMA = """
CREATE TABLE IF NOT EXISTS contacted (
    email        TEXT PRIMARY KEY,   -- prospect déjà contacté : ne JAMAIS recontacter
    contacted_at TEXT,
    mailgun_id   TEXT                -- id du message Mailgun (traçabilité)
);
CREATE TABLE IF NOT EXISTS pending (
    token      TEXT PRIMARY KEY,     -- id court (<=32c) pour les callback_data Telegram
    email      TEXT,                 -- destinataire proposé
    name       TEXT,
    subject    TEXT,                 -- objet proposé
    body       TEXT,                 -- corps proposé, en attente de validation
    angle      TEXT,                 -- douleur/angle utilisé (debug & édition)
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS suppressed (
    email    TEXT PRIMARY KEY,       -- désinscrit / bounce / plainte / opt-out manuel
    reason   TEXT,                   -- 'unsubscribe' | 'bounce' | 'complaint' | 'manual'
    added_at TEXT
);
CREATE TABLE IF NOT EXISTS broadcasts (
    token      TEXT PRIMARY KEY,     -- id court pour les callback_data Telegram
    subject    TEXT,                 -- objet proposé
    body       TEXT,                 -- corps + pied, en attente de diffusion
    topic      TEXT,                 -- sujet demandé via Telegram (debug)
    status     TEXT,                 -- 'draft' (test envoyé) | 'sent' (diffusé)
    created_at TEXT
);
"""


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def db_init(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()


# --- contacted (single-touch — Règle d'Or #4) ------------------------------ #
def is_contacted(conn: sqlite3.Connection, email: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM contacted WHERE email = ?", (norm_email(email),)
    )
    return cur.fetchone() is not None


def mark_contacted(
    conn: sqlite3.Connection, email: str, mailgun_id: str | None = None
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO contacted (email, contacted_at, mailgun_id) "
        "VALUES (?, datetime('now'), ?)",
        (norm_email(email), mailgun_id),
    )
    conn.commit()


def fetch_contacted_emails(conn: sqlite3.Connection) -> set[str]:
    cur = conn.execute("SELECT email FROM contacted")
    return {row["email"] for row in cur.fetchall()}


# --- suppressed (opt-out sacré — Règle d'Or #4) ---------------------------- #
def is_suppressed(conn: sqlite3.Connection, email: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM suppressed WHERE email = ?", (norm_email(email),)
    )
    return cur.fetchone() is not None


def add_suppressed(conn: sqlite3.Connection, email: str, reason: str = "manual") -> None:
    conn.execute(
        "INSERT OR REPLACE INTO suppressed (email, reason, added_at) "
        "VALUES (?, ?, datetime('now'))",
        (norm_email(email), reason),
    )
    conn.commit()


def fetch_suppressed_emails(conn: sqlite3.Connection) -> set[str]:
    cur = conn.execute("SELECT email FROM suppressed")
    return {row["email"] for row in cur.fetchall()}


# --- pending (brouillons en attente de validation) ------------------------- #
def add_pending(
    conn: sqlite3.Connection,
    token: str,
    email: str,
    name: str,
    subject: str,
    body: str,
    angle: str,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO pending "
        "(token, email, name, subject, body, angle, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
        (token, email, name, subject, body, angle),
    )
    conn.commit()


def get_pending(conn: sqlite3.Connection, token: str):
    cur = conn.execute("SELECT * FROM pending WHERE token = ?", (token,))
    return cur.fetchone()


def has_pending_for(conn: sqlite3.Connection, email: str) -> bool:
    """True si un brouillon est déjà en attente pour cet email (évite de
    re-proposer le même prospect avant validation).

    La colonne `pending.email` conserve la casse d'origine (c'est le destinataire
    réel de l'envoi), alors que la comparaison doit être insensible à la casse :
    on compare donc en LOWER() des deux côtés."""
    cur = conn.execute(
        "SELECT 1 FROM pending WHERE LOWER(email) = ?", (norm_email(email),)
    )
    return cur.fetchone() is not None


def fetch_pending_emails(conn: sqlite3.Connection) -> set[str]:
    cur = conn.execute("SELECT email FROM pending")
    return {norm_email(row["email"]) for row in cur.fetchall()}


def delete_pending(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM pending WHERE token = ?", (token,))
    conn.commit()


def purge_old_pending(conn: sqlite3.Connection, ttl_days: int = PENDING_TTL_DAYS) -> int:
    """Supprime les brouillons jamais validés plus vieux que ttl_days. Renvoie le
    nombre de lignes purgées."""
    cur = conn.execute(
        "DELETE FROM pending WHERE created_at < datetime('now', ?)",
        (f"-{int(ttl_days)} days",),
    )
    conn.commit()
    return cur.rowcount


# --- broadcasts (mails ad-hoc à la base, pilotés depuis Telegram) ---------- #
def add_broadcast(
    conn: sqlite3.Connection, token: str, subject: str, body: str, topic: str
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO broadcasts "
        "(token, subject, body, topic, status, created_at) "
        "VALUES (?, ?, ?, ?, 'draft', datetime('now'))",
        (token, subject, body, topic),
    )
    conn.commit()


def get_broadcast(conn: sqlite3.Connection, token: str):
    cur = conn.execute("SELECT * FROM broadcasts WHERE token = ?", (token,))
    return cur.fetchone()


def update_broadcast_body(conn: sqlite3.Connection, token: str, body: str) -> None:
    conn.execute(
        "UPDATE broadcasts SET body = ?, status = 'draft' WHERE token = ?",
        (body, token),
    )
    conn.commit()


def update_broadcast(conn: sqlite3.Connection, token: str, subject: str, body: str) -> None:
    """Met à jour objet + corps après une re-rédaction (édition conversationnelle).
    Repasse le statut à 'draft'."""
    conn.execute(
        "UPDATE broadcasts SET subject = ?, body = ?, status = 'draft' WHERE token = ?",
        (subject, body, token),
    )
    conn.commit()


def mark_broadcast_sent(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("UPDATE broadcasts SET status = 'sent' WHERE token = ?", (token,))
    conn.commit()


def delete_broadcast(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM broadcasts WHERE token = ?", (token,))
    conn.commit()


# --------------------------------------------------------------------------- #
# API Telegram (appels HTTP directs, pas de SDK) — repris de MailManager
# --------------------------------------------------------------------------- #
def tg_api(method: str, payload: dict | None = None, http_timeout: int = 30) -> dict:
    """Appel générique à l'API Bot Telegram. Lève sur erreur HTTP.

    On ne propage JAMAIS l'URL brute dans l'exception : elle contient le token
    (Règle d'Or #6 — sinon le secret finit dans journalctl). On remonte le code
    HTTP et la `description` renvoyée par Telegram, bien plus utile au debug."""
    token = os.environ["TELEGRAM_TOKEN"]
    url = f"https://api.telegram.org/bot{token}/{method}"
    resp = requests.post(url, json=payload or {}, timeout=http_timeout)
    if not resp.ok:
        try:
            desc = resp.json().get("description", "")
        except ValueError:
            desc = (resp.text or "")[:300]
        raise requests.HTTPError(
            f"Telegram {method} -> HTTP {resp.status_code}: {desc}", response=resp
        )
    return resp.json()


def tg_send(chat_id, text: str, reply_markup: dict | None = None) -> dict:
    """Envoie un message Telegram. PAS de parse_mode : le contenu (corps de mail,
    champs prospect) est non fiable (Règle d'Or #7), on ne le rend jamais comme
    du Markdown/HTML."""
    payload = {
        "chat_id": chat_id,
        "text": text[:4096],  # limite Telegram
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return tg_api("sendMessage", payload)


def tg_answer_callback(callback_query_id: str, text: str = "") -> dict:
    """Arrête le spinner de chargement sur le bouton tapé."""
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    return tg_api("answerCallbackQuery", payload)


def tg_edit_message(chat_id, message_id: int, text: str, reply_markup: dict | None = None) -> dict:
    """Remplace le texte d'un message existant. Par défaut retire les boutons ;
    si reply_markup est fourni, remplace les boutons (ex. étape de confirmation)."""
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text[:4096]}
    payload["reply_markup"] = reply_markup if reply_markup is not None else {"inline_keyboard": []}
    return tg_api("editMessageText", payload)
