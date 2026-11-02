"""AI settings: which model plays the Co-Investigator and which drafts evidence notes."""
import threading
import time

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout, QLineEdit, QScrollArea,
                               QVBoxLayout, QWidget)

from .. import theme
from ..ai import keys, prompts
from ..ai.providers import (CHATBOT_SITES, DEFAULT_URLS, NEEDS_KEY, PROVIDERS, SUGGESTED, AIError, claude_login,
                            find_claude, make)
from ..ai.runner import Run
from ..settings import settings
from .common import hsep, label, push_button, section_label

KEY_PAGES = {"anthropic": "https://console.anthropic.com/settings/keys",
             "openai": "https://platform.openai.com/api-keys", "gemini": "https://aistudio.google.com/apikey"}
MODEL_GUIDES = {
    "claude_app": "haiku — smallest and fastest, great for notes  ·  sonnet — balanced  ·  opus — most capable, best "
                  "for the Co-Investigator (uses more of your plan's limits). You can also type a full model name.",
    "anthropic": "claude-haiku-5-5 — smallest, fastest, cheapest  ·  claude-sonnet-5-5 — balanced  ·  "
                 "claude-opus-5-5 — most capable. Press List models for everything your key can use.",
    "openai": "Press List models to see every model your key can use, then pick one from the box.",
    "gemini": "Flash models are fast and cheap; Pro models reason more deeply. Press List models for the full list.",
    "ollama": "Press List models to see the models you've downloaded. Names with “vision” (or llava, gemma) can read "
              "slide images; small models (3–8B) are quick but less sharp.",
    "compatible": "Press List models to ask the server what it offers.",
}
HINTS = {
    "none": "The Co-Investigator is off. You can still copy a board's case file from its ⋯ menu.",
    "claude_app": "Uses the Claude desktop app you already have — your normal Claude plan, no API key. "
                  "Sign in once with the button below. Tools are switched off: Claude only sees what DeNotive "
                  "sends and can't touch your files.",
    "anthropic": "Pay-as-you-go through the Anthropic API. Needs an API key from the Anthropic Console.",
    "openai": "ChatGPT's models through the OpenAI API. Needs an API key (separate from a ChatGPT Plus plan).",
    "gemini": "Google's Gemini models. A free API key is available from Google AI Studio.",
    "ollama": "Runs on this computer with Ollama — nothing leaves your PC. Vision models (llava, "
              "llama3.2-vision, gemma) can read slide images too.",
    "compatible": "Any server that speaks the OpenAI chat format: LM Studio, OpenRouter, Groq, vLLM, Azure… "
                  "Enter its base URL (ending in /v1) and a key if it needs one.",
    "manual": "No API needed: DeNotive copies the question and the full case file to your clipboard and opens "
              "the chatbot site; paste its reply back in.",
}


class _Async(QObject):
    done = Signal(object)
    failed = Signal(str)

    def go(self, fn):
        def run():
            try:
                self.done.emit(fn())
            except AIError as e:
                self.failed.emit(str(e))
            except Exception as e:
                self.failed.emit(f"{type(e).__name__}: {e}")
        threading.Thread(target=run, daemon=True).start()


