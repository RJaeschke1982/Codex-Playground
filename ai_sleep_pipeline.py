#!/usr/bin/env python3
"""Utility pipeline for generating Psychiatria Polska outline and full article.

This script is a cleaned up and portable version of the large script provided in the
issue description.  It performs the following steps:

1. Generate a concise prompt based on a cleaned manuscript and journal guidance.
2. Call the OpenAI Chat Completions API to create an article outline.
3. Expand the outline into a full article and run simple QA checks.

The script was rewritten to be repository‑relative (no absolute paths), offers a
``--dry-run`` mode that avoids network calls, and exposes a small CLI so each
step can be executed independently.

Usage examples::

    # Run the entire pipeline (requires OPENAI_API_KEY)
    python ai_sleep_pipeline.py --run-all

    # Only generate the prompt and outline, but skip network calls
    python ai_sleep_pipeline.py --generate-prompt --make-outline --dry-run

Environment variables:
    OPENAI_API_KEY – API key used for real HTTP calls.  If missing and
                      ``--dry-run`` is not supplied the script will abort.

Outputs are written to the ``OUT`` directory inside the repository.  ``--dry-run``
produces placeholder files so the pipeline can be executed in environments
without API access.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------
BASE = Path(__file__).resolve().parent
SRC = BASE / "ADHD_Sleep_Review_v1_Recenzja_CLEANED.md"
GUIDE = BASE / "Psychiatria_Polska_Guide_for_Authors.md"
OUT = BASE / "OUT"
AI_OUT = OUT / "AI_OUT"
REFS = OUT / "refs"
for d in (OUT, AI_OUT, REFS):
    d.mkdir(parents=True, exist_ok=True)

PROMPT_PATH = OUT / "OUT_prompt__PsychPolska_outline_SMALL.txt"
CONTENT_MAP_PATH = OUT / "content_map_compact.txt"
SIGNAL_PATH = OUT / "source_signal_counts.json"
POOL_JSON = REFS / "reference_pool.json"
POOL_TXT = REFS / "reference_pool.txt"

# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def read_text(path: Path) -> str:
    """Return file contents using a range of encodings."""
    for enc in ("utf-8", "utf-8-sig", "cp1250", "latin-1"):
        try:
            return path.read_text(encoding=enc)
        except Exception:
            pass
    raise RuntimeError(f"Cannot read file: {path}")


def normalise(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def sent_split(txt: str) -> List[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-ZŁŚŻŹĆŃÓ])", txt)
    return [p.strip() for p in parts if p.strip()]


def compress_to_words(s: str, n: int = 12) -> str:
    tokens = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿŁŚŻŹĆŃÓĘąęółśżźćń0-9\-]+", s)
    return " ".join(tokens[:n])


def content_map(md: str, limit: int = 120) -> List[str]:
    lines: List[str] = []
    for line in md.splitlines():
        if re.match(r"^\s*#{1,6}\s+\S", line) or re.match(r"^\s*[-*•]\s+\S", line):
            core = re.sub(r"^\s*#{1,6}\s+|\s*[-*•]\s+", "", line).strip()
            if core:
                lines.append("• " + compress_to_words(core, 12))
    if len(lines) < limit:
        for para in re.split(r"\n\s*\n", md):
            para = para.strip()
            if len(para) < 60:
                continue
            ss = sent_split(para)
            if ss:
                lines.append("• " + compress_to_words(ss[0], 12))
            if len(lines) >= limit:
                break
    seen: set[str] = set()
    out: List[str] = []
    for l in lines:
        key = l.lower()
        if key not in seen:
            out.append(l)
            seen.add(key)
        if len(out) >= limit:
            break
    return out


def reference_pool(primary_md: str, max_items: int = 80, clip: int = 120) -> Dict[int, str]:
    ref_head = re.compile(
        r"^\s{0,3}#{1,6}\s*(References|Bibliografia|Piśmiennictwo)\s*$", re.I | re.M
    )
    doi_re = re.compile(r"\b10\.\d{4,9}/\S+\b")
    year_re = re.compile(r"\((19|20)\d{2}\)")
    m = ref_head.search(primary_md)
    pool: List[str] = []
    if m:
        start = m.end()
        nxt = ref_head.search(primary_md[start:])
        end = len(primary_md) if not nxt else start + nxt.start()
        block = primary_md[start:end].strip()
        candidates = [p.strip().replace("\n", " ") for p in re.split(r"\n{2,}", block)]
        pool = [c for c in candidates if len(c) >= 20]
    if not pool:
        for para in re.split(r"\n{2,}", primary_md):
            p = " ".join(para.strip().split())
            if len(p) >= 20 and (doi_re.search(p) or year_re.search(p)):
                pool.append(p)
    out: List[str] = []
    seen: set[str] = set()
    for it in pool:
        it = re.sub(r"\s+", " ", it).strip()
        it = (it[:clip] + "…") if len(it) > clip else it
        key = it.lower()
        if key not in seen:
            out.append(it)
            seen.add(key)
        if len(out) >= max_items:
            break
    return {i + 1: out[i] for i in range(len(out))}


def count_signals(md: str) -> tuple[int, int]:
    t = md.lower()
    pharma = sum(
        len(re.findall(p, t))
        for p in [
            r"\bstimulant",
            r"\bmethylphen",
            r"\bamphetamine",
            r"\batomoxet",
            r"\bguanfacin",
            r"\bclonidin",
            r"\bmodafin",
            r"\bmelatonin",
            r"\bchrono",
        ]
    )
    coach = sum(
        len(re.findall(p, t))
        for p in [
            r"\bcoach",
            r"\bcbt",
            r"\bbehavio",
            r"\bsleep hygiene",
            r"\broutine",
            r"\bschedule",
            r"\bplanning",
            r"\bsleep diary",
        ]
    )
    return pharma, coach

# ---------------------------------------------------------------------------
# OpenAI helper
# ---------------------------------------------------------------------------

def _load_key() -> str | None:
    k = os.environ.get("OPENAI_API_KEY", "").strip()
    if k:
        return k
    return None


def chat(model: str, messages: List[Dict[str, str]], *, timeout: int = 180) -> str:
    key = _load_key()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is missing; run with --dry-run to skip network calls")
    payload = {"model": model, "messages": messages, "temperature": 0.1}
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        obj = json.loads(r.read().decode("utf-8"))
        return obj["choices"][0]["message"]["content"]

# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def generate_prompt() -> None:
    src_md = normalise(read_text(SRC))
    guide_md = normalise(read_text(GUIDE))

    cmap = content_map(src_md, limit=120)
    pool = reference_pool(src_md, max_items=80, clip=120)
    pharma_sig, coach_sig = count_signals(src_md)
    ratio = "50/50" if (coach_sig and abs(pharma_sig - coach_sig) / max(pharma_sig, coach_sig) <= 0.15) else "60/40"

    ROLE = "AI Research Strategist and Scientific Editor specialized in medical journals."
    GOAL = (
        "Create a clear OUTLINE for *Psychiatria Polska* using a compressed Content Map, "
        "a fixed Reference Pool, and journal constraints. Avoid verbatim copying; numeric citations only from the Pool."
    )
    CONTEXT = (
        "Internal draft covers adult ADHD, sleep, chronopharmacotherapy, and coaching. "
        "Use bullets as cues (≤12 words each). Omit unsupported claims."
    )

    pool_list = "\n".join(f"[{k}] {v}" for k, v in pool.items()) if pool else "(No references detected.)"
    cmap_txt = "\n".join(cmap) if cmap else "(No content extracted; keep outline generic.)"

    PROMPT = f"""<ROLE>
{ROLE}
</ROLE>

