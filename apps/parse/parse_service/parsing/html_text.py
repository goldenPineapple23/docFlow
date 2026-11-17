"""
The text of an HTML file or an HTML e-mail body (B1's demo half, slice 2;
DECISIONS.md D-230, D-235 item 13; docs/designs/b1.md section 3).

Runs only inside a parse-service job (Section 7.11). Python's own
`html.parser`: no new dependency, and nothing that fetches a URL, resolves
an entity file, runs a script or evaluates a style. The answer is plain
text: what the model reads and what the reviewer is shown, escaped.

- Dropped with their contents: script, style, head, template, noscript, svg,
  object, embed, iframe, and comments.
- Kept as text: everything else. A block element and `<br>` end a line. A
  table row is one line, its cells joined by ", ", as the DOCX and XLSX
  readers write a row.
- A link keeps its text and loses its address. An image contributes nothing.
- **Text hidden by a style is kept** (founder, 2026-11-16): no style is
  evaluated, so the reviewer sees everything the model read.

No value is changed: a run of spaces becomes one space and blank lines are
dropped, as a browser would show the text, and nothing else.
"""

from __future__ import annotations

from html.parser import HTMLParser

from parse_service.parsing.conversion import ConversionError

# How deep elements may nest before the file is refused. A real page or
# e-mail is a few dozen deep; the cap keeps a hostile file's bookkeeping
# small. The job's time and memory limits stand behind it.
MAX_HTML_DEPTH = 512

# A file that can't be read is answered as a damaged file of its format is.
HTML_UNREADABLE_CODE = "DOC-005"

_DROPPED = frozenset({"script", "style", "head", "template", "noscript", "svg", "object", "embed", "iframe"})

# What a head may hold. A head with no end tag ends at the first element
# that isn't one of these, as it does in a browser.
_HEAD_CONTENT = frozenset({"base", "link", "meta", "noscript", "script", "style", "template", "title"})

# Elements with no end tag: they open nothing, so they count for no depth
# and can never leave the reader inside a dropped element.
_VOID = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
)

# Elements that start and end a line.
_BLOCK = frozenset(
    {
        "address", "article", "aside", "blockquote", "body", "caption", "center", "dd", "details", "dialog",
        "dir", "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4",
        "h5", "h6", "header", "hgroup", "hr", "html", "legend", "li", "main", "menu", "nav", "ol", "optgroup",
        "option", "p", "pre", "section", "select", "summary", "table", "tbody", "tfoot", "thead", "title", "ul",
    }
)  # fmt: skip

_CELLS = frozenset({"td", "th"})

# Elements whose end tag HTML lets a writer leave out. A long table or list
# written that way is flat, not deep, so these count for no depth.
_END_TAG_OPTIONAL = frozenset(
    {
        "body", "caption", "colgroup", "dd", "dt", "head", "html", "li", "optgroup", "option", "p", "tbody",
        "td", "tfoot", "th", "thead", "tr",
    }
)  # fmt: skip


class _Row:
    """One open table row: its finished cells and the cell being read."""

    def __init__(self) -> None:
        self.cells: list[str] = []
        self.cell: list[str] | None = None

    def close_cell(self) -> None:
        if self.cell is not None:
            self.cells.append(_one_line("".join(self.cell)))
            self.cell = None


def _one_line(text: str) -> str:
    """Runs of white space, the no-break space among them, as one space."""
    return " ".join(text.replace("\xa0", " ").split())


class _Reader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self._line: list[str] = []
        self._open: list[str] = []  # every open element, for the depth cap
        self._dropped: list[str] = []  # the open dropped elements
        self._rows: list[_Row | None] = []  # one entry per open table: its open row
        self._pre = 0
        self.saw_element = False

    # ── where text goes ──────────────────────────────────────────────────

    def _row(self) -> _Row | None:
        return self._rows[-1] if self._rows else None

    def _break(self) -> None:
        """End the line being read. Inside a table cell a break is a space:
        the row stays one line."""
        row = self._row()
        if row is not None and row.cell is not None:
            row.cell.append(" ")
            return
        text = "".join(self._line)
        self._line = []
        if self._pre:
            self.lines.extend(line.rstrip() for line in text.split("\n") if line.strip())
        elif text.strip():
            self.lines.append(_one_line(text))

    def _close_row(self) -> None:
        row = self._row()
        if row is None:
            return
        row.close_cell()
        if any(row.cells):
            self.lines.append(", ".join(row.cells))
        self._rows[-1] = None

    # ── the parser's calls ───────────────────────────────────────────────

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.saw_element = True
        if self._dropped and self._dropped[-1] == "head" and tag not in _HEAD_CONTENT and tag != "head":
            self._dropped.pop()
        if tag not in _VOID and tag not in _END_TAG_OPTIONAL:
            self._open.append(tag)
            if len(self._open) > MAX_HTML_DEPTH:
                raise ConversionError(HTML_UNREADABLE_CODE, f"HTML nests deeper than {MAX_HTML_DEPTH} elements.")
        if tag in _DROPPED:
            if tag not in _VOID:
                self._dropped.append(tag)
            return
        if self._dropped:
            return
        if tag == "table":
            self._break()
            self._rows.append(None)
        elif tag == "tr" and self._rows:
            self._close_row()
            self._rows[-1] = _Row()
        elif tag in _CELLS and self._rows:
            row = self._rows[-1]
            if row is None:
                row = self._rows[-1] = _Row()  # a cell with no row around it
            row.close_cell()
            row.cell = []
        elif tag == "br" or tag in _BLOCK:
            self._break()
        if tag == "pre":
            self._pre += 1

    def handle_comment(self, data: str) -> None:
        self.saw_element = True

    def handle_decl(self, decl: str) -> None:
        self.saw_element = True

    def handle_endtag(self, tag: str) -> None:
        self.saw_element = True
        if tag in self._open:
            # Close it and anything left open inside it: a dropped element
            # with no end tag of its own ends where its parent does.
            while True:
                closed = self._open.pop()
                if closed != tag and closed in self._dropped:
                    self._dropped.reverse()
                    self._dropped.remove(closed)
                    self._dropped.reverse()
                if closed == tag:
                    break
        if tag in self._dropped:
            while self._dropped.pop() != tag:
                pass
            return
        if self._dropped:
            return
        if tag == "table" and self._rows:
            self._close_row()
            self._rows.pop()
        elif tag == "tr" and self._rows:
            self._close_row()
        elif tag in _CELLS:
            row = self._row()
            if row is not None:
                row.close_cell()
        elif tag in _BLOCK:
            self._break()
        if tag == "pre" and self._pre:
            self._pre -= 1

    def handle_data(self, data: str) -> None:
        if self._dropped:
            return
        row = self._row()
        if row is not None and row.cell is not None:
            row.cell.append(data)
        else:
            self._line.append(data)

    def finish(self) -> str:
        self.close()
        while self._rows:
            self._close_row()
            self._rows.pop()
        self._break()
        return "\n".join(self.lines)


def html_to_text(markup: str) -> str:
    """The text of `markup`, as the module's rules have it. Raises
    ConversionError (DOC-005) for a file nested past `MAX_HTML_DEPTH`.

    Text with no tag, comment or declaration in it is not HTML, whatever the
    file is called: it is returned as it is, so an order typed as plain lines
    keeps its lines.
    """
    reader = _Reader()
    reader.feed(markup)
    text = reader.finish()
    return text if reader.saw_element else markup
