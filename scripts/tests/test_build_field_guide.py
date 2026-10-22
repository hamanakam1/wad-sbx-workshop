#!/usr/bin/env python3
"""Tests for the field-guide generator.

The generator turns the dashboard's HTML asset into Markdown. The dashboard's
own test suite is the contract for that HTML; this suite is the contract for
the conversion.

Run with:  python3 scripts/tests/test_build_field_guide.py
"""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "build_field_guide", ROOT / "scripts" / "build-field-guide.py")
builder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(builder)

FIXTURE = """
<!-- a comment the generator must ignore -->
<nav class="sbxg-toc" aria-label="Field guide contents">
  <a href="#sbxg-idea">The one idea</a>
</nav>
<section id="sbxg-idea">
  <h3>The one idea</h3>
  <p>A sandbox is a promise about what crosses a boundary. <strong>Isolation</strong>
  is <em>where</em> code runs. <span class="sbxg-tag sbxg-v">verified</span></p>
  <ul><li>First point.</li><li>Second point with <code>sbx ls</code>.</li></ul>
  <div class="sbxg-code"><span class="sbxg-who sbxg-on-host">Host</span><pre>sbx ls --json
sbx version --json</pre></div>
  <div class="sbxg-scroll">
    <table>
      <thead><tr><th>Command</th><th>Meaning</th></tr></thead>
      <tbody>
        <tr><td><code>sbx ls</code></td><td>List the sandboxes.</td></tr>
        <tr><td><code>sbx version</code></td><td>Report the version.</td></tr>
      </tbody>
    </table>
  </div>
  <details class="sbxg-ex" data-ex="1">
    <summary><span class="sbxg-num">1</span><h4 class="sbxg-ex-title">A separate kernel</h4></summary>
    <div class="sbxg-ex-body">
      <h5>Steps</h5>
      <p>Do the thing.</p>
      <div class="sbxg-code"><span class="sbxg-who sbxg-in-vm">Sandbox</span><pre>uname -sr</pre></div>
    </div>
  </details>
</section>
"""


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.markdown = builder.render(FIXTURE)

    def test_headings_become_markdown_headings(self):
        self.assertIn("### The one idea", self.markdown)
        self.assertIn("#### A separate kernel", self.markdown)
        self.assertIn("##### Steps", self.markdown)

    def test_paragraphs_keep_their_inline_markup(self):
        self.assertIn("**Isolation**", self.markdown)
        self.assertIn("*where*", self.markdown)
        self.assertIn("`sbx ls`", self.markdown)

    def test_links_keep_their_target(self):
        self.assertIn("[The one idea](#sbxg-idea)", self.markdown)

    def test_confidence_tags_become_readable_markers(self):
        self.assertIn("[verified]", self.markdown)
        self.assertNotIn("sbxg-v", self.markdown)
        self.assertNotIn("</span>", self.markdown)

    def test_lists_become_markdown_lists(self):
        self.assertIn("- First point.", self.markdown)
        self.assertIn("- Second point with `sbx ls`.", self.markdown)

    def test_code_blocks_carry_their_label_and_content(self):
        self.assertIn("**Host**", self.markdown)
        self.assertIn("```", self.markdown)
        self.assertIn("sbx version --json", self.markdown)
        self.assertIn("**Sandbox**", self.markdown)
        self.assertIn("uname -sr", self.markdown)

    def test_tables_become_markdown_tables(self):
        self.assertIn("| Command | Meaning |", self.markdown)
        self.assertIn("| --- | --- |", self.markdown)
        self.assertIn("| `sbx ls` | List the sandboxes. |", self.markdown)

    def test_exercises_are_numbered_as_headings(self):
        self.assertIn("### Exercise 1", self.markdown)

    def test_no_html_survives(self):
        for tag in ("<span", "<div", "<p>", "</p>", "<table", "<details", "<td", "<li"):
            self.assertNotIn(tag, self.markdown, tag)


class OutputTests(unittest.TestCase):
    def test_the_generated_guide_is_present_and_well_formed(self):
        path = ROOT / "FIELD-GUIDE.md"
        self.assertTrue(path.is_file(), "FIELD-GUIDE.md is missing")
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# The Docker Sandbox field guide"))
        self.assertIn("### Exercise 12", text)
        self.assertIn("sbx-sandbox-test-receipt-v1", text)
        for tag in ("<span", "<div", "</p>"):
            self.assertNotIn(tag, text, tag)


if __name__ == "__main__":
    unittest.main(verbosity=2)