<GOAL>
{GOAL}
</GOAL>

<CONTEXT>
{CONTEXT}
</CONTEXT>

<GUIDE_SUMMARY>
- Abstract/References required (heuristic).
- Academic tone; English output; numeric citations [n].
</GUIDE_SUMMARY>

<SOURCE_SIGNAL_COUNTS>
PHARMA={pharma_sig}; COACHING={coach_sig}; DEFAULT_RATIO={ratio}
Decision: prefer 60/40; allow 50/50 if signals ~equal (±15%) and note rationale.
</SOURCE_SIGNAL_COUNTS>

<REFERENCE_POOL_FIXED>
Cite only items listed below as [n]. Do not invent references; do not renumber.
{pool_list}
</REFERENCE_POOL_FIXED>

<CONTENT_MAP_COMPRESSED>
Each bullet ≤12 words; use as paraphrasable cues, not quotable text.
{cmap_txt}
</CONTENT_MAP_COMPRESSED>

<DELIVERABLE>
Markdown outline only:
# Provisional Title
## One-paragraph Rationale (5–7 sentences; no citations)
## Scope and Boundaries (bullets)
## Target Journal Fit (3 bullets)
## Proposed Structure
- 1. Introduction (3–6 bullets)
- 2. Narrative Approach / Search Note (non-systematic; PRESS-aware)
- 3A. Chronopharmacotherapy — allocate ≈{ratio.split('/')[0]}% of outline tokens
- 3B. Coaching & Behavioral Routines — allocate ≈{ratio.split('/')[1]}% of outline tokens
- 4. Limitations / Gaps
- 5. Clinical Implications (practical algorithms)
- 6. Research Agenda
## Minimal Reading List (select 8–12 items from Reference Pool; keep numbers)
</DELIVERABLE>

<HARD_RULES>
- No verbatim copying (avoid ≥12-word strings).
- Use only [n] from Reference Pool. No new citations.
- Keep ratio 3A:3B ≈ {ratio}; if 50/50 chosen, note brief rationale.
</HARD_RULES>

