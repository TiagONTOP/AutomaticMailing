"""Listener Telegram (always-on, mail-listener.service, Restart=always).

Tourne en continu en long-polling (getUpdates). Traite :
- les boutons des brouillons de PROSPECTION (send / edit / skip / block) ;
- les COMMANDES de prospection ad-hoc (/prospect <email>, /prospects [N], /who) :
  l'agent rédige à la demande des mails 1:1 pour un ou plusieurs abonnés ;
- la COMMANDE de diffusion (/mail <sujet>) pour composer un mail à toute la base
  (« test puis diffusion »), et /help ;
- les boutons de DIFFUSION (bcast / bgo / bedit / bcancel) ;
- les réponses en mode édition (prospection ou diffusion).

SEUL COMPOSANT AUTORISÉ À ENVOYER UN MAIL (Règle d'Or #1) : les DEUX points
d'envoi Mailgun du projet vivent ici — send_campaign_email() (1 destinataire,
prospection + test de diffusion) et send_broadcast_email() (diffusion batch à la
base). Tous deux ne partent que sur action explicite de l'utilisateur (Règle #2).

Toute communication entrante est filtrée par TELEGRAM_CHAT_ID (Règle d'Or #5).
"""

import json
import os
import secrets
import time

import requests

import common

logger = common.get_logger("telegram_listener")

# Timeout du long-polling Telegram (secondes). Le timeout HTTP est plus large.
POLL_TIMEOUT = 50

# Limite Mailgun par appel batch (recipient-variables). On découpe au-delà.
BROADCAST_BATCH_SIZE = 1000

HELP_TEXT = (
    "Commandes disponibles :\n\n"
    "PROSPECTION (mails 1:1, single-touch) :\n"
    "• /prospect <email> [consigne] — l'agent rédige un mail de prospection ad-hoc "
    "pour CE contact (doit être un abonné opt-in jamais contacté). La consigne est "
    "optionnelle (ex. « insiste sur l'accès au code source »). Validation par les "
    "boutons Envoyer / Éditer / Ignorer / Ne plus contacter.\n"
    "• /prospects [N] — l'agent choisit lui-même jusqu'à N prospects éligibles et "
    "rédige un mail pour chacun (défaut : CAMPAIGN_BATCH_SIZE).\n"
    "• /who [N] — aperçu (lecture seule) des prochains prospects éligibles, sans "
    "rédaction ni envoi.\n\n"
    "DIFFUSION À LA BASE :\n"
    "• /mail <consigne> — l'agent rédige un mail pour TOUTE la base. Brouillon : "
    "Valider / Éditer / Annuler.\n"
    "   – ✏️ Éditer : dis-lui en langage naturel ce qu'il faut changer, il re-rédige.\n"
    "   – ✅ Valider : il t'envoie un TEST, puis tu confirmes la diffusion (hors "
    "désinscrits).\n\n"
    "• /help — cette aide.\n\n"
    "Aucun mail ne part jamais sans ton action explicite. Les brouillons de "
    "prospection du timer arrivent aussi automatiquement, avec leurs boutons."
)


