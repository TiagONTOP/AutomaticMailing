"""Sonde de rédaction (test isolé). Lance write_mail() via claude-agent-sdk sur un
prospect SYNTHÉTIQUE et écrit le résultat dans _redaction_out.json.

Isolé dans son propre process pour borner le risque de blocage du SDK (binaire
`claude`). N'envoie AUCUN mail, n'écrit PAS dans Supabase. Lancé par le harnais.
"""

import json
import sys

import common
import prepare_campaign as pc

OUT = common.BASE_DIR / "_redaction_out.json"

# Prospect de test (jamais un vrai prospect ; sert uniquement à la rédaction).
PROSPECT = {"email": "ftiago125@gmail.com", "name": "Tiago (test)"}


def main() -> int:
    common.load_env()
    try:
        common.require_env("CLAUDE_CODE_OAUTH_TOKEN")
    except RuntimeError as exc:
        print(f"SKIP: {exc}")
        return 2

    context = pc.build_context()
    try:
        result = pc.write_mail(context, PROSPECT)
    except Exception as exc:  # binaire claude manquant, auth, quota…
        print(f"ÉCHEC rédaction: {type(exc).__name__}: {exc}")
        return 1

    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("OK rédaction. Aperçu :")
    print("  Objet :", (result.get("subject") or "")[:120])
    print("  Angle :", (result.get("angle") or "")[:120])
    print("  Corps :", (result.get("body") or "")[:200].replace("\n", " ⏎ "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
