#!/usr/bin/env python3
"""Expand a Psychiatria Polska outline into a full article.

This is a tiny convenience wrapper around :mod:`ai_sleep_pipeline`.  It
automatically locates the newest outline in ``OUT/AI_OUT`` unless
``--outline`` is given and exposes options to override the source draft or
journal guide.  ``--dry-run`` skips API calls and writes a placeholder
article so the workflow can be exercised offline.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure repository root is on ``sys.path`` so that ``ai_sleep_pipeline``
# can be imported regardless of the current working directory.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import ai_sleep_pipeline as pipeline


def _latest_outline() -> Path:
    outlines = sorted(pipeline.AI_OUT.glob("PsychPolska_article_outline_*.md"))
    if not outlines:
        raise SystemExit(
            "No outline found. Run ai_sleep_pipeline.py --make-outline first or provide --outline"
        )
    return outlines[-1]


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--outline", type=Path, help="Outline path; defaults to the newest one")
    p.add_argument("--source", type=Path, default=pipeline.SRC, help="Primary draft path")
    p.add_argument("--guide", type=Path, default=pipeline.GUIDE, help="Journal guidance path")
    p.add_argument("--dry-run", action="store_true", help="Skip API calls; write placeholder article")
    args = p.parse_args(argv)

    outline = args.outline or _latest_outline()
    pipeline.make_full_article(
        outline,
        source=args.source,
        guide=args.guide,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
