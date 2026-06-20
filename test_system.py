"""Harnais de test end-to-end du système de prospection.

*** GARDE-FOU ABSOLU : n'envoie JAMAIS de mail à une autre adresse que
TEST_RECIPIENT (ftiago125@gmail.com). *** La fonction d'envoi réelle
(telegram_listener.send_campaign_email) est enveloppée par un garde qui lève si
le destinataire diffère. Aucun vrai prospect n'est jamais contacté : le ciblage
Supabase est lu en SELECT et seulement compté (pas de préparation, pas d'envoi).

Toutes les écritures d'état se font dans une base SQLite TEMPORAIRE : la vraie
state.db du projet n'est pas touchée.

Lancer : python test_system.py
Le résultat de la rédaction est lu depuis _redaction_out.json (produit par
_redaction_probe.py) s'il existe ; sinon un corps statique de test est utilisé.
"""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import common
import prepare_campaign as pc
import telegram_listener as tl

TEST_RECIPIENT = "ftiago125@gmail.com"
REDACTION_OUT = common.BASE_DIR / "_redaction_out.json"

# Corps statique de repli si la rédaction (SDK) n'est pas disponible ici.
STATIC_SUBJECT = "[TEST] pricer une exotique sans réécrire ton moteur"
STATIC_BODY = (
    "Bonjour Tiago,\n\n"
    "Ceci est un mail de TEST du système de prospection FrenchQuant.\n"
    "Beaucoup recodent le même moteur Monte-Carlo faute d'accès au code de "
    "référence. KERNEL donne cet accès : le code source, documenté.\n\n"
    "Si tu veux en parler, on peut caler 20 minutes : "
    "https://calendly.com/frenchquant125/client-call"
)

results: list[tuple[str, str, str]] = []


def record(facet: str, ok, detail: str = "") -> None:
    status = "OK" if ok is True else ("SKIP" if ok is None else "ÉCHEC")
    results.append((facet, status, detail))
    print(f"[{status}] {facet} — {detail}")


# --------------------------------------------------------------------------- #
# GARDE-FOU : envelopper l'envoi réel pour interdire toute autre adresse
# --------------------------------------------------------------------------- #
_orig_send = tl.send_campaign_email


def _guarded_send(row):
    if common.norm_email(row["email"]) != TEST_RECIPIENT:
        raise RuntimeError(
            f"GARDE-FOU déclenché : envoi bloqué vers {row['email']!r} "
            f"(seul {TEST_RECIPIENT} est autorisé en test)."
        )
    return _orig_send(row)


tl.send_campaign_email = _guarded_send


def _push(text: str) -> int:
    """Pousse un message Telegram et renvoie son message_id (pour les handlers
    qui éditent le message)."""
    resp = common.tg_send(os.environ["TELEGRAM_CHAT_ID"], text)
    return resp["result"]["message_id"]


def _temp_pending(conn, token: str, subject: str, body: str, angle: str = "test") -> None:
    common.add_pending(conn, token, TEST_RECIPIENT, "Tiago (test)", subject, body, angle)


# --------------------------------------------------------------------------- #
def facet_env() -> None:
    required = [
        "CLAUDE_CODE_OAUTH_TOKEN", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID",
        "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY",
        "MAILGUN_API_KEY", "MAILGUN_DOMAIN", "MAILGUN_BASE_URL", "MAILGUN_FROM",
    ]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        record("Environnement (.env)", False, "manquant: " + ", ".join(missing))
    else:
        record("Environnement (.env)", True, f"{len(required)} variables présentes")


def facet_footer() -> None:
    body = "Corps de test."
    once = common.ensure_footer(body)
    twice = common.ensure_footer(once)
    ok = (common.UNSUB_SENTINEL in once) and (once == twice)
    h = common.list_unsubscribe_header(os.environ.get("MAILGUN_REPLY_TO", common.CONTACT_EMAIL))
    record("Pied conformité + List-Unsubscribe", ok, f"idempotent={once == twice} ; header={h}")


