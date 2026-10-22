#!/usr/bin/env python3
"""Generate the portable Markdown field guide from the dashboard's HTML asset.

The dashboard's Field guide is the source of truth. It is a static asset,
`static/tabs/sbx-guide.html.txt`, served same-origin and injected by
`static/tabs/sbx-guide.js`. This script turns that one file into
`FIELD-GUIDE.md`, so the workshop fork carries a readable, versioned copy of
the same text without a second copy drifting away from the first.

One direction only: HTML to Markdown. A Markdown-to-HTML converter would
have to reproduce every class name the dashboard's stylesheet targets, and a
missed class would silently change how the page looks. This direction cannot
change the dashboard, and the dashboard's own test suite is the contract for
the HTML.

    python3 scripts/build-field-guide.py                 # write FIELD-GUIDE.md
    python3 scripts/build-field-guide.py --check         # fail if it is stale
    python3 scripts/build-field-guide.py --source PATH    # a different asset

The source is found from --source, then $SBX_GUIDE_ASSET, then the default
checkout path. --check compares rather than writes, which is what CI wants.
"""

from __future__ import annotations

import argparse
from html.parser import HTMLParser
import os
from pathlib import Path
import re
import sys

DEFAULT_SOURCE = "~/Projects/ai-dashboard/static/tabs/sbx-guide.html.txt"
OUTPUT_NAME = "FIELD-GUIDE.md"

HEADING = {"h3": 3, "h4": 4, "h5": 5, "h6": 6}
# The confidence tags are a rendering device, not prose: the Markdown keeps
# them as a readable marker instead of a coloured span.
TAGS = {"sbxg-v": "verified", "sbxg-i": "inferred", "sbxg-c": "corrected"}
DROP_TEXT = {"sbxg-src", "sbxg-muted", "sbxg-good", "sbxg-warn", "sbxg-bad",
             "sbxg-here-note", "sbxg-live-chip", "sbxg-code-label"}


def _classes(attrs):
    return (dict(attrs).get("class") or "").split()


def _attr(attrs, name):
    return dict(attrs).get(name)


