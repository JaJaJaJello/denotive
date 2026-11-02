"""Text formatting beyond the font: bold, italic, underline, strikethrough, super/subscript and lists.

Board text stays plain text, so fonts, search and everything else keep working. Its formatting sits beside it:
    data["spans"] = [[start, end, kind], ...]   kind: b, i, u, s, sup, sub (character positions, may overlap)
    data["lists"] = [[line, "bullet" | "number"], ...]
Slide text is rich text and keeps the same things in its HTML.
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QTextCharFormat, QTextCursor, QTextDocument, QTextListFormat

SUP, SUB = QTextCharFormat.AlignSuperScript, QTextCharFormat.AlignSubScript
ALIGN_OF = {"sup": SUP, "sub": SUB}
KINDS = ("b", "i", "u", "s", "sup", "sub")
LIST_STYLE = {"bullet": QTextListFormat.ListDisc, "number": QTextListFormat.ListDecimal}
PLAIN_KEYS = {Qt.Key_B: "b", Qt.Key_I: "i", Qt.Key_U: "u", Qt.Key_Equal: "sup", Qt.Key_Plus: "sup",
              Qt.Key_Minus: "sub", Qt.Key_Underscore: "sub"}
SHIFT_KEYS = {Qt.Key_X: "s", Qt.Key_8: "bullet", Qt.Key_Asterisk: "bullet", Qt.Key_7: "number",
              Qt.Key_Ampersand: "number", Qt.Key_Equal: "sup", Qt.Key_Plus: "sup", Qt.Key_Minus: "sub",
              Qt.Key_Underscore: "sub"}


def key_action(e):
    """The formatting a key press asks for (a kind, 'bullet' or 'number'), else None."""
    if not e.modifiers() & Qt.ControlModifier:
        return None
    return (SHIFT_KEYS if e.modifiers() & Qt.ShiftModifier else PLAIN_KEYS).get(e.key())


def _has(fmt, kind):
    if kind in ALIGN_OF:
        return fmt.verticalAlignment() == ALIGN_OF[kind]
    if kind == "b":
        return fmt.fontWeight() >= QFont.DemiBold
    return {"i": fmt.fontItalic, "u": fmt.fontUnderline, "s": fmt.fontStrikeOut}[kind]()


def _set(fmt, kind, on):
    if kind in ALIGN_OF:
        fmt.setVerticalAlignment(ALIGN_OF[kind] if on else QTextCharFormat.AlignNormal)
    elif kind == "b":
        fmt.setFontWeight(QFont.Bold if on else QFont.Normal)
    else:
        {"i": fmt.setFontItalic, "u": fmt.setFontUnderline, "s": fmt.setFontStrikeOut}[kind](on)


def _format_at(cursor):
    """The format of the selection's first character, or of what's typed next."""
    if not cursor.hasSelection():
        return cursor.charFormat()
    probe = QTextCursor(cursor.document())
    probe.setPosition(cursor.selectionStart() + 1)
    return probe.charFormat()


def active(cursor):
    """Everything switched on where the cursor is, for lighting up toolbar buttons."""
    fmt = _format_at(cursor)
    on = {k for k in KINDS if _has(fmt, k)}
    lst = cursor.currentList()
    if lst is not None:
        on.add(_list_kind(lst))
    return on


def toggle(cursor, kind):
    """Formats the selection (or what's typed next); the same one again sets it back."""
    f = QTextCharFormat()
    _set(f, kind, not _has(_format_at(cursor), kind))
    cursor.mergeCharFormat(f)


def _list_kind(lst):
    return "bullet" if lst.format().style() == LIST_STYLE["bullet"] else "number"


def toggle_list(cursor, kind):
    """Turns the selected lines (or the current line) into a list; the same list again makes them plain lines."""
    lst = cursor.currentList()
    if lst is None or _list_kind(lst) != kind:
        f = QTextListFormat()
        f.setStyle(LIST_STYLE[kind])
        f.setIndent(1)
        cursor.createList(f)
        return
    doc = cursor.document()
    b, end = doc.findBlock(cursor.selectionStart()), doc.findBlock(cursor.selectionEnd())
    while b.isValid() and b.blockNumber() <= end.blockNumber():
        if b.textList() is not None:
            b.textList().remove(b)
            c = QTextCursor(b)
            bf = c.blockFormat()
            bf.setIndent(0)
            c.setBlockFormat(bf)
        b = b.next()


def set_indent(doc, font_px):
    doc.setIndentWidth(max(16.0, font_px * 1.3))  # Qt's 40 px wastes a small note's width


# ---------------------------------------------------------------------- to and from board data
def spans_of(data):
    return data.get("spans") or data.get("scripts") or []  # "scripts": saved before spans existed


def ranges(doc):
    """[[start, end, kind], ...] for all the formatting in a QTextDocument."""
    open_runs, out = {}, []
    b = doc.begin()
    while b.isValid():
        it = b.begin()
        while not it.atEnd():
            frag = it.fragment()
            a, e = frag.position(), frag.position() + frag.length()
            for k in KINDS:
                if _has(frag.charFormat(), k):
                    run = open_runs.get(k)
                    if run is not None and run[1] == a:
                        run[1] = e
                    else:
                        open_runs[k] = [a, e, k]
                        out.append(open_runs[k])
            it += 1
        b = b.next()
    return sorted(out, key=lambda r: (r[0], r[1], r[2]))


def list_lines(doc):
    """[[line, kind], ...] for every line that belongs to a list."""
    out = []
    b = doc.begin()
    while b.isValid():
        if b.textList() is not None:
            out.append([b.blockNumber(), _list_kind(b.textList())])
        b = b.next()
    return out


def apply(doc, spans, lists=None):
    n = doc.characterCount() - 1
    for a, e, kind in spans or ():
        a, e = max(0, min(int(a), n)), max(0, min(int(e), n))
        if e <= a or kind not in KINDS:
            continue
        c = QTextCursor(doc)
        c.setPosition(a)
        c.setPosition(e, QTextCursor.KeepAnchor)
        f = QTextCharFormat()
        _set(f, kind, True)
        c.mergeCharFormat(f)
    kinds = {int(i): k for i, k in lists or ()}
    prev, cur = None, None
    b = doc.begin()
    while b.isValid():
        k = kinds.get(b.blockNumber())
        if k in LIST_STYLE and k == prev:
            cur.add(b)
        elif k in LIST_STYLE:
            f = QTextListFormat()
            f.setStyle(LIST_STYLE[k])
            f.setIndent(1)
            cur = QTextCursor(b).createList(f)
        prev = k if k in LIST_STYLE else None
        b = b.next()


# ---------------------------------------------------------------------- readable text (case file for the AI)
_SUP_T = str.maketrans("0123456789+-=()ni", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ")
_SUB_T = str.maketrans("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")
_SUP_OK = set("0123456789+-=()ni")
_SUB_OK = set("0123456789+-=()")


def _mark(s, kind):
    core = s.strip()
    if not core:
        return s
    lead, tail = s[:len(s) - len(s.lstrip())], s[len(s.rstrip()):]
    if kind == "s":
        return f"{lead}~~{core}~~{tail}"  # crossed out means something: keep it from reading as a claim
    ok, table, sign = (_SUP_OK, _SUP_T, "^") if kind == "sup" else (_SUB_OK, _SUB_T, "_")
    if set(core) <= ok:
        return lead + core.translate(table) + tail  # 10⁻³, H₂O
    return f"{lead}{sign}({core}){tail}"  # x^(n+1), k_(B)


def text_with(text, spans, lists=None):
    """Plain text with the formatting that changes its meaning written out: 10⁻³, H₂O, ~~crossed out~~, lists."""
    out, pos = [], 0
    meaningful = [r for r in spans or () if r[2] in ("sup", "sub", "s")]
    for a, e, kind in sorted((max(0, int(a)), min(len(text), int(e)), k) for a, e, k in meaningful):
        if e <= a or a < pos:
            continue
        out += [text[pos:a], _mark(text[a:e], kind)]
        pos = e
    out.append(text[pos:])
    lines = "".join(out).split("\n")
    kinds = {int(i): k for i, k in lists or ()}
    n = 0
    for i, line in enumerate(lines):
        k = kinds.get(i)
        n = n + 1 if k == "number" else 0
        if k:
            lines[i] = f"{n}. {line}" if k == "number" else f"• {line}"
    return "\n".join(lines)


def item_text(data):
    return text_with(data.get("text", ""), spans_of(data), data.get("lists"))


def html_text(html):
    d = QTextDocument()
    d.setHtml(html or "")
    return text_with(d.toPlainText(), ranges(d), list_lines(d))


def has_marks(html):
    html = html or ""
    return "vertical-align:su" in html or "line-through" in html or "<li" in html
