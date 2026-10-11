#!/usr/bin/env python3
"""Check public product text against the AGENTS.md "Public wording" rules (NOW-2).

This is a simple, deterministic net, not a language parser. A clean run is not a wording review: the AGENTS.md
rules also cover meaning, paraphrases and text that this check does not read.

Targets: README.md, crates/haetae/README.md, crates/haetae/src/lib.rs, crates/haetae/Cargo.toml and the text
files under python/, sim/ and docs/. Never read: sim/vendor/, sim/brand/, other vendor and build output folders,
LICENSE, COPYING and NOTICE files, AGENTS.md, ROADMAP.md, CLAUDE.md and this check's own files.

Rules (matching is per line, so a phrase split across two lines is not found; the hyphens U+2010 and U+2011 count
as "-"):
  banned          a phrase from the AGENTS.md "Do not use" and "Do not claim" lists, or a Korean equivalent
  planned-name    maek, jangseung, amhaeng or Ataxia, which are planned names and not crates
  tamper-evident  "tamper-evident" (or 위변조 탐지, 변조 흔적, ...) without the condition
                  "before external anchoring, only against adversaries without host access" in the same paragraph
  scripted-label  a file that mentions a knife and a child (knife or 칼, child or 아이) on one line, or in Markdown
                  in one paragraph, does not contain "scripted adversarial proposal" or "스크립트된 적대적 제안"
  tagline         a tagline target does not contain the exact tagline
  allowlist       a malformed, duplicate or stale entry in tools/public_wording_allow.txt

A paragraph ends at a blank line and before a list item or a line that starts with an HTML tag. A list item starts
with "- ", "+ " or "1. " once comment, heading and quote marks are removed, and in Markdown also with "* ". In code,
a paragraph can still be a long block.

A banned or planned-name hit passes only when its exact line is in tools/public_wording_allow.txt: the path, a TAB
and the line's text without leading and trailing whitespace. That file lists reviewed negations and non-claims,
such as "Not a safety device." An entry that exempts no hit fails, so editing an exempted line or
deleting it also needs the entry changed, and the change gets reviewed again.

Output: one "path:line: rule: text" line per finding. Exit status 1 when there are findings, otherwise 0.
Usage: python tools/check_public_wording.py [ROOT]. Standard library only; Python 3.9 or later.
"""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWLIST = "tools/public_wording_allow.txt"
TAGLINE = ("A permit gate for AI-driven robots: actuators accept only exactly-authorized, fresh commands, "
           "and incidents leave a verifiable record. Pre-alpha, not safety-rated.")
TAGLINE_TARGETS = ("README.md", "crates/haetae/README.md", "crates/haetae/src/lib.rs", "crates/haetae/Cargo.toml",
                   "python/README.md", "python/haetae/__init__.py", "python/pyproject.toml", "sim/index.html")
TARGET_FILES = ("README.md", "crates/haetae/README.md", "crates/haetae/src/lib.rs", "crates/haetae/Cargo.toml")
TARGET_DIRS = ("python", "sim", "docs")
SKIP_PATHS = ("sim/brand", "sim/pkg", "sim/examples")  # brand assets (FROZEN) and git-ignored build copies
SKIP_DIR_NAMES = ("vendor", "node_modules", "__pycache__", "build", "dist", ".git")  # vendored code anywhere
SKIP_FILES = re.compile(r"(?:LICEN[CS]E|COPYING|NOTICE)(?:[-._].*)?|AGENTS\.md|ROADMAP\.md|CLAUDE\.md"
                        r"|check_public_wording(?:_test)?\.py|public_wording_allow\.txt")
TEXT_SUFFIXES = (".md", ".html", ".htm", ".js", ".mjs", ".cjs", ".css", ".py", ".toml", ".rs", ".sh", ".txt")

BANNED = re.compile("|".join((
    # AGENTS.md "Do not use"
    r"safety\s*(?:&amp;|&|and|/)\s*security\s+stack", r"safety\s+stack", r"force\s*/\s*workspace",
    r"tamper[-\s]?proof", r"compl(?:ian(?:ce|t)|ie[sd]|y)", r"haetae\s+signatures?",
    # AGENTS.md "Do not claim". "AI Act compliant" and "Machinery Regulation compliant" are covered above, where
    # compliance, compliant, comply, complies and complied all count.
    # "E-stop/STO" is one hit as a pair, and E-stop or STO alone also counts; the mode name `estop` does not.
    r"safety[-\s]devices?", r"e-?stop\s*(?:/|or)\s*sto(?![a-z])", r"\be-stop", r"\bsto(?![a-z])",
    r"protect(?:s|ing)?\s+people", r"collision[-\s]prevention", r"certification[-\s]ready",
    r"first\s+to\s+verify\s+signatures", r"M3\s+completes\s+real\s+controller\s+permits",
    r"detect(?:s|ed|ing)?\s+(?:six|6)\s+household\s+hazards", r"protects?\s+keys",
    r"safe\s+even\s+if\s+the\s+(?:authorizer|gate\s+MCU)", r"safe\s+under\s+policy-PC\s+root\s+compromise",
    # Korean equivalents, with or without a particle (충돌을 방지, 규정을 준수, 위변조가 불가능). 준수 counts only after
    # a rule word: 규정, 규제, 표준, 법, 법규, 법령 (AI법을 준수) or the English Act or Regulation (AI Act를 준수).
    r"안전\s*스택", r"안전\s*장치", r"사람(?:들)?(?:을)?\s*보호", r"충돌(?:을|이)?\s*방지", r"비상\s*정지\s*장치",
    r"(?:규정|규제|표준|법(?:규|령)?|\b(?:act|regulation))(?:을|를)?\s*준수", r"위?변조(?:를|가)?\s*(?:불가|방지)",
)), re.IGNORECASE)
PLANNED = re.compile(r"\b(?:maek|jangseung|amhaeng|ataxia)\b", re.IGNORECASE | re.ASCII)
TAMPER = re.compile(r"tamper[-\s]?eviden(?:t|ce)|위?변조(?:를|가|의|된)?\s*(?:탐지|감지|검출|흔적)", re.IGNORECASE)
HYPHENS = str.maketrans("\u2010\u2011", "--")  # the hyphen and the non-breaking hyphen look like "-" on screen
TAMPER_CONDITIONS = ("before external anchoring, only against adversaries without host access",)  # no Korean form yet
KNIFE = re.compile(r"knife|knives|칼", re.IGNORECASE)
CHILD = re.compile(r"child|아이", re.IGNORECASE)
SCENARIO_LABELS = ("scripted adversarial proposal", "스크립트된 적대적 제안")
MARKS = re.compile(r"^\s*(?://[/!]?|#+|\*|>)")  # comment, heading or quote marks at the start of a line
ITEM = re.compile(r"(?:[-+]|\d+\.)\s|<")  # once marks are removed: a list item or an HTML tag starts a paragraph


