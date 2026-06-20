"""Test sûr des commandes /prospect, /prospects, /who (aucun réseau, aucun envoi).

On stub le modèle, Supabase et Mailgun. On vérifie :
- les garde-fous de /prospect (désinscrit / déjà contacté / déjà en attente /
  hors base opt-in) refusent AVANT toute rédaction ;
- /prospect sur un abonné valide crée un `pending` (modèle stubbé) sans envoyer ;
- /prospects N crée N `pending` sans envoyer ;
- /who liste sans rien préparer ;
- les fonctions d'envoi Mailgun ne sont JAMAIS appelées.

Lancer : PYTHONUTF8=1 python test_prospect_cmds.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("TELEGRAM_TOKEN", "TEST:TOKEN")
os.environ.setdefault("TELEGRAM_CHAT_ID", "42")
os.environ.setdefault("CAMPAIGN_BATCH_SIZE", "5")

import common  # noqa: E402

# Répertoire temporaire isolé (on ne touche jamais au state.db réel).
_TMP_DIR = Path(tempfile.mkdtemp())
_db_counter = [0]


def new_db():
    """Crée un fichier SQLite NEUF et isolé, puis renvoie une connexion dessus.
    Chaque scénario qui veut une base propre s'en sert (db_connect lit
    common.DB_PATH au moment de l'appel)."""
    _db_counter[0] += 1
    common.DB_PATH = _TMP_DIR / f"state_{_db_counter[0]}.db"
    conn = common.db_connect()
    common.db_init(conn)
    return conn


import prepare_campaign as pc  # noqa: E402
import telegram_listener as tl  # noqa: E402

CHAT = 42
SENT_MSGS: list[str] = []

# Base opt-in factice.
FIXED_CONTACTS = [
    {"email": "Alice@Example.com", "name": "Alice"},
    {"email": "bob@example.com", "name": "Bob"},
    {"email": "carol@example.com", "name": ""},
]

DRAFT_JSON = '{"subject": "Objet de test", "body": "Bonjour,\\n\\nun corps de test.", "angle": "douleur test"}'


def _boom_send_campaign(*a, **k):
    raise AssertionError("send_campaign_email NE DOIT PAS être appelé dans ce test")


def _boom_send_broadcast(*a, **k):
    raise AssertionError("send_broadcast_email NE DOIT PAS être appelé dans ce test")


def _capture_tg_send(chat_id, text, reply_markup=None):
    SENT_MSGS.append(text)
    return {"ok": True}


def _no_tg_api(*a, **k):
    raise AssertionError("Aucun appel réseau Telegram ne doit partir dans ce test")


def setup_stubs():
    # Modèle stubbé : pas d'appel SDK / claude.
    pc._query_model = lambda system, user_content: DRAFT_JSON
    pc.build_context = lambda: "CONTEXTE DE TEST"
    # Supabase stubbé (lecture seule simulée).
    common.supabase_client = lambda: object()
    common.fetch_newsletter_contacts = lambda client: list(FIXED_CONTACTS)
    common.fetch_kernel_client_emails = lambda client: set()
    # Telegram stubbé (capture).
    common.tg_send = _capture_tg_send
    common.tg_api = _no_tg_api
    # Mailgun : interdit.
    tl.send_campaign_email = _boom_send_campaign
    tl.send_broadcast_email = _boom_send_broadcast


def reset_msgs():
    SENT_MSGS.clear()


def last() -> str:
    return SENT_MSGS[-1] if SENT_MSGS else ""


def main() -> int:
    setup_stubs()
    conn = new_db()  # base partagée des scénarios 1-5 (on y empile l'état)
    failures = []

    def check(label, cond):
        status = "OK " if cond else "ÉCHEC"
        print(f"   [{status}] {label}")
        if not cond:
            failures.append(label)

    print("=== 1) /prospect sur un DÉSINSCRIT -> refus, aucun pending ===")
    common.add_suppressed(conn, "bob@example.com", reason="manual")
    reset_msgs()
    tl.handle_prospect_command(conn, CHAT, "bob@example.com")
    check("refus opt-out", "désinscrit" in last())
    check("aucun pending créé", not common.has_pending_for(conn, "bob@example.com"))

    print("=== 2) /prospect sur un DÉJÀ CONTACTÉ -> refus (single-touch) ===")
    common.mark_contacted(conn, "carol@example.com", "mg-1")
    reset_msgs()
    tl.handle_prospect_command(conn, CHAT, "carol@example.com")
    check("refus single-touch", "déjà été contacté" in last())

    print("=== 3) /prospect HORS base opt-in -> refus (opt-in only) ===")
    reset_msgs()
    tl.handle_prospect_command(conn, CHAT, "intrus@nowhere.io")
    check("refus hors opt-in", "pas dans la base opt-in" in last())
    check("aucun pending créé", not common.has_pending_for(conn, "intrus@nowhere.io"))

    print("=== 4) /prospect sur un ABONNÉ valide -> 1 pending, AUCUN envoi ===")
    reset_msgs()
    tl.handle_prospect_command(conn, CHAT, "Alice@Example.com insiste sur le code source")
    check("pending créé pour alice", common.has_pending_for(conn, "alice@example.com"))
    check("alice non contactée (aucun envoi)", not common.is_contacted(conn, "alice@example.com"))
    pend = common.fetch_pending_emails(conn)
    check("carte de validation poussée", any("Objet de test" in m for m in SENT_MSGS))

    print("=== 5) /prospect déjà EN ATTENTE -> refus doublon ===")
    reset_msgs()
    tl.handle_prospect_command(conn, CHAT, "alice@example.com")
    check("refus doublon pending", "déjà en attente" in last())

    print("=== 6) /prospects 3 -> prépare 3 éligibles, AUCUN envoi ===")
    # Base NEUVE et isolée : aucun exclu -> les 3 contacts opt-in sont éligibles.
    conn2 = new_db()
    reset_msgs()
    tl.handle_prospects_command(conn2, CHAT, "3")
    n_pending = len(common.fetch_pending_emails(conn2))
    check("3 pendings préparés", n_pending == 3)
    check("résumé final affiché", any("brouillon(s) prêt(s)" in m for m in SENT_MSGS))
    check("aucun contacté (aucun envoi)", len(common.fetch_contacted_emails(conn2)) == 0)

    print("=== 7) /who 5 -> aperçu lecture seule, aucun pending ===")
    conn3 = new_db()
    reset_msgs()
    tl.handle_who_command(conn3, CHAT, "5")
    check("aperçu affiché", any("éligible" in m for m in SENT_MSGS))
    check("aucun pending créé par /who", len(common.fetch_pending_emails(conn3)) == 0)

    print()
    if failures:
        print(f"❌ {len(failures)} contrôle(s) en échec : {failures}")
        return 1
    print("TOUS LES CONTROLES PASSENT — aucun envoi, aucun appel Mailgun, Supabase simulé en lecture seule.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