class GuideParser(HTMLParser):
    """Turn the guide's HTML into Markdown lines.

    Inline text is accumulated in one buffer. The buffer is flushed into a
    paragraph, a heading, a table cell or a list item depending on where the
    parser is when the block closes.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines = []
        self.buf = []
        self.target = None          # 'paragraph' | 'heading' | 'cell' | 'item'
        self.heading_level = None
        self.skip_depth = 0
        self.in_pre = False
        self.pre_lines = []
        self.pre_label = ""
        self.who = None              # text of the open sbxg-who span
        self.list_stack = []
        self.table = None
        self.row = None
        self.cell = None
        self.link = None             # (href, index in buf)
        self.strong = 0
        self.emphasis = 0
        self.suppress = 0
        self.in_nav = False
        self.in_summary = False
        self.exercise = None

    # -- output helpers -----------------------------------------------------

    def emit(self, text=""):
        self.lines.append(text)

    def flush(self):
        text = "".join(self.buf)
        self.buf = []
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return
        if self.target == "heading":
            self.emit()
            self.emit("#" * (self.heading_level or 3) + " " + text)
            self.emit()
        elif self.target == "cell":
            self.cell = text
        elif self.target == "item":
            marker = self.list_stack[-1] if self.list_stack else "-"
            self.emit(marker + " " + text)
        else:
            if self.lines and self.lines[-1] != "":
                self.emit()
            self.emit(text)
            self.emit()

    # -- HTMLParser hooks ---------------------------------------------------

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip_depth += 1
            return
        if self.skip_depth:
            return

        if tag == "pre":
            self.in_pre = True
            self.pre_lines = []
            self.pre_label = (self.who or "").strip()
            self.who = None
            return
        if tag == "span" and "sbxg-who" in _classes(attrs):
            self.who = ""
            return
        if tag == "span":
            classes = _classes(attrs)
            for css, word in TAGS.items():
                if css in classes:
                    self.buf.append("[" + word + "]")
                    self.suppress += 1
                    return
            return
        if tag in HEADING:
            self.target = "heading"
            self.heading_level = HEADING[tag]
            self.buf = []
            return
        if tag == "summary":
            self.in_summary = True
            self.target = "heading"
            self.heading_level = 4
            self.buf = []
            return
        if tag == "p" or tag == "figcaption":
            self.target = "paragraph"
            self.buf = []
            return
        if tag == "a":
            if self.in_nav:
                # A table of contents: every link is its own entry.
                self.flush()
            self.link = (_attr(attrs, "href"), len(self.buf))
            return
        if tag in ("strong", "b"):
            self.strong += 1
            self.buf.append("**")
            return
        if tag in ("em", "i"):
            self.emphasis += 1
            self.buf.append("*")
            return
        if tag == "code":
            self.buf.append("`")
            return
        if tag == "br":
            self.buf.append("\n")
            return
        if tag in ("ul", "ol"):
            self.list_stack.append("- " if tag == "ul" else "1. ")
            return
        if tag == "nav":
            # The table of contents is a list of links, one per entry.
            self.in_nav = True
            self.list_stack.append("- ")
            self.target = "item"
            return
        if tag == "li":
            self.target = "item"
            self.buf = []
            return
        if tag == "table":
            self.table = []
            return
        if tag == "tr":
            self.row = []
            return
        if tag in ("td", "th"):
            self.target = "cell"
            self.cell = ""
            self.buf = []
            return
        if tag == "details":
            self.exercise = _attr(attrs, "data-ex")
            if self.exercise:
                self.emit()
                self.emit("### Exercise " + self.exercise)
                self.emit()
            return
        if tag == "img":
            alt = _attr(attrs, "alt") or ""
            self.buf.append("[figure: " + alt + "]")
            return

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            if self.skip_depth:
                self.skip_depth -= 1
            return
        if self.skip_depth:
            return

        if tag == "pre":
            self.in_pre = False
            body = "\n".join(self.pre_lines).strip("\n")
            self.emit()
            self.emit("**" + (self.pre_label or "Commands") + "**")
            self.emit()
            self.emit("```")
            for line in body.splitlines():
                self.emit(line)
            self.emit("```")
            self.emit()
            self.pre_label = ""
            return
        if tag == "span":
            if self.suppress:
                self.suppress -= 1
            elif self.who is None:
                # A span that carried text needs a separator from what follows.
                self.buf.append(" ")
            return
        if tag in HEADING or tag == "summary":
            self.flush()
            self.target = None
            self.heading_level = None
            self.in_summary = False
            return
        if tag in ("p", "figcaption"):
            self.flush()
            self.target = None
            return
        if tag == "a":
            if self.link:
                href, start = self.link
                text = "".join(self.buf[start:]).strip()
                del self.buf[start:]
                self.buf.append("[" + text + "](" + (href or "") + ")")
                self.link = None
            if self.in_nav:
                self.flush()
            return
        if tag in ("strong", "b"):
            self.strong = max(0, self.strong - 1)
            self.buf.append("**")
            return
        if tag in ("em", "i"):
            self.emphasis = max(0, self.emphasis - 1)
            self.buf.append("*")
            return
        if tag == "code":
            self.buf.append("`")
            return
        if tag == "li":
            self.flush()
            self.target = None
            return
        if tag in ("ul", "ol"):
            if self.list_stack:
                self.list_stack.pop()
            return
        if tag == "nav":
            self.flush()
            self.target = None
            self.in_nav = False
            if self.list_stack:
                self.list_stack.pop()
            self.emit()
            return
        if tag in ("td", "th"):
            self.flush()
            if self.row is not None:
                self.row.append(self.cell or "")
            self.cell = None
            self.target = None
            return
        if tag == "tr":
            if self.table is not None and self.row:
                self.table.append(self.row)
            self.row = None
            return
        if tag == "table":
            self.render_table()
            self.table = None
            return
        if tag == "details":
            self.exercise = None
            return

    def handle_data(self, data):
        if self.skip_depth or self.in_pre:
            if self.in_pre:
                self.pre_lines.append(data)
            return
        if self.suppress:
            # The text inside a confidence tag is the marker's own word.
            return
        if self.who is not None:
            self.who += data
            return
        if self.cell is not None and self.target == "cell":
            self.buf.append(data)
            return
        self.buf.append(data)

    # -- tables -------------------------------------------------------------

    def render_table(self):
        rows = [row for row in (self.table or []) if row]
        if not rows:
            return
        self.emit()
        self.emit("| " + " | ".join(rows[0]) + " |")
        self.emit("| " + " | ".join("---" for _ in rows[0]) + " |")
        for row in rows[1:]:
            self.emit("| " + " | ".join(row) + " |")
        self.emit()


def to_markdown(html_text):
    parser = GuideParser()
    parser.feed(html_text)
    parser.close()
    out = []
    in_fence = False
    for line in parser.lines:
        if line.strip().startswith("```"):
            in_fence = not in_fence
            out.append(line.strip())
            continue
        if in_fence:
            # A command's indentation is part of it. Never collapse it.
            out.append(line)
            continue
        line = re.sub(r"[ \t]+", " ", line).rstrip()
        if not line:
            if out and out[-1] != "":
                out.append("")
            continue
        out.append(line)
    text = "\n".join(out).strip() + "\n"
    return re.sub(r"\n{3,}", "\n\n", text)


HEADER = """# The Docker Sandbox field guide