# --------------------------------------------------------------------------- #
# Slot d'édition (awaiting_edit.json) — un seul édit en attente à la fois.
# Porte un `kind` ('pending' = prospection | 'broadcast' = diffusion à la base)
# pour router le texte corrigé. Limitation connue (roadmap #3) : un seul à la fois.
# --------------------------------------------------------------------------- #
def read_edit_slot() -> dict | None:
    if not common.AWAITING_EDIT_PATH.exists():
        return None
    try:
        return json.loads(common.AWAITING_EDIT_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def write_edit_slot(token: str, chat_id, kind: str = "pending") -> None:
    common.AWAITING_EDIT_PATH.write_text(
        json.dumps({"token": token, "chat_id": str(chat_id), "kind": kind}),
        encoding="utf-8",
    )


def clear_edit_slot() -> None:
    try:
        common.AWAITING_EDIT_PATH.unlink()
    except FileNotFoundError:
        pass


# --------------------------------------------------------------------------- #
# Verrou chat_id (Règle d'Or #5)
# --------------------------------------------------------------------------- #
def authorized_chat_id(chat_id) -> bool:
    return str(chat_id) == os.environ["TELEGRAM_CHAT_ID"]


def _operator_email() -> str:
    """Adresse qui reçoit les mails de TEST avant diffusion. OPERATOR_EMAIL, sinon
    le Reply-To, sinon le contact FrenchQuant."""
    return (
        os.environ.get("OPERATOR_EMAIL")
        or os.environ.get("MAILGUN_REPLY_TO")
        or common.CONTACT_EMAIL
    )


# --------------------------------------------------------------------------- #
# Envoi via Mailgun — POINTS D'ENVOI DU PROJET (Règle d'Or #1)
# --------------------------------------------------------------------------- #
def _mailgun_base() -> tuple[dict, str, str]:
    env = common.require_env(
        "MAILGUN_API_KEY", "MAILGUN_DOMAIN", "MAILGUN_BASE_URL", "MAILGUN_FROM"
    )
    reply_to = os.environ.get("MAILGUN_REPLY_TO", common.CONTACT_EMAIL)
    url = f"{env['MAILGUN_BASE_URL'].rstrip('/')}/{env['MAILGUN_DOMAIN']}/messages"
    return env, reply_to, url


def send_campaign_email(row) -> str:
    """Envoie un mail à UN destinataire via Mailgun (prospection + test de
    diffusion).

    *** Un des deux uniques points d'appel à l'API d'envoi Mailgun, tous deux dans
    ce fichier (Règle d'Or #1). *** Déclenché uniquement par une action utilisateur
    (Règle d'Or #2). Renvoie l'id Mailgun. Lève en cas d'échec HTTP.
    """
    env, reply_to, url = _mailgun_base()
    # Pied identité + désinscription garanti (Règle d'Or #4), idempotent.
    body = common.ensure_footer(row["body"])
    data = {
        "from": env["MAILGUN_FROM"],
        "to": row["email"],
        "subject": row["subject"],
        # Multipart : `text` (repli) + `html` (DA du site, forcé sombre).
        "text": body,
        "html": common.render_html_email(body),
        "h:Reply-To": reply_to,
        "h:List-Unsubscribe": common.list_unsubscribe_header(reply_to),
    }
    resp = requests.post(url, auth=("api", env["MAILGUN_API_KEY"]), data=data, timeout=30)
    resp.raise_for_status()
    try:
        return resp.json().get("id", "")
    except ValueError:
        return ""


def send_broadcast_email(subject: str, body: str, recipients: list[str]) -> tuple[int, list[str]]:
    """Diffuse un mail à une LISTE de destinataires via Mailgun batch sending.

    `recipient-variables` force Mailgun à envoyer un message INDIVIDUEL par
    destinataire : personne ne voit les autres adresses. Découpe en lots de 1000.

    *** Deuxième et dernier point d'envoi Mailgun du projet, dans ce fichier
    (Règle d'Or #1). *** Déclenché uniquement par la confirmation explicite de
    l'utilisateur (Règle d'Or #2). Le pied identité + désinscription est garanti
    (Règle d'Or #4) et l'en-tête List-Unsubscribe est posé pour tous.
    Renvoie (nb_envoyés, [mailgun_ids]). Lève en cas d'échec HTTP.
    """
    env, reply_to, url = _mailgun_base()
    body = common.ensure_footer(body)
    html = common.render_html_email(body)
    sent = 0
    ids: list[str] = []
    for i in range(0, len(recipients), BROADCAST_BATCH_SIZE):
        chunk = recipients[i:i + BROADCAST_BATCH_SIZE]
        # recipient-variables : un message individuel par destinataire.
        recipient_vars = json.dumps({e: {"id": j} for j, e in enumerate(chunk)})
        data = {
            "from": env["MAILGUN_FROM"],
            "to": chunk,  # requests répète la clé `to` ; Mailgun gère le multi-dest
            "subject": subject,
            "text": body,
            "html": html,
            "h:Reply-To": reply_to,
            "h:List-Unsubscribe": common.list_unsubscribe_header(reply_to),
            "recipient-variables": recipient_vars,
        }
        resp = requests.post(url, auth=("api", env["MAILGUN_API_KEY"]), data=data, timeout=60)
        resp.raise_for_status()
        sent += len(chunk)
        try:
            ids.append(resp.json().get("id", ""))
        except ValueError:
            pass
    return sent, ids


def get_broadcast_recipients(conn) -> list[str]:
    """Destinataires d'une diffusion : base opt-in (Supabase, LECTURE SEULE,
    Règle d'Or #3) moins les désinscrits locaux (Règle d'Or #4). Liste unique
    d'emails (casse/trim normalisés pour la dédup, email d'origine conservé)."""
    sb = common.supabase_client()
    contacts = common.fetch_newsletter_contacts(sb)
    suppressed = common.fetch_suppressed_emails(conn)
    seen: set[str] = set()
    out: list[str] = []
    for c in contacts:
        email = (c.get("email") or "").strip()
        key = common.norm_email(email)
        if not key or key in suppressed or key in seen:
            continue
        seen.add(key)
        out.append(email)
    return out


# --------------------------------------------------------------------------- #
# Handlers PROSPECTION (boutons send / edit / skip / block)
# --------------------------------------------------------------------------- #
def handle_send(conn, chat_id, message_id, token) -> None:
    row = common.get_pending(conn, token)
    if row is None:
        common.tg_edit_message(chat_id, message_id, "⏳ Brouillon introuvable ou déjà traité.")
        return
    # Garde-fou opt-out (Règle d'Or #4) : jamais d'envoi à un désinscrit.
    if common.is_suppressed(conn, row["email"]):
        common.delete_pending(conn, token)
        common.tg_edit_message(chat_id, message_id, f"🚫 {row['email']} est désinscrit — non envoyé.")
        return
    try:
        mailgun_id = send_campaign_email(row)
    except requests.RequestException as exc:
        # Échec Mailgun : on notifie, on NE marque PAS contacted, on garde le
        # pending pour réessai (cf. CLAUDE.md > Envoi via Mailgun).
        logger.exception("Échec d'envoi Mailgun pour le token %s", token)
        common.tg_edit_message(chat_id, message_id, f"❌ Échec de l'envoi : {exc}")
        return
    common.mark_contacted(conn, row["email"], mailgun_id)
    common.delete_pending(conn, token)
    common.tg_edit_message(chat_id, message_id, f"✅ Envoyé à {row['email']}.")
    logger.info("Mail envoyé (token=%s) à %s [mailgun_id=%s].", token, row["email"], mailgun_id)


def handle_edit(conn, chat_id, token) -> None:
    row = common.get_pending(conn, token)
    if row is None:
        common.tg_send(chat_id, "⏳ Brouillon introuvable ou déjà traité.")
        return
    write_edit_slot(token, chat_id, kind="pending")
    common.tg_send(
        chat_id,
        f"✏️ Envoie-moi le corps corrigé pour le mail à {row['email']}.\n"
        "Le mail partira avec ce texte dès réception (un pied de désinscription "
        "est ajouté automatiquement si besoin).",
    )


def handle_skip(conn, chat_id, message_id, token) -> None:
    """Ignore le brouillon sans rien envoyer (le prospect reste éligible)."""
    common.delete_pending(conn, token)
    common.tg_edit_message(chat_id, message_id, "🗑 Ignoré.")


def handle_block(conn, chat_id, message_id, token) -> None:
    """Ne plus contacter : ajoute à suppressed (manual, Règle d'Or #4)."""
    row = common.get_pending(conn, token)
    if row is None:
        common.tg_edit_message(chat_id, message_id, "⏳ Brouillon introuvable ou déjà traité.")
        return
    common.add_suppressed(conn, row["email"], reason="manual")
    common.delete_pending(conn, token)
    common.tg_edit_message(chat_id, message_id, f"🚫 {row['email']} ajouté à la liste « ne plus contacter ».")
    logger.info("Prospect %s ajouté à suppressed (manual).", row["email"])


# --------------------------------------------------------------------------- #
# Commandes PROSPECTION ad-hoc (/prospect, /prospects, /who) — pilotées depuis
# Telegram, propulsées par l'autonomie de l'agent. Elles ne font que PRÉPARER des
# brouillons : elles réutilisent la carte de validation existante (boutons
# send/edit/skip/block). AUCUN nouvel envoi n'est introduit (Règle d'Or #1) ;
# l'envoi reste déclenché par le tap « Envoyer » via handle_send (Règle d'Or #2).
# --------------------------------------------------------------------------- #
# Borne le nb de prospects préparés à la demande (/prospects) : garde le volume
# bas (single-touch premium, #4) et évite de bloquer trop longtemps le listener
# mono-thread (chaque rédaction prend ~30-60 s).
PROSPECTS_ON_DEMAND_MAX = 10
WHO_PREVIEW_MAX = 30


def _looks_like_email(s: str) -> bool:
    """Validation minimale d'une adresse (filtre d'entrée, pas une preuve)."""
    return "@" in s and " " not in s and "." in s.rsplit("@", 1)[-1]


def handle_prospect_command(conn, chat_id, arg) -> None:
    """/prospect <email> [consigne] : rédige un mail de prospection ad-hoc pour UN
    abonné précis, puis le propose avec les boutons habituels (Envoyer / Éditer /
    Ignorer / Ne plus contacter). N'ENVOIE RIEN (Règles d'Or #1, #2).

    Garde-fous AVANT toute rédaction (Règle d'Or #4) : on refuse un désinscrit, un
    contact déjà touché (single-touch), un brouillon déjà en attente, et toute
    adresse absente de la base opt-in (on n'écrit qu'aux abonnés de la table
    `email`, jamais à une adresse fabriquée à la main)."""
    arg = (arg or "").strip()
    if not arg:
        common.tg_send(chat_id, "Usage : /prospect <email> [consigne d'angle]")
        return
    email_part, _, directive = arg.partition(" ")
    email_part = email_part.strip()
    directive = directive.strip()
    if not _looks_like_email(email_part):
        common.tg_send(chat_id, "Adresse invalide. Usage : /prospect <email> [consigne d'angle]")
        return

    # 1) Garde-fous locaux (rapides, sans réseau) — opt-out / single-touch (#4).
    if common.is_suppressed(conn, email_part):
        common.tg_send(chat_id, f"🚫 {email_part} est désinscrit — on ne le recontacte pas (Règle opt-out).")
        return
    if common.is_contacted(conn, email_part):
        common.tg_send(chat_id, f"✅ {email_part} a déjà été contacté (single-touch) — pas de second mail.")
        return
    if common.has_pending_for(conn, email_part):
        common.tg_send(chat_id, f"⏳ Un brouillon est déjà en attente pour {email_part}. Traite-le d'abord.")
        return

    # 2) Vérif opt-in (Supabase, LECTURE SEULE #3) : doit être un abonné réel (#4).
    common.tg_send(chat_id, f"🔎 Je vérifie {email_part} dans la base opt-in…")
    try:
        sb = common.supabase_client()
        contact = common.find_newsletter_contact(sb, email_part)
    except Exception as exc:
        logger.exception("Lookup opt-in impossible pour %s", email_part)
        common.tg_send(chat_id, f"❌ Vérification de la base impossible : {exc}")
        return
    if contact is None:
        common.tg_send(
            chat_id,
            f"⛔ {email_part} n'est pas dans la base opt-in (table email). On n'écrit "
            "qu'aux abonnés de la newsletter (Règle d'Or #4) — rien préparé.",
        )
        return

    # 3) Rédaction (autonomie de l'agent) + carte de validation. PAS d'envoi.
    common.tg_send(chat_id, f"✍️ Je rédige un mail de prospection pour {contact['email']}…")
    import prepare_campaign as pc

    try:
        context = pc.build_context()
        pc.process_one(conn, chat_id, context, contact, directive)
    except Exception as exc:
        logger.exception("Échec de préparation /prospect pour %s", email_part)
        common.tg_send(chat_id, f"❌ Échec de la rédaction : {exc}")


def handle_prospects_command(conn, chat_id, arg) -> None:
    """/prospects [N] : campagne à la demande. L'agent sélectionne lui-même jusqu'à
    N prospects éligibles (jamais contactés / désinscrits / en attente / clients
    KERNEL) et rédige un mail pour chacun, poussé en carte de validation. N'ENVOIE
    RIEN (Règles d'Or #1, #2) — c'est le déclenchement manuel de ce que fait le
    timer."""
    arg = (arg or "").strip()
    n = int(os.environ.get("CAMPAIGN_BATCH_SIZE", common.DEFAULT_BATCH_SIZE))
    if arg:
        try:
            n = int(arg.split()[0])
        except ValueError:
            common.tg_send(chat_id, "Usage : /prospects [nombre]")
            return
    n = max(1, min(n, PROSPECTS_ON_DEMAND_MAX))

    common.tg_send(chat_id, f"🔎 Je cherche jusqu'à {n} prospect(s) éligible(s) et je rédige…")
    import prepare_campaign as pc

    try:
        sb = common.supabase_client()
        context = pc.build_context()
        prospects = pc.select_prospects(sb, conn, n)
    except Exception as exc:
        logger.exception("Ciblage /prospects impossible.")
        common.tg_send(chat_id, f"❌ Ciblage impossible : {exc}")
        return
    if not prospects:
        common.tg_send(
            chat_id,
            "Aucun prospect éligible pour l'instant (tous contactés / désinscrits / "
            "en attente, ou base vide).",
        )
        return

    ok = 0
    for prospect in prospects:
        try:
            pc.process_one(conn, chat_id, context, prospect)
            ok += 1
        except Exception as exc:
            # Un prospect qui plante n'arrête pas le lot (cf. Conventions de code).
            logger.exception("Échec de préparation pour %s", prospect.get("email"))
            common.tg_send(chat_id, f"⚠️ Erreur pour {prospect.get('email', '?')} : {exc}")
    common.tg_send(
        chat_id,
        f"✅ {ok} brouillon(s) prêt(s) (prospection 1:1, single-touch) — valide "
        "chacun avec ses boutons.\n\n"
        "ℹ️ /prospects est de la prospection single-touch (un prospect à la fois, "
        "borné). Pour envoyer UN SEUL mail à TOUTE ta base d'un coup (batch), "
        "utilise /mail <sujet>.",
    )


def handle_who_command(conn, chat_id, arg) -> None:
    """/who [N] : aperçu (LECTURE SEULE) des prochains prospects éligibles, SANS
    rédaction ni envoi. Aide à décider qui cibler avec /prospect ou /prospects."""
    arg = (arg or "").strip()
    n = 10
    if arg:
        try:
            n = int(arg.split()[0])
        except ValueError:
            common.tg_send(chat_id, "Usage : /who [nombre]")
            return
    n = max(1, min(n, WHO_PREVIEW_MAX))

    import prepare_campaign as pc

    try:
        sb = common.supabase_client()
        prospects = pc.select_prospects(sb, conn, n)
    except Exception as exc:
        logger.exception("Aperçu /who impossible.")
        common.tg_send(chat_id, f"❌ Lecture impossible : {exc}")
        return
    if not prospects:
        common.tg_send(chat_id, "Aucun prospect éligible pour l'instant.")
        return

    lines = [f"👥 {len(prospects)} prospect(s) éligible(s) — aperçu, aucun envoi :"]
    for p in prospects:
        nm = p.get("name") or ""
        lines.append(f"• {nm + ' — ' if nm else ''}{p['email']}")
    lines.append(f"\nPour rédiger : /prospects {len(prospects)}  ou  /prospect <email>")
    common.tg_send(chat_id, "\n".join(lines))


# --------------------------------------------------------------------------- #
# Handlers DIFFUSION À LA BASE (commande /mail + boutons bcast/bgo/bedit/bcancel)
# --------------------------------------------------------------------------- #
def _draft_text(subject, body, topic) -> str:
    return "\n".join([
        "🗞 Brouillon — mail à la BASE",
        f"🧩 Prompt : {topic}",
        f"✉️ Objet : {subject}",
        "",
        body,
    ])


def _draft_markup(token) -> dict:
    """Boutons du brouillon : Valider / Éditer (conversationnel) / Annuler."""
    return {
        "inline_keyboard": [
            [{"text": "✅ Valider", "callback_data": f"bcast:{token}"}],
            [
                {"text": "✏️ Éditer", "callback_data": f"bedit:{token}"},
                {"text": "🗑 Annuler", "callback_data": f"bcancel:{token}"},
            ],
        ]
    }


def notify_broadcast(chat_id, subject, body, topic, token) -> None:
    """Pousse le brouillon + boutons Valider/Éditer/Annuler. AUCUN envoi ici :
    /mail ne fait que proposer (Règle d'Or #2)."""
    common.tg_send(chat_id, _draft_text(subject, body, topic), reply_markup=_draft_markup(token))


def reshow_broadcast(chat_id, message_id, row) -> None:
    """Ré-affiche le brouillon (retour depuis l'étape de confirmation)."""
    common.tg_edit_message(
        chat_id, message_id,
        _draft_text(row["subject"], row["body"], row["topic"]),
        reply_markup=_draft_markup(row["token"]),
    )


def _send_broadcast_test(subject: str, body: str) -> str:
    """Envoie le mail de TEST à l'opérateur (via le chemin d'envoi unitaire).
    Renvoie une note de statut pour Telegram."""
    operator = _operator_email()
    try:
        send_campaign_email({"email": operator, "subject": f"[TEST base] {subject}", "body": body})
        return f"📨 Test envoyé à {operator} — vérifie le rendu avant diffusion."
    except requests.RequestException as exc:
        logger.exception("Échec de l'envoi du test de diffusion.")
        return f"⚠️ Échec de l'envoi du test : {exc}"


def _recipients_count_note(conn) -> tuple[int | None, str]:
    try:
        n = len(get_broadcast_recipients(conn))
        return n, f"🎯 Cible : ~{n} destinataires opt-in (hors désinscrits)."
    except Exception as exc:
        logger.exception("Comptage des destinataires de diffusion impossible.")
        return None, f"⚠️ Comptage des destinataires indisponible : {exc}"


def handle_mail_command(conn, chat_id, prompt) -> None:
    """/mail <prompt> : l'agent RÉDIGE un brouillon et le propose. N'ENVOIE RIEN
    (Règle d'Or #2). Validation / édition / diffusion se font ensuite par boutons."""
    prompt = (prompt or "").strip()
    if not prompt:
        common.tg_send(chat_id, "Usage : /mail <ce que tu veux dire à ta base>")
        return

    common.tg_send(chat_id, f"✍️ Je rédige un brouillon : « {prompt[:80]} »…")
    # Import tardif : la composition dépend de claude-agent-sdk (pas requis pour
    # le reste du listener).
    import prepare_campaign as pc

    try:
        result = pc.compose_broadcast(prompt)
    except Exception as exc:
        logger.exception("Échec de composition du broadcast.")
        common.tg_send(chat_id, f"❌ Échec de la rédaction : {exc}")
        return

    subject = (result.get("subject") or "").strip()
    raw_body = (result.get("body") or "").strip()
    if not subject or not raw_body:
        common.tg_send(chat_id, "❌ Rédaction incomplète (objet/corps vide).")
        return

    body = common.ensure_footer(raw_body)
    token = secrets.token_hex(common.TOKEN_LEN // 2)
    common.add_broadcast(conn, token, subject, body, prompt)
    notify_broadcast(chat_id, subject, body, prompt, token)
    logger.info("Broadcast préparé (token=%s) sur « %s ».", token, prompt[:60])


def handle_broadcast_validate(conn, chat_id, message_id, token) -> None:
    """Bouton « Valider » : envoie un TEST à l'opérateur, puis propose la diffusion
    avec confirmation (flux « test puis broadcast »). Ne diffuse pas encore."""
    row = common.get_broadcast(conn, token)
    if row is None:
        common.tg_edit_message(chat_id, message_id, "⏳ Brouillon introuvable ou déjà traité.")
        return
    if row["status"] == "sent":  # anti double-diffusion
        common.tg_edit_message(chat_id, message_id, "✅ Ce mail a déjà été diffusé.")
        return
    test_note = _send_broadcast_test(row["subject"], row["body"])
    n, _ = _recipients_count_note(conn)
    if n is None:
        common.tg_edit_message(chat_id, message_id, "❌ Destinataires indisponibles (Supabase ?). Réessaie.")
        return
    if n == 0:
        common.tg_edit_message(chat_id, message_id, "Aucun destinataire (base vide ou tous désinscrits).")
        return
    markup = {
        "inline_keyboard": [
            [
                {"text": f"📣 Diffuser à {n}", "callback_data": f"bgo:{token}"},
                {"text": "↩️ Retour", "callback_data": f"bback:{token}"},
            ]
        ]
    }
    common.tg_edit_message(
        chat_id, message_id,
        f"✉️ Objet : {row['subject']}\n\n{test_note}\n\n"
        f"⚠️ Diffuser à {n} destinataires (base opt-in, hors désinscrits) ? "
        "Action irréversible.",
        reply_markup=markup,
    )


def handle_broadcast_back(conn, chat_id, message_id, token) -> None:
    """Retour à l'écran de brouillon (depuis la confirmation de diffusion)."""
    row = common.get_broadcast(conn, token)
    if row is None:
        common.tg_edit_message(chat_id, message_id, "⏳ Brouillon introuvable ou déjà traité.")
        return
    reshow_broadcast(chat_id, message_id, row)


def handle_broadcast_go(conn, chat_id, message_id, token) -> None:
    """Confirmation -> DIFFUSION réelle à la base (Règle d'Or #2)."""
    row = common.get_broadcast(conn, token)
    if row is None:
        common.tg_edit_message(chat_id, message_id, "⏳ Brouillon introuvable ou déjà traité.")
        return
    if row["status"] == "sent":  # anti double-diffusion
        common.tg_edit_message(chat_id, message_id, "✅ Ce mail a déjà été diffusé.")
        return
    try:
        recipients = get_broadcast_recipients(conn)
    except Exception as exc:
        logger.exception("Récupération des destinataires impossible.")
        common.tg_edit_message(chat_id, message_id, f"❌ Destinataires indisponibles : {exc}")
        return
    if not recipients:
        common.tg_edit_message(chat_id, message_id, "Aucun destinataire. Rien envoyé.")
        return

    common.tg_edit_message(chat_id, message_id, f"📣 Diffusion en cours à {len(recipients)} destinataires…")
    try:
        sent, _ids = send_broadcast_email(row["subject"], row["body"], recipients)
    except requests.RequestException as exc:
        # Échec : on garde le brouillon pour réessai, on ne marque pas 'sent'.
        logger.exception("Échec de diffusion (token %s).", token)
        common.tg_send(chat_id, f"❌ Échec de la diffusion : {exc} (brouillon conservé).")
        return
    common.mark_broadcast_sent(conn, token)
    common.tg_send(chat_id, f"✅ Diffusé à {sent} destinataires.")
    logger.info("Broadcast %s diffusé à %d destinataires.", token, sent)


def handle_broadcast_edit(conn, chat_id, token) -> None:
    """Bouton « Éditer » : passe en mode édition CONVERSATIONNELLE — l'utilisateur
    décrit ce qu'il veut changer, l'agent re-rédige (il ne réécrit pas le corps)."""
    row = common.get_broadcast(conn, token)
    if row is None:
        common.tg_send(chat_id, "⏳ Brouillon introuvable ou déjà traité.")
        return
    write_edit_slot(token, chat_id, kind="broadcast")
    common.tg_send(
        chat_id,
        "✏️ Dis-moi ce que tu veux changer (en langage naturel). Exemples : "
        "« plus court », « ajoute le lien Calendly », « ton plus direct », "
        "« insiste sur l'accès au code source ». Je te repropose un brouillon.",
    )


def handle_broadcast_cancel(conn, chat_id, message_id, token) -> None:
    common.delete_broadcast(conn, token)
    common.tg_edit_message(chat_id, message_id, "🗑 Brouillon annulé.")


def handle_broadcast_edited(conn, chat_id, token, instruction) -> None:
    """Consigne de modification (langage naturel) : l'agent RE-RÉDIGE le brouillon
    et le repropose. N'ENVOIE RIEN (Règle d'Or #2)."""
    row = common.get_broadcast(conn, token)
    if row is None:
        clear_edit_slot()
        common.tg_send(chat_id, "⏳ Brouillon introuvable ou déjà traité.")
        return
    clear_edit_slot()
    common.tg_send(chat_id, "✍️ Je retravaille le brouillon…")
    import prepare_campaign as pc

    try:
        result = pc.recompose_broadcast(row["topic"], row["subject"], row["body"], instruction)
    except Exception as exc:
        logger.exception("Échec de re-composition du broadcast.")
        common.tg_send(chat_id, f"❌ Échec de la re-rédaction : {exc}")
        return

    subject = (result.get("subject") or "").strip() or row["subject"]
    raw_body = (result.get("body") or "").strip()
    if not raw_body:
        common.tg_send(chat_id, "❌ Re-rédaction incomplète (corps vide).")
        return
    body = common.ensure_footer(raw_body)
    common.update_broadcast(conn, token, subject, body)
    notify_broadcast(chat_id, subject, body, row["topic"], token)


# --------------------------------------------------------------------------- #
# Mode édition (prospection ou diffusion) : texte libre = corps corrigé
# --------------------------------------------------------------------------- #
def handle_edited_text(conn, chat_id, text) -> None:
    slot = read_edit_slot()
    if not slot or slot.get("chat_id") != str(chat_id):
        return  # rien en attente d'édition pour ce chat
    token = slot.get("token")
    if slot.get("kind") == "broadcast":
        handle_broadcast_edited(conn, chat_id, token, text)
        return

    # --- édition d'un brouillon de PROSPECTION = déclencheur d'envoi (#1, #2) ---
    row = common.get_pending(conn, token)
    if row is None:
        clear_edit_slot()
        common.tg_send(chat_id, "⏳ Brouillon introuvable ou déjà traité.")
        return
    if common.is_suppressed(conn, row["email"]):
        clear_edit_slot()
        common.delete_pending(conn, token)
        common.tg_send(chat_id, f"🚫 {row['email']} est désinscrit — non envoyé.")
        return
    edited = dict(row)
    edited["body"] = text
    try:
        mailgun_id = send_campaign_email(edited)
    except requests.RequestException as exc:
        logger.exception("Échec d'envoi Mailgun (édition) pour le token %s", token)
        common.tg_send(chat_id, f"❌ Échec de l'envoi : {exc}")
        return
    common.mark_contacted(conn, row["email"], mailgun_id)
    common.delete_pending(conn, token)
    clear_edit_slot()
    common.tg_send(chat_id, f"✅ Envoyé (texte édité) à {row['email']}.")
    logger.info("Mail envoyé après édition (token=%s) à %s.", token, row["email"])


# --------------------------------------------------------------------------- #
# Commandes libres
# --------------------------------------------------------------------------- #
def handle_command(conn, chat_id, text) -> None:
    cmd, _, arg = text.partition(" ")
    cmd = cmd.lower().lstrip("/")
    if cmd in ("mail", "broadcast"):
        handle_mail_command(conn, chat_id, arg)
    elif cmd == "prospect":
        handle_prospect_command(conn, chat_id, arg)
    elif cmd in ("prospects", "campaign"):
        handle_prospects_command(conn, chat_id, arg)
    elif cmd in ("who", "eligibles", "eligible"):
        handle_who_command(conn, chat_id, arg)
    elif cmd in ("help", "start"):
        common.tg_send(chat_id, HELP_TEXT)
    else:
        common.tg_send(chat_id, "Commande inconnue. /help pour la liste.")


# --------------------------------------------------------------------------- #
# Dispatch d'un update
# --------------------------------------------------------------------------- #
def handle_update(conn, update: dict) -> None:
    callback = update.get("callback_query")
    if callback is not None:
        chat_id = callback.get("message", {}).get("chat", {}).get("id")
        # Verrou chat_id (Règle d'Or #5) : on n'agit que pour le chat autorisé.
        if not authorized_chat_id(chat_id):
            common.tg_answer_callback(callback["id"], "Non autorisé.")
            logger.warning("Callback ignoré : chat %s non autorisé.", chat_id)
            return
        common.tg_answer_callback(callback["id"])  # stoppe le spinner
        message_id = callback["message"]["message_id"]
        action, _, token = (callback.get("data") or "").partition(":")
        # Prospection
        if action == "send":
            handle_send(conn, chat_id, message_id, token)
        elif action == "edit":
            handle_edit(conn, chat_id, token)
        elif action == "skip":
            handle_skip(conn, chat_id, message_id, token)
        elif action == "block":
            handle_block(conn, chat_id, message_id, token)
        # Diffusion à la base
        elif action == "bcast":  # « Valider »
            handle_broadcast_validate(conn, chat_id, message_id, token)
        elif action == "bback":  # retour au brouillon
            handle_broadcast_back(conn, chat_id, message_id, token)
        elif action == "bgo":  # confirmer la diffusion
            handle_broadcast_go(conn, chat_id, message_id, token)
        elif action == "bedit":
            handle_broadcast_edit(conn, chat_id, token)
        elif action == "bcancel":
            handle_broadcast_cancel(conn, chat_id, message_id, token)
        else:
            logger.info("Action de callback inconnue : %r", action)
        return

    message = update.get("message")
    if message is not None:
        chat_id = message.get("chat", {}).get("id")
        if not authorized_chat_id(chat_id):
            logger.warning("Message ignoré : chat %s non autorisé.", chat_id)
            return
        text = message.get("text")
        if not text:
            return
        stripped = text.strip()
        # Une commande (/...) est toujours prioritaire sur le mode édition.
        if stripped.startswith("/"):
            handle_command(conn, chat_id, stripped)
            return
        # Hors commande : seul le texte en mode édition est pris en compte (le
        # pilotage se fait par boutons). Sinon, on ignore.
        slot = read_edit_slot()
        if slot and slot.get("chat_id") == str(chat_id):
            handle_edited_text(conn, chat_id, text)


# --------------------------------------------------------------------------- #
# Boucle de long-polling
# --------------------------------------------------------------------------- #
def get_updates(offset: int | None) -> list[dict]:
    params = {"timeout": POLL_TIMEOUT, "allowed_updates": ["message", "callback_query"]}
    if offset is not None:
        params["offset"] = offset
    data = common.tg_api("getUpdates", params, http_timeout=POLL_TIMEOUT + 15)
    return data.get("result", [])


def main() -> None:
    common.load_env()
    common.require_env("TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID")

    conn = common.db_connect()
    common.db_init(conn)

    logger.info("Listener démarré (long-polling).")
    offset: int | None = None
    while True:
        try:
            updates = get_updates(offset)
        except requests.RequestException as exc:
            # Erreur réseau transitoire : on log et on retente après une pause.
            logger.warning("getUpdates a échoué (%s), nouvelle tentative…", exc)
            time.sleep(5)
            continue

        for update in updates:
            offset = update["update_id"] + 1
            try:
                handle_update(conn, update)
            except Exception as exc:
                logger.exception("Échec du traitement d'un update.")
                try:
                    chat_id = os.environ["TELEGRAM_CHAT_ID"]
                    common.tg_send(chat_id, f"⚠️ Erreur dans le listener : {exc}")
                except Exception:
                    logger.exception("Échec de la notification d'erreur Telegram.")


if __name__ == "__main__":
    main()
