#!/usr/bin/env python3
"""Tests for tools/check_public_wording.py. CI runs this file as a script, so a missing or renamed file fails."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_public_wording as wording  # noqa: E402

CHECKER = Path(wording.__file__).resolve()
TAG = wording.TAGLINE
OLD_TAG = TAG.replace("incidents leave", "every decision leaves")
CONDITION = wording.TAMPER_CONDITIONS[0]
CLEAN = {  # the smallest tree that passes: every tagline target holds the exact tagline
    "README.md": f"# Haetae\n\n**{TAG}**\n",
    "crates/haetae/README.md": f"**{TAG}**\n",
    "crates/haetae/src/lib.rs": f"//! {TAG}\n//!\n//! More text.\n",
    "crates/haetae/Cargo.toml": f'[package]\ndescription = "{TAG}"\n',
    "python/README.md": f"**{TAG}**\n",
    "python/haetae/__init__.py": f'"""{TAG}\n"""\n',
    "python/pyproject.toml": f'[project]\ndescription = "{TAG}"\n',
    "sim/index.html": f'<meta name="description" content="{TAG}">\n',
}


class WordingTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for path, text in CLEAN.items():
            self.write(path, text)

    def write(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            target.write_bytes(text)
        else:
            target.write_text(text, encoding="utf-8")

    def allow(self, *entries):
        self.write(wording.ALLOWLIST, "# reviewed\n" + "".join(f"{path}\t{text}\n" for path, text in entries))

    def found(self):
        """(path, line, rule) for each finding; the rule without its "(term)" detail."""
        return [(path, line, rule.split(" ")[0]) for path, line, rule, _ in wording.check(self.root)]

    def test_clean_tree_passes(self):
        self.assertEqual(wording.check(self.root), [])

    def test_banned_phrases_fail_even_when_negated(self):
        lines = (
            "Haetae is a robot safety & security stack.", "a safety &amp; security stack",
            "a safety and security stack", "a robot safety/security stack", "the safety stack",
            "force/workspace limits", "a tamper-proof log", "AI Act compliant", "Machinery Regulation compliant",
            "regulatory compliance", "check the Haetae signature", "an E-stop/STO", "E-stop or STO",
            "Haetae protects people.", "collision prevention", "certification-ready", "a safety device",
            "Haetae is an E-stop.", "Haetae is an STO.", "해태는 STO입니다.",
            "the first to verify signatures at the actuator", "M3 completes real controller permits",
            "detects six household hazards", "Haetae detects 6 household hazards.", "detected six household hazards",
            "detecting six household hazards", "it protects keys", "Haetae is not a safety device.",
            "safe even if the authorizer or gate MCU is compromised", "safe even if the gate MCU is compromised",
            "safe under policy-PC root compromise",
            "해태는 안전 스택입니다.", "해태는 안전장치입니다.", "사람을 보호합니다.", "충돌 방지 기능",
            "비상 정지 장치", "규정 준수", "위변조 불가능한 기록", "E-stop/STO가 아닙니다.", "해태는 충돌을 방지합니다.",
            "EU 기계 규정을 준수합니다.", "위변조가 불가능한 기록", "해태는 사람 보호 장치입니다.",
            "Haetae complies with the EU AI Act.", "built to comply with the Machinery Regulation",
            "It complied with the AI Act.", "EU AI법을 준수합니다.", "인공지능법을 준수합니다.", "AI Act를 준수합니다.",
            "Machinery Regulation을 준수합니다.", "Haetae is an E\u2011stop.", "a tamper\u2010proof log",
        )
        for line in lines:
            with self.subTest(line=line):
                self.write("docs/page.md", f"Intro.\n{line}\n")
                self.assertEqual(self.found(), [("docs/page.md", 2, "banned")])

    def test_ordinary_text_passes(self):
        lines = ("Haetae judges each proposal.", "the estop mode", "safety-rated controllers", "a stop button",
                 "a STOP sign", "restored stock", "a one-stop shop", "the project manifesto", "서명 변조 시험",
                 "변조된 서명을 거부했습니다.", "사람 곁에선 천천히", "충돌 시험", "maekju", "somaek", "ataxias", "complex",
                 "completed", "complimentary", "an accomplishment", "the compiler", "라이선스 준수", "contract를 준수")
        self.write("docs/page.md", "\n".join(lines) + "\n")
        self.assertEqual(self.found(), [])

    def test_planned_names_fail(self):
        for line in ("The `maek` crate", "Ataxia: a crate", "JANGSEUNG", "amhaeng은 계획입니다."):
            with self.subTest(line=line):
                self.write("sim/js/page.js", f"// {line}\n")
                self.assertEqual(self.found(), [("sim/js/page.js", 1, "planned-name")])

    def test_allowlisted_line_passes(self):
        self.write("docs/page.md", "  Haetae is not a safety device and `maek` is not a crate.  \n")
        self.write("docs/nb.md", "Haetae is not an E\u2011stop.\n")  # the entry keeps the line's own hyphen
        self.allow((" docs/page.md ", " Haetae is not a safety device and `maek` is not a crate. "),  # spaces ignored
                   ("docs/nb.md", "Haetae is not an E\u2011stop."))
        self.assertEqual(self.found(), [])

    def test_allowlist_is_exact_per_path_and_per_line(self):
        self.write("docs/page.md", "Haetae is not a safety device!\n")
        self.write("docs/other.md", "Haetae is not a safety device.\n")
        self.allow(("docs/page.md", "Haetae is not a safety device."))
        self.assertEqual(self.found(), [("docs/other.md", 1, "banned"), ("docs/page.md", 1, "banned"),
                                        (wording.ALLOWLIST, 2, "allowlist")])

    def test_stale_entries_fail(self):
        self.write("docs/page.md", "Nothing to see here.\n")
        self.allow(("docs/page.md", "Nothing to see here."), ("docs/gone.md", "Not a safety device."))
        self.assertEqual(self.found(), [(wording.ALLOWLIST, 2, "allowlist"), (wording.ALLOWLIST, 3, "allowlist")])

    def test_malformed_and_duplicate_entries_fail(self):
        self.write("docs/page.md", "Not a safety device.\n")
        self.write(wording.ALLOWLIST, "# reviewed\ndocs/page.md Not a safety device.\n"
                                      "docs/page.md\tNot a safety device.\ndocs/page.md\tNot a safety device.\n")
        found = [(line, text.split(" ")[0]) for _, line, _, text in wording.check(self.root)]
        self.assertEqual(found, [(2, "malformed"), (4, "duplicate")])

    def test_allowlist_does_not_exempt_other_rules(self):
        self.write("docs/page.md", "The log is tamper-evident, not a safety device.\n")
        self.allow(("docs/page.md", "The log is tamper-evident, not a safety device."))
        self.assertEqual(self.found(), [("docs/page.md", 1, "tamper-evident")])

    def test_tamper_evident_needs_the_condition(self):
        bad = ("The log is tamper-evident.", "Tamper-evident log.", "The log is tamper evident.",
               "The log is tamperevident.", "It gives tamper evidence.", "실록은 위변조를 탐지합니다.",
               "위변조를 감지합니다.", "변조 검출 기능", "위변조의 흔적이 남습니다.", "변조된 흔적이 남습니다.",
               "The log is tamper\u2011evident.")
        self.write("docs/page.md", "\n\n".join(bad) + "\n")  # one paragraph each, on lines 1, 3, 5, ...
        self.write("sim/js/page.js", "// 변조 흔적이 남습니다.\n")
        split = CONDITION.replace("only against", "only\n//! against")  # joined without the comment marks
        self.write("crates/haetae/src/lib.rs", f"//! {TAG}\n//!\n//! The log is tamper-evident\n//! {split}.\n")
        self.write("sim/js/ok.js", f"/**\n * The log is tamper-evident\n * {CONDITION}.\n */\n")  # "*": comment mark
        self.write("docs/ok.md", f"Tamper-evident,\n{CONDITION.capitalize()}.\n\n"  # the condition in any case
                                 f"실록은 위변조를 탐지합니다 ({CONDITION}).\n\n"
                                 f"- The log is tamper-evident\n  {CONDITION}.\n\n"
                                 f"* The log is tamper-evident\n  {CONDITION}.\n\n"
                                 f"The log is tamper-evident\n**{CONDITION}**.\n\n"  # bold, not a bullet
                                 f"- It holds {CONDITION}, and\n  the log is tamper-evident.\n")
        self.assertEqual(self.found(), [("docs/page.md", 2 * n + 1, "tamper-evident") for n in range(len(bad))]
                         + [("sim/js/page.js", 1, "tamper-evident")])

    def test_tamper_condition_must_be_in_the_same_paragraph(self):
        cases = (  # (path, text, line of the finding): the condition sits in the next or previous paragraph
            ("docs/a.md", f"The log is tamper-evident.\n\nIt holds {CONDITION}.\n", 1),
            ("docs/b.md", f"It holds {CONDITION}.\n\nThe log is tamper-evident.\n", 3),
            ("docs/c.md", f"> The log is tamper-evident.\n>\n> It holds {CONDITION}.\n", 1),
            ("docs/d.md", f"- The log is tamper-evident.\n- It holds {CONDITION}.\n", 1),
            ("docs/e.md", f"1. It holds {CONDITION}.\n2. The log is tamper-evident.\n", 2),
            ("docs/e2.md", f"* The log is tamper-evident.\n* It holds {CONDITION}.\n", 1),
            ("docs/e3.md", f"* It holds {CONDITION}.\n* The log is tamper-evident.\n", 2),
            ("docs/e4.md", f"+ The log is tamper-evident.\n+ It holds {CONDITION}.\n", 1),
            ("python/haetae/f.py", f"# The log is tamper-evident.\n#\n# It holds {CONDITION}.\n", 1),
            ("sim/g.html", f'<p class="claim">The log is tamper-evident.</p>\n<!-- {CONDITION} -->\n', 1),
            ("sim/js/h.js", f"/**\n * The log is tamper-evident.\n *\n * It holds {CONDITION}.\n */\n", 2),
            ("sim/js/i.js", f"// - The log is tamper-evident.\n// - It holds {CONDITION}.\n", 1),
            ("sim/js/j.js", f"// - It holds {CONDITION}.\n// - The log is tamper-evident.\n", 2),
        )
        for path, text, _ in cases:
            self.write(path, text)
        self.assertEqual(self.found(), [(path, line, "tamper-evident") for path, _, line in cases])

    def test_knife_and_child_need_the_scripted_label(self):
        self.write("docs/page.md", "Intro.\nA VLA carries a knife toward a child.\n")
        self.write("sim/js/cards.js", "title: '칼 든 로봇, 아이에게',\n")
        self.write("docs/labeled.md", "Scripted adversarial proposal: a knife toward a child.\n")
        self.write("sim/js/labeled.js", "// 스크립트된 적대적 제안\nthreat: '칼을 든 로봇을 아이에게 보냅니다.',\n")
        self.write("docs/caps.md", "Knives near the Child\n")
        self.write("docs/wrapped.md", "Intro.\n\nA VLA proposes carrying a knife\ntoward a child.\n")  # one paragraph
        self.write("docs/apart.md", "A knife.\n\nA child.\n")
        self.write("sim/js/apart.js", "const knife = 1;\nconst child = 2;\n")  # outside Markdown: one line only
        self.assertEqual(self.found(), [("docs/caps.md", 1, "scripted-label"), ("docs/page.md", 2, "scripted-label"),
                                        ("docs/wrapped.md", 3, "scripted-label"),
                                        ("sim/js/cards.js", 1, "scripted-label")])

    def test_tagline_targets_need_the_exact_tagline(self):
        for path, text in CLEAN.items():  # each of the eight tagline targets
            with self.subTest(path=path):
                self.write(path, text.replace(TAG, OLD_TAG))
                self.assertEqual(self.found(), [(path, 1, "tagline")])
                self.write(path, text)
        self.write("README.md", f"**{TAG.replace('Pre-alpha', 'pre-alpha')}**\n")
        wrapped = TAG.replace(" Pre-alpha", "\nPre-alpha")
        self.write("python/pyproject.toml", f'[project]\ndescription = "{wrapped}"\n')
        (self.root / "sim/index.html").unlink()
        self.assertEqual(self.found(), [("README.md", 1, "tagline"), ("python/pyproject.toml", 1, "tagline"),
                                        ("sim/index.html", 1, "tagline")])

    def test_excluded_files_are_not_read(self):
        claim = "Haetae is a tamper-proof safety device, `maek` is a crate, a knife toward a child.\n"
        skipped = ("python/LICENSE", "python/LICENSE-MIT.txt", "docs/NOTICE.md", "sim/vendor/three.js",
                   "sim/brand/guide.md", "sim/pkg/haetae_wasm.js", "sim/examples/notes.md", "docs/vendor/x.md",
                   "sim/node_modules/x.js", "python/build/lib/x.py", "python/haetae.egg-info/PKG-INFO.txt",
                   "docs/AGENTS.md", "docs/ROADMAP.md", "sim/CLAUDE.md", "AGENTS.md", "ROADMAP.md", "CLAUDE.md",
                   "sim/COPYING.txt", "tools/notes.md", "docs/check_public_wording.py", "docs/data.json",
                   "sim/assets/rig.json")
        for path in skipped:
            self.write(path, claim)
        self.assertEqual(self.found(), [])
        controls = ("docs/license-faq.md", "docs/x.md", "python/haetae/x.py", "sim/build.sh", "sim/js/x.js",
                    "sim/style.css", "sim/x.test.mjs")  # positive controls, sorted
        for path in controls:
            self.write(path, "a safety device\n")
        self.assertEqual(self.found(), [(path, 1, "banned") for path in controls])

    def test_unreadable_target_fails(self):
        self.write("docs/page.md", b"\xff\xfe not utf-8\n")
        self.assertEqual(self.found(), [("docs/page.md", 1, "unreadable")])

    def test_output_and_exit_status(self):
        def run():
            return subprocess.run([sys.executable, str(CHECKER), str(self.root)], capture_output=True, text=True)
        clean = run()
        self.assertEqual((clean.returncode, clean.stdout), (0, ""))
        self.write("docs/page.md", "Intro.\n  Haetae is a safety device.\n")
        dirty = run()
        self.assertEqual(dirty.returncode, 1)
        self.assertEqual(dirty.stdout, "docs/page.md:2: banned (safety device): Haetae is a safety device.\n")


if __name__ == "__main__":
    unittest.main()
