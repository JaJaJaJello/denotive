"""Left sidebar: brand, search, navigation, and the project → board tree."""
from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QInputDialog, QLabel, QMenu, QPushButton, QStyle,
                               QStyledItemDelegate, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import context, theme
from ..settings import settings
from .common import label, section_label, tool_button

ROLE_KIND = Qt.UserRole
ROLE_ID = Qt.UserRole + 1
ROLE_COLOR = Qt.UserRole + 2
ROLE_COUNT = Qt.UserRole + 3


class TreeDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        kind = index.data(ROLE_KIND)
        return QSize(option.rect.width(), 34 if kind == "project" else 30)

    def paint(self, p, option, index):
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = option.rect.adjusted(2, 1, -6, -1)
        kind = index.data(ROLE_KIND)
        selected = option.state & QStyle.State_Selected
        hover = option.state & QStyle.State_MouseOver
        if selected:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.SURFACE2))
            p.drawRoundedRect(r, 7, 7)
            p.setBrush(QColor(theme.ACCENT))
            p.drawRoundedRect(r.x(), r.y() + 7, 3, r.height() - 14, 1.5, 1.5)
        elif hover:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.SURFACE))
            p.drawRoundedRect(r, 7, 7)
        text = index.data(Qt.DisplayRole) or ""
        count = index.data(ROLE_COUNT)
        x = r.x() + 6
        if kind == "project":
            expanded = option.state & QStyle.State_Open
            p.drawPixmap(x - 2, r.center().y() - 6, theme.icon_pixmap("chevdown" if expanded else "chevright", 12,
                                                                      theme.FAINT))
            x += 14
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(index.data(ROLE_COLOR) or theme.ACCENT))
            p.drawRoundedRect(x, r.center().y() - 5, 10, 10, 3, 3)
            x += 18
            f = theme.ui_font(13.5, QFont.DemiBold)
            p.setFont(f)
            p.setPen(QColor(theme.TEXT))
        else:
            x += 14
            p.drawPixmap(x, r.center().y() - 8, theme.icon_pixmap("board", 16, theme.MUTED if not selected else theme.TEXT))
            x += 24
            p.setFont(theme.ui_font(13))
            p.setPen(QColor(theme.TEXT if selected else "#c9ccd3"))
        cw = 0
        if count:
            fs = theme.ui_font(11)
            p.setFont(fs)
            cs = str(count)
            cw = p.fontMetrics().horizontalAdvance(cs) + 8
            p.setPen(QColor(theme.FAINT))
            p.drawText(r.adjusted(0, 0, -6, 0), Qt.AlignRight | Qt.AlignVCenter, cs)
            p.setFont(theme.ui_font(13.5, QFont.DemiBold) if kind == "project" else theme.ui_font(13))
            p.setPen(QColor(theme.TEXT if (kind == "project" or selected) else "#c9ccd3"))
        tr = r.adjusted(x - r.x(), 0, -cw - 8, 0)
        p.drawText(tr, Qt.AlignLeft | Qt.AlignVCenter, p.fontMetrics().elidedText(text, Qt.ElideRight, tr.width()))
        p.restore()


