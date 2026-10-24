"""Lightweight syntax highlighting for code blocks and attached source files."""
import re

from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat

PY_KEYWORDS = (
    "False None True and as assert async await break class continue def del elif else except finally for from "
    "global if import in is lambda nonlocal not or pass raise return try while with yield match case self"
).split()
C_KEYWORDS = (
    "auto break case char const continue default do double else enum extern float for goto if int long register "
    "return short signed sizeof static struct switch typedef union unsigned void volatile while class public "
    "private protected new delete function var let const import export from true false null this end "
    "elseif endfunction"
).split()
BUILTINS = "print len range enumerate zip map filter sorted min max sum abs open int float str list dict set tuple " \
           "isinstance super np plt pd".split()


def _fmt(color, bold=False, italic=False):
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.Bold)
    if italic:
        f.setFontItalic(True)
    return f


class CodeHighlighter(QSyntaxHighlighter):
    def __init__(self, document, lang="py", dark=True):
        super().__init__(document)
        kw = PY_KEYWORDS if lang in ("py", "pyw", "ipynb") else C_KEYWORDS
        pal = dict(kw="#c586c0", builtin="#4ec9b0", string="#ce9178", comment="#6a9955", number="#b5cea8",
                   deco="#dcdcaa", func="#dcdcaa") if dark else \
            dict(kw="#af00db", builtin="#267f99", string="#a31515", comment="#008000", number="#098658",
                 deco="#795e26", func="#795e26")
        comment = r"#[^\n]*" if lang in ("py", "pyw", "ipynb", "r", "sh", "yaml", "yml", "toml", "ini", "cfg") \
            else r"//[^\n]*|%[^\n]*" if lang == "m" else r"//[^\n]*|#[^\n]*"
        self.rules = [
            (re.compile(r"\b(" + "|".join(kw) + r")\b"), _fmt(pal["kw"], bold=False)),
            (re.compile(r"\b(" + "|".join(BUILTINS) + r")\b"), _fmt(pal["builtin"])),
            (re.compile(r"\b[0-9]+(\.[0-9]*)?([eE][-+]?[0-9]+)?\b"), _fmt(pal["number"])),
            (re.compile(r"@\w+"), _fmt(pal["deco"])),
            (re.compile(r"\b(def|class|function)\s+(\w+)"), _fmt(pal["func"])),
            (re.compile(r"'[^'\\\n]*(\\.[^'\\\n]*)*'|\"[^\"\\\n]*(\\.[^\"\\\n]*)*\""), _fmt(pal["string"])),
            (re.compile(comment), _fmt(pal["comment"], italic=True)),
        ]
        self.tri = re.compile(r'"""|\'\'\'')
        self.tri_fmt = _fmt(pal["string"])

    def highlightBlock(self, text):
        for rx, fmt in self.rules:
            for m in rx.finditer(text):
                if rx.pattern.startswith(r"\b(def|class"):
                    self.setFormat(m.start(2), m.end(2) - m.start(2), fmt)
                else:
                    self.setFormat(m.start(), m.end() - m.start(), fmt)
        # triple-quoted strings spanning lines (block state 1 = inside one)
        pos = 0
        if self.previousBlockState() == 1:
            m = self.tri.search(text)
            if not m:
                self.setFormat(0, len(text), self.tri_fmt)
                self.setCurrentBlockState(1)
                return
            self.setFormat(0, m.end(), self.tri_fmt)
            pos = m.end()
        self.setCurrentBlockState(0)
        while True:
            m = self.tri.search(text, pos)
            if not m:
                break
            m2 = self.tri.search(text, m.end())
            if not m2:
                self.setFormat(m.start(), len(text) - m.start(), self.tri_fmt)
                self.setCurrentBlockState(1)
                break
            self.setFormat(m.start(), m2.end() - m.start(), self.tri_fmt)
            pos = m2.end()