This file is generated. The source of truth is the dashboard's static asset
`static/tabs/sbx-guide.html.txt`, which is served same-origin and injected by
`static/tabs/sbx-guide.js`; the dashboard's own test suite is the contract for
that HTML.

Rebuild it with:

    python3 scripts/build-field-guide.py

and check it is current with:

    python3 scripts/build-field-guide.py --check

The confidence markers travel with the claims. `[verified]` means a primary
source said so, `[inferred]` means the source supports it but not directly, and
`[corrected]` means an earlier version of this guide was wrong and the text
has been fixed.

---

"""


def render(html_text):
    body = to_markdown(html_text)
    # The remaining span classes carry no prose of their own; the text inside
    # them does, so only the wrapper goes.
    body = re.sub(r"</?span[^>]*>", "", body)
    body = re.sub(r"</?button[^>]*>", "", body)
    body = re.sub(r"</?label[^>]*>", "", body)
    body = re.sub(r"</?div[^>]*>", "", body)
    body = re.sub(r"</?p[^>]*>", "", body)
    body = re.sub(r"</?li[^>]*>", "", body)
    body = re.sub(r"</?details[^>]*>", "", body)
    body = re.sub(r"</?summary[^>]*>", "", body)
    body = re.sub(r"</?nav[^>]*>", "", body)
    body = re.sub(r"</?section[^>]*>", "", body)
    body = re.sub(r"</?figcaption[^>]*>", "", body)
    body = re.sub(r"</?table[^>]*>", "", body)
    body = re.sub(r"</?thead[^>]*>", "", body)
    body = re.sub(r"</?tbody[^>]*>", "", body)
    body = re.sub(r"</?tr[^>]*>", "", body)
    body = re.sub(r"</?td[^>]*>", " | ", body)
    body = re.sub(r"</?th[^>]*>", " | ", body)
    body = re.sub(r"</?h[1-6][^>]*>", "", body)
    body = re.sub(r"</?nav[^>]*>", "", body)
    body = re.sub(r"[ \t]+\|", " |", body)
    body = re.sub(r"\|[ \t]+", "| ", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return HEADER + body.strip() + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", help="the guide HTML asset to read")
    parser.add_argument("--output", help="where to write the Markdown")
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero when the output is not current")
    args = parser.parse_args(argv)

    source = args.source or os.environ.get("SBX_GUIDE_ASSET") or DEFAULT_SOURCE
    source_path = Path(source).expanduser()
    if not source_path.is_file():
        print("guide asset not found: %s" % source_path, file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parent.parent
    output_path = Path(args.output).expanduser() if args.output else root / OUTPUT_NAME

    markdown = render(source_path.read_text(encoding="utf-8"))
    if args.check:
        current = output_path.read_text(encoding="utf-8") if output_path.is_file() else ""
        if current != markdown:
            print("%s is out of date; run scripts/build-field-guide.py" % output_path.name,
                  file=sys.stderr)
            return 1
        print("%s is current" % output_path.name)
        return 0
    output_path.write_text(markdown, encoding="utf-8")
    print("wrote %s (%d lines)" % (output_path, markdown.count("\n")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
