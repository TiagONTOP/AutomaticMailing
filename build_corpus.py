"""Utilitaire de construction du corpus distillé (texte uniquement).

Exporte la prose markdown des notebooks `_release` (jupyter nbconvert --to
markdown) vers `corpus/notebooks/`, et copie les scripts YouTube vers
`corpus/scripts/`. **Exclut tout média** (on ne déploie que du texte sur le VPS).

Usage :
    python build_corpus.py [--scripts-root DIR] [--notebooks-root DIR]

Par défaut, les racines pointent vers l'arborescence locale FrenchQuant décrite
dans CLAUDE.md (à adapter selon ta machine). Ce script tourne en LOCAL ; ensuite
on `rsync` le dossier corpus/ (texte) vers le VPS.

Ce script ne touche ni à Supabase, ni à Mailgun, ni à Telegram : c'est un pur
utilitaire de préparation de contenu.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import common

logger = common.get_logger("build_corpus")

CORPUS_DIR = common.CORPUS_DIR
SCRIPTS_OUT = CORPUS_DIR / "scripts"
NOTEBOOKS_OUT = CORPUS_DIR / "notebooks"

# Racines par défaut (cf. CLAUDE.md > Corpus). À adapter via les arguments CLI.
DEFAULT_SCRIPTS_ROOT = common.BASE_DIR.parent.parent / "Vidéos"
DEFAULT_NOTEBOOKS_ROOT = common.BASE_DIR.parent / "frenchquant-lab"

# Extensions de scripts texte à copier (jamais de média).
SCRIPT_GLOBS = ("script*.md", "script*.txt")


def copy_scripts(scripts_root: Path) -> int:
    """Copie les fichiers de script (markdown/txt) vers corpus/scripts/."""
    if not scripts_root.exists():
        logger.warning("Racine scripts introuvable : %s (ignoré).", scripts_root)
        return 0
    SCRIPTS_OUT.mkdir(parents=True, exist_ok=True)
    count = 0
    for pattern in SCRIPT_GLOBS:
        for src in scripts_root.rglob(pattern):
            # Nom de sortie = dossier parent + nom, pour éviter les collisions.
            safe = f"{src.parent.name}__{src.name}".replace(" ", "_")
            if not safe.endswith(".md"):
                safe += ".md"
            dst = SCRIPTS_OUT / safe
            try:
                shutil.copyfile(src, dst)
                count += 1
            except OSError:
                logger.warning("Copie échouée : %s", src)
    logger.info("%d script(s) copié(s) vers %s.", count, SCRIPTS_OUT)
    return count


def export_notebooks(notebooks_root: Path) -> int:
    """Exporte les cellules markdown des notebooks `_release/*.ipynb` en .md via
    jupyter nbconvert. N'utilise QUE les `_release/` (pas `_work`/`_scratch`),
    cf. CLAUDE.md. Exclut tout média (nbconvert --to markdown ne sort que texte
    + références d'images, qu'on ne déploie pas)."""
    if not notebooks_root.exists():
        logger.warning("Racine notebooks introuvable : %s (ignoré).", notebooks_root)
        return 0
    NOTEBOOKS_OUT.mkdir(parents=True, exist_ok=True)
    count = 0
    for nb in notebooks_root.rglob("_release/*.ipynb"):
        out_name = f"{nb.parent.parent.name}__{nb.stem}.md".replace(" ", "_")
        out_path = NOTEBOOKS_OUT / out_name
        try:
            # --to markdown : prose + code, sans média lourd. --stdout pour
            # contrôler le nom de sortie nous-mêmes.
            result = subprocess.run(
                [
                    sys.executable, "-m", "jupyter", "nbconvert",
                    "--to", "markdown", "--stdout", str(nb),
                ],
                capture_output=True, text=True, check=True,
            )
            out_path.write_text(result.stdout, encoding="utf-8")
            count += 1
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            logger.warning("nbconvert a échoué pour %s : %s", nb, exc)
    logger.info("%d notebook(s) exporté(s) vers %s.", count, NOTEBOOKS_OUT)
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Construit le corpus distillé (texte).")
    parser.add_argument("--scripts-root", type=Path, default=DEFAULT_SCRIPTS_ROOT)
    parser.add_argument("--notebooks-root", type=Path, default=DEFAULT_NOTEBOOKS_ROOT)
    args = parser.parse_args()

    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    copy_scripts(args.scripts_root)
    export_notebooks(args.notebooks_root)
    logger.info(
        "Corpus construit. Pense à `rsync` corpus/ (texte uniquement) vers le VPS."
    )


if __name__ == "__main__":
    main()
