"""Home: greeting, library stats, recent boards and the latest evidence."""
from datetime import datetime

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from .. import context, theme
from .common import clear_layout, label, nice_time, push_button, section_label


class BoardTile(QFrame):
    clicked = Signal(str)

    def __init__(self, b, thumbs):
        super().__init__()
        self.b = b
        self.thumbs = thumbs
        self.setFixedHeight(168)
        self.setMinimumWidth(240)
        self.setMaximumWidth(420)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)
        self._hover = False

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.b["id"])

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QColor(theme.BORDER2 if self._hover else theme.BORDER))
        p.setBrush(QColor(theme.SURFACE2 if self._hover else theme.SURFACE))
        p.drawRoundedRect(r, 12, 12)
        # thumbnail strip (latest evidence on the board)
        strip = QRectF(r.x() + 12, r.y() + 12, r.width() - 24, 84)
        clip = QPainterPath()
        clip.addRoundedRect(strip, 8, 8)
        p.save()
        p.setClipPath(clip)
        p.fillRect(strip, QColor("#0f1217"))
        n = len(self.thumbs)
        if n:
            w = strip.width() / min(3, n)
            for i, th in enumerate(self.thumbs[:3]):
                pm = context.images.get(th, 256)
                cell = QRectF(strip.x() + i * w, strip.y(), w - (2 if i < n - 1 else 0), strip.height())
                if pm is not None:
                    s = max(cell.width() / pm.width(), cell.height() / pm.height())
                    tw, thh = pm.width() * s, pm.height() * s
                    p.save()
                    p.setClipRect(cell, Qt.IntersectClip)
                    p.drawPixmap(QRectF(cell.center().x() - tw / 2, cell.center().y() - thh / 2, tw, thh), pm,
                                 QRectF(pm.rect()))
                    p.restore()
        else:
            p.setPen(QColor(255, 255, 255, 30))
            p.setFont(theme.icon_font(30))
            p.drawText(strip, Qt.AlignCenter, theme.GLYPHS["pin"])
        p.restore()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self.b.get("project_color") or theme.ACCENT))
        p.drawRoundedRect(QRectF(r.x() + 14, r.y() + 108, 8, 8), 2, 2)
        p.setFont(theme.ui_font(11))
        p.setPen(QColor(theme.MUTED))
        p.drawText(QRectF(r.x() + 28, r.y() + 102, r.width() - 40, 20), Qt.AlignLeft | Qt.AlignVCenter,
                   self.b.get("project_name", ""))
        p.setFont(theme.ui_font(14, QFont.DemiBold))
        p.setPen(QColor(theme.TEXT))
        p.drawText(QRectF(r.x() + 14, r.y() + 120, r.width() - 28, 24), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(self.b["name"], Qt.ElideRight, int(r.width() - 28)))
        p.setFont(theme.ui_font(11))
        p.setPen(QColor(theme.FAINT))
        last = self.b.get("last") or self.b.get("updated")
        p.drawText(QRectF(r.x() + 14, r.y() + 142, r.width() - 28, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   f"{self.b.get('n') or 0} items · {nice_time(last)}")
        p.end()


class HomePage(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.setObjectName("Page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        body.setObjectName("Page")
        self.v = QVBoxLayout(body)
        self.v.setContentsMargins(44, 36, 44, 36)
        self.v.setSpacing(14)
        scroll.setWidget(body)
        outer.addWidget(scroll)
        context.images.loaded.connect(lambda _: self.update_tiles())
        self.tiles = []

    def update_tiles(self):
        for t in self.tiles:
            t.update()

    def refresh(self):
        clear_layout(self.v)
        self.tiles = []
        st = context.store
        hour = datetime.now().hour
        greet = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
        self.v.addWidget(label(f"{greet}, detective.", "BigTitle"))
        s = st.stats()
        self.v.addWidget(label(f"{datetime.now():%A, %B %d}  ·  {s['projects']} projects · {s['boards']} boards · "
                               f"{s['evidence']} evidence · {s['files']} files · {s['strings']} strings", "Muted"))
        acts = QHBoxLayout()
        a1 = push_button("New project", "add", primary=True)
        a1.clicked.connect(self.main.sidebar.new_project)
        a2 = push_button("Timeline", "timeline")
        a2.clicked.connect(lambda: self.main.open_timeline())
        a3 = push_button("Search", "search")
        a3.clicked.connect(lambda: self.main.open_search("all"))
        for b in (a1, a2, a3):
            acts.addWidget(b)
        acts.addStretch(1)
        self.v.addLayout(acts)
        self.v.addSpacing(10)
        boards = st.recent_boards(9)
        if not boards:
            box = QFrame()
            box.setObjectName("Card")
            bl = QVBoxLayout(box)
            bl.setContentsMargins(30, 30, 30, 30)
            bl.addWidget(label("Start your first case", "Title"))
            bl.addWidget(label("Projects hold boards. Boards hold evidence — slides you build like PowerPoint — "
                               "which you pin anywhere and tie together with red string.", "Muted", wrap=True))
            b = push_button("Create a project", "add", primary=True)
            b.clicked.connect(self.main.sidebar.new_project)
            bl.addWidget(b, 0, Qt.AlignLeft)
            self.v.addWidget(box)
        else:
            self.v.addWidget(section_label("Recent boards"))
            grid = QGridLayout()
            grid.setSpacing(14)
            for i, b in enumerate(boards):
                thumbs = [r["thumb"] for r in st.feed(board_id=b["id"], limit=3) if r.get("thumb")]
                t = BoardTile(b, thumbs)
                t.clicked.connect(self.main.open_board)
                grid.addWidget(t, i // 3, i % 3)
                self.tiles.append(t)
            grid.setColumnStretch(3, 1)
            self.v.addLayout(grid)
            self.v.addSpacing(10)
            recent = st.feed(limit=8)
            if recent:
                h = QHBoxLayout()
                h.addWidget(section_label("Latest evidence"))
                h.addStretch(1)
                more = push_button("Open timeline", name="Ghost")
                more.clicked.connect(lambda: self.main.open_timeline())
                h.addWidget(more)
                self.v.addLayout(h)
                for r in recent:
                    row = push_button("")
                    row.setObjectName("Ghost")
                    row.setText(f"   {r['title'] or 'Untitled'}")
                    row.setIcon(theme.color_dot_icon(r.get("project_color") or theme.ACCENT, 12))
                    row.setStyleSheet("QPushButton { text-align: left; padding: 8px 10px; }")
                    row.setToolTip(f"{r['project_name']} › {r['board_name']} · {nice_time(r['updated'])}")
                    row.clicked.connect(lambda _=False, rr=r: self.main.open_board(rr["board_id"], focus_item=rr["id"]))
                    line = QHBoxLayout()
                    line.addWidget(row, 1)
                    line.addWidget(label(f"{r['project_name']} › {r['board_name']}", "Faint"))
                    line.addSpacing(16)
                    t = label(nice_time(r["updated"]), "Muted")
                    t.setMinimumWidth(150)
                    t.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    line.addWidget(t)
                    self.v.addLayout(line)
        self.v.addStretch(1)
