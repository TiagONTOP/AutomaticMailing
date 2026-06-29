"""Sync des suppressions Mailgun -> table locale `suppressed` (Règle d'Or #4).

Lit les listes de suppression Mailgun (désinscriptions 1-clic, plaintes spam,
bounces durs) et les recopie dans le SQLite local `suppressed` pour qu'aucune de
ces adresses ne soit jamais recontactée (opt-out sacré, et délivrabilité).

LECTURE SEULE côté Mailgun (uniquement des GET sur les listes de suppression) :
*** Ce module N'EST PAS un point d'envoi *** — il n'appelle jamais .../messages
(Règle d'Or #1). Il n'écrit QUE dans le SQLite local (jamais Supabase, Règle #3).

Complémentaire de la détection « STOP » faite par MailManager sur les réponses
entrantes : ici on capte ce qui transite côté Mailgun (plaintes, bounces,
désinscriptions 1-clic via l'en-tête List-Unsubscribe). Déclenché par un timer
systemd (sync-suppressions.timer).
"""

import requests

import common

logger = common.get_logger("sync_suppressions")

# Listes de suppression Mailgun -> raison stockée dans `suppressed`.
# (endpoint Mailgun, reason). Toutes en LECTURE SEULE (GET).
_SUPPRESSION_LISTS = [
    ("unsubscribes", "unsubscribe"),
    ("complaints", "complaint"),
    ("bounces", "bounce"),
]

_PAGE_LIMIT = 1000


def _iter_suppressions(list_name: str):
    """Itère les entrées d'une liste de suppression Mailgun (paginée).

    GET {MAILGUN_BASE_URL}/{domaine}/{list_name}. Auth basic ("api", clé). On suit
    paging.next jusqu'à épuisement. LECTURE SEULE (Règle d'Or #1 — aucun envoi)."""
    env = common.require_env("MAILGUN_API_KEY", "MAILGUN_DOMAIN", "MAILGUN_BASE_URL")
    base = env["MAILGUN_BASE_URL"].rstrip("/")
    url = f"{base}/{env['MAILGUN_DOMAIN']}/{list_name}"
    params: dict | None = {"limit": _PAGE_LIMIT}
    auth = ("api", env["MAILGUN_API_KEY"])
    while url:
        resp = requests.get(url, auth=auth, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", []) or []
        for item in items:
            yield item
        nxt = (data.get("paging") or {}).get("next")
        # Mailgun renvoie toujours un paging.next ; on s'arrête dès qu'une page
        # est vide ou que l'URL ne change plus (garde anti-boucle).
        if not items or not nxt or nxt == url:
            break
        url, params = nxt, None  # l'URL `next` porte déjà ses paramètres


def main() -> None:
    common.load_env()
    conn = common.db_connect()
    common.db_init(conn)

    added = 0
    for list_name, reason in _SUPPRESSION_LISTS:
        try:
            for item in _iter_suppressions(list_name):
                email = (item.get("address") or item.get("recipient") or "").strip()
                if not email or common.is_suppressed(conn, email):
                    continue
                common.add_suppressed(conn, email, reason=reason)
                added += 1
                logger.info("Suppression Mailgun -> suppressed : %s (%s)", email, reason)
        except requests.RequestException:
            # Une liste qui échoue (réseau, droits) ne bloque pas les autres.
            logger.exception("Lecture de la liste Mailgun '%s' échouée.", list_name)

    logger.info("Sync des suppressions terminé (%d nouvelle(s) entrée(s)).", added)
    conn.close()


if __name__ == "__main__":
    main()
