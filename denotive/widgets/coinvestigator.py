"""The Co-Investigator panel: ask a model about the board, with the case file attached automatically."""
import re
import time

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPalette
from PySide6.QtWidgets import (QApplication, QComboBox, QFrame, QHBoxLayout, QLabel, QMenu, QPlainTextEdit,
                               QPushButton, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from .. import context, theme
from ..ai import casefile, prompts
from ..ai.providers import describe, open_sign_in, role_config, sign_in_label
from ..ai.runner import Run
from .common import clear_layout, label, nice_time, push_button, tool_button

HANDLE_LINK = re.compile(r"(?<![\w/:])([EFNB]\d{1,4})(?![\w])")


def linkify(md, handles):
    """Turns item handles such as E3 into links (outside code blocks) so clicking jumps to the item."""
    out, in_code = [], False
    for line in md.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
        elif not in_code:
            line = HANDLE_LINK.sub(lambda m: f"[{m.group(1)}](dn:{m.group(1)})" if m.group(1) in handles
                                   else m.group(1), line)
            line = re.sub(r"\[\[([EFNB]\d{1,4})\]\(dn:", r"[\1](dn:", line)  # [[E3](dn:E3)] -> [E3](dn:E3)
            line = re.sub(r"\(dn:([EFNB]\d{1,4})\)\]", r"(dn:\1)", line)
        out.append(line)
    return "\n".join(out)


def _drop(w):
    """Removes a widget from view right away (deleteLater alone leaves it on screen until the next loop pass)."""
    w.hide()
    w.setParent(None)
    w.deleteLater()


class Bubble(QFrame):
    """One message in the conversation."""

    def __init__(self, panel, msg):
        super().__init__()
        self.panel = panel
        self.msg = msg
        role = msg["role"]
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)
        if role == "user":
            self.setStyleSheet(f"QFrame {{ background: {theme.SURFACE3}; border-radius: 12px; }}")
            t = QLabel(msg["text"])
            t.setWordWrap(True)
            t.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lay.addWidget(t)
            return
        err = role == "error"
        self.setStyleSheet(f"QFrame#Bubble {{ background: {'#2a1a1e' if err else theme.SURFACE}; "
                           f"border: 1px solid {'#5a2a2e' if err else theme.COINV_DIM}; border-radius: 12px; }}")
        self.setObjectName("Bubble")
        head = QHBoxLayout()
        head.setSpacing(6)
        ic = QLabel()
        ic.setPixmap(theme.icon_pixmap("coinv", 16, theme.COINV))
        head.addWidget(ic)
        who = QLabel("Co-Investigator" if not err else "Couldn't get an answer")
        who.setStyleSheet(f"color: {theme.COINV if not err else '#ff8a8f'}; font-weight: 600;")
        head.addWidget(who)
        meta = QLabel(msg.get("model", "") + (" · " + nice_time(msg["time"]) if msg.get("time") else ""))
        meta.setObjectName("Hint")
        meta.setToolTip(meta.text())
        meta.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        meta.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)  # a long model name must not widen the bubble
        head.addWidget(meta, 1)
        lay.addLayout(head)
        if msg.get("persona") and not err:
            who_as = QLabel(f"as {msg['persona']}")
            who_as.setObjectName("Hint")
            who_as.setStyleSheet(f"color: {theme.COINV}; font-style: italic;")
            who_as.setWordWrap(True)
            lay.addWidget(who_as)
        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.MarkdownText if not err else Qt.PlainText)
        self.body.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.body.setOpenExternalLinks(False)
        self.body.linkActivated.connect(self._link)
        pal = self.body.palette()
        pal.setColor(QPalette.Link, QColor(theme.COINV))
        self.body.setPalette(pal)
        self.body.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        lay.addWidget(self.body)
        self.footer = QHBoxLayout()
        self.footer.setSpacing(4)
        lay.addLayout(self.footer)
        self.set_text(msg.get("text", ""))
        if err:
            if msg.get("login"):
                b = push_button(sign_in_label(role_config("coinv")), "openfile")
                b.clicked.connect(self.panel.sign_in)
                self.footer.addWidget(b)
            s = push_button("AI settings…", "settings")
            s.clicked.connect(self.panel.main.open_ai_settings)
            self.footer.addWidget(s)
            self.footer.addStretch(1)
        elif msg.get("text"):
            self.add_actions()

    def set_text(self, text, streaming=False):
        if self.msg["role"] == "error":
            self.body.setText(text)
            return
        shown = linkify(text, self.msg.get("handles") or {}) if text else ""
        self.body.setText(shown + (" ▍" if streaming else "") if shown else ("_Reading the case file…_"
                                                                            if streaming else ""))

    def add_actions(self):
        clear_layout(self.footer)
        pin = QToolButton()
        pin.setText(" Pin to board")
        pin.setIcon(theme.icon("pin", 14))
        pin.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        pin.setPopupMode(QToolButton.InstantPopup)
        m = QMenu(pin)
        m.addAction(theme.icon("sticky", 16), "As a Co-Investigator sticky note",
                    lambda: self.panel.pin(self.msg, "sticky"))
        m.addAction(theme.icon("evidence", 16), "As an evidence slide", lambda: self.panel.pin(self.msg, "evidence"))
        pin.setMenu(m)
        cp = QToolButton()
        cp.setText(" Copy")
        cp.setIcon(theme.icon("copy", 14))
        cp.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        cp.clicked.connect(lambda: (QApplication.clipboard().setText(self.msg["text"]),
                                    self.panel.main.toast("Answer copied")))
        self.footer.addWidget(pin)
        self.footer.addWidget(cp)
        self.footer.addStretch(1)

    def _link(self, href):
        if href.startswith("dn:"):
            self.panel.jump(self.msg.get("handles") or {}, href[3:])
        else:
            QDesktopServices.openUrl(QUrl(href))


