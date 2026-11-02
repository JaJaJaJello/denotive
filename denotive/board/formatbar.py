"""The little toolbar that floats above a note, sticky or shape while you type in it."""
from PySide6.QtCore import QPoint, QPointF, QSize, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout

from .. import theme
from ..widgets.common import tool_button, vsep

BUTTONS = [("bold", "b", "Bold (Ctrl+B)"), ("italic", "i", "Italic (Ctrl+I)"), ("underline", "u", "Underline (Ctrl+U)"),
           ("strike", "s", "Strikethrough (Ctrl+Shift+X)"), None,
           ("bullets", "bullet", "Bulleted list (Ctrl+Shift+8)"), ("list", "number", "Numbered list (Ctrl+Shift+7)"),
           None, ("superscript", "sup", "Superscript (Ctrl+=)"), ("subscript", "sub", "Subscript (Ctrl+-)")]


class FormatBar(QFrame):
    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.item = None
        self.setObjectName("Overlay")
        self.setStyleSheet(f"QFrame#Overlay {{ background: {theme.SURFACE2}; border: 1px solid {theme.BORDER}; "
                           "border-radius: 8px; }")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(5, 4, 5, 4)
        lay.setSpacing(2)
        self.buttons = {}
        for spec in BUTTONS:
            if spec is None:
                lay.addWidget(vsep(18))
                continue
            icon, action, tip = spec
            b = tool_button(icon, tip, True, 16)
            b.setFixedSize(QSize(28, 26))
            b.setFocusPolicy(Qt.NoFocus)  # a click must not end the typing
            b.clicked.connect(lambda _=False, a=action: self._apply(a))
            lay.addWidget(b)
            self.buttons[action] = b
        self.adjustSize()
        self.hide()
        view.viewport_changed.connect(self.place)

    def attach(self, item):
        """Follows the item being typed in; None when typing ends."""
        self.item = item
        self.setVisible(item is not None)
        if item is not None:
            self.raise_()
            self.place()
            self.refresh()

    def _apply(self, action):
        if self.item is not None and self.item.editor is not None:
            self.item.editor.format(action)

    def refresh(self):
        if self.item is None or self.item.editor is None:
            return
        on = self.item.editor.active()
        for action, b in self.buttons.items():
            b.setChecked(action in on)

    def place(self):
        """Sits just above the item, or just below it when it's near the top, always inside the canvas."""
        if self.item is None:
            return
        r = self.item.scene_rect()
        vp = self.view.viewport()
        top = vp.mapTo(self.view, self.view.mapFromScene(QPointF(r.left(), r.top())))
        bottom = vp.mapTo(self.view, self.view.mapFromScene(QPointF(r.left(), r.bottom())))
        y = top.y() - self.height() - 8
        if y < 6:
            y = bottom.y() + 8
        x = max(6, min(top.x(), self.view.width() - self.width() - 6))
        self.move(QPoint(x, max(6, min(y, self.view.height() - self.height() - 6))))