class RoleBox(QFrame):
    def __init__(self, dlg, role, cfg):
        super().__init__()
        self.dlg = dlg
        self.role = role
        self.setObjectName("Card")
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(8)
        self.provider = QComboBox()
        for key, name in PROVIDERS:
            self.provider.addItem(name, key)
        self.provider.setCurrentIndex(max(0, self.provider.findData(cfg.get("provider", "none"))))
        v.addLayout(self._row("Provider", self.provider))
        self.hint = label("", "Hint", wrap=True)
        v.addWidget(self.hint)
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setMinimumWidth(240)
        self.refresh = push_button("List models", "refresh")
        self.refresh.clicked.connect(self._list_models)
        self.model_row = self._row("Model", self.model, self.refresh)
        v.addLayout(self.model_row)
        self.model_guide = label("", "Hint", wrap=True)
        v.addWidget(self.model_guide)
        self.persona = QComboBox()
        self.persona.setEditable(True)
        self.persona.addItems(prompts.PERSONAS)
        self.persona.setCurrentText(cfg.get("persona") or "Normal")
        self.persona.setToolTip("Pick a style or type your own — a job title or a short description")
        self.persona_row = self._row("Personality", self.persona)
        v.addLayout(self.persona_row)
        self.persona_hint = label("Pick one or type your own (a job title or short description). It changes the "
                                  "voice, not the facts.", "Hint", wrap=True)
        v.addWidget(self.persona_hint)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("Paste API key (stored encrypted for your Windows account)")
        self.get_key = push_button("Get a key", "openfile", name="Ghost")
        self.get_key.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(KEY_PAGES.get(self.kind(), ""))))
        self.key_row = self._row("API key", self.key, self.get_key)
        v.addLayout(self.key_row)
        self.url = QLineEdit(cfg.get("url", ""))
        self.url_row = self._row("Address", self.url)
        v.addLayout(self.url_row)
        self.site = QComboBox()
        for name, url in CHATBOT_SITES:
            self.site.addItem(name, url)
        self.site.setCurrentIndex(max(0, self.site.findData(cfg.get("site", "https://claude.ai/new"))))
        self.site_row = self._row("Open", self.site)
        v.addLayout(self.site_row)
        self.claude_status = label("", "Muted", wrap=True)
        v.addWidget(self.claude_status)
        self.sign_in = push_button("Sign in to Claude…", "openfile")
        self.sign_in.clicked.connect(self._sign_in)
        self.images = QCheckBox("Send slide images too (for models that can see pictures)")
        self.images.setChecked(bool(cfg.get("images", True)))
        v.addWidget(self.images)
        trow = QHBoxLayout()
        trow.addWidget(self.sign_in)
        self.test = push_button("Test", "check")
        self.test.clicked.connect(self._test)
        trow.addWidget(self.test)
        self.result = label("", "Muted", wrap=True)
        trow.addWidget(self.result, 1)
        v.addLayout(trow)
        self._model_cfg = cfg.get("model", "")
        self.provider.currentIndexChanged.connect(self._kind_changed)
        self._kind_changed()

    @staticmethod
    def _row(name, *widgets):
        h = QHBoxLayout()
        lab = label(name, "Muted")
        lab.setMinimumWidth(64)
        h.addWidget(lab)
        for w in widgets:
            h.addWidget(w, 1 if isinstance(w, (QComboBox, QLineEdit)) else 0)
        return h

    @staticmethod
    def _show_row(row, on):
        for i in range(row.count()):
            w = row.itemAt(i).widget()
            if w is not None:
                w.setVisible(on)

    def kind(self):
        return self.provider.currentData()

    def _kind_changed(self):
        k = self.kind()
        self.hint.setText(HINTS.get(k, ""))
        has_model = k not in ("none", "manual")
        self._show_row(self.model_row, has_model)
        self.model_guide.setText(MODEL_GUIDES.get(k, ""))
        self.model_guide.setVisible(has_model and k in MODEL_GUIDES)
        show_persona = self.role == "coinv" and k != "none"
        self._show_row(self.persona_row, show_persona)
        self.persona_hint.setVisible(show_persona)
        self._show_row(self.key_row, k in NEEDS_KEY or k == "compatible")
        self.get_key.setVisible(k in KEY_PAGES)
        self._show_row(self.url_row, k in ("ollama", "compatible"))
        self.url.setPlaceholderText(DEFAULT_URLS.get(k, ""))
        self._show_row(self.site_row, k == "manual")
        self.images.setVisible(has_model)
        self.test.setVisible(has_model)
        self.sign_in.setVisible(k == "claude_app")
        self.claude_status.setVisible(k == "claude_app")
        if k == "claude_app":
            exe = find_claude()
            self.claude_status.setText(f"Found Claude's command-line tool:\n{exe}" if exe else
                                       "Couldn't find Claude's command-line tool. Install or update the Claude "
                                       "desktop app, then reopen this window.")
        self.key.setText(keys.get_key(k) if k in NEEDS_KEY or k == "compatible" else "")
        cur = self.model.currentText() or self._model_cfg
        self.model.clear()
        self.model.addItems(SUGGESTED.get(k, []))
        if cur and self._model_cfg and k == self._kind_loaded():
            self.model.setCurrentText(self._model_cfg)
        self.result.setText("")

    def _kind_loaded(self):
        return (settings.get("ai") or {}).get(self.role, {}).get("provider")

    def config(self):
        return {"provider": self.kind(), "model": self.model.currentText().strip(), "url": self.url.text().strip(),
                "images": self.images.isChecked(), "site": self.site.currentData(),
                "persona": prompts.clean_persona(self.persona.currentText())}

    def save_key(self):
        k = self.kind()
        if k in NEEDS_KEY or k == "compatible":
            keys.set_key(k, self.key.text().strip())

    def _list_models(self):
        self.save_key()
        cfg = self.config()
        self.result.setText("Asking for the model list…")
        a = _Async(self)
        a.done.connect(self._models_listed)
        a.failed.connect(lambda m: self.result.setText(f"⚠ {m}"))
        a.go(lambda: make(cfg).list_models())

    def _models_listed(self, names):
        cur = self.model.currentText()
        self.model.clear()
        self.model.addItems(names or [])
        if cur:
            self.model.setCurrentText(cur)
        if names:
            self.result.setText(f"{len(names)} models — pick one from the Model list")
            self.model.showPopup()  # show the names right away
        else:
            self.result.setText("No models found")

    def _test(self):
        self.save_key()
        cfg = self.config()
        self.result.setText("Testing…")
        t0 = time.time()
        r = Run(cfg, "You are a connection test. Reply with exactly the word: Ready",
                [{"role": "user", "text": "Connection test."}], max_tokens=20)
        r.done.connect(lambda text: self.result.setText(f"✓ Working — replied “{text.strip()[:40]}” in "
                                                         f"{time.time() - t0:.1f} s"))
        r.failed.connect(lambda m, login: self.result.setText(f"⚠ {m}"))
        self._run = r
        r.start()

    def _sign_in(self):
        exe = find_claude()
        if exe:
            claude_login(exe)
            self.result.setText("Finish signing in in the window that opened, then press Test.")


class AISettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Co-Investigator (AI) settings")
        self.resize(640, 820)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(22, 18, 22, 12)
        v.setSpacing(10)
        head = QHBoxLayout()
        ic = label("")
        ic.setPixmap(theme.icon_pixmap("coinv", 28, theme.COINV))
        head.addWidget(ic)
        head.addWidget(label("Co-Investigator", "Title"))
        head.addStretch(1)
        v.addLayout(head)
        v.addWidget(label("AI only runs when you ask: the Co-Investigator panel on a board, or “Draft notes” in the "
                          "evidence editor. Whatever you choose receives that board's text (and slide images if "
                          "enabled). Ollama keeps everything on this computer.", "Muted", wrap=True))
        ai = settings.get("ai") or {}
        v.addWidget(section_label("Co-Investigator — answers questions about your boards"))
        self.coinv = RoleBox(self, "coinv", ai.get("coinv") or {})
        v.addWidget(self.coinv)
        v.addWidget(hsep())
        v.addWidget(section_label("Note drafting — the “Draft notes” button in the evidence editor"))
        notes_cfg = ai.get("notes") or {}
        self.same = QCheckBox("Use the same model as the Co-Investigator")
        self.same.setChecked(notes_cfg.get("same", True))
        v.addWidget(self.same)
        self.persona_too = QCheckBox("Use the Co-Investigator's personality for drafted notes too")
        self.persona_too.setChecked(bool(notes_cfg.get("persona_too", False)))
        v.addWidget(self.persona_too)
        v.addWidget(label("Tip: a small, fast model is plenty for notes — e.g. Claude haiku, gemini flash or a "
                          "local llama3.2-vision.", "Hint", wrap=True))
        self.notes = RoleBox(self, "notes", notes_cfg)
        self.notes.setVisible(not self.same.isChecked())
        self.same.toggled.connect(lambda on: self.notes.setVisible(not on))
        v.addWidget(self.notes)
        v.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        bar = QHBoxLayout()
        bar.setContentsMargins(22, 8, 22, 16)
        bar.addStretch(1)
        c = push_button("Cancel")
        c.clicked.connect(self.reject)
        ok = push_button("Save", "save", primary=True)
        ok.clicked.connect(self.accept)
        bar.addWidget(c)
        bar.addWidget(ok)
        outer.addLayout(bar)

    def accept(self):
        self.coinv.save_key()
        notes = self.notes.config()
        if not self.same.isChecked():
            self.notes.save_key()
        notes["same"] = self.same.isChecked()
        notes["persona_too"] = self.persona_too.isChecked()
        notes.pop("persona", None)
        settings.set("ai", {"coinv": self.coinv.config(), "notes": notes})
        super().accept()