class PasteCard(QFrame):
    """Copy & paste mode: the prompt is on the clipboard; paste the chatbot's reply here."""

    def __init__(self, panel, handles):
        super().__init__()
        self.panel = panel
        self.handles = handles
        self.setObjectName("Bubble")
        self.setStyleSheet(f"QFrame#Bubble {{ background: {theme.SURFACE}; border: 1px dashed {theme.COINV}; "
                           f"border-radius: 12px; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.addWidget(label("The question and the whole case file are on your clipboard. Paste them into your "
                            "chatbot, then paste its reply here.", "Muted", wrap=True))
        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText("Paste the reply…")
        self.edit.setFixedHeight(110)
        lay.addWidget(self.edit)
        row = QHBoxLayout()
        again = push_button("Copy again", "copy")
        again.clicked.connect(lambda: QApplication.clipboard().setText(panel.last_manual_prompt))
        add = push_button("Add reply", "check", primary=True)
        add.clicked.connect(self._add)
        cancel = push_button("Cancel", name="Ghost")
        cancel.clicked.connect(lambda: panel.cancel_paste(self))
        row.addWidget(again)
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(add)
        lay.addLayout(row)

    def _add(self):
        text = self.edit.toPlainText().strip()
        if text:
            self.panel.finish_paste(self, text, self.handles)


