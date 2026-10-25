import os
import sys
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QIcon, QPalette
from PySide6.QtWidgets import QApplication


def resource(name):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return str(base / "assets" / name)


def _install_excepthook():
    """Windowed builds have no console: log unhandled errors and show them instead of dying silently."""
    import traceback
    log_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "DeNotive"

    def hook(etype, value, tb):
        text = "".join(traceback.format_exception(etype, value, tb))
        sys.__stderr__ and sys.__stderr__.write(text)
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            with open(log_dir / "error.log", "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except OSError:
            pass
        try:
            from PySide6.QtWidgets import QMessageBox
            if QApplication.instance():
                QMessageBox.critical(None, "DeNotive error",
                                     f"{value}\n\nYour work is saved as you go. Details were written to "
                                     f"{log_dir / 'error.log'}")
        except Exception:
            pass
    sys.excepthook = hook


def main():
    _install_excepthook()
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DeNotive.App")
    app = QApplication(sys.argv)
    app.setApplicationName("DeNotive")
    app.setOrganizationName("DeNotive")
    app.setStyle("Fusion")
    from . import theme
    pal = QPalette()
    for role, col in [(QPalette.Window, theme.PANEL), (QPalette.WindowText, theme.TEXT),
                      (QPalette.Base, theme.SURFACE), (QPalette.AlternateBase, theme.SURFACE2),
                      (QPalette.Text, theme.TEXT), (QPalette.Button, theme.SURFACE2),
                      (QPalette.ButtonText, theme.TEXT), (QPalette.Highlight, theme.SELECT),
                      (QPalette.HighlightedText, "#ffffff"), (QPalette.ToolTipBase, theme.SURFACE2),
                      (QPalette.ToolTipText, theme.TEXT), (QPalette.PlaceholderText, theme.FAINT),
                      (QPalette.Link, theme.SELECT)]:
        pal.setColor(role, QColor(col))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor("#5a5d66"))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#5a5d66"))
    app.setPalette(pal)
    f = QFont(theme.UI_FONT)
    f.setPointSizeF(10)
    app.setFont(f)
    app.setStyleSheet(theme.STYLESHEET)
    ico = resource("icon.ico")
    if os.path.exists(ico):
        app.setWindowIcon(QIcon(ico))

    from .window import MainWindow
    w = MainWindow()
    w.show()
    if "--selftest" in sys.argv:
        sys.exit(_selftest(app, w, sys.argv[sys.argv.index("--selftest") + 1]))
    sys.exit(app.exec())


def _selftest(app, w, out_dir):
    """Exercises the bundled pieces (rendering, image codecs, PDF, PowerPoint) and reports to out_dir."""
    import time
    import traceback
    from . import context, export
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    lines = []
    try:
        for _ in range(30):
            app.processEvents()
            time.sleep(0.02)
        bid = w.board_page.board["id"] if w.board_page.board else context.store.boards()[0]["id"]
        lines.append(f"board items: {len(context.store.items(bid))}")
        lines.append(f"pdf pages: {export.export_pdf(str(out / 'selftest.pdf'), [bid])}")
        lines.append(f"pptx slides: {export.export_pptx(str(out / 'selftest.pptx'), [bid])}")
        w.grab().save(str(out / "selftest.png"))
        from .ai import casefile, providers
        lines.append(f"case file chars: {len(casefile.board_case(bid, images=True).text)}")
        lines.append(f"claude app found: {bool(providers.find_claude())}")
        os.environ.setdefault("ANTHROPIC_API_KEY", "selftest-not-a-key")
        try:
            providers.make({"provider": "anthropic", "model": "x"}).list_models()
            lines.append("https: unexpected success")
        except providers.AIError as e:
            lines.append("https: " + ("ok (key rejected as expected)" if "401" in str(e) else str(e)[:120]))
        lines.append("ok")
        code = 0
    except Exception:
        lines.append(traceback.format_exc())
        code = 1
    (out / "selftest.txt").write_text("\n".join(lines), encoding="utf-8")
    w.close()
    return code


if __name__ == "__main__":
    main()