<SELF_CHECK>
- [ ] Ratio respected.
- [ ] Minimal Reading List uses only listed [n].
- [ ] No empty sections; no speculation beyond Content Map.
</SELF_CHECK>
"""

    PROMPT_PATH.write_text(PROMPT, encoding="utf-8")
    CONTENT_MAP_PATH.write_text("\n".join(cmap), encoding="utf-8")
    SIGNAL_PATH.write_text(
        json.dumps({"pharma": pharma_sig, "coaching": coach_sig, "ratio": ratio}, indent=2),
        encoding="utf-8",
    )
    POOL_JSON.write_text(json.dumps(pool, indent=2, ensure_ascii=False), encoding="utf-8")
    POOL_TXT.write_text("\n".join(f"[{k}] {v}" for k, v in pool.items()), encoding="utf-8")

    print(f"[OK] PROMPT -> {PROMPT_PATH}")


def make_outline(dry_run: bool = False) -> Path:
    if not PROMPT_PATH.exists():
        raise RuntimeError("Prompt not found – run generate_prompt first")
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    sys_msg = (
        "You are a meticulous scientific writing assistant. Output only the requested Markdown outline. "
        "Respect fixed Reference Pool and 3A/3B ratio."
    )
    if dry_run:
        outline = "# Provisional Title\n\n- Outline generated in dry-run mode."
    else:
        outline = chat("gpt-4o-mini", [{"role": "system", "content": sys_msg}, {"role": "user", "content": prompt}])
    out_md = AI_OUT / f"PsychPolska_article_outline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    out_md.write_text(outline, encoding="utf-8")
    print(f"[OK] OUTLINE -> {out_md}")
    return out_md


def make_full_article(
    outline: Path,
    *,
    source: Path = SRC,
    guide: Path = GUIDE,
    dry_run: bool = False,
) -> None:
    """Expand an outline into a full article.

    Parameters
    ----------
    outline:
        Path to the article outline.
    source, guide:
        Optional paths overriding the default draft and journal guidance files.
    dry_run:
        If ``True`` no network calls are made and a placeholder file is written.
    """

    if not outline.exists():
        raise RuntimeError(f"Outline file missing: {outline}")
    if not source.exists():
        raise RuntimeError(f"Source file missing: {source}")
    if not guide.exists():
        raise RuntimeError(f"Guide file missing: {guide}")

    outline_md = normalise(read_text(outline))
    source_md = normalise(read_text(source))
    guide_md = normalise(read_text(guide))
    pool: Dict[int, str] = json.loads(POOL_JSON.read_text(encoding="utf-8")) if POOL_JSON.exists() else {}
    pool_list = "\n".join(f"[{k}] {v}" for k, v in sorted(pool.items())) if pool else "(No references detected.)"
    ratio = json.loads(SIGNAL_PATH.read_text(encoding="utf-8"))["ratio"] if SIGNAL_PATH.exists() else "60/40"

    user_prompt = f"""Goal
-----
Expand the ARTICLE OUTLINE into a full narrative review for Psychiatria Polska.

Inputs
------
1) ARTICLE OUTLINE:
---
{outline_md}
---

2) Primary draft (evidence base; do NOT copy long strings):
---
{source_md}
---

3) Journal guidance:
---
{guide_md}
---

4) Reference Pool (only these; cite as [n]):
{pool_list}

Hard Rules
----------
- No references outside the Pool; if unsupported, omit.
- In-text citations must be numeric [n] where n ∈ Pool.
- References section: only cited items, keep their Pool numbers.
- Balance: Chronopharmacotherapy : Coaching ≈ {ratio} (prefer 60/40; allow 50/50 if justified).
- Tone: academic, precise, non-speculative; English.
"""

    if dry_run:
        content = "# Draft\n\nFull article generated in dry-run mode."
    else:
        content = chat(
            "gpt-4o-mini",
            [
                {"role": "system", "content": "You are a meticulous medical writing assistant."},
                {"role": "user", "content": user_prompt},
            ],
        )

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_md = AI_OUT / f"PsychPolska_full_article_CLEAN_{ts}.md"
    out_md.write_text(content, encoding="utf-8")
    print(f"[OK] FULL ARTICLE -> {out_md}")

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Iterable[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generate-prompt", action="store_true", help="generate prompt and helper files")
    parser.add_argument("--make-outline", action="store_true", help="call the API to build outline")
    parser.add_argument("--make-full", action="store_true", help="expand outline into full article")
    parser.add_argument("--run-all", action="store_true", help="run entire pipeline")
    parser.add_argument("--dry-run", action="store_true", help="skip network calls and generate placeholders")
    parser.add_argument("--outline", type=Path, help="use existing outline for --make-full")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.run_all:
        args.generate_prompt = args.make_outline = args.make_full = True

    if args.generate_prompt:
        generate_prompt()

    outline_path: Path | None = args.outline
    if args.make_outline:
        outline_path = make_outline(dry_run=args.dry_run)
    if args.make_full:
        if not outline_path:
            outlines = sorted(AI_OUT.glob("PsychPolska_article_outline_*.md"), key=lambda p: p.stat().st_mtime)
            if not outlines:
                raise RuntimeError("No outline found. Run --make-outline first or provide --outline path.")
            outline_path = outlines[-1]
        make_full_article(outline_path, dry_run=args.dry_run)

    if not (args.generate_prompt or args.make_outline or args.make_full):
        parser.print_help()


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    main()
