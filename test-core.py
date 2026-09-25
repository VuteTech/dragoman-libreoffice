#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Blagovest Petrov <blagovest@petrovs.info>
# SPDX-FileCopyrightText: 2026 Vute Tech Ltd. <https://vute.tech>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests the UNO-free core against a stub dragomanctl."""

import json
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src", "pythonpath"))
import dragoman_lo  # noqa: E402


STUB_OK = """#!/usr/bin/env python3
import json, sys
if sys.argv[1:] == ["--json", "pairs"]:
    print(json.dumps([
        {"source": "bg", "target": "en", "installed_version": "3.0"},
        {"source": "en", "target": "bg", "available_version": "3.0"},
        {"source": "en", "target": "de", "available_version": "3.1"},
    ]))
    sys.exit(0)
html = "--html" in sys.argv
argv = [a for a in sys.argv[1:] if a != "--html"]
assert argv == ["--json", "translate", "-f", "bg", "-t", "en"], sys.argv
prefix = "[H] " if html else "[T] "
lines = sys.stdin.read().split("\\n")
print(json.dumps({"translations": [prefix + l for l in lines]}))
"""

STUB_FAIL = """#!/usr/bin/env python3
import sys
sys.stderr.write("dragomanctl: something broke\\n")
sys.exit(1)
"""


class CoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["DRAGOMAN_LO_CONFIG"] = os.path.join(self.tmp.name, "lo.json")
        self.addCleanup(os.environ.pop, "DRAGOMAN_LO_CONFIG", None)
        self.addCleanup(os.environ.pop, "DRAGOMAN_LO_CTL", None)

    def stub(self, content):
        path = os.path.join(self.tmp.name, "dragomanctl")
        with open(path, "w") as f:
            f.write(content)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        os.environ["DRAGOMAN_LO_CTL"] = path

    def test_translate_preserves_line_structure(self):
        self.stub(STUB_OK)
        out = dragoman_lo.translate_text("bg", "en", "ред едно\r\n\r\nред три")
        # The blank middle line is preserved, not translated.
        self.assertEqual(out, "[T] ред едно\n\n[T] ред три")

    def test_trailing_blank_line_survives(self):
        # The regression the user hit: a selection ending on an empty
        # paragraph ("expected 7 segments, got 6").
        self.stub(STUB_OK)
        out = dragoman_lo.translate_text("bg", "en", "а\nб\n")
        self.assertEqual(out, "[T] а\n[T] б\n")
        out = dragoman_lo.translate_text("bg", "en", "а\n\n\nб\n\n")
        self.assertEqual(out, "[T] а\n\n\n[T] б\n\n")
        # Whitespace-only lines stay verbatim too.
        out = dragoman_lo.translate_text("bg", "en", "а\n   \nб")
        self.assertEqual(out, "[T] а\n   \n[T] б")
        # Nothing translatable: the text comes back unchanged.
        self.assertEqual(dragoman_lo.translate_text("bg", "en", "\n \n"), "\n \n")

    def test_cli_failure_surfaces_last_stderr_line(self):
        self.stub(STUB_FAIL)
        with self.assertRaises(dragoman_lo.TranslateError) as caught:
            dragoman_lo.translate_text("bg", "en", "x")
        self.assertIn("something broke", str(caught.exception))

    def test_missing_cli_is_a_clear_error(self):
        os.environ["DRAGOMAN_LO_CTL"] = os.path.join(self.tmp.name, "nope")
        with self.assertRaises(dragoman_lo.TranslateError) as caught:
            dragoman_lo.translate_text("bg", "en", "x")
        self.assertIn("dragomand package", str(caught.exception))

    def test_pair_roundtrip_and_validation(self):
        self.assertEqual(dragoman_lo.load_pair(), ("bg", "en"))
        dragoman_lo.save_pair("en", "de")
        self.assertEqual(dragoman_lo.load_pair(), ("en", "de"))
        with self.assertRaises(dragoman_lo.TranslateError):
            dragoman_lo.save_pair("en", "en")
        with self.assertRaises(dragoman_lo.TranslateError):
            dragoman_lo.save_pair("bad tag!", "en")
        # Corrupt config falls back to the default.
        with open(os.environ["DRAGOMAN_LO_CONFIG"], "w") as f:
            f.write("not json")
        self.assertEqual(dragoman_lo.load_pair(), ("bg", "en"))

    def test_language_labels_roundtrip(self):
        self.assertEqual(dragoman_lo.language_label("bg"), "Bulgarian (bg)")
        self.assertEqual(dragoman_lo.language_label("xx"), "xx")
        self.assertEqual(dragoman_lo.code_from_label("Bulgarian (bg)"), "bg")
        self.assertEqual(dragoman_lo.code_from_label(" bg "), "bg")
        self.assertEqual(dragoman_lo.code_from_label("german"), "de")
        self.assertEqual(
            dragoman_lo.code_from_label("Chinese (Simplified) (zh-Hans)"),
            "zh-Hans",
        )

    def test_available_languages_from_cli_and_fallback(self):
        self.stub(STUB_OK)
        sources, targets = dragoman_lo.available_languages()
        self.assertEqual(sources, ["bg", "en"])
        self.assertEqual(targets, ["bg", "de", "en"])
        # CLI missing: the static table backs the dialog.
        os.environ["DRAGOMAN_LO_CTL"] = os.path.join(self.tmp.name, "nope")
        sources, targets = dragoman_lo.available_languages()
        self.assertIn("bg", sources)
        self.assertGreater(len(sources), 50)
        self.assertEqual(sources, targets)

    def test_locale_mapping_and_script_category(self):
        self.assertEqual(dragoman_lo.locale_to_code("bg", ""), "bg")
        self.assertEqual(dragoman_lo.locale_to_code("zh", "CN"), "zh-Hans")
        self.assertEqual(dragoman_lo.locale_to_code("zh", "TW"), "zh-Hant")
        self.assertIsNone(dragoman_lo.locale_to_code("", ""))
        self.assertEqual(dragoman_lo.script_category("Здравей"), "western")
        self.assertEqual(dragoman_lo.script_category("hello"), "western")
        self.assertEqual(dragoman_lo.script_category("12 こんにちは"), "asian")
        self.assertEqual(dragoman_lo.script_category("中文"), "asian")
        self.assertEqual(dragoman_lo.script_category("שלום"), "complex")
        self.assertEqual(dragoman_lo.script_category("مرحبا"), "complex")
        self.assertEqual(dragoman_lo.script_category("123 !?"), "western")

    def test_choose_direction(self):
        saved = ("bg", "en")
        self.assertEqual(dragoman_lo.choose_direction(saved, None), ("bg", "en"))
        self.assertEqual(dragoman_lo.choose_direction(saved, "bg"), ("bg", "en"))
        self.assertEqual(dragoman_lo.choose_direction(saved, "en"), ("en", "bg"))
        self.assertEqual(dragoman_lo.choose_direction(saved, "de"), ("de", "en"))

    def test_sanitize_strips_web_artifacts(self):
        text = "Пре\u00adзи\u200bдент\u00a0Йотова\u2028втори"
        self.assertEqual(
            dragoman_lo.sanitize_text(text), "Президент Йотова\nвтори"
        )
        # NFC: decomposed й (и + combining breve) becomes one codepoint.
        self.assertEqual(dragoman_lo.sanitize_text("и\u0306"), "й")

    def test_sanitize_feeds_translation(self):
        self.stub(STUB_OK)
        out = dragoman_lo.translate_text("bg", "en", "а\u00adб\u00a0в")
        self.assertEqual(out, "[T] аб в")

    def test_wrong_script_detection(self):
        soup = "Prezidentъt Iliяna Йotova se sreщna s bъlgarskata obщnost"
        self.assertTrue(dragoman_lo.looks_like_wrong_script("bg", soup))
        clean = "Президентът Илияна Йотова се срещна с българската общност"
        self.assertFalse(dragoman_lo.looks_like_wrong_script("bg", clean))
        # Latin sources are never flagged, nor is empty/numeric text.
        self.assertFalse(dragoman_lo.looks_like_wrong_script("de", "Hallo Welt"))
        self.assertFalse(dragoman_lo.looks_like_wrong_script("bg", "1234 !?"))

    def test_homoglyph_folding(self):
        laced = "Пpeзидeнтът ce cpeщнa c бългapcкaтa oбщнocт"
        fixed = dragoman_lo.sanitize_text(laced, "bg")
        self.assertEqual(fixed, "Президентът се срещна с българската общност")
        # Genuine Latin words survive even in polluted text.
        kept = dragoman_lo.sanitize_text("Пpeзидeнтът ползва Windows", "bg")
        self.assertEqual(kept, "Президентът ползва Windows")
        # Unpolluted Latin text is never rewritten.
        clean = "peace and love, ace"
        self.assertEqual(dragoman_lo.sanitize_text(clean, "bg"), clean)
        # Cyrillic homoglyphs inside Latin words fold the other way.
        self.assertEqual(dragoman_lo.fold_homoglyphs("Тhе Тext"), "The Text")

    def test_sanitize_html_touches_text_nodes_only(self):
        html = '<p lang="bg">Пpe\u00adзидeнтът&nbsp;<a href="http://x/у">Hю Йopк</a></p>\n<p>b</p>'
        out = dragoman_lo.sanitize_html(html, "bg")
        self.assertIn('<a href="http://x/у">', out)   # markup untouched
        self.assertIn("Президентът&nbsp;", out)        # entity kept, text fixed
        self.assertIn(">Ню Йорк<", out)
        self.assertNotIn("\n", out)

    def test_translate_html_uses_html_mode(self):
        self.stub(STUB_OK)
        out = dragoman_lo.translate_html("bg", "en", "<b>а</b>\n<i>б</i>")
        self.assertEqual(out, "[H] <b>а</b> <i>б</i>")

    def test_stub_json_is_wellformed(self):
        self.stub(STUB_OK)
        out = dragoman_lo.translate_lines("bg", "en", ["a", "b"])
        self.assertEqual(out, ["[T] a", "[T] b"])
        json.dumps(out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