def facet_db() -> None:
    conn = common.db_connect()
    common.db_init(conn)
    common.add_pending(conn, "tk", "a@b.com", "A", "Obj", "Corps", "ang")
    ok = common.has_pending_for(conn, "A@B.com")
    common.mark_contacted(conn, "a@b.com", "mg-1")
    ok = ok and common.is_contacted(conn, "A@B.COM")
    common.add_suppressed(conn, "c@d.com", "manual")
    ok = ok and common.is_suppressed(conn, "c@d.com")
    common.delete_pending(conn, "tk")
    ok = ok and not common.has_pending_for(conn, "a@b.com")
    conn.close()
    record("Machine d'état SQLite (contacted/pending/suppressed)", ok,
           "insensible à la casse, CRUD OK")


def facet_redaction() -> tuple[str, str, str]:
    """Renvoie (subject, body_with_footer, angle). Utilise la sortie du SDK si
    disponible, sinon un corps statique."""
    if REDACTION_OUT.exists():
        try:
            r = json.loads(REDACTION_OUT.read_text(encoding="utf-8"))
            subject = (r.get("subject") or STATIC_SUBJECT).strip()
            raw_body = (r.get("body") or STATIC_BODY).strip()
            angle = (r.get("angle") or "").strip() or "(rédigé par le SDK)"
            record("Rédaction (claude-agent-sdk)", True,
                   f"objet « {subject[:60]} »")
            return subject, common.ensure_footer(raw_body), angle
        except Exception as exc:
            record("Rédaction (claude-agent-sdk)", False, f"sortie illisible: {exc}")
    else:
        record("Rédaction (claude-agent-sdk)", None,
               "binaire claude/SDK indisponible ici — corps de test statique utilisé")
    return STATIC_SUBJECT, common.ensure_footer(STATIC_BODY), "angle de test (statique)"


def facet_telegram_notify(subject: str, body: str, angle: str) -> None:
    """Pousse une vraie notif avec les 4 boutons (démonstration visuelle)."""
    try:
        pc.notify(
            os.environ["TELEGRAM_CHAT_ID"],
            {"email": TEST_RECIPIENT, "name": "Tiago (test)"},
            subject, body, angle, token="demo-token-non-actif",
        )
        record("Notification Telegram (4 boutons)", True, "envoyée sur le chat autorisé")
    except Exception as exc:
        record("Notification Telegram (4 boutons)", False, str(exc))


def facet_handler_skip(conn) -> None:
    mid = _push("TEST handler SKIP — ce message va être édité en « 🗑 Ignoré ».")
    _temp_pending(conn, "tok_skip", "obj skip", "corps skip")
    tl.handle_skip(conn, os.environ["TELEGRAM_CHAT_ID"], mid, "tok_skip")
    ok = not common.has_pending_for(conn, TEST_RECIPIENT) and not common.is_contacted(conn, TEST_RECIPIENT)
    record("Handler SKIP (aucun envoi)", ok, "pending supprimé, pas de contact")


def facet_handler_block(conn) -> None:
    mid = _push("TEST handler BLOCK — ce message va être édité en « 🚫 ».")
    _temp_pending(conn, "tok_block", "obj block", "corps block")
    blk_email = "bloque-moi@example.com"
    # On teste l'effet suppressed sur une adresse jetable (pas le test recipient),
    # via un pending dédié.
    common.add_pending(conn, "tok_block2", blk_email, "X", "o", "c", "a")
    tl.handle_block(conn, os.environ["TELEGRAM_CHAT_ID"], mid, "tok_block2")
    ok = common.is_suppressed(conn, blk_email) and not common.has_pending_for(conn, blk_email)
    # nettoyage du pending de démonstration
    common.delete_pending(conn, "tok_block")
    record("Handler BLOCK (suppressed, aucun envoi)", ok,
           f"{blk_email} ajouté à suppressed")


def facet_handler_send(conn, subject: str, body: str) -> None:
    """Envoi RÉEL via le handler send (email #1 vers ftiago125)."""
    mid = _push("TEST handler SEND — envoi réel en cours vers " + TEST_RECIPIENT)
    _temp_pending(conn, "tok_send", subject, body, "angle send")
    try:
        tl.handle_send(conn, os.environ["TELEGRAM_CHAT_ID"], mid, "tok_send")
        ok = common.is_contacted(conn, TEST_RECIPIENT) and not common.has_pending_for(conn, TEST_RECIPIENT)
        record("Envoi RÉEL Mailgun via handler SEND", ok,
               f"email #1 -> {TEST_RECIPIENT}, marqué contacted")
    except Exception as exc:
        record("Envoi RÉEL Mailgun via handler SEND", False, str(exc))


