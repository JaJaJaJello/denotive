"""Main window: sidebar + page stack (home, board, evidence editor, timeline, trash) + search palette."""
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QPoint, QTimer, Qt
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtPrintSupport import QPrinter, QPrintPreviewDialog
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QMainWindow, QMessageBox, QSplitter,
                               QStackedWidget, QWidget)

from . import context, theme
from .board.items import EvidenceCard
from .board.page import BoardPage
from .settings import settings
from .slide.editor import EditorPage
from .widgets.common import Toast
from .widgets.home import HomePage
from .widgets.search import SearchOverlay
from .widgets.sidebar import Sidebar
from .widgets.timeline import TimelinePage
from .widgets.trash import TrashPage


class MainWindow(QMainWindow):
    def __init__(self, window_board=None):
        super().__init__()
        # a second window opened on a board ("Open in new window"): it leaves the main window's state alone
        self.secondary = bool(window_board)
        self.setWindowTitle("DeNotive")
        self.setMinimumSize(1060, 660)
        self.resize(1560, 940)
        self._open_library(settings["library"])

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.split = QSplitter(Qt.Horizontal)
        self.split.setChildrenCollapsible(False)
        self.sidebar = Sidebar(self)
        self.stack = QStackedWidget()
        self.split.addWidget(self.sidebar)
        self.split.addWidget(self.stack)
        self.split.setStretchFactor(1, 1)
        self.split.setSizes([260, 1300])
        lay.addWidget(self.split)

        self.home = HomePage(self)
        self.board_page = BoardPage(self)
        self.editor = EditorPage(self)
        self.timeline = TimelinePage(self)
        self.trash = TrashPage(self)
        for w in (self.home, self.board_page, self.editor, self.timeline, self.trash):
            self.stack.addWidget(w)
        self.editor.finished.connect(self._editor_finished)

        self.sidebar.board_selected.connect(lambda bid: self.open_board(bid, fit=True))
        self.sidebar.nav.connect(self._nav)
        self.sidebar.search.connect(lambda: self.open_search("all"))

        self.search = SearchOverlay(root)
        self.search.activated.connect(self.navigate_result)
        self.toast_w = Toast(root)
        self._counts_timer = QTimer(self)
        self._counts_timer.setSingleShot(True)
        self._counts_timer.setInterval(400)
        self._counts_timer.timeout.connect(self.sidebar.update_counts)

        for seq, fn in (("Ctrl+K", lambda: self.open_search("all")), ("Ctrl+F", lambda: self.open_search("auto")),
                        ("Ctrl+Shift+F", lambda: self.open_search("all")), ("Ctrl+T", lambda: self.open_timeline()),
                        ("Ctrl+Shift+N", self.sidebar.new_project), ("Ctrl+N", self.new_board_here),
                        ("Ctrl+,", lambda: self._nav("settings")), ("F11", self._toggle_full)):
            s = QShortcut(QKeySequence(seq), self)
            s.setContext(Qt.WindowShortcut)
            s.activated.connect(fn)

        self.apply_settings()
        self.sidebar.rebuild()
        geo = settings.get("geometry")
        if geo:
            self.restoreGeometry(QByteArray.fromHex(geo.encode()))
        if self.secondary:
            self._place_on_other_screen()
        sizes = settings.get("sidebar_sizes")
        if sizes:
            self.split.setSizes(sizes)
        # another DeNotive window may change the library: keep what's on screen current
        self._seen_version = context.store.data_version()
        self._lib_fp = context.store.library_fingerprint()
        self._board_fp = None
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(1500)
        self._sync_timer.timeout.connect(self._check_outside_changes)
        self._sync_timer.start()
        if window_board and context.store.board(window_board):
            self.open_board(window_board, fit=True)
            return
        last = settings.get("last_board")
        b = context.store.board(last) if last else None
        if b and not b.get("deleted") and context.store.project(b["project_id"]) and \
                not context.store.project(b["project_id"]).get("deleted"):
            self.open_board(last)
        else:
            self.go_home()

    # ------------------------------------------------------------------ library
    def _open_library(self, path):
        try:
            st = context.open_library(path)
        except Exception as ex:
            QMessageBox.critical(None, "DeNotive", f"Couldn't open the library at\n{path}\n\n{ex}\n\n"
                                                   "Falling back to the default location.")
            from .settings import DEFAULT_LIBRARY
            st = context.open_library(DEFAULT_LIBRARY)
            settings.set("library", str(DEFAULT_LIBRARY))
        if self.secondary:
            return
        st.daily_backup()
        if not st.projects() and not st.meta("seeded"):
            from .seed import create_welcome
            try:
                bid = create_welcome()
                settings.set("last_board", bid)
            except Exception:
                import traceback
                traceback.print_exc()
            st.set_meta("seeded", "1")

    def switch_library(self, path):
        if not self._leave_editor():
            return
        self.board_page.close_board()
        self._open_library(path)
        settings.set("library", str(path))
        settings.set("last_board", None)
        self.sidebar.current_board = None
        self.sidebar.rebuild()
        self.go_home()
        self.toast("Library switched")

    def apply_settings(self):
        self.board_page.view.wheel_zooms = bool(settings.get("wheel_zooms", True))

    # ------------------------------------------------------------------ navigation
    def _leave_editor(self):
        if self.stack.currentWidget() is self.editor and self.editor.dirty:
            r = QMessageBox.question(self, "Unsaved evidence", "Save the evidence you're editing first?",
                                     QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
            if r == QMessageBox.Cancel:
                return False
            if r == QMessageBox.Save:
                self.editor.save(True)
            else:
                self.editor.dirty = False
        return True

    def _show(self, page):
        if self.stack.currentWidget() is self.board_page and page is not self.board_page:
            self.board_page.save_view_state()
        self.stack.setCurrentWidget(page)
        self.search.hide()

    def _nav(self, key):
        if key == "settings":
            from .widgets.dialogs import SettingsDialog
            SettingsDialog(self).exec()
            return
        if not self._leave_editor():
            return
        if key == "home":
            self.go_home()
        elif key == "timeline":
            self.open_timeline()
        elif key == "trash":
            self.trash.refresh()
            self._show(self.trash)
            self.sidebar.set_nav("trash")

    def go_home(self):
        self.home.refresh()
        self._show(self.home)
        self.sidebar.set_nav("home")
        self.setWindowTitle("DeNotive")

    def open_board(self, board_id, focus_item=None, fit=False):
        """Shows a board. fit=True zooms to everything on it (what clicking it in the sidebar does)."""
        if not self._leave_editor():
            return
        bp = self.board_page
        same = bp.board is not None and bp.board["id"] == board_id and bp.scene is not None
        if same and not context.store.is_map(bp.board):
            self._show(bp)
            if focus_item:
                bp.focus_item(focus_item)
            elif fit:
                bp.fit(max_zoom=1.0)
        else:
            if not bp.open_board(board_id, focus_item, fit):
                return
            self._show(bp)
        st = context.store
        if st.is_map(bp.board):
            self.sidebar.select_project(bp.board["project_id"])
            p = st.project(bp.board["project_id"])
            self.setWindowTitle(f"{p['name'] if p else ''} map — DeNotive")
        else:
            self.sidebar.select_board(board_id)
            self.setWindowTitle(f"{bp.board['name']} — DeNotive")
        if not self.secondary:
            settings.set("last_board", board_id)
        self._baseline()
        bp.view.setFocus()

    def open_project_map(self, project_id, fit=True):
        m = context.store.project_map(project_id)
        self.open_board(m["id"], fit=fit)

    # ------------------------------------------------------------------ more than one window
    def open_in_new_window(self, board_id):
        """Opens a board (or a project map) in a second DeNotive window, e.g. on the other screen."""
        if getattr(sys, "frozen", False):
            cmd = [sys.executable]
        else:
            exe = Path(sys.executable)
            quiet = exe.with_name("pythonw.exe")
            cmd = [str(quiet if quiet.exists() else exe), str(Path(__file__).resolve().parent.parent / "run.py")]
        try:
            subprocess.Popen(cmd + ["--window", board_id], close_fds=True)
            self.toast("Opening in a new window…")
        except OSError as ex:
            QMessageBox.warning(self, "DeNotive", f"Couldn't open a new window:\n{ex}")

    def _place_on_other_screen(self):
        """A second window opens on another screen when there is one, else just offset from the first."""
        self.setWindowState(Qt.WindowNoState)
        here = QGuiApplication.screenAt(self.geometry().center()) or QGuiApplication.primaryScreen()
        other = next((s for s in QGuiApplication.screens() if s is not here), None)
        if other is not None:
            self.setGeometry(other.availableGeometry().adjusted(30, 30, -30, -30))
        else:
            self.move(self.pos() + QPoint(60, 60))

    def _check_outside_changes(self):
        """If another window wrote to the library, refresh what's showing. Never writes, so windows don't echo."""
        st = context.store
        v = st.data_version()
        if v == self._seen_version:
            return
        bp = self.board_page
        busy = (QApplication.mouseButtons() != Qt.NoButton or QApplication.activePopupWidget() is not None
                or QApplication.activeModalWidget() is not None or self.stack.currentWidget() is self.editor
                or (bp.scene is not None and bp.view._editing_text()))
        if busy:
            return  # try again on the next tick
        self._seen_version = v
        lib = st.library_fingerprint()
        if lib != self._lib_fp:
            self._lib_fp = lib
            self.sidebar.rebuild()
            self.sidebar.update_counts()
            if bp.board is not None and not st.is_map(bp.board):
                self.sidebar.select_board(bp.board["id"])
        if bp.board is None or bp.scene is None:
            return
        b = st.board(bp.board["id"])
        if b is None or b.get("deleted"):
            bp.close_board()
            self.go_home()
            return
        fp = st.board_fingerprint(b["id"])
        if fp != self._board_fp:
            view = bp.view.view_state()
            selected = {i.id for i in bp.scene.selected_board_items()}
            bp.open_board(b["id"])

            def restore():
                bp.view.restore_state(view)
                for iid in selected:
                    it = bp.scene.items_by_id.get(iid) if bp.scene else None
                    if it is not None:
                        it.setSelected(True)
            QTimer.singleShot(0, restore)
        self._board_fp = fp

    def new_board_here(self):
        bp = self.board_page
        if bp.board is not None:
            self.sidebar.new_board(bp.board["project_id"])
        else:
            ps = context.store.projects()
            if ps:
                self.sidebar.new_board(ps[0]["id"])
            else:
                self.sidebar.new_project()

    def open_editor_new(self, board_id, pos):
        self.editor.open_new(board_id, pos)
        self._show(self.editor)
        self.setWindowTitle("New evidence — DeNotive")

    def open_editor_item(self, item_id, version_id=None):
        if not self._leave_editor():
            return
        it = context.store.item(item_id)
        if it is None:
            return
        bp = self.board_page
        if bp.board is None or bp.board["id"] != it["board_id"]:
            bp.open_board(it["board_id"], item_id)
            self.sidebar.select_board(it["board_id"])
        elif self.stack.currentWidget() is bp:
            bp.save_view_state()
        if self.editor.open_item(item_id, version_id):
            self._show(self.editor)
            self.setWindowTitle(f"{it.get('title') or 'Evidence'} — DeNotive")

    def _editor_finished(self, result):
        bp = self.board_page
        bid = result.get("board_id")
        if bp.board is None or bp.board["id"] != bid:
            bp.open_board(bid)
        self._show(bp)
        if context.store.is_map(bp.board):
            self.sidebar.select_project(bp.board["project_id"])
        else:
            self.sidebar.select_board(bid)
        bp.after_editor(result)
        bp.view.setFocus()
        self.setWindowTitle(f"{bp.board['name']} — DeNotive")
        if result.get("saved"):
            self.toast("Evidence saved" if result.get("created") else "Saved — new version stacked on top")
        self.sidebar.update_counts()

    def open_timeline(self, project_id=None, board_id=None):
        if not self._leave_editor():
            return
        self.timeline.open_scope(project_id, board_id)
        self._show(self.timeline)
        self.sidebar.set_nav("timeline")
        self.setWindowTitle("Timeline — DeNotive")

    # ------------------------------------------------------------------ search
    def open_ai_settings(self):
        from .widgets.aisettings import AISettingsDialog
        if AISettingsDialog(self).exec():
            self.board_page.coinv.refresh_model()
            self.toast("Co-Investigator settings saved")

    def copy_project_case(self, project_id):
        from .ai import casefile
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            text = casefile.project_case(project_id).text
        finally:
            QApplication.restoreOverrideCursor()
        QApplication.clipboard().setText(text)
        self.toast("Project case file copied — paste it into any AI chat")

    def open_search(self, scope="all", project_id=None):
        st = context.store
        project = board = item = None
        cur = self.stack.currentWidget()
        if cur is self.board_page and self.board_page.board:
            b = self.board_page.board
            board = (b["id"], b["name"])
            p = st.project(b["project_id"])
            project = (p["id"], p["name"]) if p else None
            sel = [i for i in self.board_page.scene.selected_board_items() if i.KIND in ("evidence", "file")]
            if len(sel) == 1:
                item = (sel[0].id, sel[0].row.get("title") or "selected")
        elif cur is self.editor and self.editor.board_id:
            b = st.board(self.editor.board_id)
            board = (b["id"], b["name"])
            p = st.project(b["project_id"])
            project = (p["id"], p["name"]) if p else None
            if self.editor.item_id:
                item = (self.editor.item_id, self.editor.title.text() or "this evidence")
        elif cur is self.timeline:
            pid = self.timeline.project.currentData()
            if pid:
                p = st.project(pid)
                project = (p["id"], p["name"])
            bid = self.timeline.board.currentData()
            if bid:
                board = (bid, st.board(bid)["name"])
        if project_id:
            p = st.project(project_id)
            project = (p["id"], p["name"])
            board = item = None
            scope = "project"
        if scope == "auto":
            scope = "item" if (cur is self.editor and item) else "board" if board else "all"
        self.search.open(scope, project, board, item)

    def navigate_result(self, r):
        kind = r["kind"]
        if kind == "project":
            self.open_timeline(project_id=r["project_id"])
            return
        if kind == "board":
            self.open_board(r["board"])
            return
        if not r.get("board"):
            return
        self.open_board(r["board"], focus_item=r.get("item") or None)
        bp = self.board_page
        if kind == "string":
            s = bp.scene.strings_by_id.get(r["ref"]) if bp.scene else None
            if s is not None:
                bp.scene.clearSelection()
                s.setSelected(True)
                bp.view.centerOn(s.path.pointAtPercent(0.5))
        elif kind in ("evidence", "file") and r.get("old"):
            it = bp.scene.items_by_id.get(r["item"]) if bp.scene else None
            if it is not None:
                QTimer.singleShot(150, lambda: bp.show_history(it, version_id=r["ref"]))

    # ------------------------------------------------------------------ export
    def export_board(self, board_id, kind):
        b = context.store.board(board_id)
        sel_ids = None
        bp = self.board_page
        if bp.board and bp.board["id"] == board_id and bp.scene:
            sel = [i.id for i in bp.scene.selected_board_items() if isinstance(i, EvidenceCard)]
            sel_ids = sel or None
        self._export([board_id], kind, b["name"], sel_ids)

    def export_project(self, project_id, kind):
        p = context.store.project(project_id)
        ids = [b["id"] for b in context.store.boards(project_id)]
        if not ids:
            self.toast("This project has no boards yet")
            return
        pm = context.store.project_map(project_id)
        context.store.sync_project_map(pm["id"])
        ids.insert(0, pm["id"])
        self._export(ids, kind, p["name"], None)

    def _export(self, board_ids, kind, name, sel_ids):
        from . import export
        from .widgets.dialogs import ExportDialog
        d = ExportDialog(kind, name, bool(sel_ids), self)
        if d.exec() != ExportDialog.Accepted:
            return
        o = d.options()
        only = set(sel_ids) if (sel_ids and o["only_selected"]) else None
        if kind == "print":
            printer = QPrinter(QPrinter.HighResolution)
            printer.setDocName(name)
            prev = QPrintPreviewDialog(printer, self)
            prev.setWindowTitle(f"Print — {name}")
            prev.resize(1200, 860)
            prev.paintRequested.connect(lambda pr: export.print_boards(pr, board_ids, o["overview"], o["order"], only))
            prev.exec()
            return
        ext = {"pdf": "pdf", "pptx": "pptx"}[kind]
        filt = {"pdf": "PDF document (*.pdf)", "pptx": "PowerPoint presentation (*.pptx)"}[kind]
        default = str(Path.home() / "Documents" / f"{name}.{ext}")
        path, _ = QFileDialog.getSaveFileName(self, "Export", default, filt)
        if not path:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            if kind == "pdf":
                n = export.export_pdf(path, board_ids, o["overview"], o["order"], only)
            else:
                n = export.export_pptx(path, board_ids, o["overview"], o["order"], only, o["editable"])
        except PermissionError:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Export", "Couldn't write the file — is it open in another program?")
            return
        except Exception as ex:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Export failed", str(ex))
            return
        QApplication.restoreOverrideCursor()
        box = QMessageBox(self)
        box.setWindowTitle("Exported")
        box.setText(f"Exported {n} page{'s' if n != 1 else ''} to\n{path}")
        open_b = box.addButton("Open", QMessageBox.AcceptRole)
        folder_b = box.addButton("Show in folder", QMessageBox.ActionRole)
        box.addButton("Close", QMessageBox.RejectRole)
        box.exec()
        from . import evidence
        if box.clickedButton() is open_b:
            evidence.open_path(path)
        elif box.clickedButton() is folder_b:
            evidence.reveal(path)

    # ------------------------------------------------------------------ deletion
    def delete_board(self, board_id):
        b = context.store.board(board_id)
        if QMessageBox.question(self, "Delete board", f"Move the board “{b['name']}” to the trash?") \
                != QMessageBox.Yes:
            return
        if self.board_page.board and self.board_page.board["id"] == board_id:
            self.board_page.close_board()
            self.go_home()
        context.store.delete_board(board_id)
        self.sidebar.rebuild()
        self._refresh_open_map()
        self.toast("Board moved to trash")

    def _refresh_open_map(self):
        """A project map lists boards; rebuild it if it's on screen when boards come or go."""
        bp = self.board_page
        if bp.board is not None and context.store.is_map(bp.board) and self.stack.currentWidget() is bp:
            bp.open_board(bp.board["id"])

    def delete_project(self, project_id):
        p = context.store.project(project_id)
        if QMessageBox.question(self, "Delete project", f"Move the project “{p['name']}” and its boards to the "
                                                         "trash?") != QMessageBox.Yes:
            return
        if self.board_page.board and self.board_page.board["project_id"] == project_id:
            self.board_page.close_board()
            self.go_home()
        context.store.delete_project(project_id)
        self.sidebar.rebuild()
        self.toast("Project moved to trash")

    # ------------------------------------------------------------------ misc
    def refresh_sidebar(self):
        self.sidebar.rebuild()

    def reload_board(self):
        """Rebuilds the open board from the store (after changes made outside it, e.g. trash restores)."""
        bp = self.board_page
        if bp.board is not None:
            bid = bp.board["id"]
            b = context.store.board(bid)
            if b is None or b.get("deleted"):
                bp.close_board()
            else:
                bp.open_board(bid)

    def board_changed(self):
        self._counts_timer.start()
        self._baseline()  # our own change isn't news to us

    def _baseline(self):
        bp = self.board_page
        self._board_fp = context.store.board_fingerprint(bp.board["id"]) if bp.board is not None else None

    def board_meta_changed(self):
        bp = self.board_page
        self._refresh_open_map()
        if bp.board is not None:
            b = context.store.board(bp.board["id"])
            if b is None or b.get("deleted"):
                return
            bp.board.update(project_id=b["project_id"], name=b["name"])
            p = context.store.project(b["project_id"])
            bp.proj_dot.setPixmap(theme.color_dot_icon(p["color"] if p else "#888", 12).pixmap(12, 12))
            bp.proj_name.setText(p["name"] if p else "")
            bp.name_edit.setText(b["name"])

    def toast(self, msg, ms=2200):
        self.toast_w.show_message(msg, ms)

    def raise_and_activate(self):
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def _toggle_full(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.search.isVisible():
            self.search.place()

    def showEvent(self, e):
        super().showEvent(e)
        if os.name == "nt":
            try:
                import ctypes
                hwnd = int(self.winId())
                val = ctypes.c_int(1)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), ctypes.sizeof(val))
                cap = ctypes.c_int(0x00151514)  # COLORREF 0x00BBGGRR of the panel colour
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(cap), ctypes.sizeof(cap))
            except Exception:
                pass

    def closeEvent(self, e):
        if not self._leave_editor():
            e.ignore()
            return
        self.board_page.save_view_state()
        if not self.secondary:
            settings["geometry"] = bytes(self.saveGeometry().toHex()).decode()
            settings["sidebar_sizes"] = self.split.sizes()
            settings.save()
        if self.board_page.scene is not None:
            self.board_page.scene.teardown()
        context.store.close()
        super().closeEvent(e)