def targets(root):
    """Relative paths of the target files under root, in a fixed order."""
    found = [name for name in TARGET_FILES if (root / name).is_file()]
    for top in TARGET_DIRS:
        for dirpath, dirnames, filenames in os.walk(root / top):
            rel = Path(dirpath).relative_to(root).as_posix()
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIR_NAMES and not d.endswith(".egg-info")
                                 and f"{rel}/{d}" not in SKIP_PATHS)
            found += [f"{rel}/{name}" for name in sorted(filenames)
                      if name.endswith(TEXT_SUFFIXES) and not SKIP_FILES.fullmatch(name)]
    return found


def bare(line):
    return MARKS.sub("", line, count=1).strip()


def starts(line, prose):
    """Whether line starts a paragraph: a list item or an HTML tag, and in Markdown (prose) a "* " bullet."""
    return bool(ITEM.match(bare(line))) or (prose and line.lstrip().startswith("* "))


def paragraph(lines, i, prose):
    """The paragraph around lines[i] (see the module docstring), without marks, joined and one-spaced."""
    start, end = i, i + 1
    while start > 0 and bare(lines[start - 1]) and not starts(lines[start], prose):
        start -= 1
    while end < len(lines) and bare(lines[end]) and not starts(lines[end], prose):
        end += 1
    return " ".join(" ".join(bare(line) for line in lines[start:end]).split())


def load_allowlist(path):
    """({(path, line text): allowlist line number}, findings for malformed and duplicate entries)."""
    entries, findings = {}, []
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    for n, raw in enumerate(lines, 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        name, tab, text = raw.partition("\t")
        key = (name.strip(), text.strip())
        if not tab or not all(key):
            findings.append((ALLOWLIST, n, "allowlist", "malformed entry, expected <path><TAB><line text>"))
        elif key in entries:
            findings.append((ALLOWLIST, n, "allowlist", f"duplicate entry: {key[0]}: {key[1]}"))
        else:
            entries[key] = n
    return entries, findings


def check(root=ROOT):
    """All findings for the tree at root, as sorted (path, line, rule, text) tuples."""
    root = Path(root)
    allow, findings = load_allowlist(root / ALLOWLIST)
    used, texts = set(), {}
    for rel in targets(root):
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            findings.append((rel, 1, "unreadable", "not a readable UTF-8 text file"))
            continue
        texts[rel] = text
        lines = text.splitlines()
        prose = rel.endswith(".md")  # hard-wrapped, so a sentence can go on over the next lines of its paragraph
        for i, line in enumerate(lines):
            plain = line.translate(HYPHENS)  # for matching only; the allowlist key and the output keep the line
            hits = [f"banned ({m.group(0)})" for m in BANNED.finditer(plain)]
            hits += [f"planned-name ({m.group(0)})" for m in PLANNED.finditer(plain)]
            key = (rel, line.strip())
            if hits and key in allow:
                used.add(key)
            else:
                findings += [(rel, i + 1, hit, line.strip()) for hit in hits]
            if TAMPER.search(plain):
                nearby = paragraph(lines, i, prose).lower()
                if not any(condition.lower() in nearby for condition in TAMPER_CONDITIONS):
                    findings.append((rel, i + 1, "tamper-evident", line.strip()))
        mention = next((i for i, line in enumerate(lines)
                        if KNIFE.search(line) and CHILD.search(paragraph(lines, i, prose) if prose else line)), None)
        if mention is not None and not any(label in text.lower() for label in SCENARIO_LABELS):
            findings.append((rel, mention + 1, "scripted-label", lines[mention].strip()))
    for rel in TAGLINE_TARGETS:
        if TAGLINE not in texts.get(rel, ""):
            findings.append((rel, 1, "tagline", "the exact tagline is missing"))
    for key, n in allow.items():
        if key not in used:
            findings.append((ALLOWLIST, n, "allowlist", f"stale entry, it exempts no hit: {key[0]}: {key[1]}"))
    return sorted(findings)


def main(argv):
    root = Path(argv[0]) if argv else ROOT
    findings = check(root)
    for path, line, rule, text in findings:
        print(f"{path}:{line}: {rule}: {text}")
    sys.stdout.flush()
    print(f"public wording: {len(targets(root))} files read, {len(findings)} findings", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