class CoInvestigatorPanel(QFrame):
    def __init__(self, page):
        super().__init__()
        self.page = page
        self.main = page.main
        self.setObjectName("Inspector")
        self.setMinimumWidth(340)
        self.setMaximumWidth(560)
        self.messages = []
        self.scope_key = None
        self.run = None
        self.live = None
        self.buffer = ""
        self.last_manual_prompt = ""
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(8)
        head = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(theme.icon_pixmap("coinv", 22, theme.COINV))
        head.addWidget(ic)
        t = QLabel("Co-Investigator")
        t.setStyleSheet("font-size: 12.5pt; font-weight: 650;")
        head.addWidget(t)
        head.addStretch(1)
        self.model_btn = QPushButton()
        self.model_btn.setObjectName("Chip")
        self.model_btn.setCursor(Qt.PointingHandCursor)
        self.model_btn.setToolTip("Change the model in AI settings")
        self.model_btn.clicked.connect(self.main.open_ai_settings)
        head.addWidget(self.model_btn)
        more = tool_button("more", "More")
        more.setPopupMode(QToolButton.InstantPopup)
        mm = QMenu(more)
        mm.addAction(theme.icon("add", 16), "New conversation", self.clear)
        mm.addSeparator()
        mm.addAction(theme.icon("copy", 16), "Copy case file", self.copy_case)
        mm.addAction(theme.icon("download", 16), "Save case file…", self.save_case)
        mm.addSeparator()
        mm.addAction(theme.icon("settings", 16), "AI settings…", self.main.open_ai_settings)
        more.setMenu(mm)
        head.addWidget(more)
        v.addLayout(head)
        srow = QHBoxLayout()
        srow.addWidget(label("Looking at", "Muted"))
        self.scope = QComboBox()
        self.scope.addItem("This board", "board")
        self.scope.addItem("Whole project", "project")
        self.scope.addItem("Selected items", "selection")
        srow.addWidget(self.scope, 1)
        v.addLayout(srow)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("Inspector")
        self.list = QVBoxLayout(body)
        self.list.setContentsMargins(0, 4, 4, 4)
        self.list.setSpacing(10)
        self.list.addStretch(1)
        self.scroll.setWidget(body)
        v.addWidget(self.scroll, 1)

        self.quick = QToolButton()
        self.quick.setText(" Suggested questions")
        self.quick.setIcon(theme.icon("bulb", 14, theme.COINV))
        self.quick.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.quick.setPopupMode(QToolButton.InstantPopup)
        qm = QMenu(self.quick)
        for short, full in prompts.QUICK_QUESTIONS:
            qm.addAction(short, lambda f=full: self.ask(f))
        self.quick.setMenu(qm)
        v.addWidget(self.quick, 0, Qt.AlignLeft)
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("Ask about this board…  (Ctrl+Enter to send)")
        self.input.setFixedHeight(84)
        self.input.installEventFilter(self)
        v.addWidget(self.input)
        brow = QHBoxLayout()
        self.status = label("", "Hint")
        brow.addWidget(self.status, 1)
        self.send_b = push_button("Ask", "forward", primary=True)
        self.send_b.clicked.connect(self._send_clicked)
        brow.addWidget(self.send_b)
        v.addLayout(brow)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._flush)

    # ------------------------------------------------------------------ scope / storage
    def eventFilter(self, obj, e):
        from PySide6.QtCore import QEvent
        if obj is self.input and e.type() == QEvent.KeyPress and e.key() in (Qt.Key_Return, Qt.Key_Enter) \
                and e.modifiers() & Qt.ControlModifier:
            self._send_clicked()
            return True
        return super().eventFilter(obj, e)

    def refresh_model(self):
        cfg = role_config("coinv")
        self.model_btn.setText(describe(cfg).replace("&", "&&"))
        persona = prompts.clean_persona(cfg.get("persona"))
        self.model_btn.setToolTip("Change the model or personality in AI settings"
                                  + (f"\nPersonality: {persona}" if persona else ""))
        manual = cfg["provider"] in ("manual", "none")
        self.send_b.setText("Copy for chatbot" if manual else "Ask")

    def set_board(self, board):
        """Called when a board opens: shows the conversation kept for it."""
        if self.run is not None:
            self.run.stop()
            self.run = None
        self.board = board
        self.scope_key = f"board:{board['id']}"
        self.messages = context.store.chat(self.scope_key)
        self.refresh_model()
        self._rebuild()

    def _save(self):
        if self.scope_key:
            context.store.save_chat(self.scope_key, [m for m in self.messages if m["role"] in ("user", "assistant")])

    def clear(self):
        if self.run is not None:
            self.run.stop()
            self.run = None
        self.messages = []
        self._save()
        self._rebuild()

    def _rebuild(self):
        while self.list.count() > 1:
            it = self.list.takeAt(0)
            if it.widget():
                _drop(it.widget())
        if not self.messages:
            self._welcome()
        for m in self.messages:
            self._add_bubble(m)
        self._idle()

    def _welcome(self):
        cfg = role_config("coinv")
        w = QFrame()
        w.setObjectName("Bubble")
        w.setStyleSheet(f"QFrame#Bubble {{ background: {theme.SURFACE}; border: 1px solid {theme.COINV_DIM}; "
                        f"border-radius: 12px; }}")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        lay.addWidget(label("Stumped? Ask your Co-Investigator.", "Title", wrap=True))
        lay.addWidget(label("It reads this board's evidence, notes, attached files and the strings you've tied "
                            "between them, then answers with references like E3 that jump to the item.",
                            "Muted", wrap=True))
        if cfg["provider"] == "none":
            lay.addWidget(label("No model is set up yet. Pick Claude (with your Claude app sign-in), ChatGPT, "
                                "Gemini, a local Ollama model — or just copy & paste into any chatbot website.",
                                "Muted", wrap=True))
            b = push_button("Set up the Co-Investigator…", "settings", primary=True)
            b.clicked.connect(self.main.open_ai_settings)
            lay.addWidget(b)
        for short, full in prompts.QUICK_QUESTIONS[:4]:
            q = QPushButton(short)
            q.setObjectName("Chip")
            q.setCursor(Qt.PointingHandCursor)
            q.clicked.connect(lambda _=False, f=full: self.ask(f))
            lay.addWidget(q, 0, Qt.AlignLeft)
        self.list.insertWidget(self.list.count() - 1, w)

    def _add_bubble(self, m):
        b = Bubble(self, m)
        row = QHBoxLayout()
        if m["role"] == "user":
            row.addStretch(1)
            b.setMaximumWidth(int(self.width() * 0.85) if self.width() > 200 else 320)
        row.addWidget(b, 0 if m["role"] == "user" else 1)
        holder = QWidget()
        holder.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        self.list.insertWidget(self.list.count() - 1, holder)
        QTimer.singleShot(30, self._scroll_end)
        return b

    def _scroll_end(self):
        sb = self.scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ------------------------------------------------------------------ asking
    def _send_clicked(self):
        if self.run is not None:
            self.run.stop()
            self.status.setText("Stopping…")
            return
        q = self.input.toPlainText().strip()
        if q:
            self.ask(q)

    def build_case(self, cfg):
        scope = self.scope.currentData()
        sc = self.page.scene
        images = bool(cfg.get("images", True)) and cfg["provider"] not in ("manual", "none")
        sel = [i.id for i in sc.selected_board_items()] if sc else []
        if scope == "project":
            return casefile.project_case(self.board["project_id"], images=images)
        if scope == "selection":
            if not sel:
                self.main.toast("Nothing is selected — using the whole board")
            return casefile.board_case(self.board["id"], only=set(sel) or None, images=images, prefer=sel)
        return casefile.board_case(self.board["id"], images=images, prefer=sel)

    def ask(self, question):
        if self.run is not None:
            return
        cfg = role_config("coinv")
        if cfg["provider"] == "none":
            self.main.open_ai_settings()
            cfg = role_config("coinv")
            self.refresh_model()
            if cfg["provider"] == "none":
                return
        self.input.clear()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            cf = self.build_case(cfg)
        finally:
            QApplication.restoreOverrideCursor()
        history = [m for m in self.messages if m["role"] in ("user", "assistant")][-8:]
        user = {"role": "user", "text": question, "time": time.time()}
        self.messages.append(user)
        if self.list.count() > 1 and not [m for m in self.messages if m is not user]:
            self._rebuild()
        else:
            self._add_bubble(user)
        if cfg["provider"] == "manual":
            system = prompts.with_persona(prompts.COINV_SYSTEM, cfg.get("persona"))
            self.last_manual_prompt = prompts.manual_prompt(system, cf.text, question, history)
            QApplication.clipboard().setText(self.last_manual_prompt)
            if cfg.get("site"):
                QDesktopServices.openUrl(QUrl(cfg["site"]))
            card = PasteCard(self, dict(cf.handles))
            self.list.insertWidget(self.list.count() - 1, card)
            QTimer.singleShot(30, self._scroll_end)
            self._save()
            return
        api_msgs = [{"role": m["role"], "text": m["text"]} for m in history]
        api_msgs.append({"role": "user", "images": [(mime, data) for mime, data, _ in cf.images],
                         "text": f"Here is the current case file.\n\n{cf.text}\n\n---\n\nMy question: {question}"})
        reply = {"role": "assistant", "text": "", "model": describe(cfg), "time": time.time(),
                 "handles": dict(cf.handles), "persona": prompts.persona_phrase(cfg.get("persona"))}
        self.live = self._add_bubble(reply)
        self.live.set_text("", streaming=True)
        self.buffer = ""
        self.run = Run(cfg, prompts.with_persona(prompts.COINV_SYSTEM, cfg.get("persona")), api_msgs)
        self.run.chunk.connect(self._chunk)
        self.run.done.connect(lambda text: self._done(reply, text))
        self.run.failed.connect(lambda msg, login: self._failed(reply, msg, login))
        self._busy()
        self.run.start()

    def _chunk(self, piece):
        self.buffer += piece
        if not self._timer.isActive():
            self._timer.start()

    def _flush(self):
        if self.live is not None:
            sb = self.scroll.verticalScrollBar()
            at_end = sb.value() >= sb.maximum() - 30
            self.live.set_text(self.buffer, streaming=True)
            if at_end:
                QTimer.singleShot(0, self._scroll_end)

    def _done(self, reply, text):
        self.run = None
        reply["text"] = (text or self.buffer).strip() or "(no answer)"
        self.messages.append(reply)
        if self.live is not None:
            self.live.set_text(reply["text"])
            self.live.add_actions()
        self.live = None
        self._save()
        self._idle()

    def _failed(self, reply, msg, login):
        self.run = None
        if self.live is not None:
            _drop(self.live.parentWidget())
        self.live = None
        err = {"role": "error", "text": msg, "login": login, "model": reply.get("model", "")}
        self.messages.append(err)
        self._add_bubble(err)
        self._idle()

    def _busy(self):
        self.send_b.setText("Stop")
        self.send_b.setIcon(theme.icon("square", 14, "#ffffff"))
        self.status.setText("The Co-Investigator is reading the case file…")

    def _idle(self):
        self.refresh_model()
        self.send_b.setIcon(theme.icon("forward", 16, "#ffffff"))
        n = sum(1 for m in self.messages if m["role"] == "assistant")
        self.status.setText(f"{n} answer{'s' if n != 1 else ''} in this conversation" if n else "")

    # ------------------------------------------------------------------ copy & paste mode
    def finish_paste(self, card, text, handles):
        reply = {"role": "assistant", "text": text, "model": "pasted reply", "time": time.time(), "handles": handles}
        self.messages.append(reply)
        _drop(card)
        self._add_bubble(reply)
        self._save()
        self._idle()

    def cancel_paste(self, card):
        _drop(card)

    # ------------------------------------------------------------------ actions
    def sign_in(self):
        if open_sign_in(role_config("coinv")):
            self.main.toast("Finish signing in in the window that opened, then ask again")
        else:
            self.main.toast("Couldn't find that provider's command-line tool")

    def jump(self, handles, handle):
        iid = handles.get(handle)
        it = context.store.item(iid) if iid else None
        if it is None or it.get("deleted"):
            self.main.toast(f"{handle} isn't on the board anymore")
            return
        self.main.open_board(it["board_id"], focus_item=iid)

    def pin(self, msg, how):
        st = context.store
        bp = self.page
        text = msg["text"]
        q = next((m["text"] for m in reversed(self.messages[:self.messages.index(msg)]) if m["role"] == "user"), "") \
            if msg in self.messages else ""
        center = bp.free_spot(bp.view_center(), 360, 320)
        data_ai = {"author": "coinv", "model": msg.get("model", "")}
        if how == "sticky":
            plain = re.sub(r"\*\*|__|`|^#+\s*", "", text, flags=re.M)
            row = st.new_item(bp.board["id"], "sticky", center.x() - 180, center.y() - 160, 360, 320,
                              dict(data_ai, text=plain.strip()[:1500], fill=theme.COINV_PAPER, size=14,
                                   font=theme.UI_FONT))
            it = bp.scene.add_rows([row], label="Pin Co-Investigator note")[0]
        else:
            from .. import evidence
            title = "Co-Investigator: " + (q[:60] + ("…" if len(q) > 60 else "") if q else "answer")
            doc = evidence.text_doc(re.sub(r"\*\*|__|`", "", text)[:3000])
            doc["items"][0]["size"] = 20
            doc["notes"] = f"Question: {q}\n\nAnswer ({msg.get('model', '')}):\n{text}"
            iid = evidence.new_evidence(bp.board["id"], center, title, doc)
            r = st.item(iid)
            r["data"].update(data_ai, color=theme.COINV_PAPER)
            st.put_item(r)
            it = bp.scene.add_existing(iid)
        if it is not None:
            bp.pulse(it)
        self.main.toast("Pinned to the board — it's marked as written by the Co-Investigator")

    def case_text(self):
        cfg = dict(role_config("coinv"), images=False)
        return self.build_case(cfg).text

    def copy_case(self):
        QApplication.clipboard().setText(self.case_text())
        self.main.toast("Case file copied — paste it into any AI chat")

    def save_case(self):
        from PySide6.QtWidgets import QFileDialog
        name = (self.board.get("name") or "case").replace("/", "-")
        path, _ = QFileDialog.getSaveFileName(self, "Save case file", f"{name} case file.md",
                                              "Markdown (*.md);;Text (*.txt)")
        if path:
            casefile.save_text(path, self.case_text())
            self.main.toast("Case file saved")
