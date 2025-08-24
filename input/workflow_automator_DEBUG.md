```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
workflow_automator.py

Cel:
  1) Ekstrakcja tematyczna z manuskryptu i zbudowanie szkicu artykułu:
     /Users/lidka/AI_SLEEP_ADHD/Psychiatria_Polska_DRAFT.md
  2) Walidowany handoff pełnego pakietu do Codex/GitHub (git add/commit/push po autoryzacji operatora).

Wymagania:
  - Python 3.9+
  - Standard library only: os, re, subprocess, datetime
  - Brak zewnętrznych bibliotek

UWAGA:
  Ten skrypt nie wykonuje niczego bez Twojej zgody w fazie handoff. Najpierw tworzy lokalne artefakty.
"""

import os
import re
import sys
import subprocess
from datetime import datetime
try:
    # Python 3.9+: strefa czasowa dla Warszawy (opcjonalnie)
    from zoneinfo import ZoneInfo  # type: ignore
    _TZ_WARSAW = ZoneInfo("Europe/Warsaw")
except ImportError:
    _TZ_WARSAW = None  # fallback: czas lokalny bez TZ

# --- Stałe ścieżki ---
BASE_DIR = "/Users/lidka/AI_SLEEP_ADHD"
SRC_MANUSCRIPT = os.path.join(BASE_DIR, "ADHD_Sleep_Review_v1_Recenzja_CLEANED.md")
SRC_GUIDE = os.path.join(BASE_DIR, "Psychiatria_Polska_Guide_for_Authors.md")
OUT_DRAFT = os.path.join(BASE_DIR, "Psychiatria_Polska_DRAFT.md")

# --- Konfiguracja ekstrakcji tematycznej (prosta, oparta na słowach kluczowych) ---
KEYWORDS = [
    # rdzeń kliniczny
    r"\bADHD\b", r"\badult\b", r"\badults\b", r"\bsleep\b", r"\binsomnia\b",
    r"\bcircadian\b", r"\bchrono(pharm|biology|therapy)\w*\b",
    r"\bstimulant(s)?\b", r"\bmethylphenidate\b", r"\bamphet\w*\b",
    r"\bmelatonin\w*\b", r"\bagomelatine\b", r"\bramelteon\b",
    r"\brebound\b", r"\bwear[- ]off\b", r"\bcoaching\b", r"\bbehavior(al)?\b",
    r"\bexecutive\b", r"\battention\b", r"\bhyperactivity\b",
    r"\bactigraphy\b", r"\bpolysomnograph\w*\b", r"\bEMA\b", r"\bchronotype\b",
    r"\bdose\b", r"\btiming\b", r"\bbedtime\b", r"\bwake\b", r"\bnapping?\b",
    r"\bcomorbidity\b", r"\bdepression\b", r"\banxiety\b",
]

SECTION_HEURISTICS = {
    "Introduction": [r"\bADHD\b", r"\bsleep\b", r"\binsomnia\b", r"\bcircadian\b"],
    "Chronopharmacology of Stimulants": [r"\bstimulant(s)?\b", r"\bmethylphenidate\b", r"\bamphet\w*\b", r"\brebound\b", r"\bwear[- ]off\b", r"\btiming\b"],
    "Melatoninergics and Sleep Modulation": [r"\bmelatonin\w*\b", r"\bagomelatine\b", r"\bramelteon\b", r"\bcircadian\b", r"\bchronotype\b"],
    "Behavioral Coaching Synergy": [r"\bcoaching\b", r"\bbehavior(al)?\b", r"\bexecutive\b"],
    "Methods / Narrative Approach": [r"\bactigraphy\b", r"\bpolysomnograph\w*\b", r"\bEMA\b"],
    "Clinical Implications and Algorithms": [r"\bdose\b", r"\btiming\b", r"\bbedtime\b", r"\bwake\b", r"\bnapping?\b"],
}

CHECKLIST_HINTS = [
    "abstract", "keywords", "word", "limit", "tables", "figures", "references",
    "vancouver", "harvard", "style", "ethics", "consent", "conflict of interest",
    "funding", "acknowledg", "ORCID", "plagiarism", "cover letter", "blind"
]


# ---------- Pomocnicze funkcje IO ----------

def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def write_text(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

def now_pl_str() -> str:
    dt = datetime.now(tz=_TZ_WARSAW) if _TZ_WARSAW else datetime.now()
    return dt.strftime("%Y-%m-%d %H:%M")


# ---------- Ekstrakcja tematyczna ----------

def split_paragraphs(md_text: str):
    # Dziel według pustych linii, zachowując proste akapity
    # Usuwa leading/trailing spaces i filtry puste
    blocks = re.split(r"\n\s*\n", md_text.strip())
    return [b.strip() for b in blocks if b.strip()]

def keyword_score(text: str, patterns) -> int:
    score = 0
    for pat in patterns:
        # flagi: case-insensitive, wieloliniowe
        score += len(re.findall(pat, text, flags=re.IGNORECASE | re.MULTILINE))
    return score

def guess_title(md_text: str) -> str:
    # Pierwszy nagłówek H1 lub H2 jako tytuł
    m = re.search(r"^\s*#{1,2}\s+(.+)$", md_text, flags=re.IGNORECASE | re.MULTILINE)
    if m:
        return m.group(1).strip()
    # fallback
    return "Draft for Psychiatria Polska: Adult ADHD, Sleep, and Chronopharmacology"

def route_paragraph(paragraph: str) -> str:
    # Przypisz akapit do sekcji na podstawie dopasowania heurystyk
    best_section = "Introduction"
    best_score = 0 # Inicjalizacja na 0 jest bezpieczniejsza
    for section, pats in SECTION_HEURISTICS.items():
        sc = keyword_score(paragraph, pats)
        if sc > best_score:
            best_score = sc
            best_section = section
    return best_section

def extract_checklist(guide_text: str, max_items: int = 12):
    lines = [ln.strip() for ln in guide_text.splitlines() if ln.strip()]
    scored = []
    for ln in lines:
        s = keyword_score(ln, [rf"{re.escape(h)}" for h in CHECKLIST_HINTS])
        if s > 0:
            scored.append((s, ln))
    # Unikalne i posortowane po wyniku malejąco, później po długości rosnąco
    unique = []
    seen = set()
    for s, ln in sorted(scored, key=lambda x: (-x[0], len(x[1]))):
        key = ln.lower()
        if key not in seen:
            seen.add(key)
            unique.append(ln)
        if len(unique) >= max_items:
            break
    return unique

def build_draft(src_text: str, guide_text: str) -> str:
    title = guess_title(src_text)
    paragraphs = split_paragraphs(src_text)

    # Oceń każdy akapit globalnym wynikiem słów kluczowych
    scored_pars = [(keyword_score(p, KEYWORDS), p) for p in paragraphs]
    # Zachowaj akapity istotne (prog lub Top-N)
    # Prog dynamiczny: co najmniej 2 dopasowania lub Top 40 akapitów
    filtered = [p for s, p in scored_pars if s >= 2]
    if not filtered:
        # jeśli zbyt restrykcyjne, weź top 20
        filtered = [p for _, p in sorted(scored_pars, key=lambda x: -x[0])[:20]]

    # Rozprowadź akapity do sekcji
    section_bins = {sec: [] for sec in SECTION_HEURISTICS.keys()}
    for p in filtered:
        section = route_paragraph(p)
        section_bins[section].append(p)

    # Checklista z wytycznych
    checklist = extract_checklist(guide_text)

    # Zbuduj szkic
    lines = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append("> Auto-generated draft for submission to Psychiatria Polska. This is a keyword-guided narrative draft; please refine for accuracy, flow, and journal-specific formatting before submission.")
    lines.append("")
    lines.append("## Abstract (150–250 words, to refine)")
    lines.append("Placeholder abstract. Summarize background (adult ADHD and bidirectional links with sleep), aims, narrative approach, key findings (chronopharmacology of stimulants; melatoninergics; behavioral coaching synergy), and practical implications for clinicians.")
    lines.append("")
    lines.append("## Keywords")
    lines.append("Adult ADHD; Sleep; Insomnia; Circadian; Chronopharmacology; Stimulants; Melatonin; Coaching")
    lines.append("")

    # Methods / Narrative Approach wyróżniona sekcja, nawet jeśli pusta
    methods_section = "Methods / Narrative Approach"
    lines.append(f"## {methods_section}")
    if section_bins.get(methods_section):
        lines.extend(section_bins[methods_section])
    else:
        lines.append("Narrative, problem-focused synthesis of recent clinical and mechanistic literature (2005–2025), with emphasis on timing-sensitive pharmacotherapy and behavioral routines supporting sleep.")
    lines.append("")

    # Pozostałe sekcje w logicznym porządku
    ordered_sections = [
        "Introduction",
        "Chronopharmacology of Stimulants",
        "Melatoninergics and Sleep Modulation",
        "Behavioral Coaching Synergy",
        "Clinical Implications and Algorithms",
    ]
    for sec in ordered_sections:
        lines.append(f"## {sec}")
        content = section_bins.get(sec, [])
        if content:
            lines.extend(content)
        else:
            lines.append("_Content to be refined based on manuscript paragraphs and figures/tables._")
        lines.append("")

    # Limitations i Future work
    lines.append("## Limitations")
    lines.append("Keyword-based extraction may over/under-include content; clinical nuance, contraindications, and dosing details require careful manual verification. This draft should be edited for coherence and compliance with the journal requirements.")
    lines.append("")
    lines.append("## Implications for Practice and Research")
    lines.append("Provide concise, actionable guidance for timing of stimulants relative to sleep, melatoninergic strategies, and integration with behavioral coaching; propose testable hypotheses and minimal monitoring sets (sleep diaries, actigraphy, EMA).")
    lines.append("")

    # Checklista z wytycznych czasopisma
    lines.append("## Submission Checklist (auto-extracted from Guide for Authors)")
    if checklist:
        for item in checklist:
            lines.append(f"- {item}")
    else:
        lines.append("- Verify abstract length, keywords, reference style, ethical statements, and file formatting per the Guide for Authors.")
    lines.append("")

    # References placeholder
    lines.append("## References")
    lines.append("- Format per journal style (e.g., Vancouver/Harvard as required). Ensure recent, relevant citations and correct DOIs.")
    lines.append("")

    return "\n".join(lines)


# ---------- Weryfikacja środowiska Git i handoff ----------

def check_git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False

def is_git_repo(path: str) -> bool:
    if not os.path.isdir(path):
        return False
    try:
        r = subprocess.run(["git", "-C", path, "rev-parse", "--is-inside-work-tree"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return r.stdout.strip() == "true"
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False

def run_cmd(cmd_list, cwd: str) -> bool:
    """Uruchamia komendę. Zwraca True w przypadku sukcesu, False w przeciwnym razie, drukując błąd."""
    try:
        r = subprocess.run(
            cmd_list,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE, # Przechwyć, aby uniknąć zaśmiecania konsoli
            stderr=subprocess.PIPE  # Przechwyć standardowy błąd
        )
        if r.returncode != 0:
            # Jeśli komenda się nie powiodła, wyświetl błąd z Git
            print(f"[ERROR] Polecenie `{' '.join(cmd_list)}` nie powiodło się.")
            if r.stderr:
                print(f" > Błąd Git: {r.stderr.strip()}")
            return False
        return True
    except Exception as e:
        print(f"[ERROR] Krytyczny błąd podczas uruchamiania polecenia: {' '.join(cmd_list)}\n{e}")
        return False


def main():
    # --- KROK 1-3: Ekstrakcja tematyczna i kompozycja szkicu ---
    if not os.path.isfile(SRC_MANUSCRIPT):
        print(f"[ERROR] Brak pliku wejściowego: {SRC_MANUSCRIPT}")
        sys.exit(1)
    if not os.path.isfile(SRC_GUIDE):
        print(f"[ERROR] Brak pliku wejściowego: {SRC_GUIDE}")
        sys.exit(1)

    try:
        src_text = read_text(SRC_MANUSCRIPT)
        guide_text = read_text(SRC_GUIDE)
        draft_md = build_draft(src_text, guide_text)
        write_text(OUT_DRAFT, draft_md)
        print(f"[OK] Zapisano szkic: {OUT_DRAFT}")
    except Exception as e:
        print(f"[ERROR] Ekstrakcja/kompozycja nie powiodła się: {e}")
        sys.exit(1)

    # --- KROK 4: Weryfikacja git i definicja pakietu ---
    if not check_git_available():
        print("[ERROR] Git nie jest dostępny w systemie (polecenie `git --version` nie powiodło się).")
        sys.exit(1)
    if not is_git_repo(BASE_DIR):
        print(f"[ERROR] Katalog nie jest repozytorium Git: {BASE_DIR}")
        sys.exit(1)

    files_to_commit = [OUT_DRAFT, SRC_MANUSCRIPT, SRC_GUIDE]
    missing = [p for p in files_to_commit if not os.path.isfile(p)]
    if missing:
        print("[ERROR] Brakujące pliki w pakiecie:")
        for m in missing:
            print(f" - {m}")
        sys.exit(1)

    timestamp = now_pl_str()
    commit_msg = f"Handoff: Draft i kontekst dla Psychiatrii Polskiej ({timestamp})"

    # --- KROK 5: Interakcja z Operatorem ---
    print("\n" + "-"*52)
    print("PLAN OPERACJI GIT - HANDOFF PAKIETU DO REPOZYTORIUM")
    print("-"*52)
    print(f"Skrypt zamierza wykonać następujące komendy w katalogu:\n > {BASE_DIR}\n")

    # Wyświetl DOKŁADNE komendy, które zostaną wykonane
    cmd_idx = 1
    for file_path in files_to_commit:
        rel_path = os.path.relpath(file_path, BASE_DIR)
        print(f"{cmd_idx}. git add {rel_path}")
        cmd_idx += 1
    
    print(f'{cmd_idx}. git commit -m "{commit_msg}"')
    cmd_idx += 1
    print(f"{cmd_idx}. git push")
    
    print("-"*52)
    resp = input("CZY AUTORYZUJESZ WYKONANIE POWYŻSZYCH OPERACJI? (t/n): ").strip().lower()

    # --- KROK 6: Egzekucja po autoryzacji ---
    if resp == "t":
        print("\n[INFO] Autoryzacja otrzymana. Rozpoczynam operacje Git...")
        for file_path in files_to_commit:
            if not run_cmd(["git", "add", file_path], cwd=BASE_DIR):
                print("[FATAL] Niepowodzenie podczas `git add`. Przerywam.")
                sys.exit(1)
        print("[INFO] Pliki dodane do przechowalni (staged).")

        if not run_cmd(["git", "commit", "-m", commit_msg], cwd=BASE_DIR):
            print("[FATAL] Niepowodzenie podczas `git commit`. Przerywam.")
            sys.exit(1)
        print("[INFO] Zmiany zatwierdzone (committed).")

        if not run_cmd(["git", "push"], cwd=BASE_DIR):
            print("[FATAL] Niepowodzenie podczas `git push`.")
            sys.exit(1)
        print("[INFO] Zmiany wysłane do zdalnego repozytorium (pushed).")
        
        print("\n[OK] Proces zakończony pomyślnie. Pakiet roboczy został wysłany.")
    else:
        print("\n[INFO] Operacja anulowana przez operatora. Artefakty zostały utworzone lokalnie, ale nie zostały wysłane do repozytorium.")


if __name__ == "__main__":
    main()