class ProjectTree(QTreeWidget):
    moved = Signal(str, str, str, int)  # kind, id, new parent project id, new index

    def __init__(self):
        super().__init__()
        self.setHeaderHidden(True)
        self.setColumnCount(1)
        self.setIndentation(0)
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(False)
        self.setItemDelegate(TreeDelegate(self))
        self.setMouseTracking(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setExpandsOnDoubleClick(False)
        self.setAnimated(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setStyleSheet("QTreeView::branch { image: none; border-image: none; }")

    def mimeData(self, items):
        """Boards dragged out of the tree can be dropped on a canvas as a link to that board."""
        md = super().mimeData(items)
        if items and items[0].data(0, ROLE_KIND) == "board":
            md.setData("application/x-denotive-board-ref", items[0].data(0, ROLE_ID).encode())
        return md

    def dropEvent(self, e):
        src = self.currentItem()
        tgt = self.itemAt(e.position().toPoint())
        if src is None:
            e.ignore()
            return
        pos = self.dropIndicatorPosition()
        kind = src.data(0, ROLE_KIND)
        if kind == "board":
            if tgt is None:
                e.ignore()
                return
            if tgt.data(0, ROLE_KIND) == "project":
                parent, index = tgt, tgt.childCount() if pos == QAbstractItemView.OnItem else 0
                if pos == QAbstractItemView.AboveItem:
                    # dropping above a project means "end of the previous project"
                    i = self.indexOfTopLevelItem(tgt)
                    if i > 0:
                        parent = self.topLevelItem(i - 1)
                        index = parent.childCount()
            else:
                parent = tgt.parent()
                index = parent.indexOfChild(tgt) + (1 if pos == QAbstractItemView.BelowItem else 0)
            self.moved.emit("board", src.data(0, ROLE_ID), parent.data(0, ROLE_ID), index)
        elif kind == "project":
            if tgt is not None and tgt.data(0, ROLE_KIND) == "board":
                tgt = tgt.parent()
            index = self.topLevelItemCount() if tgt is None else self.indexOfTopLevelItem(tgt) + (
                1 if pos == QAbstractItemView.BelowItem else 0)
            self.moved.emit("project", src.data(0, ROLE_ID), "", index)
        e.setDropAction(Qt.CopyAction)
        e.accept()


class Sidebar(QWidget):
    board_selected = Signal(str)
    nav = Signal(str)
    search = Signal()

    def __init__(self, main):
        super().__init__()
        self.main = main
        self.setObjectName("Sidebar")
        self.setMinimumWidth(220)
        self.setMaximumWidth(420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 14, 10, 10)
        lay.setSpacing(6)
        brand = QHBoxLayout()
        logo = QLabel()
        from ..main import resource
        pm = QPixmap(resource("icon.png"))
        if not pm.isNull():
            logo.setPixmap(pm.scaled(30, 30, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        brand.addWidget(logo)
        brand.addWidget(label("DeNotive", "Brand"))
        brand.addStretch(1)
        st = tool_button("settings", "Settings & library")
        st.clicked.connect(lambda: self.nav.emit("settings"))
        brand.addWidget(st)
        lay.addLayout(brand)
        lay.addSpacing(6)
        sb = QPushButton("  Search everything…")
        sb.setIcon(theme.icon("search", 15, theme.MUTED))
        sb.setStyleSheet(f"QPushButton {{ text-align: left; color: {theme.MUTED}; background: {theme.SURFACE}; "
                         f"border-radius: 9px; padding: 8px 10px; }} QPushButton:hover {{ background: {theme.SURFACE2}; }}")
        sb.setCursor(Qt.PointingHandCursor)
        sb.clicked.connect(self.search.emit)
        hint = QLabel("Ctrl+K", sb)
        hint.setObjectName("Hint")
        hint.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._hint = hint
        self._sb = sb
        lay.addWidget(sb)
        lay.addSpacing(4)
        self.nav_btns = {}
        for key, ic, text in (("home", "home", "Home"), ("timeline", "timeline", "Timeline"),
                              ("trash", "trash", "Trash")):
            b = QPushButton("  " + text)
            b.setObjectName("Nav")
            b.setIcon(theme.icon(ic, 17, theme.MUTED))
            b.setIconSize(QSize(17, 17))
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.nav.emit(k))
            lay.addWidget(b)
            self.nav_btns[key] = b
        lay.addSpacing(10)
        hdr = QHBoxLayout()
        hdr.addWidget(section_label("Projects"))
        hdr.addStretch(1)
        add = tool_button("add", "New project", size=15)
        add.clicked.connect(self.new_project)
        hdr.addWidget(add)
        lay.addLayout(hdr)
        self.tree = ProjectTree()
        self.tree.itemClicked.connect(self._clicked)
        self.tree.itemDoubleClicked.connect(self._double)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.moved.connect(self._moved)
        self.tree.itemExpanded.connect(lambda it: self._save_collapsed())
        self.tree.itemCollapsed.connect(lambda it: self._save_collapsed())
        lay.addWidget(self.tree, 1)
        self.foot = label("", "Hint")
        self.foot.setWordWrap(True)
        lay.addWidget(self.foot)
        self.current_board = None
        self.current_project = None

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._hint.adjustSize()
        self._hint.move(self._sb.width() - self._hint.width() - 12, (self._sb.height() - self._hint.height()) // 2)

    def set_nav(self, key):
        for k, b in self.nav_btns.items():
            b.setChecked(k == key)
        if key != "board":
            self.tree.clearSelection()

    # ------------------------------------------------------------------ data
    def rebuild(self):
        st = context.store
        counts = st.board_counts()
        collapsed = set(settings.get("collapsed_projects", []))
        self.tree.blockSignals(True)
        self.tree.clear()
        sel = None
        for p in st.projects():
            pi = QTreeWidgetItem([p["name"]])
            pi.setData(0, ROLE_KIND, "project")
            pi.setData(0, ROLE_ID, p["id"])
            pi.setData(0, ROLE_COLOR, p["color"])
            pi.setFlags(pi.flags() | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
            boards = st.boards(p["id"])
            pi.setData(0, ROLE_COUNT, len(boards) if boards else None)
            self.tree.addTopLevelItem(pi)
            if p["id"] == self.current_project:
                sel = pi
            for b in boards:
                bi = QTreeWidgetItem([b["name"]])
                bi.setData(0, ROLE_KIND, "board")
                bi.setData(0, ROLE_ID, b["id"])
                bi.setData(0, ROLE_COUNT, counts.get(b["id"]) or None)
                bi.setFlags((bi.flags() | Qt.ItemIsDragEnabled) & ~Qt.ItemIsDropEnabled)
                pi.addChild(bi)
                if b["id"] == self.current_board:
                    sel = bi
            pi.setExpanded(p["id"] not in collapsed)
        if sel is not None:
            self.tree.setCurrentItem(sel)
        self.tree.blockSignals(False)
        s = st.stats()
        self.foot.setText(f"{s['evidence']} evidence · {s['files']} files · {s['strings']} strings")
        self.foot.setToolTip(str(st.root))

    def update_counts(self):
        counts = context.store.board_counts()
        for i in range(self.tree.topLevelItemCount()):
            pi = self.tree.topLevelItem(i)
            for j in range(pi.childCount()):
                bi = pi.child(j)
                bi.setData(0, ROLE_COUNT, counts.get(bi.data(0, ROLE_ID)) or None)
        s = context.store.stats()
        self.foot.setText(f"{s['evidence']} evidence · {s['files']} files · {s['strings']} strings")

    def select_project(self, pid):
        """Highlights a project row (its map is open)."""
        self.current_board, self.current_project = None, pid
        self.set_nav("board")
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            pi = self.tree.topLevelItem(i)
            if pi.data(0, ROLE_ID) == pid:
                self.tree.setCurrentItem(pi)
        self.tree.blockSignals(False)

    def select_board(self, bid):
        self.current_board, self.current_project = bid, None
        self.set_nav("board")
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            pi = self.tree.topLevelItem(i)
            for j in range(pi.childCount()):
                if pi.child(j).data(0, ROLE_ID) == bid:
                    pi.setExpanded(True)
                    self.tree.setCurrentItem(pi.child(j))
        self.tree.blockSignals(False)

    def _save_collapsed(self):
        out = []
        for i in range(self.tree.topLevelItemCount()):
            pi = self.tree.topLevelItem(i)
            if not pi.isExpanded():
                out.append(pi.data(0, ROLE_ID))
        settings.set("collapsed_projects", out)

    # ------------------------------------------------------------------ interaction
    def _clicked(self, item, col):
        if item.data(0, ROLE_KIND) == "board":
            self.board_selected.emit(item.data(0, ROLE_ID))
            return
        x = self.tree.viewport().mapFromGlobal(QCursor.pos()).x()
        if x < 30:  # the chevron folds the project
            item.setExpanded(not item.isExpanded())
            return
        item.setExpanded(True)
        self.main.open_project_map(item.data(0, ROLE_ID))

    def _double(self, item, col):
        if item.data(0, ROLE_KIND) == "project":
            item.setExpanded(not item.isExpanded())

    def _moved(self, kind, oid, parent_id, index):
        st = context.store
        if kind == "board":
            boards = [b for b in st.boards(parent_id) if b["id"] != oid]
            index = max(0, min(index, len(boards)))
            boards.insert(index, {"id": oid})
            with st.tx():
                for n, b in enumerate(boards):
                    fields = {"sort": n + 1}
                    if b["id"] == oid:
                        fields["project_id"] = parent_id
                    st.update_board(b["id"], touch=False, **fields)
        else:
            ps = [p for p in st.projects() if p["id"] != oid]
            index = max(0, min(index, len(ps)))
            ps.insert(index, {"id": oid})
            with st.tx():
                for n, p in enumerate(ps):
                    st.db.execute("UPDATE projects SET sort=? WHERE id=?", (n + 1, p["id"]))
        QTimer.singleShot(0, self.rebuild)
        QTimer.singleShot(0, self.main.board_meta_changed)

    def new_project(self):
        name, ok = QInputDialog.getText(self, "New project", "Project name:")
        if ok and name.strip():
            p = context.store.create_project(name.strip())
            b = context.store.create_board(p["id"], "Board 1")
            self.rebuild()
            self.board_selected.emit(b["id"])

    def new_board(self, project_id):
        n = len(context.store.boards(project_id)) + 1
        name, ok = QInputDialog.getText(self, "New board", "Board name:", text=f"Board {n}")
        if ok and name.strip():
            b = context.store.create_board(project_id, name.strip())
            self.rebuild()
            self.board_selected.emit(b["id"])

    def _menu(self, pos):
        item = self.tree.itemAt(pos)
        m = QMenu(self)
        st = context.store
        if item is None:
            m.addAction(theme.icon("add", 16), "New project…", self.new_project)
        elif item.data(0, ROLE_KIND) == "project":
            pid = item.data(0, ROLE_ID)
            m.addAction(theme.icon("board", 16), "Project map", lambda: self.main.open_project_map(pid))
            m.addAction(theme.icon("add", 16), "New board…", lambda: self.new_board(pid))
            m.addAction(theme.icon("edit", 16), "Rename…", lambda: self._rename("project", pid, item.text(0)))
            col = m.addMenu(theme.icon("palette", 16), "Colour")
            for c in theme.PROJECT_COLORS:
                col.addAction(theme.color_dot_icon(c), c, lambda cc=c: (st.update_project(pid, color=cc),
                                                                       self.rebuild(),
                                                                       self.main.board_meta_changed()))
            m.addAction(theme.icon("timeline", 16), "Project timeline", lambda: self.main.open_timeline(project_id=pid))
            m.addAction(theme.icon("search", 16), "Search this project",
                        lambda: self.main.open_search("project", project_id=pid))
            ex = m.addMenu(theme.icon("export", 16), "Export project")
            ex.addAction(theme.icon("pdf", 16), "PDF…", lambda: self.main.export_project(pid, "pdf"))
            ex.addAction(theme.icon("slides", 16), "PowerPoint…", lambda: self.main.export_project(pid, "pptx"))
            m.addSeparator()
            m.addAction(theme.icon("delete", 16), "Delete project…", lambda: self.main.delete_project(pid))
        else:
            bid = item.data(0, ROLE_ID)
            m.addAction(theme.icon("board", 16), "Open", lambda: self.board_selected.emit(bid))
            m.addAction(theme.icon("edit", 16), "Rename…", lambda: self._rename("board", bid, item.text(0)))
            mv = m.addMenu(theme.icon("forward", 16), "Move to project")
            cur = item.parent().data(0, ROLE_ID)
            for p in st.projects():
                if p["id"] != cur:
                    mv.addAction(theme.color_dot_icon(p["color"]), p["name"],
                                 lambda pid=p["id"]: self._moved("board", bid, pid, 10 ** 6))
            if mv.isEmpty():
                mv.setEnabled(False)
            m.addAction(theme.icon("timeline", 16), "Board timeline", lambda: self.main.open_timeline(board_id=bid))
            ex = m.addMenu(theme.icon("export", 16), "Export")
            ex.addAction(theme.icon("pdf", 16), "PDF…", lambda: self.main.export_board(bid, "pdf"))
            ex.addAction(theme.icon("slides", 16), "PowerPoint…", lambda: self.main.export_board(bid, "pptx"))
            ex.addAction(theme.icon("print", 16), "Print…", lambda: self.main.export_board(bid, "print"))
            m.addSeparator()
            m.addAction(theme.icon("delete", 16), "Delete board…", lambda: self.main.delete_board(bid))
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def _rename(self, kind, oid, cur):
        name, ok = QInputDialog.getText(self, "Rename", "Name:", text=cur)
        if ok and name.strip():
            if kind == "project":
                context.store.update_project(oid, name=name.strip())
            else:
                context.store.update_board(oid, name=name.strip())
            self.rebuild()
            self.main.board_meta_changed()