def facet_handler_edit(conn, subject: str) -> None:
    """Envoi RÉEL via le mode édition (email #2, corps édité)."""
    _temp_pending(conn, "tok_edit", subject, "corps initial (sera remplacé)", "angle edit")
    tl.write_edit_slot("tok_edit", os.environ["TELEGRAM_CHAT_ID"])
    edited = (
        "Bonjour Tiago,\n\nCeci est le TEST du mode ÉDITION : ce corps a été "
        "fourni à la main et envoyé tel quel (le pied de désinscription est "
        "ajouté automatiquement)."
    )
    try:
        tl.handle_edited_text(conn, os.environ["TELEGRAM_CHAT_ID"], edited)
        ok = common.is_contacted(conn, TEST_RECIPIENT)
        record("Envoi RÉEL Mailgun via mode ÉDITION", ok,
               f"email #2 (corps édité) -> {TEST_RECIPIENT}")
    except Exception as exc:
        record("Envoi RÉEL Mailgun via mode ÉDITION", False, str(exc))
    finally:
        tl.clear_edit_slot()


def facet_targeting() -> None:
    """Ciblage Supabase en LECTURE SEULE. Compte seulement ; n'envoie/prépare
    rien pour les vrais prospects (Règle d'Or #4)."""
    try:
        import supabase  # noqa: F401
    except Exception:
        record("Ciblage Supabase (lecture seule)", None, "paquet supabase indisponible")
        return
    try:
        sb = common.supabase_client()
        # Base temporaire vide -> reflète le vivier éligible complet.
        tmp = common.db_connect()
        common.db_init(tmp)
        contacts = common.fetch_newsletter_contacts(sb)
        kernel = common.fetch_kernel_client_emails(sb)
        eligible = pc.select_prospects(sb, tmp, batch_size=10**9)
        tmp.close()
        sample = ", ".join(common.norm_email(e["email"])[:3] + "***" for e in eligible[:3])
        record("Ciblage Supabase (lecture seule)", True,
               f"{len(contacts)} opt-in, {len(kernel)} clients KERNEL exclus, "
               f"{len(eligible)} éligibles (échantillon masqué: {sample})")
    except Exception as exc:
        record("Ciblage Supabase (lecture seule)", False,
               f"{type(exc).__name__}: {exc}")


def main() -> None:
    common.load_env()
    # >>> Base d'état TEMPORAIRE : la vraie state.db n'est pas touchée. <<<
    common.DB_PATH = Path(tempfile.mkdtemp(prefix="fq_test_")) / "test_state.db"
    print(f"(base d'état de test : {common.DB_PATH})\n")

    facet_env()
    facet_footer()
    facet_db()

    subject, body, angle = facet_redaction()
    facet_telegram_notify(subject, body, angle)

    conn = common.db_connect()
    common.db_init(conn)
    facet_handler_skip(conn)
    facet_handler_block(conn)
    facet_handler_send(conn, subject, body)
    facet_handler_edit(conn, subject)
    conn.close()

    facet_targeting()

    # Message de clôture sur Telegram + synthèse.
    n_ok = sum(1 for _, s, _ in results if s == "OK")
    n_skip = sum(1 for _, s, _ in results if s == "SKIP")
    n_fail = sum(1 for _, s, _ in results if s == "ÉCHEC")
    try:
        common.tg_send(
            os.environ["TELEGRAM_CHAT_ID"],
            f"🧪 Test système terminé : {n_ok} OK, {n_skip} ignorés, {n_fail} échecs. "
            f"Tu devrais avoir reçu 2 mails de test à {TEST_RECIPIENT}.",
        )
    except Exception:
        pass

    print("\n================ SYNTHÈSE ================")
    for facet, status, detail in results:
        print(f"  {status:5} | {facet}")
    print(f"\n  {n_ok} OK · {n_skip} SKIP · {n_fail} ÉCHEC")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(2)
